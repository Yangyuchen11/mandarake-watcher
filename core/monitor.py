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
from hooks.auto_order import auto_order_engine

logger = logging.getLogger("mandarake.monitor")

SEEN_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seen_ids.json")


class MandarakeMonitor:
    """Mandarake 在库监控器"""

    def __init__(self, config: Dict[str, Any], notifier_mgr: NotificationManager):
        self.config = config
        self.notifier_mgr = notifier_mgr
        self.scraper = MandarakeScraper()
        self.db = archive_db
        self.auto_order = auto_order_engine

        self.seen_ids: Set[str] = set()
        self.load_seen_cache()

        # 统计计数
        self.last_check_time: float = 0.0
        self.total_checks: int = 0
        self.total_matches: int = 0
        self.last_check_status: str = "就绪"

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

    def init_baseline(self):
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

        self.save_seen_cache()
        logger.info(f"基准线建立完成，已记录 {count} 件在库商品为已知库存")

    def check_once(self) -> List[Dict[str, Any]]:
        """执行单轮监控检测，带自动重试机制"""
        self.last_check_time = time.time()
        self.total_checks += 1
        found_matches: List[Dict[str, Any]] = []

        sched_cfg = self.config.get("schedule", {})
        retry_count = max(0, int(sched_cfg.get("retry_count", 3)))
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
                "auto_order": False,
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
            enable_auto_order = rule.get("auto_order", False)

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

                # 若该关注商品在官网上已经售罄：立即同步本地数据库状态为售罄，并从已扫描缓存中移除以防止重复打印
                if is_in_stock == 0:
                    if pid in self.seen_ids:
                        self.seen_ids.discard(pid)
                        self.db.upsert_products_batch([item])
                        self.db.mark_product_stock(pid, is_in_stock=0)
                        logger.info(f"🏷️ [监控追踪] 规则 [{rule_name}] 设定的关注商品已售罄: {title} (ID: {pid})，已调整为售罄。")
                    continue

                # 在库商品去重：已发现过的商品跳过上新提醒
                if pid in self.seen_ids:
                    continue

                # 命中新上架在库目标
                self.seen_ids.add(pid)
                rule_hits.append(item)
                found_matches.append(item)

            if rule_hits:
                self.total_matches += len(rule_hits)
                logger.info(f"🎯 规则 [{rule_name}] 命中 {len(rule_hits)} 件新上架有货商品！")
                
                # 写入数据库归档
                self.db.upsert_products_batch(rule_hits)

                # 分发通知 (同时推送到已启用的邮箱、Discord、桌面弹窗与提示音)
                self.notifier_mgr.dispatch(rule_hits, rule_name)

                # 自动触发下单 (若该规则开启了关联自动下单)
                if enable_auto_order:
                    for target_item in rule_hits:
                        logger.info(f"⚡ 自动下单触发: {target_item.get('title')}")
                        self.auto_order.order_item(
                            item_id=target_item["id"],
                            title=target_item.get("title", ""),
                            price=target_item.get("price", 0),
                            img_url=target_item.get("img_url", ""),
                            shop=target_item.get("shop", ""),
                            trigger_type="auto"
                        )

        self.last_check_status = f"正常完成，命中 {len(found_matches)} 件"
        self.save_seen_cache()
        return found_matches
