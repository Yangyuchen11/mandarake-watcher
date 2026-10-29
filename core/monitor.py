"""
Mandarake 【ドール】 实时上新与在库变动监控引擎
负责执行关键词规则匹配、在库与售罄过滤、智能去重、多渠道提醒与自动触发下单
支持配置扫描间隔、扫描失败重试次数及重试间隔
"""
import os
import time
import json
import logging
from typing import List, Dict, Set, Optional, Any

from core.categories import ROOT_DOLL_CODE
from core.scraper import MandarakeScraper
from core.archive_db import archive_db
from notifiers.manager import NotificationManager

logger = logging.getLogger("mandarake.monitor")

SEEN_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seen_ids.json")


class MandarakeMonitor:
    """Mandarake 在库监控器"""

    def __init__(self, config: Dict[str, Any], notifier_mgr: NotificationManager):
        self.config = config
        self.notifier_mgr = notifier_mgr
        self.scraper = MandarakeScraper()
        self.db = archive_db
        self.scheduler = None

        self.seen_ids: Set[str] = set()
        self.load_seen_cache()

        # 上新记录监听器列表
        self._record_listeners = []

        # 统计计数
        self.last_check_time: float = 0.0
        self.total_checks: int = 0
        self.total_matches: int = 0
        self.last_check_status: str = "就绪"

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
                        self.seen_ids = set(data)
                logger.info(f"已加载已扫描商品缓存: {len(self.seen_ids)} 件")
            except Exception as e:
                logger.warning(f"读取已扫描缓存失败: {e}")

        # 若缓存文件为空或未初始化，尝试从本地归档数据库自动同步在库商品，避免重启或初次运行时将已有库存当作新品
        if not self.seen_ids and os.path.exists(self.db.db_path):
            try:
                with self.db._get_connection() as conn:
                    rows = conn.execute("SELECT id FROM products WHERE is_in_stock = 1").fetchall()
                    if rows:
                        self.seen_ids = {str(r["id"]) for r in rows}
                        logger.info(f"已扫描缓存为空，已从本地数据库恢复 {len(self.seen_ids)} 件在库商品至基准线")
                        self.save_seen_cache()
            except Exception as e:
                logger.warning(f"从本地数据库恢复在库商品至基准线异常: {e}")

    def save_seen_cache(self):
        """持久化已发现商品 ID"""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(SEEN_CACHE_FILE)), exist_ok=True)
            with open(SEEN_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(list(self.seen_ids)[-10000:], f)  # 最多保留最近 10000 条
        except Exception as e:
            logger.warning(f"持久化已扫描缓存失败: {e}")

    def reset_seen(self):
        """清空已发现缓存"""
        self.seen_ids.clear()
        self.save_seen_cache()
        logger.info("已清空已扫描商品去重缓存")

    def init_baseline(self) -> int:
        """一键将当前所有在售商品设为基准线（只收录不报警）"""
        logger.info("正在建立当前库存基准线...")
        rules = self.config.get("rules")
        if rules is None:
            rules = [{"name": "全品基准线", "category_code": ROOT_DOLL_CODE, "keywords": []}]

        count = 0
        for r in rules:
            if not r.get("enabled", True):
                continue
            cat_code = r.get("category_code") or ROOT_DOLL_CODE
            items, _, _ = self.scraper.fetch_category_page(category_code=cat_code, page=1, in_stock_only=True)
            for item in items:
                self.seen_ids.add(str(item["id"]))
                count += 1

        # 同时同步本地归档数据库中所有在库商品至基准线
        try:
            with self.db._get_connection() as conn:
                rows = conn.execute("SELECT id FROM products WHERE is_in_stock = 1").fetchall()
                for r in rows:
                    self.seen_ids.add(str(r["id"]))
        except Exception as e:
            logger.warning(f"同步数据库已有商品至基准线失败: {e}")

        self.save_seen_cache()
        logger.info(f"基准线建立完成，已记录 {len(self.seen_ids)} 件在库商品为已知库存")
        return len(self.seen_ids)

    def check_once(self) -> List[Dict[str, Any]]:
        """执行单轮监控检测，带自动重试机制"""
        self.last_check_time = time.time()
        self.total_checks += 1
        found_matches: List[Dict[str, Any]] = []

        sched_cfg = self.config.get("schedule", {})
        retry_count = max(0, int(sched_cfg.get("retry_count", 3)))
        in_burst = getattr(self, "scheduler", None) and self.scheduler.in_burst_mode
        if in_burst:
            retry_delay = max(0.1, float(sched_cfg.get("burst_retry_delay", 1.0)))
        else:
            retry_delay = max(0.5, float(sched_cfg.get("retry_delay", 5.0)))

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

        for rule in rules:
            if not rule.get("enabled", True):
                continue

            rule_name = rule.get("name", "未命名规则")
            cat_code = rule.get("category_code") or ROOT_DOLL_CODE
            kws = [k.strip() for k in rule.get("keywords", []) if k.strip()]
            ex_kws = [k.strip() for k in rule.get("exclude_keywords", []) if k.strip()]
            match_mode = rule.get("match_mode", "and").lower()  # and 或 or
            max_price = rule.get("max_price", 0)

            logger.debug(f"[Monitor] 检查规则: {rule_name} (分类: {cat_code}, 关键词: {kws})")

            # 若规则设定了关键词，直接作为检索参数发起精准查询
            kw_param = " ".join(kws) if (kws and match_mode == "and") else (kws[0] if len(kws) == 1 else None)

            # 带重试抓取第一页最新全量商品 (包含在库与售罄，dispCount=120)
            items = None
            for attempt in range(retry_count + 1):
                try:
                    items, _, _ = self.scraper.fetch_category_page(
                        category_code=cat_code,
                        page=1,
                        in_stock_only=False,
                        disp_count=120,
                        keyword=kw_param
                    )
                    if items is not None and not self.scraper.last_fetch_failed:
                        break
                except Exception as e:
                    logger.warning(f"规则 [{rule_name}] 第 {attempt + 1} 次抓取异常: {e}")

                if attempt < retry_count:
                    logger.warning(f"规则 [{rule_name}] 页面抓取失败，将在 {retry_delay} 秒后重试 ({attempt + 1}/{retry_count})...")
                    time.sleep(retry_delay)

            if items is None:
                logger.error(f"规则 [{rule_name}] 连续重试 {retry_count} 次后仍抓取失败，已跳过本轮")
                self.last_check_status = f"规则 [{rule_name}] 抓取失败"
                continue

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

                # --- 状态与新发现判定 ---
                if is_in_stock == 0:
                    # 1. 之前已扫描并提醒过的商品，现在在官网上售罄了：同步数据库为售罄
                    if pid in self.seen_ids:
                        self.db.mark_product_stock(pid, is_in_stock=0)
                        logger.info(f"🏷️ [监控追踪] 规则 [{rule_name}] 设定的关注商品已售罄: {title} (ID: {pid})")
                        continue

                    # 2. 从未见过的全新商品，但在系统检测前就已经售罄 (上新即秒切)
                    notify_instant_soldout = rule.get("notify_instant_soldout", True)
                    # 仅针对带有官网新着标志 (is_new == 1) 的商品触发秒切提醒，避免干扰陈年历史售罄商品
                    if notify_instant_soldout and is_new == 1:
                        self.seen_ids.add(pid)
                        item_copy = dict(item)
                        item_copy["instant_soldout"] = True
                        rule_hits.append(item_copy)
                        found_matches.append(item_copy)
                        logger.info(f"⚡ [秒切提醒] 规则 [{rule_name}] 捕获新上架但已被买走的秒切商品: {title} (ID: {pid}, 售价: {price})！")
                    else:
                        # 普通陈年历史售罄商品 (is_new == 0)，直接收录到 seen_ids 防止后续重复处理
                        self.seen_ids.add(pid)
                    continue

                # 在库商品 (is_in_stock == 1)
                if pid in self.seen_ids:
                    # 检查是否为“此前记录为售罄，如今补货/退订复活”的商品
                    prev_rec = self.db.get_product(pid)
                    if prev_rec and prev_rec.get("is_in_stock") == 0:
                        self.db.mark_product_stock(pid, is_in_stock=1)
                        item_copy = dict(item)
                        item_copy["is_restock"] = True
                        rule_hits.append(item_copy)
                        found_matches.append(item_copy)
                        logger.info(f"🔄 [补货复活] 规则 [{rule_name}] 发现此前售罄商品重新上架有货: {title} (ID: {pid})！")
                    continue

                # 命中全新上架在库目标
                self.seen_ids.add(pid)
                item_copy = dict(item)
                item_copy["instant_soldout"] = False
                rule_hits.append(item_copy)
                found_matches.append(item_copy)

            if rule_hits:
                self.total_matches += len(rule_hits)
                in_stock_hits = [x for x in rule_hits if x.get("is_in_stock") == 1]
                soldout_hits = [x for x in rule_hits if x.get("is_in_stock") == 0 or x.get("instant_soldout")]
                logger.info(f"🎯 规则 [{rule_name}] 命中 {len(rule_hits)} 件新商品 (在库: {len(in_stock_hits)}, 秒切已售: {len(soldout_hits)})！")
                
                # 写入数据库归档
                self.db.upsert_products_batch(rule_hits)

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

                # 分发通知 (推送到已启用的 Discord Webhook 与 SMTP 邮件)
                self.notifier_mgr.dispatch(rule_hits, rule_name)

        self.last_check_status = f"正常完成，命中 {len(found_matches)} 件"
        self.save_seen_cache()
        return found_matches
