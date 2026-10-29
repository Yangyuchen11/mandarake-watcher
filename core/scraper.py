"""
Mandarake (order.mandarake.co.jp) 专用抓取与解析模块
专注于大分类【ドール】(020114) 下属所有分类的商品流与检索，支持高效翻页、在库过滤与详细信息提取
"""
import os
import re
import time
import random
import logging
from typing import List, Dict, Optional, Tuple, Any
from urllib.parse import urljoin, parse_qs, urlparse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

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
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        # 配置针对 HTTP Keep-Alive 空闲超时的底层透明重试适配器
        retries = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.3,
            status_forcelist=[502, 503, 504],
            raise_on_status=False
        )
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.last_fetch_failed = False
        self._ensure_cookies()

    def _ensure_cookies(self):
        """访问主站初始化关键追踪 Cookie (tr_mndrk_user)，防止 listPage 遭遇 302 重定向"""
        try:
            r = self.session.get(BASE_SITE_URL, timeout=self.timeout)
            if r.status_code == 200:
                logger.debug("Mandarake 基础 Session 与 Cookie 初始化完成")
        except Exception as e:
            logger.warning(f"初始化 Mandarake 基础会话异常: {e}")

    def _delay_polite(self):
        """礼貌延时与随机抖动，防止触发高频风控"""
        delay = random.uniform(self.min_delay, self.max_delay)
        time.sleep(delay)

    def _get(self, url: str, params: Optional[dict] = None) -> Optional[str]:
        """带自动重试与反封禁退避机制的 HTTP GET"""
        self.last_fetch_failed = False
        last_exc = None

        for attempt in range(self.max_retries + 1):
            try:
                # 检查 session 中是否有必要的 cookie
                if "tr_mndrk_user" not in self.session.cookies:
                    self._ensure_cookies()

                resp = self.session.get(url, params=params, timeout=self.timeout, allow_redirects=True)
                
                # 若被意外重定向回主站，重置 session 后再试
                if "order.mandarake.co.jp" not in resp.url and "mandarake.co.jp" in resp.url:
                    logger.warning(f"请求被重定向至 {resp.url}，正在刷新 cookie 重试...")
                    self._ensure_cookies()
                    if attempt < self.max_retries:
                        time.sleep(1.0)
                        continue

                if resp.status_code == 200:
                    return resp.text

                # 应对 429 Too Many Requests / 503 繁忙
                if resp.status_code in (429, 502, 503, 504):
                    wait_sec = (attempt + 1) * 2.0
                    logger.warning(f"官网响应 {resp.status_code}，正在退避重试 ({attempt + 1}/{self.max_retries + 1})，等待 {wait_sec}s...")
                    time.sleep(wait_sec)
                    continue
                else:
                    logger.warning(f"请求 {url} 返回非预期状态码: {resp.status_code}")

            except Exception as e:
                last_exc = e
                # 若连接被远端静默断开，主动重置连接池
                try:
                    self.session.close()
                except Exception:
                    pass
                wait_sec = (attempt + 1) * 1.5
                if attempt > 0:
                    logger.warning(f"网络请求发生错误: {e}，正在重试 ({attempt + 1}/{self.max_retries + 1})...")
                else:
                    logger.debug(f"检测到远端空闲长连接重置 ({e})，正在自动重连...")
                time.sleep(wait_sec)

        self.last_fetch_failed = True
        logger.error(f"抓取页面失败: {url}, 参数: {params}, 最后异常: {last_exc}")
        return None

    def fetch_category_page(
        self,
        category_code: str = ROOT_DOLL_CODE,
        page: int = 1,
        in_stock_only: bool = False,
        disp_count: int = 240,
        keyword: Optional[str] = None,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
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
        :return: (商品列表, 是否还有下一页, 当前页商品数)
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

        self._delay_polite()
        html = self._get(LIST_ENDPOINT, params=params)
        if not html:
            return [], False, 0

        items, has_next = self.parse_list_page(html, category_code)
        return items, has_next, len(items)

    def parse_list_page(self, html: str, current_cat_code: str) -> Tuple[List[Dict[str, Any]], bool]:
        """解析商品列表页 HTML，提取全字段商品信息与分页判断"""
        if not html:
            return [], False

        soup = BeautifulSoup(html, "html.parser")
        items: List[Dict[str, Any]] = []

        # 查找所有商品 block
        blocks = soup.select("div.thumlarge div.block, .thumlist div.block, div.block[data-itemidx]")
        cat_name = category_mgr.get_category_name(current_cat_code)

        for block in blocks:
            try:
                # 1. 唯一标识 item idx
                item_id = block.get("data-itemidx")
                link_el = block.select_one("div.title a, div.thum a, a[href*='detailPage']")
                item_href = link_el.get("href", "") if link_el else ""

                if not item_id and item_href:
                    # 从 detailPage 链接提取参数 itemCode
                    parsed_qs = parse_qs(urlparse(item_href).query)
                    if "itemCode" in parsed_qs:
                        item_id = parsed_qs["itemCode"][0]

                if not item_id:
                    continue

                item_id = str(item_id).strip()

                # 2. 标题
                title_el = block.select_one("div.title p a, div.title a, .title")
                title = title_el.get_text(strip=True) if title_el else ""

                # 3. 价格与含税提取
                price_el = block.select_one("div.price p, .price, .money")
                price_str = price_el.get_text(strip=True) if price_el else ""
                
                # 提取纯数字价格 (优先税前基本价，如 2,500円)
                price_val = 0
                if price_str:
                    clean_p = price_str.replace(",", "")
                    match = re.search(r"(\d+)\s*円", clean_p)
                    if match:
                        price_val = int(match.group(1))

                # 4. 商品详情直达链接
                full_url = urljoin(BASE_ORDER_URL, item_href) if item_href else f"{DETAIL_ENDPOINT}?itemCode={item_id}&lang=ja"

                # 5. 缩略图
                img_el = block.select_one("div.pic img, div.thum img, img")
                img_url = ""
                if img_el:
                    img_url = img_el.get("src") or img_el.get("data-src") or ""
                    if img_url and not img_url.startswith("http"):
                        img_url = urljoin(BASE_ORDER_URL, img_url)

                # 6. 店铺归属 (如 SAHRA, 中野店, 札幌店等)
                shop_el = block.select_one("div.basic p.shop, .shop")
                shop = shop_el.get_text(strip=True) if shop_el else ""

                # 7. 商品管理编号
                itemno_el = block.select_one("div.basic p.itemno, .itemno")
                item_no = itemno_el.get_text(strip=True) if itemno_el else ""

                # 8. 在库状态检验 (多重互斥严谨判定：必须有加入购物车按钮且无缺货标志)
                stock_el = block.select_one("div.basic p.stock, .stock")
                stock_text = stock_el.get_text(strip=True) if stock_el else ""
                has_soldout_mark = bool(block.select_one(".soldout, .sold_out, .sold, .addalert"))
                has_cart_btn = bool(block.select_one(".addcart, .cart_btn, a[class*='addbasket'], div.addcart a"))
                is_soldout = (
                    has_soldout_mark
                    or not has_cart_btn
                    or "売り切れ" in stock_text
                    or "在庫なし" in stock_text
                    or "品切" in stock_text
                )
                is_in_stock = 0 if is_soldout else 1

                # 9. 新着标签
                is_new = 1 if block.select_one(".new_arrival, .new") else 0

                now_ts = int(time.time())

                # 优先提取官网链接中的原始官方分类代码，保证100%忠于官网
                official_cat = ""
                if item_href:
                    parsed_qs = parse_qs(urlparse(item_href).query)
                    if "categoryCode" in parsed_qs and parsed_qs["categoryCode"]:
                        official_cat = parsed_qs["categoryCode"][0].strip()

                cat_code_to_use = official_cat if (official_cat and category_mgr.is_valid_doll_code(official_cat)) else (current_cat_code or ROOT_DOLL_CODE)
                final_cat_code, final_cat_name = category_mgr.infer_category(title, cat_code_to_use)

                items.append({
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
                })
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

        soup = BeautifulSoup(html, "html.parser")
        
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
            resp = self.session.post(url, data={"idx": str(item_id).strip(), "lang": "ja"}, headers=headers, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "ok" and "zaiko" in data:
                    return int(data["zaiko"])
                logger.warning(f"实时库存校验返回非预期响应 (ID: {item_id}): {resp.text}")
        except Exception as e:
            logger.warning(f"实时库存校验请求异常 (ID: {item_id}): {e}")
        return None
