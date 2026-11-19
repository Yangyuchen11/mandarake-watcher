"""
定时调度器模块 (Scheduler)
支持定点准时触发 + 上新密集爆发检测 (Burst Mode) + 日常平缓轮询巡检
"""
import time
import threading
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple, Any

from core.monitor import MandarakeMonitor

logger = logging.getLogger("mandarake.scheduler")


class MandarakeScheduler:
    def __init__(self, monitor: MandarakeMonitor):
        self.monitor = monitor
        self.monitor.scheduler = self
        self.running = False
        self.paused = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._resume_event = threading.Event()
        self._interrupt_event = threading.Event()
        self.in_burst_mode = False
        self.burst_reason: str = ""
        self.burst_end_time = 0.0
        self.consecutive_rate_limits: int = 0
        self.backoff_until: float = 0.0
        self._status_listeners = []

        # 每日凌晨 05:00 库存巡检状态追踪
        self.last_daily_sync_date: str = ""
        self.is_syncing_daily: bool = False
        self.last_daily_sync_summary: str = "尚未执行"
        self._sync_lock = threading.Lock()

    def add_status_listener(self, listener):
        """注册调度状态变更监听器（用于主动向前端 SSE 推送状态切换）"""
        if callable(listener) and listener not in self._status_listeners:
            self._status_listeners.append(listener)

    def _emit_status(self):
        if not self._status_listeners:
            return
        st = self.get_status()
        for cb in list(self._status_listeners):
            try:
                cb(st)
            except Exception:
                pass

    def wake(self):
        """立即唤醒调度等待（用于新参数保存后即时生效）"""
        self._interrupt_event.set()

    def start(self):
        if self.running:
            return
        self.running = True
        self.paused = False
        self._stop_event.clear()
        self._resume_event.clear()
        self._interrupt_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="MandarakeSchedulerThread")
        self._thread.start()
        logger.info("Mandarake 定时监控调度器已启动")

    def stop(self):
        if not self.running:
            return
        self.running = False
        self.paused = False
        self._stop_event.set()
        self._resume_event.set()
        self._interrupt_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        try:
            self.monitor.wait_notifications(timeout=10.0)
        except Exception:
            pass
        logger.info("Mandarake 定时监控调度器已停止")

    def pause(self) -> bool:
        """暂停监控调度"""
        if not self.running:
            return False
        self.paused = True
        self._interrupt_event.set()
        logger.info("⏸️ Mandarake 监控调度器已暂停")
        return True

    def resume(self) -> bool:
        """恢复监控调度"""
        if not self.running:
            return False
        self.paused = False
        self._resume_event.set()
        logger.info("▶️ Mandarake 监控调度器已恢复运行")
        return True

    def trigger_burst(self, duration_minutes: Optional[int] = None, reason: str = "manual", wake: bool = True):
        """启动高频爆发模式（支持手动触发与命中自动追击，立即打断日常等待睡眠）"""
        if duration_minutes is None:
            sched_cfg = self.monitor.config.get("schedule", {})
            duration_minutes = int(sched_cfg.get("burst_duration_minutes", 3))
        duration_minutes = max(1, int(duration_minutes))
        self.in_burst_mode = True
        self.burst_reason = reason or "manual"
        self.burst_end_time = time.time() + duration_minutes * 60
        if wake:
            self._interrupt_event.set()
        if self.burst_reason == "auto":
            logger.info(f"⚡ 命中新上架商品！自动启动高频追击模式，持续 {duration_minutes} 分钟！")
        else:
            logger.info(f"⚡ 手动启动高频爆发抢购模式，持续 {duration_minutes} 分钟！")

    def stop_burst(self):
        """手动停止爆发检测模式"""
        self.in_burst_mode = False
        self.burst_reason = ""
        self.burst_end_time = 0.0
        self._interrupt_event.set()
        logger.info("⚡ 手动停止高频爆发模式，恢复日常平缓监控")

    def run_daily_stock_sync(self) -> Dict[str, Any]:
        """
        每日凌晨 05:00 全量库存巡检任务 (亦支持手动即时触发)
        全量遍历扫描各分类包含售罄的所有分页数据，若发现商品已售罄则自动更新为售罄状态 (is_in_stock = 0)
        """
        with self._sync_lock:
            if self.is_syncing_daily:
                return {"success": False, "message": "每日库存巡检任务正在进行中，请勿重复触发"}
            self.is_syncing_daily = True

        from core.categories import category_mgr
        from core.scraper import MandarakeScraper

        target_codes = category_mgr.get_leaf_category_codes()
        logger.info(f"⏰ [05:00 全量库存巡检] 开始深度全量扫描 {len(target_codes)} 个细分分类的所有分页数据...")
        start_ts = time.time()
        total_scanned = 0
        sold_out_found = 0
        sync_scraper = MandarakeScraper()

        try:
            for idx, cat_code in enumerate(target_codes, 1):
                if self._stop_event.is_set():
                    break

                cat_name = category_mgr.get_category_name(cat_code)
                cat_items_count = 0
                cat_sold_out_count = 0
                page = 1

                while not self._stop_event.is_set():
                    try:
                        items, has_next, count = sync_scraper.fetch_category_page(
                            category_code=cat_code,
                            page=page,
                            in_stock_only=False,  # 抓取包含已售罄的全部商品
                            disp_count=240
                        )
                    except Exception as e:
                        logger.error(f"⏰ [05:00 巡检] 分类 {cat_name} 第 {page} 页抓取异常: {e}")
                        break

                    if not items and count == 0:
                        break

                    total_scanned += len(items)
                    cat_items_count += len(items)

                    # 批量更新入库
                    self.monitor.db.upsert_products_batch(items)

                    # 提取已标记为售罄的商品批次，明确锁定售罄状态
                    sold_ids = [it["id"] for it in items if it.get("is_in_stock") == 0]
                    if sold_ids:
                        self.monitor.db.mark_products_stock_batch(sold_ids, is_in_stock=0)
                        sold_out_found += len(sold_ids)
                        cat_sold_out_count += len(sold_ids)

                    logger.debug(
                        f"⏰ [05:00 巡检] [{idx}/{len(target_codes)}] {cat_name} 第 {page} 页: 本页 {len(items)} 件 (售罄 {len(sold_ids)})"
                    )

                    if not has_next:
                        break

                    page += 1

                # 记录分类抓取进度归档
                cat_in_stock_count = cat_items_count - cat_sold_out_count
                self.monitor.db.update_crawl_progress(
                    category_code=cat_code,
                    category_name=cat_name,
                    total_items=cat_items_count,
                    in_stock_items=cat_in_stock_count,
                    last_page=page,
                    status="COMPLETED" if not self._stop_event.is_set() else "STOPPED"
                )

                logger.info(
                    f"⏰ [05:00 巡检] [{idx}/{len(target_codes)}] {cat_name} 巡检完毕，共 {page} 页，扫描 {cat_items_count} 件 (确认售罄 {cat_sold_out_count} 件)"
                )

            duration = round(time.time() - start_ts, 1)
            summary = f"每日全量库存巡检完成！累计扫描 {total_scanned} 件，同步确认售罄 {sold_out_found} 件，耗时 {duration}s"
            self.last_daily_sync_summary = summary
            logger.info(f"✅ {summary}")
            return {
                "success": True,
                "total_scanned": total_scanned,
                "sold_out_found": sold_out_found,
                "duration": duration,
                "message": summary
            }
        except Exception as e:
            err = f"每日全量库存巡检异常: {e}"
            logger.error(err)
            self.last_daily_sync_summary = err
            return {"success": False, "error": err}
        finally:
            self.is_syncing_daily = False

    def get_status(self) -> Dict[str, Any]:
        """获取调度器当前状态"""
        now = time.time()
        burst_left = max(0, int(self.burst_end_time - now)) if self.in_burst_mode else 0
        backoff_left = max(0, int(self.backoff_until - now)) if self.backoff_until > now else 0
        sched_cfg = self.monitor.config.get("schedule", {})
        return {
            "running": self.running,
            "paused": self.paused,
            "in_burst_mode": self.in_burst_mode,
            "burst_reason": self.burst_reason if self.in_burst_mode else "",
            "burst_remaining_seconds": burst_left,
            "burst_duration_minutes": sched_cfg.get("burst_duration_minutes", 3),
            "burst_interval_seconds": sched_cfg.get("burst_interval_seconds", 3),
            "burst_retry_delay": sched_cfg.get("burst_retry_delay", 1),
            "interval_seconds": sched_cfg.get("interval_seconds", 15),
            "bypass_cache": bool(sched_cfg.get("bypass_cache", True)),
            "auto_burst_on_hit": bool(sched_cfg.get("auto_burst_on_hit", True)),
            "monitor_disp_count": int(sched_cfg.get("monitor_disp_count", 48)),
            "last_check_time": self.monitor.last_check_time,
            "last_scan_ms": getattr(self.monitor, "last_scan_ms", 0),
            "last_cache_control": getattr(self.monitor, "last_cache_control", ""),
            "total_checks": self.monitor.total_checks,
            "total_matches": self.monitor.total_matches,
            "total_rate_limits": getattr(self.monitor, "total_rate_limits", 0),
            "in_backoff": backoff_left > 0,
            "backoff_remaining_seconds": backoff_left,
            "last_daily_sync_date": self.last_daily_sync_date,
            "is_syncing_daily": self.is_syncing_daily,
            "last_daily_sync_summary": self.last_daily_sync_summary
        }

    def _run_loop(self):
        logger.info("调度循环线程启动...")
        while not self._stop_event.is_set():
            try:
                # 0. 检查是否处于暂停状态
                if self.paused:
                    self._resume_event.wait(timeout=0.5)
                    self._resume_event.clear()
                    continue

                # 1. 检查是否触发每日凌晨 05:00 定点库存巡检
                cur_dt = datetime.now()
                today_str = cur_dt.strftime("%Y-%m-%d")
                if cur_dt.hour == 5 and self.last_daily_sync_date != today_str and not self.is_syncing_daily:
                    self.last_daily_sync_date = today_str
                    logger.info("⏰ [05:00 定时触发] 触发每日凌晨 5:00 全量库存与售罄状态自动巡检...")
                    threading.Thread(target=self.run_daily_stock_sync, daemon=True, name="DailyStockSyncThread").start()

                # 2. 检查爆发模式状态
                now = time.time()
                if self.in_burst_mode and now >= self.burst_end_time:
                    self.in_burst_mode = False
                    self.burst_reason = ""
                    self.burst_end_time = 0.0
                    logger.info("爆发检测模式结束，恢复日常平缓监控")
                    self._emit_status()

                # 3. 执行日常上新与规则监控检测（统计单轮耗时以消除轮询漂移）
                cycle_start = time.time()
                matches = self.monitor.check_once()
                cycle_elapsed = time.time() - cycle_start

                if self._stop_event.is_set() or self.paused:
                    continue

                # 4. 429/503 限流自适应防封退避
                sched_cfg = self.monitor.config.get("schedule", {})
                if getattr(self.monitor, "last_rate_limited", False):
                    self.consecutive_rate_limits += 1
                    backoff_sec = min(120, 20 * (2 ** (self.consecutive_rate_limits - 1)))
                    self.backoff_until = time.time() + backoff_sec
                    if self.in_burst_mode:
                        self.in_burst_mode = False
                        self.burst_reason = ""
                        self.burst_end_time = 0.0
                    logger.warning(
                        f"🛡️ 检测到官网限流 (连续 {self.consecutive_rate_limits} 次)，已暂停高频并自动退避 {backoff_sec} 秒..."
                    )
                    self._emit_status()
                    self._interrupt_event.clear()
                    self._interrupt_event.wait(timeout=backoff_sec)
                    self._interrupt_event.clear()
                    self.backoff_until = 0.0
                    continue

                self.consecutive_rate_limits = 0
                self.backoff_until = 0.0

                # 5. 命中新上架后自动开启/续期短时高频追击，捕获同批次连续上架商品
                if matches and bool(sched_cfg.get("auto_burst_on_hit", True)):
                    self.trigger_burst(reason="auto", wake=False)
                    self._emit_status()

                # 6. 根据模式确定精确睡眠间隔（扣除本轮抓取耗时）
                if self.in_burst_mode:
                    burst_interval = float(sched_cfg.get("burst_interval_seconds", 3.0))
                    target_interval = max(0.5, burst_interval)
                    sleep_interval = max(0.5, target_interval - cycle_elapsed)
                else:
                    target_interval = max(3.0, float(sched_cfg.get("interval_seconds", 15)))
                    sleep_interval = max(0.5, target_interval - cycle_elapsed)
                    if not matches:
                        cache_tag = "缓存穿透" if getattr(self.monitor, "last_bypass_cache", True) else "普通请求"
                        logger.info(
                            f"⚡ [监控巡检] 第 {self.monitor.total_checks} 轮扫描完成 "
                            f"(耗时 {getattr(self.monitor, 'last_scan_ms', 0)}ms | {cache_tag})，"
                            f"暂无新上架商品 (下次扫描在 {sleep_interval:.1f} 秒后)"
                        )

                # 睡眠可被 pause()、stop()、trigger_burst()、wake() 立即中断
                self._interrupt_event.clear()
                self._interrupt_event.wait(timeout=sleep_interval)
                self._interrupt_event.clear()

            except Exception as e:
                logger.error(f"调度循环发生异常: {e}")
                self._stop_event.wait(timeout=5.0)
