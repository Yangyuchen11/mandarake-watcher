"""
Mandarake 商品归档数据库管理模块 (SQLite)
持久化存储 【ドール】(020114) 全品类商品档案、下单历史与各子分类爬取进度
内置高效索引、WAL 并发控制、多条件灵活筛选与批量入库
"""
import os
import time
import math
import json
import sqlite3
import logging
from typing import List, Dict, Optional, Tuple, Any, Union

logger = logging.getLogger("mandarake.archive_db")

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "archive.db")


class MandarakeArchiveDB:
    """Mandarake SQLite 归档数据库管理器"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # 启用 WAL 模式大幅提升读写并发性能与稳定性
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def init_db(self):
        """初始化数据表与索引结构"""
        try:
            with self._get_connection() as conn:
                # 1. 商品表
                conn.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    price INTEGER DEFAULT 0,
                    price_str TEXT DEFAULT '',
                    url TEXT DEFAULT '',
                    img_url TEXT DEFAULT '',
                    shop TEXT DEFAULT '',
                    item_no TEXT DEFAULT '',
                    category_code TEXT DEFAULT '',
                    category_name TEXT DEFAULT '',
                    is_in_stock INTEGER DEFAULT 1,
                    is_new INTEGER DEFAULT 0,
                    first_seen_at INTEGER DEFAULT 0,
                    updated_at INTEGER DEFAULT 0
                );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_products_cat ON products(category_code);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_products_stock ON products(is_in_stock);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_products_updated ON products(updated_at DESC);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_products_price ON products(price);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_products_new ON products(is_new);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_products_title ON products(title);")

                # 2. 订单记录表
                conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    price INTEGER DEFAULT 0,
                    price_str TEXT DEFAULT '',
                    url TEXT DEFAULT '',
                    img_url TEXT DEFAULT '',
                    shop TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    dry_run INTEGER DEFAULT 1,
                    trigger_type TEXT DEFAULT 'manual',
                    message TEXT DEFAULT '',
                    duration_ms INTEGER DEFAULT 0,
                    created_at INTEGER DEFAULT 0,
                    raw_details TEXT DEFAULT ''
                );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at DESC);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_item ON orders(item_id);")

                # 3. 分类爬取进度记录表
                conn.execute("""
                CREATE TABLE IF NOT EXISTS crawl_progress (
                    category_code TEXT PRIMARY KEY,
                    category_name TEXT DEFAULT '',
                    total_items INTEGER DEFAULT 0,
                    in_stock_items INTEGER DEFAULT 0,
                    last_page INTEGER DEFAULT 0,
                    last_crawled_at INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'IDLE'
                );
                """)

                # 4. 键值元数据表
                conn.execute("""
                CREATE TABLE IF NOT EXISTS archive_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """)
                conn.commit()
            logger.info(f"SQLite 归档数据库初始化完成: {self.db_path}")
        except Exception as e:
            logger.error(f"初始化 SQLite 数据库失败: {e}")

    def upsert_products_batch(self, items: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        批量写入/更新商品列表 (支持单批次去重与 ON CONFLICT 原子更新)
        :return: (新增数量, 更新数量)
        """
        if not items:
            return 0, 0

        now_ts = int(time.time())

        # 1. 对传入批次按 ID 去重（保留最新的记录）
        unique_map: Dict[str, Dict[str, Any]] = {}
        for x in items:
            pid = str(x.get("id", "")).strip()
            if pid:
                unique_map[pid] = x
        deduped_items = list(unique_map.values())
        if not deduped_items:
            return 0, 0

        inserted_count = 0
        updated_count = 0

        try:
            with self._get_connection() as conn:
                # 2. 查询已有商品以准确统计新增与更新数量
                ids = [str(x["id"]) for x in deduped_items]
                placeholders = ",".join(["?"] * len(ids))
                cursor = conn.cursor()
                cursor.execute(f"SELECT id FROM products WHERE id IN ({placeholders})", ids)
                existing_ids = {row["id"] for row in cursor.fetchall()}

                inserted_count = len([x for x in deduped_items if str(x["id"]) not in existing_ids])
                updated_count = len(deduped_items) - inserted_count

                batch_params = []
                for item in deduped_items:
                    pid = str(item.get("id", "")).strip()
                    title = item.get("title", "")
                    price = int(item.get("price", 0))
                    price_str = item.get("price_str", "")
                    url = item.get("url", "")
                    img_url = item.get("img_url", "")
                    shop = item.get("shop", "")
                    item_no = item.get("item_no", "")
                    cat_code = item.get("category_code", "")
                    cat_name = item.get("category_name", "")
                    is_in_stock = int(item.get("is_in_stock", 1))
                    is_new = int(item.get("is_new", 0))

                    batch_params.append((
                        pid, title, price, price_str, url, img_url, shop, item_no,
                        cat_code, cat_name, is_in_stock, is_new, now_ts, now_ts
                    ))

                # 3. 使用 SQLite 原生 UPSERT 保证原子性与抗并发冲突
                upsert_sql = """
                INSERT INTO products (
                    id, title, price, price_str, url, img_url, shop, item_no,
                    category_code, category_name, is_in_stock, is_new, first_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    price = excluded.price,
                    price_str = excluded.price_str,
                    url = excluded.url,
                    img_url = excluded.img_url,
                    shop = excluded.shop,
                    item_no = excluded.item_no,
                    category_code = CASE 
                        WHEN products.category_code LIKE '%' || excluded.category_code || '%' THEN products.category_code
                        WHEN products.category_code = '' OR length(excluded.category_code) > length(products.category_code) THEN excluded.category_code
                        ELSE products.category_code || ',' || excluded.category_code 
                    END,
                    category_name = CASE 
                        WHEN length(excluded.category_code) >= length(products.category_code) THEN excluded.category_name 
                        ELSE products.category_name 
                    END,
                    is_in_stock = excluded.is_in_stock,
                    is_new = excluded.is_new,
                    updated_at = excluded.updated_at;
                """
                conn.executemany(upsert_sql, batch_params)
                conn.commit()

            return inserted_count, updated_count
        except Exception as e:
            logger.error(f"批量入库商品异常: {e}")
            return 0, 0

    def get_category_counts(self) -> Dict[str, int]:
        """快速聚合各层级分类下的商品数量快照 (包含多标签与前缀继承)"""
        from core.categories import category_mgr
        try:
            with self._get_connection() as conn:
                rows = conn.execute("SELECT category_code, count(*) FROM products GROUP BY category_code").fetchall()

            flat = category_mgr.get_all_categories_flat()
            counts = {}
            for c in flat:
                p = c["code"]
                counts[p] = sum(cnt for code_str, cnt in rows if code_str and any(x.startswith(p) for x in code_str.split(",")))
            return counts
        except Exception as e:
            logger.error(f"统计各分类商品数量异常: {e}")
            return {}

    def reclassify_all_products(self) -> int:
        """
        全量扫描现有商品数据库，利用分类管理器智能规则将未细分的二级分类重新归整为精确的三级分类
        :return: 成功重新归类的商品数量
        """
        from core.categories import category_mgr

        updated_count = 0
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, title, category_code FROM products")
                rows = cursor.fetchall()

                updates = []
                for row in rows:
                    pid = row["id"]
                    title = row["title"]
                    code = row["category_code"]

                    new_code, new_name = category_mgr.infer_category(title, code)
                    if new_code != code:
                        updates.append((new_code, new_name, pid))

                if updates:
                    cursor.executemany("""
                    UPDATE products 
                    SET category_code = ?, category_name = ?
                    WHERE id = ?
                    """, updates)
                    conn.commit()
                    updated_count = len(updates)
                    logger.info(f"智能分类规整完成！已成功将 {updated_count} 件商品归入精确三级分类")
        except Exception as e:
            logger.error(f"智能分类规整异常: {e}")

        return updated_count

    def mark_product_stock(self, item_id: str, is_in_stock: int = 0) -> bool:
        """更新单个商品的在库/售罄状态 (0=售罄, 1=在库)"""
        now_ts = int(time.time())
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE products SET is_in_stock = ?, updated_at = ? WHERE id = ?",
                    (int(is_in_stock), now_ts, str(item_id).strip())
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"更新商品 {item_id} 库存状态异常: {e}")
            return False

    def mark_products_stock_batch(self, item_ids: List[str], is_in_stock: int = 0) -> int:
        """批量更新商品在库/售罄状态"""
        if not item_ids:
            return 0
        now_ts = int(time.time())
        try:
            with self._get_connection() as conn:
                placeholders = ",".join(["?"] * len(item_ids))
                params = [int(is_in_stock), now_ts] + [str(x).strip() for x in item_ids]
                cursor = conn.cursor()
                cursor.execute(f"UPDATE products SET is_in_stock = ?, updated_at = ? WHERE id IN ({placeholders})", params)
                conn.commit()
                return cursor.rowcount
        except Exception as e:
            logger.error(f"批量更新库存状态异常: {e}")
            return 0

    def query_products(
        self,
        page: int = 1,
        page_size: int = 40,
        category_code: Optional[str] = None,
        is_in_stock: Optional[int] = None,
        keyword: Optional[str] = None,
        exclude_keyword: Optional[str] = None,
        preset_keywords: Optional[Union[List[str], str]] = None,
        preset_mode: str = "or",
        shop: Optional[str] = None,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
        is_new: Optional[int] = None,
        sort_by: str = "updated_at",
        sort_order: str = "desc"
    ) -> Dict[str, Any]:
        """
        分页且多维度组合筛选查询商品档案
        :param page: 页码 (1 起始)
        :param page_size: 每页数量
        :param category_code: 分类代码前缀匹配 (例如 02011403 会匹配该子分类及所有下属商品)
        :param is_in_stock: 在库状态 (1=仅在库, 0=仅售罄, None=全量)
        :param keyword: 多关键词支持 (空格隔开支持多词 AND 查询)
        :param exclude_keyword: 排除关键词
        :param shop: 店铺过滤 (如 SAHRA / 中野店 等)
        :param min_price: 最低价格
        :param max_price: 最高价格
        :param is_new: 是否新着 (1=仅新着, None=全量)
        :param sort_by: 排序字段 (updated_at / price / first_seen_at / id)
        :param sort_order: 排序方向 (desc / asc)
        """
        page = max(1, page)
        page_size = max(5, min(200, page_size))
        offset = (page - 1) * page_size

        where_clauses = ["1=1"]
        params: List[Any] = []

        # 1. 分类匹配 (支持前缀匹配与多分类逗号分隔匹配)
        if category_code and category_code.strip():
            c = category_code.strip()
            where_clauses.append("(category_code LIKE ? OR category_code LIKE ?)")
            params.extend([f"{c}%", f"%,{c}%"])

        # 2. 在库/售罄筛选
        if is_in_stock is not None and is_in_stock in (0, 1):
            where_clauses.append("is_in_stock = ?")
            params.append(is_in_stock)

        # 3. 新着商品
        if is_new is not None and is_new == 1:
            where_clauses.append("is_new = 1")

        # 4. 店铺筛选
        if shop and shop.strip():
            where_clauses.append("shop LIKE ?")
            params.append(f"%{shop.strip()}%")

        # 5. 价格区间
        if min_price is not None and min_price >= 0:
            where_clauses.append("price >= ?")
            params.append(min_price)
        if max_price is not None and max_price > 0:
            where_clauses.append("price <= ?")
            params.append(max_price)

        # 6. 多关键词联合 (AND 匹配)
        if keyword and keyword.strip():
            words = keyword.strip().split()
            for w in words:
                where_clauses.append("(title LIKE ? OR item_no LIKE ? OR shop LIKE ?)")
                p = f"%{w}%"
                params.extend([p, p, p])

        # 6.1 预设关键词组合筛选 (支持 AND / OR 模式)
        if preset_keywords:
            if isinstance(preset_keywords, str):
                p_words = [pw.strip() for pw in preset_keywords.split(",") if pw.strip()]
            else:
                p_words = [str(pw).strip() for pw in preset_keywords if str(pw).strip()]

            if p_words:
                sub_clauses = []
                for pw in p_words:
                    sub_clauses.append("(title LIKE ? OR item_no LIKE ? OR shop LIKE ?)")
                    p = f"%{pw}%"
                    params.extend([p, p, p])
                connector = " AND " if preset_mode.lower() == "and" else " OR "
                where_clauses.append(f"({connector.join(sub_clauses)})")

        # 7. 排除关键词
        if exclude_keyword and exclude_keyword.strip():
            ex_words = exclude_keyword.strip().split()
            for ew in ex_words:
                where_clauses.append("title NOT LIKE ?")
                params.append(f"%{ew}%")

        # 排序安全校验
        valid_sort_fields = {
            "updated_at": "updated_at",
            "price": "price",
            "first_seen_at": "first_seen_at",
            "id": "CAST(id AS INTEGER)",
            "is_new": "is_new",
        }
        order_col = valid_sort_fields.get(sort_by, "updated_at")
        order_dir = "ASC" if str(sort_order).lower() == "asc" else "DESC"

        where_sql = " AND ".join(where_clauses)

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 统计总数
                count_sql = f"SELECT COUNT(*) AS total FROM products WHERE {where_sql}"
                cursor.execute(count_sql, params)
                total = cursor.fetchone()["total"]

                # 分页查询
                query_sql = f"""
                SELECT id, title, price, price_str, url, img_url, shop, item_no,
                       category_code, category_name, is_in_stock, is_new, first_seen_at, updated_at
                FROM products
                WHERE {where_sql}
                ORDER BY {order_col} {order_dir}
                LIMIT ? OFFSET ?
                """
                query_params = list(params) + [page_size, offset]
                cursor.execute(query_sql, query_params)
                rows = cursor.fetchall()

                items = [dict(r) for r in rows]
                total_pages = math.ceil(total / page_size) if total > 0 else 1

                return {
                    "items": items,
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages,
                }
        except Exception as e:
            logger.error(f"查询商品数据异常: {e}")
            return {"items": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 1}

    def get_stats(self) -> Dict[str, Any]:
        """获取归档库全局统计概览"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN is_in_stock = 1 THEN 1 ELSE 0 END) AS in_stock_count,
                    SUM(CASE WHEN is_in_stock = 0 THEN 1 ELSE 0 END) AS soldout_count,
                    SUM(CASE WHEN is_new = 1 THEN 1 ELSE 0 END) AS new_arrival_count,
                    MAX(updated_at) AS last_updated_at
                FROM products
                """)
                row = cursor.fetchone()

                # 各主要二级分类商品分布
                cursor.execute("""
                SELECT category_code, category_name, COUNT(*) AS count
                FROM products
                GROUP BY category_code
                ORDER BY count DESC
                """)
                cat_rows = cursor.fetchall()
                cat_dist = [{ "code": r["category_code"], "name": r["category_name"], "count": r["count"] } for r in cat_rows]

                return {
                    "total_products": row["total"] or 0,
                    "in_stock_products": row["in_stock_count"] or 0,
                    "soldout_products": row["soldout_count"] or 0,
                    "new_arrival_products": row["new_arrival_count"] or 0,
                    "last_updated_at": row["last_updated_at"] or 0,
                    "category_distribution": cat_dist
                }
        except Exception as e:
            logger.error(f"获取统计概览异常: {e}")
            return {
                "total_products": 0,
                "in_stock_products": 0,
                "soldout_products": 0,
                "new_arrival_products": 0,
                "last_updated_at": 0,
                "category_distribution": []
            }

    def update_crawl_progress(
        self,
        category_code: str,
        category_name: str,
        total_items: int,
        in_stock_items: int,
        last_page: int,
        status: str = "DONE"
    ):
        """记录子分类爬取进度"""
        try:
            with self._get_connection() as conn:
                conn.execute("""
                INSERT INTO crawl_progress (
                    category_code, category_name, total_items, in_stock_items, last_page, last_crawled_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(category_code) DO UPDATE SET
                    category_name = excluded.category_name,
                    total_items = excluded.total_items,
                    in_stock_items = excluded.in_stock_items,
                    last_page = excluded.last_page,
                    last_crawled_at = excluded.last_crawled_at,
                    status = excluded.status
                """, (category_code, category_name, total_items, in_stock_items, last_page, int(time.time()), status))
                conn.commit()
        except Exception as e:
            logger.error(f"更新分类爬取进度失败: {e}")

    def get_all_crawl_progress(self) -> List[Dict[str, Any]]:
        """获取所有分类爬取进度列表"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM crawl_progress ORDER BY last_crawled_at DESC")
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error(f"获取分类进度列表失败: {e}")
            return []

    def clear_products(self) -> bool:
        """清空商品库"""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM products;")
                conn.execute("DELETE FROM crawl_progress;")
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"清空商品数据失败: {e}")
            return False

    # --- 订单记录管理 ---

    def add_order(self, order_data: Dict[str, Any]) -> bool:
        """新增订单记录"""
        try:
            with self._get_connection() as conn:
                conn.execute("""
                INSERT INTO orders (
                    id, item_id, title, price, price_str, url, img_url, shop,
                    status, dry_run, trigger_type, message, duration_ms, created_at, raw_details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    order_data.get("id"),
                    order_data.get("item_id", ""),
                    order_data.get("title", ""),
                    int(order_data.get("price", 0)),
                    order_data.get("price_str", ""),
                    order_data.get("url", ""),
                    order_data.get("img_url", ""),
                    order_data.get("shop", ""),
                    order_data.get("status", "PENDING"),
                    1 if order_data.get("dry_run", True) else 0,
                    order_data.get("trigger_type", "manual"),
                    order_data.get("message", ""),
                    int(order_data.get("duration_ms", 0)),
                    int(order_data.get("created_at", int(time.time()))),
                    order_data.get("raw_details", "")
                ))
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"插入订单记录失败: {e}")
            return False

    def query_orders(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """查询订单历史记录"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                SELECT * FROM orders ORDER BY created_at DESC LIMIT ? OFFSET ?
                """, (limit, offset))
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error(f"查询订单记录失败: {e}")
            return []

    def delete_order(self, order_id: str) -> bool:
        """删除指定订单记录"""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"删除订单记录失败: {e}")
            return False

    def clear_orders(self) -> bool:
        """清空所有订单记录"""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM orders;")
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"清空订单记录失败: {e}")
            return False


# 全局单例
archive_db = MandarakeArchiveDB()
