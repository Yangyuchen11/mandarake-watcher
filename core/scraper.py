"""
Mandarake (order.mandarake.co.jp) 专用抓取与解析模块
专注于大分类【ドール】(020114) 下属所有分类的商品流与检索，支持高效翻页、在库过滤与详细信息提取
"""
import os
import re
import time
import random
import logging
import threading
import itertools
from typing import List, Dict, Optional, Tuple, Any
from urllib.parse import urljoin, parse_qs, parse_qsl, urlencode, urlparse, urlunparse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

try:
    import lxml  # noqa: F401
    from lxml import html as lxml_html, etree as lxml_etree
    BS_PARSER = "lxml"
    _XP_BLOCKS = lxml_etree.XPath(
        "//div[contains(concat(' ', normalize-space(@class), ' '), ' thumlarge ')]//div[contains(concat(' ', normalize-space(@class), ' '), ' block ')]"
        " | //*[contains(concat(' ', normalize-space(@class), ' '), ' thumlist ')]//div[contains(concat(' ', normalize-space(@class), ' '), ' block ')]"
        " | //div[contains(concat(' ', normalize-space(@class), ' '), ' block ') and @data-itemidx]"
    )
    _XP_NEXT_PAGE = lxml_etree.XPath(
        "//div[contains(concat(' ', normalize-space(@class), ' '), ' pager ')]"
        "//div[contains(concat(' ', normalize-space(@class), ' '), ' next ')]//a[@href]"
    )
except ImportError:
    lxml_html = None
    lxml_etree = None
    BS_PARSER = "html.parser"
    _XP_BLOCKS = None
    _XP_NEXT_PAGE = None

from core.categories import category_mgr, ROOT_DOLL_CODE

logger = logging.getLogger("mandarake.scraper")
# 抑制 urllib3 自动重试时的非致命警告日志，避免终端与日志被 RemoteDisconnected 重试刷屏
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

BASE_ORDER_URL = "https://order.mandarake.co.jp/order"
BASE_SITE_URL = "https://www.mandarake.co.jp/"
LIST_ENDPOINT = f"{BASE_ORDER_URL}/listPage/list"
DETAIL_ENDPOINT = f"{BASE_ORDER_URL}/detailPage/item"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://order.mandarake.co.jp/order/listPage/list?categoryCode=020114&lang=ja",
}


class MandarakeScraper:
    """Mandarake ドール专区爬虫与解析引擎"""

    def __init__(self, timeout: int = 15, max_retries: int = 3, min_delay: float = 0.5, max_delay: float = 1.2):
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._thread_local = threading.local()
        self._cookie_lock = threading.Lock()
        self._base_cookies: Dict[str, str] = {}
        self._cb_counter = itertools.count(1)
        self._owner_thread_id = threading.get_ident()
        self.session = self._build_session()
        self.last_fetch_failed = False
        self.last_rate_limited = False
        self.last_cache_control = ""
        self.last_status_code = 0
        self._ensure_cookies(self.session)

    def _build_session(self) -> requests.Session:
        """创建带独立连接池与底层安全重试策略的 Session"""
        sess = requests.Session()
        sess.headers.update(DEFAULT_HEADERS)
        # 仅对 502/504 网关瞬断做轻量重试，将 429/503 交由上层退避策略感知处理
        retries = Retry(
            total=2,
            connect=2,
            read=1,
            backoff_factor=0.3,
            status_forcelist=[502, 504],
            raise_on_status=False
        )
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=retries)
        sess.mount("https://", adapter)
        sess.mount("http://", adapter)
        with self._cookie_lock:
            if self._base_cookies:
                sess.cookies.update(self._base_cookies)
        return sess

    def _get_session(self) -> requests.Session:
        """获取当前线程专属 Session（主线程沿用 self.session，子线程懒创建隔离会话）"""
        if threading.get_ident() == self._owner_thread_id:
            return self.session
        sess = getattr(self._thread_local, "session", None)
        if sess is None:
            sess = self._build_session()
            self._thread_local.session = sess
        return sess

    def _ensure_cookies(self, session: Optional[requests.Session] = None):
        """访问主站初始化关键追踪 Cookie (tr_mndrk_user)，防止 listPage 遭遇 302 重定向"""
        sess = session or self._get_session()
        try:
            with self._cookie_lock:
                if "tr_mndrk_user" in self._base_cookies and "tr_mndrk_user" not in sess.cookies:
                    sess.cookies.update(self._base_cookies)
                    return
            r = sess.get(BASE_SITE_URL, timeout=self.timeout)
            if r.status_code == 200:
                with self._cookie_lock:
                    self._base_cookies.update(requests.utils.dict_from_cookiejar(sess.cookies))
                logger.debug("Mandarake 基础 Session 与 Cookie 初始化完成")
        except Exception as e:
            logger.warning(f"初始化 Mandarake 基础会话异常: {e}")

    def _delay_polite(self):
        """礼貌延时与随机抖动，防止触发高频风控"""
        delay = random.uniform(self.min_delay, self.max_delay)
        time.sleep(delay)

    def _get_with_meta(
        self,
        url: str,
        params: Optional[dict] = None,
        max_retries: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """带自动重试、限流感知与耗时/缓存头元信息的 HTTP GET"""
        retries = self.max_retries if max_retries is None else max(0, int(max_retries))
        req_timeout = self.timeout if timeout is None else max(1, int(timeout))
        sess = self._get_session()

        t_start = time.perf_counter()
        last_exc = None
        last_status = 0
        rate_limited = False
        cache_control = ""

        for attempt in range(retries + 1):
            try:
                # 检查 session 中是否有必要的 cookie
                if "tr_mndrk_user" not in sess.cookies:
                    self._ensure_cookies(sess)

                resp = sess.get(url, params=params, timeout=req_timeout, allow_redirects=True)
                last_status = resp.status_code
                cache_control = resp.headers.get("Cache-Control", "")

                # 若被意外重定向回主站，刷新 cookie 后再试
                if "order.mandarake.co.jp" not in resp.url and "mandarake.co.jp" in resp.url:
                    logger.warning(f"请求被重定向至 {resp.url}，正在刷新 cookie 重试...")
                    with self._cookie_lock:
                        self._base_cookies.pop("tr_mndrk_user", None)
                    sess.cookies.clear()
                    self._ensure_cookies(sess)
                    if attempt < retries:
                        time.sleep(1.0)
                        continue
                    break

                if resp.status_code == 200:
                    elapsed_ms = int((time.perf_counter() - t_start) * 1000)
                    return {
                        "html": resp.text,
                        "ok": True,
                        "status_code": 200,
                        "rate_limited": False,
                        "cache_control": cache_control,
                        "elapsed_ms": elapsed_ms,
                        "error": "",
                    }

                # 应对 429 Too Many Requests / 503 繁忙
                if resp.status_code in (429, 503):
                    rate_limited = True
                    if attempt < retries:
                        wait_sec = (attempt + 1) * 2.0
                        logger.warning(f"官网响应 {resp.status_code}，正在退避重试 ({attempt + 1}/{retries + 1})，等待 {wait_sec}s...")
                        time.sleep(wait_sec)
                        continue
                    break

                if resp.status_code in (502, 504):
                    if attempt < retries:
                        wait_sec = (attempt + 1) * 2.0
                        logger.warning(f"官网响应 {resp.status_code}，正在退避重试 ({attempt + 1}/{retries + 1})，等待 {wait_sec}s...")
                        time.sleep(wait_sec)
                        continue
                    break

                logger.warning(f"请求 {url} 返回非预期状态码: {resp.status_code}")
                break

            except Exception as e:
                last_exc = e
                # 若当前线程连接被远端静默断开，仅重置当前线程连接池
                try:
                    sess.close()
                except Exception:
                    pass
                if attempt < retries:
                    wait_sec = (attempt + 1) * 1.5
                    if attempt > 0:
                        logger.warning(f"网络请求发生错误: {e}，正在重试 ({attempt + 1}/{retries + 1})...")
                    else:
                        logger.debug(f"检测到远端空闲长连接重置 ({e})，正在自动重连...")
                    time.sleep(wait_sec)
                else:
                    logger.debug(f"单次请求异常 ({e})")

        elapsed_ms = int((time.perf_counter() - t_start) * 1000)
        err_msg = str(last_exc) if last_exc else f"HTTP {last_status}"
        if retries > 0:
            logger.error(f"抓取页面失败: {url}, 参数: {params}, 原因: {err_msg}")
        return {
            "html": None,
            "ok": False,
            "status_code": last_status,
            "rate_limited": rate_limited,
            "cache_control": cache_control,
            "elapsed_ms": elapsed_ms,
            "error": err_msg,
        }

    def _get(self, url: str, params: Optional[dict] = None) -> Optional[str]:
        """带自动重试与反封禁退避机制的 HTTP GET"""
        res = self._get_with_meta(url, params=params)
        self.last_fetch_failed = not res["ok"]
        self.last_rate_limited = res["rate_limited"]
        self.last_cache_control = res["cache_control"]
        self.last_status_code = res["status_code"]
        return res["html"]

    def fetch_category_page_meta(
        self,
        category_code: str = ROOT_DOLL_CODE,
        page: int = 1,
        in_stock_only: bool = False,
        disp_count: int = 240,
        keyword: Optional[str] = None,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
        polite_delay: bool = True,
        bypass_cache: bool = False,
        max_retries: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        抓取指定【ドール】子分类的特定分页，并返回解析结果与请求元信息
        """
        # 确保只在 ドール 范围内抓取
        if not category_code or not category_code.startswith(ROOT_DOLL_CODE):
            category_code = ROOT_DOLL_CODE

        params = {
            "categoryCode": category_code,
            "dispCount": str(disp_count),
            "page": str(page),
            "lang": "ja",
            "soldOut": "1" if in_stock_only else "0",
        }

        if keyword and keyword.strip():
            params["keyword"] = keyword.strip()
        if min_price is not None and min_price >= 0:
            params["minPrice"] = str(min_price)
        if max_price is not None and max_price > 0:
            params["maxPrice"] = str(max_price)
        if bypass_cache:
            # 附加唯一防缓存查询参数，穿透 Mandarake 180 秒反向代理缓存直达源站
            params["_"] = f"{int(time.time() * 1000)}_{next(self._cb_counter)}"

        if polite_delay:
            self._delay_polite()

        res = self._get_with_meta(
            LIST_ENDPOINT,
            params=params,
            max_retries=max_retries,
            timeout=timeout,
        )
        self.last_fetch_failed = not res["ok"]
        self.last_rate_limited = res["rate_limited"]
        self.last_cache_control = res["cache_control"]
        self.last_status_code = res["status_code"]

        if not res["html"]:
            return {
                "items": [],
                "has_next": False,
                "count": 0,
                "ok": False,
                "rate_limited": res["rate_limited"],
                "status_code": res["status_code"],
                "cache_control": res["cache_control"],
                "elapsed_ms": res["elapsed_ms"],
                "error": res["error"],
            }

        items, has_next = self.parse_list_page(res["html"], category_code)
        return {
            "items": items,
            "has_next": has_next,
            "count": len(items),
            "ok": True,
            "rate_limited": res["rate_limited"],
            "status_code": res["status_code"],
            "cache_control": res["cache_control"],
            "elapsed_ms": res["elapsed_ms"],
            "error": "",
        }

    def fetch_category_page(
        self,
        category_code: str = ROOT_DOLL_CODE,
        page: int = 1,
        in_stock_only: bool = False,
        disp_count: int = 240,
        keyword: Optional[str] = None,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
        polite_delay: bool = True,
        bypass_cache: bool = False,
        max_retries: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], bool, int]:
        """
        抓取指定【ドール】子分类的特定分页
        :param category_code: 分类代码 (必须以 020114 开头)
        :param page: 页码 (1 起始)
        :param in_stock_only: 是否仅抓取有货在库商品 (True 时 soldOut=1)
        :param disp_count: 每页抓取条数 (默认 240 最大化效率)
        :param keyword: 关键词过滤
        :param min_price: 最低价格
        :param max_price: 最高价格
        :param polite_delay: 是否在请求前执行礼貌随机延时
        :param bypass_cache: 是否附加防缓存参数穿透 180 秒代理缓存
        :param max_retries: 覆盖底层重试次数
        :param timeout: 覆盖请求超时秒数
        :return: (商品列表, 是否还有下一页, 当前页商品数)
        """
        res = self.fetch_category_page_meta(
            category_code=category_code,
            page=page,
            in_stock_only=in_stock_only,
            disp_count=disp_count,
            keyword=keyword,
            min_price=min_price,
            max_price=max_price,
            polite_delay=polite_delay,
            bypass_cache=bypass_cache,
            max_retries=max_retries,
            timeout=timeout,
        )
        return res["items"], res["has_next"], res["count"]

    @staticmethod
    def _lxml_text_strip(el) -> str:
        """等价于 BeautifulSoup.get_text(strip=True) 的高速文本提取"""
        if el is None:
            return ""
        return "".join(part.strip() for part in el.itertext() if part.strip())

    @staticmethod
    def _has_div_ancestor_with_class(el, stop_el, target_classes: Tuple[str, ...]) -> bool:
        """检查在 stop_el 之下是否存在带指定 class 的 div 祖先节点"""
        parent = el.getparent()
        while parent is not None and parent is not stop_el:
            if parent.tag == "div":
                p_cls = parent.get("class")
                if p_cls:
                    p_classes = p_cls.split()
                    for tc in target_classes:
                        if tc in p_classes:
                            return True
            parent = parent.getparent()
        return False

    @staticmethod
    def _build_parsed_item(
        item_id: Optional[str],
        item_href: str,
        title: str,
        price_str: str,
        img_url: str,
        shop: str,
        item_no: str,
        stock_text: str,
        has_soldout_mark: bool,
        has_cart_btn: bool,
        is_new: int,
        current_cat_code: str,
        now_ts: int,
    ) -> Optional[Dict[str, Any]]:
        """规范化提取字段并构建统一的商品字典"""
        if not item_id and item_href:
            parsed_qs = parse_qs(urlparse(item_href).query)
            if "itemCode" in parsed_qs:
                item_id = parsed_qs["itemCode"][0]

        if not item_id:
            return None

        item_id = str(item_id).strip()

        # 提取纯数字价格 (优先税前基本价，如 2,500円)
        price_val = 0
        if price_str:
            clean_p = price_str.replace(",", "")
            match = re.search(r"(\d+)\s*円", clean_p)
            if match:
                price_val = int(match.group(1))

        # 商品详情直达链接（剔除任何防缓存临时查询参数 _）
        full_url = urljoin(BASE_ORDER_URL, item_href) if item_href else f"{DETAIL_ENDPOINT}?itemCode={item_id}&lang=ja"
        if "_=" in full_url:
            parsed_u = urlparse(full_url)
            filtered_qs = [(k, v) for k, v in parse_qsl(parsed_u.query, keep_blank_values=True) if k != "_"]
            full_url = urlunparse(parsed_u._replace(query=urlencode(filtered_qs)))

        if img_url and not img_url.startswith("http"):
            img_url = urljoin(BASE_ORDER_URL, img_url)

        # 在库状态检验 (多重互斥严谨判定：必须有加入购物车按钮且无缺货标志)
        is_soldout = (
            has_soldout_mark
            or not has_cart_btn
            or "売り切れ" in stock_text
            or "在庫なし" in stock_text
            or "品切" in stock_text
        )
        is_in_stock = 0 if is_soldout else 1

        # 优先提取官网链接中的原始官方分类代码，保证100%忠于官网
        official_cat = ""
        if item_href:
            parsed_qs = parse_qs(urlparse(item_href).query)
            if "categoryCode" in parsed_qs and parsed_qs["categoryCode"]:
                official_cat = parsed_qs["categoryCode"][0].strip()

        cat_code_to_use = official_cat if (official_cat and category_mgr.is_valid_doll_code(official_cat)) else (current_cat_code or ROOT_DOLL_CODE)
        final_cat_code, final_cat_name = category_mgr.infer_category(title, cat_code_to_use)

        return {
            "id": item_id,
            "title": title,
            "price": price_val,
            "price_str": price_str,
            "url": full_url,
            "img_url": img_url,
            "shop": shop,
            "item_no": item_no,
            "category_code": final_cat_code,
            "category_name": final_cat_name,
            "is_in_stock": is_in_stock,
            "is_new": is_new,
            "first_seen_at": now_ts,
            "updated_at": now_ts,
        }

    def parse_list_page(self, html: str, current_cat_code: str) -> Tuple[List[Dict[str, Any]], bool]:
        """解析商品列表页 HTML，优先使用 lxml 原生极速路径，异常时回退 BeautifulSoup"""
        if not html:
            return [], False

        if lxml_html is not None and _XP_BLOCKS is not None:
            try:
                return self._parse_list_page_lxml(html, current_cat_code)
            except Exception as e:
                logger.debug(f"lxml 极速解析列表页回退 BS4: {e}")

        return self._parse_list_page_bs4(html, current_cat_code)

    def _parse_list_page_lxml(self, html: str, current_cat_code: str) -> Tuple[List[Dict[str, Any]], bool]:
        """基于 lxml.html 的单次文档序极速解析路径"""
        doc = lxml_html.fromstring(html)
        blocks = _XP_BLOCKS(doc)
        items: List[Dict[str, Any]] = []
        now_ts = int(time.time())

        for block in blocks:
            try:
                item_id = block.get("data-itemidx")
                link_el = None
                title_el = None
                price_el = None
                img_el = None
                shop_el = None
                itemno_el = None
                stock_el = None
                has_soldout_mark = False
                has_cart_btn = False
                is_new = 0

                for el in block.iter():
                    if el is block or not isinstance(el.tag, str):
                        continue
                    tag = el.tag
                    cls_str = el.get("class")
                    classes = cls_str.split() if cls_str else ()

                    if link_el is None and tag == "a":
                        href_val = el.get("href") or ""
                        if "detailPage" in href_val or self._has_div_ancestor_with_class(el, block, ("title", "thum")):
                            link_el = el

                    if title_el is None:
                        if "title" in classes or (tag == "a" and self._has_div_ancestor_with_class(el, block, ("title",))):
                            title_el = el

                    if price_el is None:
                        if "price" in classes or "money" in classes or (tag == "p" and self._has_div_ancestor_with_class(el, block, ("price",))):
                            price_el = el

                    if img_el is None and tag == "img":
                        img_el = el

                    if classes:
                        if shop_el is None and "shop" in classes:
                            shop_el = el
                        if itemno_el is None and "itemno" in classes:
                            itemno_el = el
                        if stock_el is None and "stock" in classes:
                            stock_el = el
                        if not has_soldout_mark and ("soldout" in classes or "sold_out" in classes or "sold" in classes or "addalert" in classes):
                            has_soldout_mark = True
                        if not has_cart_btn and ("addcart" in classes or "cart_btn" in classes):
                            has_cart_btn = True
                        if not is_new and ("new_arrival" in classes or "new" in classes):
                            is_new = 1

                    if not has_cart_btn and tag == "a" and cls_str and "addbasket" in cls_str:
                        has_cart_btn = True

                item_href = link_el.get("href", "") if link_el is not None else ""
                img_url = ""
                if img_el is not None:
                    img_url = img_el.get("src") or img_el.get("data-src") or ""

                parsed = self._build_parsed_item(
                    item_id=item_id,
                    item_href=item_href,
                    title=self._lxml_text_strip(title_el),
                    price_str=self._lxml_text_strip(price_el),
                    img_url=img_url,
                    shop=self._lxml_text_strip(shop_el),
                    item_no=self._lxml_text_strip(itemno_el),
                    stock_text=self._lxml_text_strip(stock_el),
                    has_soldout_mark=has_soldout_mark,
                    has_cart_btn=has_cart_btn,
                    is_new=is_new,
                    current_cat_code=current_cat_code,
                    now_ts=now_ts,
                )
                if parsed is not None:
                    items.append(parsed)
            except Exception as e:
                logger.debug(f"lxml 解析单个商品 block 异常: {e}")
                continue

        has_next = False
        for next_a in _XP_NEXT_PAGE(doc):
            if "次のページ" in "".join(next_a.itertext()):
                has_next = True
                break

        return items, has_next

    def _parse_list_page_bs4(self, html: str, current_cat_code: str) -> Tuple[List[Dict[str, Any]], bool]:
        """解析商品列表页 HTML (BeautifulSoup 安全回退路径)"""
        if not html:
            return [], False

        soup = BeautifulSoup(html, BS_PARSER)
        items: List[Dict[str, Any]] = []
        now_ts = int(time.time())

        # 查找所有商品 block
        blocks = soup.select("div.thumlarge div.block, .thumlist div.block, div.block[data-itemidx]")

        for block in blocks:
            try:
                item_id = block.get("data-itemidx")
                link_el = block.select_one("div.title a, div.thum a, a[href*='detailPage']")
                item_href = link_el.get("href", "") if link_el else ""

                title_el = block.select_one("div.title p a, div.title a, .title")
                title = title_el.get_text(strip=True) if title_el else ""

                price_el = block.select_one("div.price p, .price, .money")
                price_str = price_el.get_text(strip=True) if price_el else ""

                img_el = block.select_one("div.pic img, div.thum img, img")
                img_url = (img_el.get("src") or img_el.get("data-src") or "") if img_el else ""

                shop_el = block.select_one("div.basic p.shop, .shop")
                shop = shop_el.get_text(strip=True) if shop_el else ""

                itemno_el = block.select_one("div.basic p.itemno, .itemno")
                item_no = itemno_el.get_text(strip=True) if itemno_el else ""

                stock_el = block.select_one("div.basic p.stock, .stock")
                stock_text = stock_el.get_text(strip=True) if stock_el else ""
                has_soldout_mark = bool(block.select_one(".soldout, .sold_out, .sold, .addalert"))
                has_cart_btn = bool(block.select_one(".addcart, .cart_btn, a[class*='addbasket'], div.addcart a"))
                is_new = 1 if block.select_one(".new_arrival, .new") else 0

                parsed = self._build_parsed_item(
                    item_id=item_id,
                    item_href=item_href,
                    title=title,
                    price_str=price_str,
                    img_url=img_url,
                    shop=shop,
                    item_no=item_no,
                    stock_text=stock_text,
                    has_soldout_mark=has_soldout_mark,
                    has_cart_btn=has_cart_btn,
                    is_new=is_new,
                    current_cat_code=current_cat_code,
                    now_ts=now_ts,
                )
                if parsed is not None:
                    items.append(parsed)
            except Exception as e:
                logger.debug(f"解析单个商品 block 异常: {e}")
                continue

        # 分页判断：检测 pager 中是否存在下一个页面链接
        has_next = False
        next_div = soup.select_one("div.pager div.next")
        if next_div:
            # 如果包含可点击的 <a> 标签且不是纯 <p>，则表示还有下一页
            next_a = next_div.select_one("a[href]")
            if next_a and "次のページ" in next_a.get_text():
                has_next = True

        return items, has_next

    def fetch_item_detail(self, item_code: str) -> Optional[Dict[str, Any]]:
        """抓取单个商品详情页完整数据（高清图集、成色状态、各店铺在库一览）"""
        url = f"{DETAIL_ENDPOINT}?itemCode={item_code}&lang=ja"
        html = self._get(url)
        if not html:
            return None

        soup = BeautifulSoup(html, BS_PARSER)
        
        # 标题
        title_el = soup.select_one("h1, .content_head .title, .detail_panel")
        title = title_el.get_text(strip=True) if title_el else ""

        # 多图集
        images = []
        for img in soup.select("#elevate_zoom_gallery a, .pic_select a img, .pic img"):
            src = img.get("data-zoom-image") or img.get("data-image") or img.get("src") or ""
            if src and src not in images:
                if not src.startswith("http"):
                    src = urljoin(BASE_ORDER_URL, src)
                images.append(src)

        # 状态与描述
        desc_el = soup.select_one(".status table, .detail_panel .status, .detail_panel")
        desc = desc_el.get_text(separator="\n", strip=True) if desc_el else ""

        # 价格
        price_meta = soup.select_one("meta[itemprop='price']")
        price_val = 0
        if price_meta and price_meta.get("content"):
            try:
                price_val = int(price_meta["content"].replace(",", ""))
            except Exception:
                pass

        return {
            "id": str(item_code),
            "title": title,
            "price": price_val,
            "images": images,
            "description": desc,
            "url": url,
        }

    def check_item_realtime_stock(self, item_id: str) -> Optional[int]:
        """
        通过 Mandarake 官方核心内部 API 实时精准校验单品在库状态
        :param item_id: 商品 ID (itemCode)
        :return: 1 (在库有货), 0 (售罄缺货), None (接口异常/网络超时)
        """
        url = "https://my.mandarake.co.jp/ItemDetailInfo/getInfo/"
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Referer": f"{DETAIL_ENDPOINT}?itemCode={item_id}&lang=ja",
            "Origin": "https://order.mandarake.co.jp"
        }
        try:
            resp = self._get_session().post(url, data={"idx": str(item_id).strip(), "lang": "ja"}, headers=headers, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "ok" and "zaiko" in data:
                    return int(data["zaiko"])
                logger.warning(f"实时库存校验返回非预期响应 (ID: {item_id}): {resp.text}")
        except Exception as e:
            logger.warning(f"实时库存校验请求异常 (ID: {item_id}): {e}")
        return None
