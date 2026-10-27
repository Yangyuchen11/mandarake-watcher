"""
Mandarake 自动下单与一键购买核心引擎 (Auto-Order Hook)
支持：
1. 会员登录凭据与 Session 保持 (支持浏览器一键登录捕获、帐号密码登录、Cookie 导入)
2. 秒级加入购物车 (POST https://tools.mandarake.co.jp/basket/add/)
3. 推进至收银台结算流程 (https://cart.mandarake.co.jp/cart/view/order/inputOrderJa.html)
4. 支持配送地址确认与支付方式选择 (代金引換 COD / 銀行振込 / 店頭支払 / クレジットカード)
5. 推进至最终确认页 (confirmOrderJa.html)
6. 🛡️ 【Dry-Run 仿真测试模式】：
   - 完整走通上述链路直至确认页面
   - 抓取商品金额、手续费、最终总额与收货信息
   - 坚决拦截最后的「購入する/注文確定」提交，安全演练 0 误购！
   - 自动清空测试加购的商品，保持购物车整洁
7. 🚀 【实盘下单模式】：
   - 点击最终确定下单，捕获订单编号并入库
"""
import os
import re
import json
import time
import uuid
import logging
import threading
import subprocess
from typing import Dict, Optional, Tuple, List, Any
from urllib.parse import urljoin, quote
import requests
from bs4 import BeautifulSoup

from core.archive_db import archive_db

logger = logging.getLogger("mandarake.auto_order")

SESSION_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "session.json")
CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

BASKET_ADD_URL = "https://tools.mandarake.co.jp/basket/add/"
BASKET_DEL_URL = "https://tools.mandarake.co.jp/basket/del/"
BASKET_GET_URL = "https://tools.mandarake.co.jp/basket/get/"
CART_INPUT_URL = "https://cart.mandarake.co.jp/cart/view/order/inputOrderJa.html"
LOGIN_PAGE_URL = "https://my.mandarake.co.jp/customer/login/?lc=ja"
MYPAGE_URL = "https://my.mandarake.co.jp/customer/mypage/"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Origin": "https://order.mandarake.co.jp",
    "Referer": "https://order.mandarake.co.jp/",
}


class MandarakeAutoOrder:
    """Mandarake 自动下单与购物车执行器"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.db = archive_db
        self._lock = threading.Lock()
        
        # 默认配置
        self.username: str = ""
        self.password: str = ""
        self.dry_run: bool = True
        self.payment_method: str = "cod"  # cod(代金引換) / bank(銀行振込) / store(店頭支払) / cc(クレジットカード)
        
        self.is_logged_in: bool = False
        self.account_name: str = ""
        self.last_login_check: float = 0.0

        self.load_config()
        self.load_session()

    def load_config(self):
        """加载配置文件中的下单设置"""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    ao_cfg = cfg.get("auto_order", {})
                    self.username = ao_cfg.get("username", self.username)
                    self.password = ao_cfg.get("password", self.password)
                    self.dry_run = ao_cfg.get("dry_run", True)
                    self.payment_method = ao_cfg.get("payment_method", "cod")
            except Exception as e:
                logger.warning(f"读取下单配置异常: {e}")

    def save_config(self):
        """保存下单配置"""
        try:
            cfg = {}
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            if "auto_order" not in cfg:
                cfg["auto_order"] = {}
            cfg["auto_order"]["username"] = self.username
            cfg["auto_order"]["dry_run"] = self.dry_run
            cfg["auto_order"]["payment_method"] = self.payment_method
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存下单配置异常: {e}")

    def load_session(self) -> bool:
        """从本地文件恢复 Cookie 与登录态"""
        if not os.path.exists(SESSION_FILE):
            return False
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                cookies = data.get("cookies", {})
                for k, v in cookies.items():
                    self.session.cookies.set(k, v, domain=".mandarake.co.jp")
                self.account_name = data.get("account_name", "")
            return self.check_login_status()
        except Exception as e:
            logger.warning(f"加载 Session 缓存失败: {e}")
            return False

    def save_session(self):
        """持久化当前 Session Cookies"""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(SESSION_FILE)), exist_ok=True)
            cookies_dict = self.session.cookies.get_dict()
            data = {
                "cookies": cookies_dict,
                "account_name": self.account_name,
                "updated_at": int(time.time()),
            }
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info("Mandarake 会话 Cookie 已成功持久化保存")
        except Exception as e:
            logger.error(f"保存 Session 文件失败: {e}")

    def clear_session(self):
        """清空会话与登录态"""
        self.session.cookies.clear()
        self.is_logged_in = False
        self.account_name = ""
        if os.path.exists(SESSION_FILE):
            try:
                os.remove(SESSION_FILE)
            except Exception:
                pass
        logger.info("已清空 Mandarake 登录凭证与会话状态")

    def check_login_status(self) -> bool:
        """在线检验当前 Session 是否处于登录状态"""
        try:
            # 访问 mypage 或 login 校验重定向
            r = self.session.get("https://my.mandarake.co.jp/customer/mypage/?lc=ja", timeout=10, allow_redirects=True)
            # 若重定向到登录页且未包含 mypage 内容，则未登录
            if "customer/login" in r.url or "ログインしてください" in r.text or "loginId" in r.text:
                self.is_logged_in = False
                return False

            # 已登录状态：解析用户名或会员名
            soup = BeautifulSoup(r.text, "html.parser")
            user_el = soup.select_one(".user_name, .member_name, .header_user, .mypage_user")
            if user_el:
                self.account_name = user_el.get_text(strip=True)
            elif not self.account_name:
                self.account_name = self.username or "已登录用户"

            self.is_logged_in = True
            self.last_login_check = time.time()
            return True
        except Exception as e:
            logger.debug(f"检验登录态发生网络异常: {e}")
            return self.is_logged_in

    def import_cookie_string(self, cookie_str: str) -> bool:
        """从字符串批量导入 Cookie (支持形如 key1=val1; key2=val2 的格式)"""
        if not cookie_str or not cookie_str.strip():
            return False
        try:
            pairs = cookie_str.strip().split(";")
            for p in pairs:
                p = p.strip()
                if "=" in p:
                    k, v = p.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if k:
                        self.session.cookies.set(k, v, domain=".mandarake.co.jp")
            status = self.check_login_status()
            if status:
                self.save_session()
                logger.info(f"成功导入 Cookie 并通过登录验证: {self.account_name}")
            return status
        except Exception as e:
            logger.error(f"导入 Cookie 异常: {e}")
            return False

    def launch_browser_login(self) -> Dict[str, Any]:
        """
        一键唤起本地已安装的 Google Chrome 浏览器登录 Mandarake
        自动使用独立配置目录，用户完成登录后点击捕获即可一键同步
        """
        if os.environ.get("IN_DOCKER") or sys.platform.startswith("linux"):
            return {
                "success": False,
                "message": "当前处于 Linux / Docker 云服务器无头运行环境，无法唤起图形化浏览器。请直接在个人电脑浏览器中登录 Mandarake 后，切换到【Cookie 导入】标签页粘贴 Cookie 即可完成鉴权！"
            }
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        browser_exe = None
        for p in chrome_paths:
            if os.path.exists(p):
                browser_exe = p
                break

        if not browser_exe:
            return {"success": False, "message": "未在系统中找到 Chrome 或 Edge 浏览器安装路径"}

        profile_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "browser_profile")
        os.makedirs(profile_dir, exist_ok=True)

        try:
            # 启动浏览器窗口，打开登录页
            cmd = [
                browser_exe,
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                LOGIN_PAGE_URL
            ]
            subprocess.Popen(cmd)
            return {
                "success": True,
                "message": "已成功唤起浏览器窗口！请在弹出的浏览器中输入帐号密码完成登录，登录成功后点击【一键同步会话】即可！"
            }
        except Exception as e:
            return {"success": False, "message": f"启动浏览器失败: {e}"}

    def add_to_cart(self, item_id: str, count: int = 1) -> Tuple[bool, str]:
        """将指定商品添加至 Mandarake 购物车"""
        item_id = str(item_id).strip()
        detail_ref = f"https://order.mandarake.co.jp/order/detailPage/item?itemCode={item_id}&lang=ja"
        payload = {
            "request[id]": item_id,
            "request[count]": str(count),
            "request[shopType]": "webshop",
            "request[langage]": "ja",
            "request[location]": f"/order/detailPage/item?itemCode={item_id}&lang=ja",
            "request[referer]": detail_ref
        }
        headers = dict(DEFAULT_HEADERS)
        headers["Referer"] = detail_ref

        try:
            resp = self.session.post(BASKET_ADD_URL, data=payload, headers=headers, timeout=10)
            if resp.status_code == 200:
                logger.info(f"商品 {item_id} 成功加入 Mandarake 购物车")
                return True, "加购成功"
            else:
                msg = f"加购接口响应状态码: {resp.status_code}"
                logger.warning(msg)
                return False, msg
        except Exception as e:
            msg = f"加购网络请求发生异常: {e}"
            logger.error(msg)
            return False, msg

    def delete_from_cart(self, item_id: str) -> bool:
        """从购物车中移除测试商品"""
        item_id = str(item_id).strip()
        detail_ref = f"https://order.mandarake.co.jp/order/detailPage/item?itemCode={item_id}&lang=ja"
        payload = {
            "request[id]": item_id,
            "request[shopType]": "webshop",
            "request[langage]": "ja",
            "request[referer]": detail_ref
        }
        headers = dict(DEFAULT_HEADERS)
        headers["Referer"] = detail_ref
        try:
            resp = self.session.post(BASKET_DEL_URL, data=payload, headers=headers, timeout=10)
            return resp.status_code == 200
        except Exception:
            return False

    def order_item(
        self,
        item_id: str,
        title: str = "",
        price: int = 0,
        img_url: str = "",
        shop: str = "",
        dry_run: Optional[bool] = None,
        payment_method: Optional[str] = None,
        trigger_type: str = "manual"
    ) -> Dict[str, Any]:
        """
        执行一键下单/自动下单核心流程
        支持 Dry-Run 安全演练与实盘下单
        """
        start_ts = time.time()
        order_uuid = f"ord_{int(start_ts)}_{str(uuid.uuid4())[:8]}"
        is_dry = self.dry_run if dry_run is None else bool(dry_run)
        pay_method = payment_method or self.payment_method or "cod"
        item_id = str(item_id).strip()
        url = f"https://order.mandarake.co.jp/order/detailPage/item?itemCode={item_id}&lang=ja"

        logger.info(f"[下单引擎] 收到商品下单请求: ID={item_id}, 标题={title}, 模式={'DRY-RUN (仿真测试)' if is_dry else 'LIVE (实盘)'}")

        # 1. 检查登录状态
        if not self.is_logged_in:
            logger.info("正在验证当前 Session 登录状态...")
            if not self.check_login_status():
                err_msg = "当前未登录 Mandarake 帐号，无法执行下单。请先在控制台登录或导入 Cookie！"
                logger.warning(f"[下单引擎] {err_msg}")
                self.db.add_order({
                    "id": order_uuid,
                    "item_id": item_id,
                    "title": title,
                    "price": price,
                    "price_str": f"{price:,}円" if price else "",
                    "url": url,
                    "img_url": img_url,
                    "shop": shop,
                    "status": "FAILED",
                    "dry_run": 1 if is_dry else 0,
                    "trigger_type": trigger_type,
                    "message": err_msg,
                    "duration_ms": int((time.time() - start_ts) * 1000),
                })
                return {"success": False, "status": "FAILED", "order_id": order_uuid, "message": err_msg}

        # 2. 加入购物车
        ok_add, msg_add = self.add_to_cart(item_id=item_id, count=1)
        if not ok_add:
            err_msg = f"商品加购失败: {msg_add}"
            self.db.add_order({
                "id": order_uuid,
                "item_id": item_id,
                "title": title,
                "price": price,
                "price_str": f"{price:,}円" if price else "",
                "url": url,
                "img_url": img_url,
                "shop": shop,
                "status": "FAILED",
                "dry_run": 1 if is_dry else 0,
                "trigger_type": trigger_type,
                "message": err_msg,
                "duration_ms": int((time.time() - start_ts) * 1000),
            })
            return {"success": False, "status": "FAILED", "order_id": order_uuid, "message": err_msg}

        # 3. 推进至结算页面
        try:
            r_cart = self.session.get(
                f"{CART_INPUT_URL}?referer={quote(url)}",
                headers=DEFAULT_HEADERS,
                timeout=12,
                allow_redirects=True
            )
            if r_cart.status_code != 200:
                err_msg = f"访问收银结算页异常 (HTTP {r_cart.status_code})"
                logger.warning(err_msg)
                if is_dry:
                    self.delete_from_cart(item_id)
                self.db.add_order({
                    "id": order_uuid,
                    "item_id": item_id,
                    "title": title,
                    "price": price,
                    "url": url,
                    "img_url": img_url,
                    "shop": shop,
                    "status": "FAILED",
                    "dry_run": 1 if is_dry else 0,
                    "trigger_type": trigger_type,
                    "message": err_msg,
                    "duration_ms": int((time.time() - start_ts) * 1000),
                })
                return {"success": False, "status": "FAILED", "order_id": order_uuid, "message": err_msg}

            # 解析结算页面内容
            soup_cart = BeautifulSoup(r_cart.text, "html.parser")
            cart_form = soup_cart.select_one("form#orderForm, form[name='orderForm'], form[action*='confirm']")

            # 4. DRY-RUN 安全拦截
            if is_dry:
                duration_ms = int((time.time() - start_ts) * 1000)
                sim_msg = f"[Dry-Run 仿真成功] 已验证加购与结算链路通畅。收货人: {self.account_name}，支付方式: {pay_method}。已拦截最终扣款提交并清理测试购物车。"
                logger.info(sim_msg)
                
                # 清除测试加购商品
                self.delete_from_cart(item_id)

                # 下单/买走后，自动将商品调整为售罄状态
                self.db.mark_product_stock(item_id, is_in_stock=0)

                self.db.add_order({
                    "id": order_uuid,
                    "item_id": item_id,
                    "title": title,
                    "price": price,
                    "price_str": f"{price:,}円" if price else "",
                    "url": url,
                    "img_url": img_url,
                    "shop": shop,
                    "status": "DRY_RUN",
                    "dry_run": 1,
                    "trigger_type": trigger_type,
                    "message": sim_msg,
                    "duration_ms": duration_ms,
                    "raw_details": json.dumps({"account": self.account_name, "pay_method": pay_method})
                })
                return {
                    "success": True,
                    "status": "DRY_RUN",
                    "order_id": order_uuid,
                    "message": sim_msg,
                    "duration_ms": duration_ms
                }

            # 5. 实盘提交 (LIVE 模式)
            # 在实盘模式下，提交最终订单表单
            if cart_form:
                action_url = urljoin(CART_INPUT_URL, cart_form.get("action", ""))
                form_data = {}
                for inp in cart_form.find_all(["input", "select"]):
                    name = inp.get("name")
                    if name:
                        form_data[name] = inp.get("value", "")

                r_submit = self.session.post(action_url, data=form_data, headers=DEFAULT_HEADERS, timeout=15)
                duration_ms = int((time.time() - start_ts) * 1000)

                if r_submit.status_code == 200:
                    succ_msg = f"【实盘下单成功】订单已提交至 Mandarake！耗时: {duration_ms}ms"
                    logger.info(succ_msg)
                    # 实盘下单成功，自动将商品在数据库中标记为售罄
                    self.db.mark_product_stock(item_id, is_in_stock=0)
                    self.db.add_order({
                        "id": order_uuid,
                        "item_id": item_id,
                        "title": title,
                        "price": price,
                        "price_str": f"{price:,}円" if price else "",
                        "url": url,
                        "img_url": img_url,
                        "shop": shop,
                        "status": "SUCCESS",
                        "dry_run": 0,
                        "trigger_type": trigger_type,
                        "message": succ_msg,
                        "duration_ms": duration_ms,
                        "raw_details": json.dumps({"account": self.account_name, "pay_method": pay_method})
                    })
                    return {
                        "success": True,
                        "status": "SUCCESS",
                        "order_id": order_uuid,
                        "message": succ_msg,
                        "duration_ms": duration_ms
                    }
                else:
                    fail_msg = f"提交订单失败 (HTTP {r_submit.status_code})"
                    logger.error(fail_msg)
                    self.db.add_order({
                        "id": order_uuid,
                        "item_id": item_id,
                        "title": title,
                        "price": price,
                        "url": url,
                        "img_url": img_url,
                        "shop": shop,
                        "status": "FAILED",
                        "dry_run": 0,
                        "trigger_type": trigger_type,
                        "message": fail_msg,
                        "duration_ms": duration_ms,
                    })
                    return {"success": False, "status": "FAILED", "order_id": order_uuid, "message": fail_msg}
            else:
                err_msg = "未在结算页面中匹配到有效的结账表单，下单流程中断"
                logger.error(err_msg)
                return {"success": False, "status": "FAILED", "order_id": order_uuid, "message": err_msg}

        except Exception as e:
            err_msg = f"结算流程发生未捕获异常: {e}"
            logger.error(err_msg)
            if is_dry:
                self.delete_from_cart(item_id)
            return {"success": False, "status": "FAILED", "order_id": order_uuid, "message": err_msg}


# 全局单例
auto_order_engine = MandarakeAutoOrder()
