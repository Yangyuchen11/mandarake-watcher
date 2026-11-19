"""
Mandarake 【ドール】 实时上新与在库变动监控引擎
负责执行关键词规则匹配、在库与售罄过滤、智能去重、多渠道提醒与自动触发下单
支持配置扫描间隔、扫描失败重试次数及重试间隔
"""
import os
import time
import json
import logging
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, Future, wait as futures_wait
from typing import List, Dict, Optional, Any, Tuple

from core.categories import ROOT_DOLL_CODE
from core.scraper import MandarakeScraper
from core.archive_db import archive_db
from notifiers.manager import NotificationManager

logger = logging.getLogger("mandarake.monitor")

SEEN_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seen_ids.json")
SEEN_MAX_SIZE = 50000


class MandarakeMonitor:
    """Mandarake 在库监控器"""

    def __init__(self, config: Dict[str, Any], notifier_mgr: NotificationManager):
        self.config = config
        self.notifier_mgr = notifier_mgr
        self.scraper = MandarakeScraper()
        self.db = archive_db
        self.scheduler = None

        self._check_lock = threading.Lock()
        self._notify_lock = threading.Lock()
        self._fetch_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="MandarakeFetch")
        self._notify_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="MandarakeNotify")
        self._pending_notify_futures: List[Future] = []

        self.seen_ids: "OrderedDict[str, None]" = OrderedDict()
        self._seen_dirty: bool = False
        self._last_seen_save_time: float = 0.0
        self.load_seen_cache()

        # 上新记录监听器列表
        self._record_listeners = []

        # 复活通知冷却追踪: {item_id: last_notified_timestamp}
        self.restock_notify_times: Dict[str, float] = {}
        # 复活防抖冷却时间 (默认 1800 秒 = 30 分钟)
        self.restock_cooldown_seconds = int(self.config.get("schedule", {}).get("restock_cooldown_seconds", 1800))

        # 统计与遥测计数
        self.last_check_time: float = 0.0
        self.total_checks: int = 0
        self.total_matches: int = 0
        self.last_check_status: str = "就绪"
        self.last_scan_ms: int = 0
        self.last_cache_control: str = ""
        self.last_rate_limited: bool = False
        self.last_bypass_cache: bool = bool(self.config.get("schedule", {}).get("bypass_cache", True))
        self.total_rate_limits: int = 0
        self.last_fetch_queries: int = 0

    def _mark_seen(self, pid: str):
        """有序 LRU 标记商品已扫描，最近见过的商品移至尾部避免随机淘汰"""
        key = str(pid).strip()
        if not key:
            return
        if key in self.seen_ids:
            self.seen_ids.move_to_end(key)
        else:
            self.seen_ids[key] = None
            self._seen_dirty = True
            while len(self.seen_ids) > SEEN_MAX_SIZE:
                self.seen_ids.popitem(last=False)

    def _submit_notification(self, items: List[Dict[str, Any]], rule_name: str):
        """异步提交推送任务，避免 Discord/SMTP 耗时阻塞后续扫描或下一轮轮询"""
        if not items:
            return

        def _send():
            try:
                self.notifier_mgr.dispatch(items, rule_name)
            except Exception as e:
                logger.error(f"异步分发规则 [{rule_name}] 通知异常: {e}")

        with self._notify_lock:
            self._pending_notify_futures = [f for f in self._pending_notify_futures if not f.done()]
            fut = self._notify_executor.submit(_send)
            self._pending_notify_futures.append(fut)

    def wait_notifications(self, timeout: float = 15.0):
        """等待队列中的异步通知投递完成（用于单次检测模式或优雅退出）"""
        with self._notify_lock:
            pending = [f for f in self._pending_notify_futures if not f.done()]
        if pending:
            futures_wait(pending, timeout=timeout)

    def shutdown(self):
        """优雅关闭监控线程池与异步通知执行器"""
        if self._seen_dirty:
            self.save_seen_cache()
        self.wait_notifications(timeout=10.0)
        self._fetch_executor.shutdown(wait=False)
        self._notify_executor.shutdown(wait=False)

    def add_record_listener(self, listener):
        """注册上新提醒记录监听器"""
        if callable(listener) and listener not in self._record_listeners:
            self._record_listeners.append(listener)

    def load_seen_cache(self):
        """加载已发现商品 ID 缓存，防止重复报警"""
        if os.path.exists(SEEN_CACHE_FILE):
            try:
                with open(SEEN_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.seen_ids.clear()
                        for pid in data:
                            self._mark_seen(str(pid))
                        self._seen_dirty = False
                        self._last_seen_save_time = time.time()
                logger.info(f"已加载已扫描商品缓存: {len(self.seen_ids)} 件")
            except Exception as e:
                logger.warning(f"读取已扫描缓存失败: {e}")

        # 若缓存文件为空或未初始化，从本地归档数据库恢复在库及历史新着商品，避免将已有/已售罄旧商品误判为秒切新品
        if not self.seen_ids and os.path.exists(self.db.db_path):
            try:
                with self.db._get_connection() as conn:
                    rows = conn.execute("SELECT id FROM products WHERE is_in_stock = 1 OR is_new = 1").fetchall()
                    if rows:
                        for r in rows:
                            self._mark_seen(str(r["id"]))
                        logger.info(f"已扫描缓存为空，已从本地数据库恢复 {len(self.seen_ids)} 件基准商品")
                        self.save_seen_cache()
            except Exception as e:
                logger.warning(f"从本地数据库恢复基准商品异常: {e}")

    def save_seen_cache(self):
        """原子持久化已发现商品 ID（保留有序 LRU 队列末尾最新记录）"""
        try:
            cache_dir = os.path.dirname(os.path.abspath(SEEN_CACHE_FILE))
            os.makedirs(cache_dir, exist_ok=True)
            tmp_file = f"{SEEN_CACHE_FILE}.tmp"
            items_to_save = list(self.seen_ids.keys())[-SEEN_MAX_SIZE:]
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(items_to_save, f)
            os.replace(tmp_file, SEEN_CACHE_FILE)
            self._seen_dirty = False
            self._last_seen_save_time = time.time()
        except Exception as e:
            logger.warning(f"持久化已扫描缓存失败: {e}")

    def reset_seen(self):
        """清空已发现缓存"""
        self.seen_ids.clear()
        self._seen_dirty = True
        self.save_seen_cache()
        logger.info("已清空已扫描商品去重缓存")

    def init_baseline(self) -> int:
        """一键将当前所有在售及首页最新商品设为基准线（只收录不报警）"""
        with self._check_lock:
            logger.info("正在建立当前库存基准线...")
            rules = self.config.get("rules")
            if rules is None:
                rules = [{"name": "全品基准线", "category_code": ROOT_DOLL_CODE, "keywords": []}]

            seen_queries = set()
            for r in rules:
                if not r.get("enabled", True):
                    continue
                cat_code = r.get("category_code") or ROOT_DOLL_CODE
                kws = [k.strip() for k in r.get("keywords", []) if k.strip()]
                match_mode = r.get("match_mode", "and").lower()
                kw_param = " ".join(kws) if (kws and match_mode == "and") else (kws[0] if len(kws) == 1 else None)
                q_key = (cat_code, kw_param)
                if q_key in seen_queries:
                    continue
                seen_queries.add(q_key)
                items, _, _ = self.scraper.fetch_category_page(
                    category_code=cat_code,
                    page=1,
                    in_stock_only=False,
                    disp_count=120,
                    keyword=kw_param,
                    polite_delay=False,
                    bypass_cache=True,
                )
                for item in items:
                    self._mark_seen(str(item["id"]))

            # 同时同步本地归档数据库中所有在库及新着商品至基准线
            try:
                with self.db._get_connection() as conn:
                    rows = conn.execute("SELECT id FROM products WHERE is_in_stock = 1 OR is_new = 1").fetchall()
                    for r in rows:
                        self._mark_seen(str(r["id"]))
            except Exception as e:
                logger.warning(f"同步数据库已有商品至基准线失败: {e}")

            self.save_seen_cache()
            logger.info(f"基准线建立完成，已记录 {len(self.seen_ids)} 件商品为已知基准")
            return len(self.seen_ids)

    def check_once(self) -> List[Dict[str, Any]]:
        """执行单轮监控检测（互斥加锁防重叠、查询去重并发抓取、自适应防嵌套重试）"""
        with self._check_lock:
            return self._check_once_impl()

    def _check_once_impl(self) -> List[Dict[str, Any]]:
        scan_start = time.perf_counter()
        self.last_check_time = time.time()
        self.total_checks += 1
        self.last_rate_limited = False
        self.last_cache_control = ""
        self.last_fetch_queries = 0
        found_matches: List[Dict[str, Any]] = []

        sched_cfg = self.config.get("schedule", {})
        self.restock_cooldown_seconds = int(sched_cfg.get("restock_cooldown_seconds", 1800))
        bypass_cache = bool(sched_cfg.get("bypass_cache", True))
        self.last_bypass_cache = bypass_cache
        try:
            monitor_disp_count = max(48, min(240, int(sched_cfg.get("monitor_disp_count", 48))))
        except (ValueError, TypeError):
            monitor_disp_count = 48

        raw_retry = max(0, int(sched_cfg.get("retry_count", 3)))
        in_burst = bool(getattr(self, "scheduler", None) and self.scheduler.in_burst_mode)
        if in_burst:
            effective_retries = min(raw_retry, 2)
            retry_delay = max(0.1, float(sched_cfg.get("burst_retry_delay", 1.0)))
            req_timeout = 8
        else:
            effective_retries = min(raw_retry, 3)
            retry_delay = max(0.5, float(sched_cfg.get("retry_delay", 2.0)))
            req_timeout = 10

        rules = self.config.get("rules")
        if rules is None:
            # 默认规则：监控【ドール】大分类下的最新在库商品
            rules = [{
                "id": "rule_root_doll",
                "name": "ドール全品上新监控",
                "category_code": ROOT_DOLL_CODE,
                "keywords": [],
                "exclude_keywords": [],
                "max_price": 0,
                "enabled": True
            }]

        active_rules: List[Tuple[Dict[str, Any], Tuple[str, Optional[str]]]] = []
        for rule in rules:
            if not rule.get("enabled", True):
                continue
            cat_code = rule.get("category_code") or ROOT_DOLL_CODE
            kws = [k.strip() for k in rule.get("keywords", []) if k.strip()]
            match_mode = rule.get("match_mode", "and").lower()
            kw_param = " ".join(kws) if (kws and match_mode == "and") else (kws[0] if len(kws) == 1 else None)
            active_rules.append((rule, (cat_code, kw_param)))

        if not active_rules:
            self.last_scan_ms = int((time.perf_counter() - scan_start) * 1000)
            self.last_check_status = "无启用监控规则"
            return []

        unique_keys = list(dict.fromkeys(q_key for _, q_key in active_rules))
        self.last_fetch_queries = len(unique_keys)

        def _fetch_query(q_key: Tuple[str, Optional[str]]) -> Dict[str, Any]:
            cat_code, kw_param = q_key
            last_res: Dict[str, Any] = {
                "ok": False, "items": [], "rate_limited": False,
                "cache_control": "", "status_code": 0, "error": ""
            }
            for attempt in range(effective_retries + 1):
                try:
                    res = self.scraper.fetch_category_page_meta(
                        category_code=cat_code,
                        page=1,
                        in_stock_only=False,
                        disp_count=monitor_disp_count,
                        keyword=kw_param,
                        polite_delay=False,
                        bypass_cache=bypass_cache,
                        max_retries=0,
                        timeout=req_timeout,
                    )
                    last_res = res
                    if res.get("ok"):
                        return res
                    if res.get("rate_limited"):
                        logger.warning(
                            f"查询 ({cat_code}, {kw_param or '全量'}) 触发官网限流 ({res.get('status_code')})，立即中止本轮重试等待退避"
                        )
                        return res
                except Exception as e:
                    logger.warning(f"查询 ({cat_code}, {kw_param or '全量'}) 第 {attempt + 1} 次抓取异常: {e}")
                    last_res = {
                        "ok": False, "items": [], "rate_limited": False,
                        "cache_control": "", "status_code": 0, "error": str(e)
                    }

                if attempt < effective_retries:
                    logger.warning(
                        f"查询 ({cat_code}, {kw_param or '全量'}) 页面抓取失败，将在 {retry_delay} 秒后重试 ({attempt + 1}/{effective_retries})..."
                    )
                    time.sleep(retry_delay)
            return last_res

        results_map: Dict[Tuple[str, Optional[str]], Dict[str, Any]] = {}
        if len(unique_keys) == 1:
            q_key = unique_keys[0]
            results_map[q_key] = _fetch_query(q_key)
        else:
            future_to_key = {self._fetch_executor.submit(_fetch_query, k): k for k in unique_keys}
            for fut, q_key in future_to_key.items():
                try:
                    results_map[q_key] = fut.result()
                except Exception as e:
                    logger.error(f"并发抓取任务异常 ({q_key}): {e}")
                    results_map[q_key] = {
                        "ok": False, "items": [], "rate_limited": False,
                        "cache_control": "", "status_code": 0, "error": str(e)
                    }

        any_rate_limited = any(r.get("rate_limited") for r in results_map.values())
        if any_rate_limited:
            self.last_rate_limited = True
            self.total_rate_limits += 1

        for r in results_map.values():
            cc = r.get("cache_control")
            if cc:
                self.last_cache_control = cc
                break

        # 整轮单次批量查询商品在库状态，彻底消除循环内 N+1 查库开销
        all_pids: List[str] = []
        seen_pid_set = set()
        for r in results_map.values():
            if r.get("ok"):
                for it in (r.get("items") or []):
                    p = str(it.get("id", "")).strip()
                    if p and p not in seen_pid_set:
                        seen_pid_set.add(p)
                        all_pids.append(p)

        if hasattr(self.db, "get_products_stock_map"):
            stock_map = self.db.get_products_stock_map(all_pids)
        else:
            stock_map = {}
            for p in all_pids:
                rec = self.db.get_product(p)
                if rec is not None:
                    stock_map[p] = int(rec.get("is_in_stock", 1))

        sold_out_to_mark: List[str] = []
        failed_rules = 0
        for rule, q_key in active_rules:
            rule_name = rule.get("name", "未命名规则")
            kws = [k.strip() for k in rule.get("keywords", []) if k.strip()]
            ex_kws = [k.strip() for k in rule.get("exclude_keywords", []) if k.strip()]
            match_mode = rule.get("match_mode", "and").lower()  # and 或 or
            max_price = rule.get("max_price", 0)

            logger.debug(f"[Monitor] 检查规则: {rule_name} (分类: {q_key[0]}, 关键词: {kws})")
            res = results_map.get(q_key, {})
            if not res.get("ok"):
                failed_rules += 1
                logger.error(f"规则 [{rule_name}] 抓取失败 ({res.get('error') or res.get('status_code')})，已跳过本轮")
                continue

            items = res.get("items") or []

            # 规则匹配过滤
            rule_hits = []
            for item in items:
                pid = str(item["id"])
                title = item.get("title", "")
                price = item.get("price", 0)
                item_no = item.get("item_no", "")
                is_in_stock = int(item.get("is_in_stock", 1))
                combined_text = f"{title} {item_no}".lower()

                # 排除关键词过滤
                if any(ex.lower() in combined_text for ex in ex_kws):
                    continue

                # 关键词匹配
                if kws:
                    if match_mode == "or":
                        # OR 模式：满足任一关键词即命中
                        if not any(kw.lower() in combined_text for kw in kws):
                            continue
                    else:
                        # AND 模式：必须同时满足所有关键词
                        if not all(kw.lower() in combined_text for kw in kws):
                            continue

                # 价格上限过滤
                if max_price > 0 and price > max_price:
                    continue

                is_new = int(item.get("is_new", 0))
                prev_stock = stock_map.get(pid)

                # --- 状态与新发现判定 ---
                if is_in_stock == 0:
                    # 1. 状态由在库(1)刚转为售罄(0)：此前在库记录有效，官网刚被买走切掉
                    if prev_stock == 1:
                        sold_out_to_mark.append(pid)
                        stock_map[pid] = 0
                        self._mark_seen(pid)
                        logger.info(f"🏷️ [监控追踪] 规则 [{rule_name}] 设定的关注商品已售罄: {title} (ID: {pid})")
                        continue

                    # 2. 数据库此前已记录为售罄(0)：历史旧售罄商品，即使仍挂着 is_new 标签也绝不误报秒切
                    if prev_stock == 0:
                        self._mark_seen(pid)
                        continue

                    # 3. 从未见过的全新商品，但在列表页显示为售罄 (上新即秒切，先用官方实时 API 复核库存)
                    notify_instant_soldout = rule.get("notify_instant_soldout", True)
                    if pid not in self.seen_ids and notify_instant_soldout and is_new == 1:
                        real_stock = self.scraper.check_item_realtime_stock(pid)
                        self._mark_seen(pid)
                        if real_stock == 1:
                            item_copy = dict(item)
                            item_copy["is_in_stock"] = 1
                            item_copy["instant_soldout"] = False
                            rule_hits.append(item_copy)
                            found_matches.append(item_copy)
                            logger.info(
                                f"⚡ [实时库存修正] 规则 [{rule_name}] 列表页显示售罄但官网实时接口确认为有货: {title} (ID: {pid}, 售价: {price})！"
                            )
                            continue

                        item_copy = dict(item)
                        item_copy["instant_soldout"] = True
                        rule_hits.append(item_copy)
                        found_matches.append(item_copy)
                        logger.info(f"⚡ [秒切提醒] 规则 [{rule_name}] 捕获新上架但已被买走的秒切商品: {title} (ID: {pid}, 售价: {price})！")
                        continue

                    # 4. 历史已售罄旧商品：静默记录，杜绝每轮重复刷屏
                    self._mark_seen(pid)
                    continue

                # 在库商品 (is_in_stock == 1)
                if pid in self.seen_ids:
                    # 检查是否为“此前记录为售罄，如今补货/退订复活”的商品
                    if prev_stock == 0:
                        # 核心校验 1: 官方实时库存核心 API 二次 Double-Check，过滤列表页缓存抖动
                        real_stock = self.scraper.check_item_realtime_stock(pid)
                        if real_stock == 0:
                            # 官方核心接口确认为售罄，属列表页缓存延迟/抖动，维持售罄并忽略
                            logger.debug(f"[Monitor] 商品 {title} (ID: {pid}) 列表页疑似复活但官方实时接口确认为售罄，已过滤虚假抖动")
                            self._mark_seen(pid)
                            continue
                        elif real_stock == 1:
                            logger.info(f"✅ [复活二次校验通过] 商品 {title} (ID: {pid}) 官方实时接口确认为在库有货！")

                        now_time = time.time()
                        last_restock_time = self.restock_notify_times.get(pid, 0.0)
                        self.db.mark_product_stock(pid, is_in_stock=1)
                        stock_map[pid] = 1

                        # 核心校验 2: 复活提醒防抖冷却机制 (默认 30 分钟内不重复发信轰炸)
                        if (now_time - last_restock_time) < self.restock_cooldown_seconds:
                            logger.info(
                                f"🔄 [补货复活-冷却中] 规则 [{rule_name}] 商品 {title} (ID: {pid}) 处于防抖冷却期内 "
                                f"({int(now_time - last_restock_time)}s < {self.restock_cooldown_seconds}s)，已静默更新在库状态"
                            )
                            self._mark_seen(pid)
                            continue

                        self.restock_notify_times[pid] = now_time
                        item_copy = dict(item)
                        item_copy["is_restock"] = True
                        rule_hits.append(item_copy)
                        found_matches.append(item_copy)
                        logger.info(f"🔄 [补货复活] 规则 [{rule_name}] 发现此前售罄商品重新上架有货: {title} (ID: {pid})！")
                    self._mark_seen(pid)
                    continue

                # 命中全新上架在库目标
                self._mark_seen(pid)
                item_copy = dict(item)
                item_copy["instant_soldout"] = False
                rule_hits.append(item_copy)
                found_matches.append(item_copy)

            if rule_hits:
                self.total_matches += len(rule_hits)
                in_stock_hits = [x for x in rule_hits if x.get("is_in_stock") == 1]
                soldout_hits = [x for x in rule_hits if x.get("is_in_stock") == 0 or x.get("instant_soldout")]
                logger.info(f"🎯 规则 [{rule_name}] 命中 {len(rule_hits)} 件新商品 (在库: {len(in_stock_hits)}, 秒切已售: {len(soldout_hits)})！")

                # 优先异步分发通知（不阻塞后续规则匹配与数据库写入）
                self._submit_notification(list(rule_hits), rule_name)

                # 写入数据库归档并同步更新内存库存映射
                self.db.upsert_products_batch(rule_hits)
                for hit_item in rule_hits:
                    hp = str(hit_item.get("id", "")).strip()
                    if hp:
                        stock_map[hp] = int(hit_item.get("is_in_stock", 1))

                # 写入上新提醒记录表
                now_ts = int(time.time())
                records_to_insert = []
                for hit_item in rule_hits:
                    records_to_insert.append({
                        "item_id": str(hit_item.get("id", "")),
                        "title": hit_item.get("title", ""),
                        "price": hit_item.get("price", 0),
                        "price_str": hit_item.get("price_str", ""),
                        "url": hit_item.get("url", ""),
                        "img_url": hit_item.get("img_url", ""),
                        "shop": hit_item.get("shop", ""),
                        "item_no": hit_item.get("item_no", ""),
                        "category_code": hit_item.get("category_code", ""),
                        "category_name": hit_item.get("category_name", ""),
                        "rule_name": rule_name,
                        "created_at": now_ts,
                        "raw_details": json.dumps(hit_item, ensure_ascii=False) if isinstance(hit_item, dict) else ""
                    })
                self.db.insert_monitor_records(records_to_insert)

                # 触发上新记录监听回调 (通知前端实时广播)
                for cb in self._record_listeners:
                    try:
                        cb(records_to_insert)
                    except Exception as e:
                        logger.warning(f"触发上新记录监听回调异常: {e}")

        if sold_out_to_mark:
            dedup_sold = list(dict.fromkeys(sold_out_to_mark))
            if hasattr(self.db, "mark_products_stock_batch"):
                self.db.mark_products_stock_batch(dedup_sold, is_in_stock=0)
            else:
                for spid in dedup_sold:
                    self.db.mark_product_stock(spid, is_in_stock=0)

        self.last_scan_ms = int((time.perf_counter() - scan_start) * 1000)
        if self.last_rate_limited:
            self.last_check_status = f"触发官网限流，等待自动退避 ({self.last_scan_ms}ms)"
        elif failed_rules > 0 and not found_matches:
            self.last_check_status = f"部分规则抓取失败 ({self.last_scan_ms}ms)"
        else:
            self.last_check_status = f"正常完成，命中 {len(found_matches)} 件 ({self.last_scan_ms}ms)"
        if self._seen_dirty or (self.seen_ids and (time.time() - self._last_seen_save_time) >= 300.0):
            self.save_seen_cache()
        return found_matches
