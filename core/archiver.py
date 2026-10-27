"""
Mandarake 【ドール】 分类后台爬取与归档引擎
支持全品类批量抓取、指定子分类深度检索、自动翻页、在库过滤、断点控制与实时进度流
"""
import time
import logging
import threading
from typing import List, Dict, Optional, Callable, Any

from core.categories import category_mgr, ROOT_DOLL_CODE
from core.scraper import MandarakeScraper
from core.archive_db import archive_db

logger = logging.getLogger("mandarake.archiver")


class MandarakeArchiver:
    """分类多轮抓取调度与归档引擎"""

    def __init__(self, scraper: Optional[MandarakeScraper] = None):
        self.scraper = scraper or MandarakeScraper()
        self.db = archive_db
        self.cat_mgr = category_mgr

        # 运行状态
        self.is_running: bool = False
        self.is_paused: bool = False
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # 默认不暂停
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # 进度追踪
        self.current_cat_code: str = ""
        self.current_cat_name: str = ""
        self.current_page: int = 0
        self.total_pages_estimate: int = 0
        self.current_cat_index: int = 0
        self.total_cats_count: int = 0
        self.total_crawled_count: int = 0
        self.new_crawled_count: int = 0
        self.start_time: float = 0.0
        self.last_error: str = ""
        self.status_message: str = "空闲就绪"

        # 回调列表
        self._progress_callbacks: List[Callable[[Dict[str, Any]], None]] = []

    def add_progress_listener(self, callback: Callable[[Dict[str, Any]], None]):
        if callback not in self._progress_callbacks:
            self._progress_callbacks.append(callback)

    def remove_progress_listener(self, callback: Callable[[Dict[str, Any]], None]):
        if callback in self._progress_callbacks:
            self._progress_callbacks.remove(callback)

    def _notify_progress(self):
        status = self.get_status()
        for cb in list(self._progress_callbacks):
            try:
                cb(status)
            except Exception:
                pass

    def get_status(self) -> Dict[str, Any]:
        """获取当前爬虫运行状态快照"""
        elapsed = (time.time() - self.start_time) if (self.is_running and self.start_time > 0) else 0.0
        return {
            "is_running": self.is_running,
            "is_paused": self.is_paused,
            "current_cat_code": self.current_cat_code,
            "current_cat_name": self.current_cat_name,
            "current_page": self.current_page,
            "current_cat_index": self.current_cat_index,
            "total_cats_count": self.total_cats_count,
            "total_crawled_count": self.total_crawled_count,
            "new_crawled_count": self.new_crawled_count,
            "elapsed_seconds": round(elapsed, 1),
            "status_message": self.status_message,
            "last_error": self.last_error,
        }

    def start(
        self,
        category_codes: Optional[List[str]] = None,
        in_stock_only: bool = False,
        max_pages_per_cat: Optional[int] = None,
        keyword: Optional[str] = None
    ) -> bool:
        """
        启动分类抓取任务
        :param category_codes: 待抓取的分类代码列表。若为空，则抓取全站所有ドール下属分类
        :param in_stock_only: 是否只爬取有货在库商品
        :param max_pages_per_cat: 每个分类最大爬取页数 (None 为爬完该分类所有数据)
        :param keyword: 关键词筛选
        """
        with self._lock:
            if self.is_running:
                logger.warning("爬虫任务已在运行中，请勿重复启动")
                return False

            self.is_running = True
            self.is_paused = False
            self._stop_event.clear()
            self._pause_event.set()
            self.start_time = time.time()
            self.total_crawled_count = 0
            self.new_crawled_count = 0
            self.last_error = ""

            # 确定待爬分类列表 (自动下钻至末级细分叶子分类，确保各级分类均有真实数据)
            if not category_codes:
                target_codes = self.cat_mgr.get_leaf_category_codes()
                if not target_codes:
                    target_codes = [ROOT_DOLL_CODE]
            else:
                expanded = []
                for c in category_codes:
                    if not self.cat_mgr.is_valid_doll_code(c):
                        continue
                    if c in self.cat_mgr.tree and self.cat_mgr.tree[c].get("children"):
                        expanded.extend(sorted(self.cat_mgr.tree[c]["children"].keys()))
                    else:
                        expanded.append(c)
                target_codes = list(dict.fromkeys(expanded))
                if not target_codes:
                    target_codes = [ROOT_DOLL_CODE]

            self.total_cats_count = len(target_codes)
            self.status_message = f"准备启动，待爬分类数: {self.total_cats_count}"
            logger.info(f"启动 Mandarake 爬虫，目标分类: {target_codes}, 仅在库: {in_stock_only}")

            self._thread = threading.Thread(
                target=self._worker_loop,
                args=(target_codes, in_stock_only, max_pages_per_cat, keyword),
                daemon=True,
                name="MandarakeCrawlerThread"
            )
            self._thread.start()
            self._notify_progress()
            return True

    def pause(self) -> bool:
        """暂停抓取"""
        with self._lock:
            if not self.is_running or self.is_paused:
                return False
            self.is_paused = True
            self._pause_event.clear()
            self.status_message = "已暂停"
            logger.info("爬虫任务已暂停")
            self._notify_progress()
            return True

    def resume(self) -> bool:
        """恢复抓取"""
        with self._lock:
            if not self.is_running or not self.is_paused:
                return False
            self.is_paused = False
            self._pause_event.set()
            self.status_message = "正在继续抓取..."
            logger.info("爬虫任务已恢复执行")
            self._notify_progress()
            return True

    def stop(self) -> bool:
        """终止抓取"""
        with self._lock:
            if not self.is_running:
                return False
            self.status_message = "正在停止任务..."
            self._stop_event.set()
            self._pause_event.set()  # 解除暂停阻塞以退出
            logger.info("正在停止爬虫任务...")
            self._notify_progress()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

        with self._lock:
            self.is_running = False
            self.is_paused = False
            self.status_message = "任务已停止"
            logger.info("爬虫任务已完全停止")
            self._notify_progress()
            return True

    def _worker_loop(
        self,
        category_codes: List[str],
        in_stock_only: bool,
        max_pages: Optional[int],
        keyword: Optional[str]
    ):
        """爬虫主循环执行线程"""
        try:
            for idx, cat_code in enumerate(category_codes):
                if self._stop_event.is_set():
                    break

                cat_name = self.cat_mgr.get_category_name(cat_code)
                self.current_cat_code = cat_code
                self.current_cat_name = cat_name
                self.current_cat_index = idx + 1
                self.current_page = 1
                page_num = 1
                cat_items_count = 0
                cat_in_stock_count = 0

                logger.info(f"[{self.current_cat_index}/{self.total_cats_count}] 开始抓取分类: {cat_name} ({cat_code})...")
                self.status_message = f"正在抓取分类: {cat_name}"
                self._notify_progress()

                while not self._stop_event.is_set():
                    # 暂停检查
                    self._pause_event.wait()
                    if self._stop_event.is_set():
                        break

                    self.current_page = page_num
                    self.status_message = f"正在抓取: {cat_name} (第 {page_num} 页)..."
                    self._notify_progress()

                    try:
                        items, has_next, count = self.scraper.fetch_category_page(
                            category_code=cat_code,
                            page=page_num,
                            in_stock_only=in_stock_only,
                            disp_count=240,
                            keyword=keyword
                        )
                    except Exception as e:
                        logger.error(f"抓取 {cat_name} 第 {page_num} 页发生异常: {e}")
                        self.last_error = str(e)
                        break

                    if not items and count == 0:
                        logger.info(f"{cat_name} 第 {page_num} 页无更多商品，该分类抓取完成")
                        break

                    # 批量写入数据库
                    inserted, updated = self.db.upsert_products_batch(items)
                    self.total_crawled_count += len(items)
                    self.new_crawled_count += inserted
                    cat_items_count += len(items)
                    cat_in_stock_count += sum(1 for x in items if x.get("is_in_stock") == 1)

                    logger.info(
                        f"已抓取 {cat_name} 第 {page_num} 页: 本页 {len(items)} 件 (新增 {inserted}, 更新 {updated}) | 累计抓取 {self.total_crawled_count} 件"
                    )
                    self._notify_progress()

                    # 检查最大页数限制
                    if max_pages and page_num >= max_pages:
                        logger.info(f"{cat_name} 已达到设定的单类最大页数 ({max_pages})")
                        break

                    if not has_next:
                        logger.info(f"{cat_name} 已到达最后一页 (第 {page_num} 页)")
                        break

                    page_num += 1

                # 记录分类抓取进度归档
                self.db.update_crawl_progress(
                    category_code=cat_code,
                    category_name=cat_name,
                    total_items=cat_items_count,
                    in_stock_items=cat_in_stock_count,
                    last_page=page_num,
                    status="COMPLETED" if not self._stop_event.is_set() else "STOPPED"
                )

            if not self._stop_event.is_set():
                self.status_message = f"全部抓取完成！共抓取 {self.total_crawled_count} 件商品 (新增 {self.new_crawled_count})"
                logger.info(self.status_message)
            else:
                self.status_message = "抓取任务已由用户手动中止"

        except Exception as e:
            logger.error(f"爬虫线程发生严重未捕获异常: {e}")
            self.last_error = str(e)
            self.status_message = f"任务发生错误中止: {e}"
        finally:
            with self._lock:
                self.is_running = False
                self.is_paused = False
                self._notify_progress()


# 全局单例
archiver_engine = MandarakeArchiver()
