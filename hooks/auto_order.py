"""
Mandarake 自动抢购与一键购买核心引擎 (Auto-Order Hook)

支持功能：
1. Web 面板登录体系：
   - 账号密码直接登录 (POST my.mandarake.co.jp/customer/login/?lc=ja，遵守官方校验，不绕过验证码)
   - 真实浏览器辅助登录 + Cookie / Session 导入 (支持 document.cookie、Header Cookie、JSON、SessionId/UserId)
   - 基于官方 my.mandarake.co.jp/customer/ajax/my/ 与 order.mandarake.co.jp/order/mypage/mypageTop 在线验活
2. 收货地址与配送偏好配置 (支持使用会员默认登记地址或 Web 面板自定义日本收货信息覆盖)
3. 支付方式分析与选择：仅提供「无需下单时即时外部认证即可提交成立订单」的官方支付方式，
   并支持主选不可用时的备选降级支付方式
4. Seasar2 / Teeda 结算链路执行：
   - 加购 (POST tools.mandarake.co.jp/basket/add/)
   - 填表确认 (inputOrderJa.html -> confirmOrderJa.html)
   - Dry-Run 仿真演练（拦截最终下单并清理测试购物车，不污染真实在库状态）
   - Live 实盘极速下单（提交 completeOrderJa 并记录订单号与付款链接）
"""
import os
import re
import sys
import json
import time
import uuid
import copy
import logging
import threading
import subprocess
import webbrowser
from typing import Dict, Optional, Tuple, List, Any
from html import unescape
from urllib.parse import urljoin, quote, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

from core.archive_db import archive_db
from core.config_helper import load_config as load_cfg_helper, save_config as save_cfg_helper

logger = logging.getLogger("mandarake.auto_order")

SESSION_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "session.json")

BASKET_ADD_URL = "https://tools.mandarake.co.jp/basket/add/"
BASKET_DEL_URL = "https://tools.mandarake.co.jp/basket/del/"
BASKET_GET_URL = "https://tools.mandarake.co.jp/basket/get/"
CART_INPUT_URL = "https://cart.mandarake.co.jp/cart/view/order/inputOrderJa.html"
LOGIN_PAGE_URL = "https://my.mandarake.co.jp/customer/login/?lc=ja"
LOGIN_STATUS_AJAX_URL = "https://my.mandarake.co.jp/customer/ajax/my/"
MYPAGE_TOP_URL = "https://order.mandarake.co.jp/order/mypage/mypageTop?lang=ja"
LOGOUT_URL = "https://my.mandarake.co.jp/customer/logout/"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Origin": "https://order.mandarake.co.jp",
    "Referer": "https://order.mandarake.co.jp/",
}

# 日本 47 都道府县 (JIS 标准顺序 01-47)
JP_PREFECTURES: List[str] = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]

# 经核验官方支付说明 (https://www.mandarake.co.jp/mail-order/help/payment.html & receive_at_store.html)：
# 以下 7 种支付方式均可在 Mandarake 订单页直接提交完成下单（无需下单当下实时 SMS/OAuth 阻塞）
INSTANT_PAYMENT_METHODS: List[Dict[str, Any]] = [
    {
        "code": "bank",
        "label": "銀行振込（银行转账 · 推荐）",
        "jp_name": "銀行振込",
        "recommended": True,
        "badge": "最稳 · 全店通用 · 零认证",
        "summary": "下单立即锁单成立，全门店及通販仓库 SAHRA 均可使用；确认邮件后再按邮件内決済URL的PayPay银行专户汇款（当日含5日内）。",
        "post_order_action": "收到门店在庫確認メール后，5日内向指定口座汇款。",
        "caveats": "请勿在确认邮件到达前提前转账（各门店账号不同）；逾期未付款将被限制账户使用。",
        "exact_values": ["011"],
        "match_keywords": ["銀行振込", "銀行振込み", "銀行", "振込", "paymentmethodbank", "bank"],
        "requires_store_pickup": False,
    },
    {
        "code": "cod",
        "label": "代金引換 / 代引き（货到付款 · 推荐）",
        "jp_name": "代金引換",
        "recommended": True,
        "badge": "极速 · 货到付款 · 零认证",
        "summary": "无需任何在线认证或跳转即可在下单瞬间成立订单，配送员（佐川急便）上门时以现金、信用卡或借记卡支付。",
        "post_order_action": "商品送达时向配送员支付货款与代引手续费。",
        "caveats": "含代引手续费（1万以下300円+税 / 3万以下400円+税 / 10万以下600円+税 / 30万以下1000円+税）；部分离岛、冲绳及邮件便不可用。",
        "exact_values": ["008"],
        "match_keywords": ["代金引換", "代引き", "代引", "paymentmethodcod", "cod"],
        "requires_store_pickup": False,
    },
    {
        "code": "credit_card",
        "label": "クレジットカード（信用卡 · 先锁单邮件后付）",
        "jp_name": "クレジットカード",
        "recommended": False,
        "badge": "先锁单 · 邮件后付",
        "summary": "Mandarake 规范为先提交下单锁库存，待门店发送「ご注文確定メール」后再访问邮件中的決済URL完成付款（支持 VISA/JCB/Master）。",
        "post_order_action": "收到确认邮件后打开決済URL完成 3D Secure 2.0 付款。",
        "caveats": "若部分即时库存仓库（如 SAHRA）在提交时直接跳转 3DS 外部认证页面，系统将中止静默扣款并记录付款链接供手动完成。",
        "exact_values": ["015"],
        "match_keywords": ["クレジットカード", "クレジット", "カード決済", "paymentmethodpaypalcredit", "credit"],
        "requires_store_pickup": False,
    },
    {
        "code": "paypal",
        "label": "PayPal（先锁单 · 邮件后付）",
        "jp_name": "PayPal",
        "recommended": False,
        "badge": "先锁单 · 邮件后付",
        "summary": "一般门店商品下单时无需即时认证即可直接锁单成立，确认库存后再凭邮件内決済URL登录 PayPal 支付。",
        "post_order_action": "收到确认邮件后打开決済URL使用 PayPal 付款。",
        "caveats": "含成人向商品订单不可用；单笔上限 100 万日元；若页面即时跳转 PayPal 授权将返回链接供手动确认。",
        "exact_values": ["005"],
        "match_keywords": ["paymentmethodpaypal", "paypal", "ペイパル"],
        "requires_store_pickup": False,
    },
    {
        "code": "store",
        "label": "店頭受取・店頭支払（门店自提现付）",
        "jp_name": "店頭受取",
        "recommended": False,
        "badge": "零认证 · 免运费 · 仅实体店",
        "summary": "下单直接为实体门店预留商品，无需任何线上认证或提前支付；库存确认邮件后 5 日内凭订单号与姓名到店付款自提。",
        "post_order_action": "5 日内前往下单实体门店现场付款取货。",
        "caveats": "仅限实体店自提，SAHRA 仓库订单不可选到店现金支付（SAHRA 自提仍需事前入金），且到店后不可取消。",
        "exact_values": ["006"],
        "match_keywords": ["店頭受取", "店頭支払", "店頭", "店舗受取", "store"],
        "requires_store_pickup": True,
    },
    {
        "code": "postpay_cvs",
        "label": "後払い（コンビニ/銀行ATM）",
        "jp_name": "後払い",
        "recommended": False,
        "badge": "先提交 · 随后SMS取码",
        "summary": "在注文页选中「後払い（コンビニ/銀行ATM）」即可直接完成下单提交，订单成立后再于支付页面完成日本手机号 SMS 验证。",
        "post_order_action": "下单后在支付页面完成 SMS 验证，10 日内于便利店或银行 ATM 付款。",
        "caveats": "仅限日本国内注册地址及日本国内手机号码，含后付手续费，海外配送不可用。",
        "exact_values": [],
        "match_keywords": ["後払い（コンビニ", "後払い(コンビニ", "コンビニ", "後払い", "銀行atm"],
        "requires_store_pickup": False,
    },
    {
        "code": "shopping_loan",
        "label": "ショッピングローン（无息分期 · ≥5万日元）",
        "jp_name": "ショッピングローン",
        "recommended": False,
        "badge": "≥5万円 · 免即时认证",
        "summary": "商品总额 5 万日元以上可用，下单时直接提交成立，1~2 日后由三井住友 C-Web 发送分期办理邮件。",
        "post_order_action": "等待 C-Web 邮件并凭 Entry Key 完成分期审查。",
        "caveats": "仅限商品代金满 50,000 日元及以上订单，且需通过 C-Web 审核。",
        "exact_values": [],
        "match_keywords": ["ショッピングローン", "ショッピングクレジット", "ローン", "cedyna"],
        "requires_store_pickup": False,
    },
]

PAYMENT_METHOD_MAP: Dict[str, Dict[str, Any]] = {m["code"]: m for m in INSTANT_PAYMENT_METHODS}

# 明确排除的「下单时需实时认证/无法自动完成」支付方式说明
EXCLUDED_PAYMENT_METHODS: List[Dict[str, str]] = [
    {
        "name": "Amazon Pay",
        "reason": "下单时必须跳转 Amazon 账户 OAuth 实时登录并交互确认支付方式，无法免认证自动完成。",
    },
    {
        "name": "あと払い（ペイディ / Paidy）",
        "reason": "结账提交当下需要实时接收并输入 4 位日本手机 SMS 验证码才能成立订单。",
    },
    {
        "name": "atone 翌月後払い",
        "reason": "结账提交当下需要登录 atone 账户并实时输入 SMS 短信验证码才能完成下单。",
    },
    {
        "name": "株主優待券 / まんだらけπ",
        "reason": "株主优待券需向门店邮寄实体券；まんだらけπ仅能在收到确认邮件后的信用卡/PayPal付款页面内抵扣，无法于下单页直接提交。",
    },
]

DEFAULT_ADDRESS_CONFIG: Dict[str, str] = {
    "last_name": "",
    "first_name": "",
    "last_name_kana": "",
    "first_name_kana": "",
    "postal_code": "",
    "prefecture": "",
    "address1": "",
    "address2": "",
    "phone": "",
    "email": "",
    "delivery_method": "auto",
    "delivery_time": "none",
    "remarks": "",
}

DEFAULT_PURCHASE_CONFIG: Dict[str, Any] = {
    "username": "",
    "password": "",
    "save_credentials": False,
    "dry_run": True,
    "auto_buy_enabled": False,
    "auto_buy_scope": "rule_enabled",  # "rule_enabled" | "all_rules"
    "auto_buy_on_restock": True,
    "auto_buy_max_price": 0,
    "auto_buy_max_orders_per_day": 5,
    "auto_buy_cooldown_seconds": 3600,
    "quick_buy_skip_confirm": False,
    "default_payment_method": "bank",
    "fallback_payment_method": "cod",
    "use_custom_address": False,
    "address": copy.deepcopy(DEFAULT_ADDRESS_CONFIG),
}


def _normalize_digits(value: str) -> str:
    return re.sub(r"[^0-9]", "", str(value or ""))


def _split_postal_code(postal: str) -> Tuple[str, str]:
    digits = _normalize_digits(postal)
    if len(digits) >= 7:
        return digits[:3], digits[3:7]
    if "-" in str(postal or ""):
        parts = [p.strip() for p in str(postal).split("-", 1)]
        return parts[0], parts[1] if len(parts) > 1 else ""
    return digits[:3], digits[3:]


def _split_phone(phone: str) -> Tuple[str, str, str]:
    raw = str(phone or "").strip()
    if "-" in raw:
        parts = [re.sub(r"[^0-9]", "", p) for p in raw.split("-") if p.strip()]
        if len(parts) >= 3:
            return parts[0], parts[1], "".join(parts[2:])
    digits = _normalize_digits(raw)
    if len(digits) == 11:
        return digits[:3], digits[3:7], digits[7:]
    if len(digits) == 10:
        if digits.startswith(("03", "06")):
            return digits[:2], digits[2:6], digits[6:]
        return digits[:3], digits[3:6], digits[6:]
    return digits, "", ""


class MandarakeAutoOrder:
    """Mandarake 自动抢购与一键下单核心执行器"""

    def __init__(self, db=None, session_file: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        self.db = db or archive_db
        self.session_file = session_file or SESSION_FILE
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self._order_lock = threading.Lock()
        self._state_lock = threading.Lock()

        self._order_listeners: List[Any] = []
        self._status_listeners: List[Any] = []

        self.config: Dict[str, Any] = copy.deepcopy(DEFAULT_PURCHASE_CONFIG)
        self.is_logged_in: bool = False
        self.account_name: str = ""
        self.last_login_check: float = 0.0
        self.last_login_message: str = "尚未验证登录态"
        self.cookie_count: int = 0

        self.reload_config(config)
        self.load_session()

    # --- 事件监听器 ---

    def add_order_listener(self, listener):
        if callable(listener) and listener not in self._order_listeners:
            self._order_listeners.append(listener)

    def add_status_listener(self, listener):
        if callable(listener) and listener not in self._status_listeners:
            self._status_listeners.append(listener)

    def _emit_order_event(self, order_data: Dict[str, Any]):
        for cb in list(self._order_listeners):
            try:
                cb(order_data)
            except Exception as e:
                logger.debug(f"订单监听回调异常: {e}")

    def _emit_status_event(self):
        summary = self.get_status_summary()
        for cb in list(self._status_listeners):
            try:
                cb(summary)
            except Exception as e:
                logger.debug(f"购买状态监听回调异常: {e}")

    # --- 配置加载与保存 ---

    def reload_config(self, root_config: Optional[Dict[str, Any]] = None):
        """从全局配置加载 purchase (并兼容旧 auto_order 字段)"""
        try:
            cfg = root_config if root_config is not None else load_cfg_helper()
            p_cfg = cfg.get("purchase") or {}
            legacy_cfg = cfg.get("auto_order") or {}

            merged = copy.deepcopy(DEFAULT_PURCHASE_CONFIG)
            for k, v in legacy_cfg.items():
                if k == "payment_method" and v in PAYMENT_METHOD_MAP:
                    merged["default_payment_method"] = v
                elif k in merged and not isinstance(merged[k], dict):
                    merged[k] = v

            for k, v in p_cfg.items():
                if k == "address" and isinstance(v, dict):
                    for ak, av in v.items():
                        if ak in merged["address"]:
                            merged["address"][ak] = str(av or "").strip() if ak != "remarks" else str(av or "")
                elif k in merged:
                    merged[k] = v

            self.config = self._sanitize_purchase_config(merged)
        except Exception as e:
            logger.warning(f"加载购买配置异常: {e}")

    @staticmethod
    def _sanitize_purchase_config(raw: Dict[str, Any]) -> Dict[str, Any]:
        clean = copy.deepcopy(DEFAULT_PURCHASE_CONFIG)
        clean["username"] = str(raw.get("username") or "").strip()
        clean["save_credentials"] = bool(raw.get("save_credentials", False))
        clean["password"] = str(raw.get("password") or "") if clean["save_credentials"] else ""
        clean["dry_run"] = bool(raw.get("dry_run", True))
        clean["auto_buy_enabled"] = bool(raw.get("auto_buy_enabled", False))
        scope = str(raw.get("auto_buy_scope") or "rule_enabled").strip().lower()
        clean["auto_buy_scope"] = scope if scope in ("rule_enabled", "all_rules") else "rule_enabled"
        clean["auto_buy_on_restock"] = bool(raw.get("auto_buy_on_restock", True))
        try:
            clean["auto_buy_max_price"] = max(0, int(raw.get("auto_buy_max_price") or 0))
        except (ValueError, TypeError):
            clean["auto_buy_max_price"] = 0
        try:
            clean["auto_buy_max_orders_per_day"] = max(1, min(100, int(raw.get("auto_buy_max_orders_per_day") or 5)))
        except (ValueError, TypeError):
            clean["auto_buy_max_orders_per_day"] = 5
        try:
            clean["auto_buy_cooldown_seconds"] = max(10, min(86400, int(raw.get("auto_buy_cooldown_seconds") or 3600)))
        except (ValueError, TypeError):
            clean["auto_buy_cooldown_seconds"] = 3600
        clean["quick_buy_skip_confirm"] = bool(raw.get("quick_buy_skip_confirm", False))

        pay_code = str(raw.get("default_payment_method") or "bank").strip().lower()
        if pay_code == "cc":
            pay_code = "credit_card"
        clean["default_payment_method"] = pay_code if pay_code in PAYMENT_METHOD_MAP else "bank"

        fb_code = str(raw.get("fallback_payment_method") or "cod").strip().lower()
        if fb_code == "cc":
            fb_code = "credit_card"
        if fb_code != "none" and fb_code not in PAYMENT_METHOD_MAP:
            fb_code = "cod"
        clean["fallback_payment_method"] = fb_code

        clean["use_custom_address"] = bool(raw.get("use_custom_address", False))

        raw_addr = raw.get("address") if isinstance(raw.get("address"), dict) else {}
        for ak in DEFAULT_ADDRESS_CONFIG:
            val = raw_addr.get(ak, DEFAULT_ADDRESS_CONFIG[ak])
            if ak == "delivery_method":
                clean["address"][ak] = str(val or "auto").strip()
            elif ak == "delivery_time":
                clean["address"][ak] = str(val or "none").strip()
            elif ak == "remarks":
                clean["address"][ak] = str(val or "")
            else:
                clean["address"][ak] = str(val or "").strip()

        return clean

    def get_config(self, mask_password: bool = True) -> Dict[str, Any]:
        with self._state_lock:
            cfg = copy.deepcopy(self.config)
        has_pwd = bool(cfg.get("password"))
        if mask_password:
            cfg["password"] = ""
        cfg["has_saved_password"] = has_pwd
        return cfg

    def update_config(
        self,
        updates: Dict[str, Any],
        root_config: Optional[Dict[str, Any]] = None,
        persist: bool = True
    ) -> Dict[str, Any]:
        """增量更新购买设置并持久化"""
        with self._state_lock:
            merged = copy.deepcopy(self.config)
            old_pwd = merged.get("password", "")
            for k, v in (updates or {}).items():
                if k == "address" and isinstance(v, dict):
                    merged["address"].update(v)
                elif k == "password":
                    # 空字符串不覆盖已保存密码，除非显式取消保存凭据
                    if v:
                        merged["password"] = str(v)
                elif k in merged:
                    merged[k] = v

            if "save_credentials" in (updates or {}) and not bool(updates["save_credentials"]):
                merged["password"] = ""
            elif not merged.get("password") and merged.get("save_credentials") and old_pwd:
                merged["password"] = old_pwd

            self.config = self._sanitize_purchase_config(merged)
            to_save = copy.deepcopy(self.config)
            if not to_save.get("save_credentials"):
                to_save["password"] = ""

        if root_config is not None:
            root_config["purchase"] = copy.deepcopy(to_save)

        if persist:
            try:
                disk_cfg = load_cfg_helper() if root_config is None else root_config
                disk_cfg["purchase"] = copy.deepcopy(to_save)
                save_cfg_helper(disk_cfg)
            except Exception as e:
                logger.warning(f"保存购买配置到文件失败: {e}")

        self._emit_status_event()
        return self.get_config(mask_password=True)

    def get_status_summary(self) -> Dict[str, Any]:
        """返回给 Web UI 与 SSE 广播的购买引擎实时状态"""
        default_pay = self.config.get("default_payment_method", "bank")
        pay_meta = PAYMENT_METHOD_MAP.get(default_pay, PAYMENT_METHOD_MAP["bank"])
        cookies_dict = self._get_cookies_dict()
        return {
            "is_logged_in": self.is_logged_in,
            "account_name": self.account_name if self.is_logged_in else "",
            "last_login_check": self.last_login_check,
            "last_login_message": self.last_login_message,
            "has_session_cookies": bool(cookies_dict.get("mandarake_sessionId") and cookies_dict.get("mandarake_userId")),
            "cookie_count": len(cookies_dict),
            "dry_run": bool(self.config.get("dry_run", True)),
            "auto_buy_enabled": bool(self.config.get("auto_buy_enabled", False)),
            "auto_buy_scope": self.config.get("auto_buy_scope", "rule_enabled"),
            "auto_buy_max_price": int(self.config.get("auto_buy_max_price", 0) or 0),
            "auto_buy_max_orders_per_day": int(self.config.get("auto_buy_max_orders_per_day", 5) or 5),
            "quick_buy_skip_confirm": bool(self.config.get("quick_buy_skip_confirm", False)),
            "default_payment_method": default_pay,
            "default_payment_label": pay_meta["label"],
            "fallback_payment_method": self.config.get("fallback_payment_method", "cod"),
            "use_custom_address": bool(self.config.get("use_custom_address", False)),
        }

    # --- Session / Cookie / 登录态管理 ---

    def _get_cookies_dict(self) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for cookie in self.session.cookies:
            result[cookie.name] = cookie.value
        return result

    def _has_mandarake_auth_cookies(self, cookies: Optional[Dict[str, str]] = None) -> bool:
        c = cookies if cookies is not None else self._get_cookies_dict()
        return bool(str(c.get("mandarake_sessionId") or "").strip() and str(c.get("mandarake_userId") or "").strip())

    def load_session(self) -> bool:
        """从本地 session.json 恢复 Cookie，并校验是否具备有效登录 Cookie"""
        if not os.path.exists(self.session_file):
            self.is_logged_in = False
            self.account_name = ""
            self.last_login_message = "尚未登录 Mandarake"
            return False
        try:
            with open(self.session_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            cookies = data.get("cookies", {})
            if isinstance(cookies, dict):
                for k, v in cookies.items():
                    if k and v is not None:
                        self.session.cookies.set(str(k), str(v), domain=".mandarake.co.jp", path="/")
            self.cookie_count = len(self._get_cookies_dict())
            if not self._has_mandarake_auth_cookies():
                self.is_logged_in = False
                self.account_name = ""
                self.last_login_message = "本地仅存在访客会话，请先登录 Mandarake 会员账号"
                return False
            self.account_name = str(data.get("account_name") or cookies.get("mandarake_userId") or "")
            # 启动阶段不强制阻塞外网请求；标明已载入鉴权 Cookie，支持在线复核
            res = self.check_login_status(verify_online=True)
            return bool(res.get("is_logged_in"))
        except Exception as e:
            logger.warning(f"加载 Session 缓存失败: {e}")
            self.is_logged_in = False
            return False

    def save_session(self):
        """原子持久化当前会话 Cookies"""
        try:
            sess_dir = os.path.dirname(os.path.abspath(self.session_file))
            os.makedirs(sess_dir, exist_ok=True)
            cookies_dict = self._get_cookies_dict()
            data = {
                "cookies": cookies_dict,
                "account_name": self.account_name,
                "updated_at": int(time.time()),
            }
            tmp_path = f"{self.session_file}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.session_file)
            self.cookie_count = len(cookies_dict)
            logger.info("Mandarake 会话 Cookie 已持久化保存")
        except Exception as e:
            logger.error(f"保存 Session 文件失败: {e}")

    def clear_session(self) -> Dict[str, Any]:
        """清空当前会话与登录状态"""
        self.session.cookies.clear()
        self.is_logged_in = False
        self.account_name = ""
        self.cookie_count = 0
        self.last_login_check = time.time()
        self.last_login_message = "已退出登录并清空本地会话"
        if os.path.exists(self.session_file):
            try:
                os.remove(self.session_file)
            except Exception:
                pass
        logger.info("已清空 Mandarake 登录凭证与会话状态")
        self._emit_status_event()
        return {"success": True, "message": "已退出 Mandarake 账号并清除会话 Cookie"}

    def check_login_status(self, verify_online: bool = True) -> Dict[str, Any]:
        """
        检验当前会话登录状态：
        1. 必须具备 mandarake_sessionId 与 mandarake_userId 两个核心 Cookie（与官方 mdk.logincheck.obf.js 一致）
        2. 在线验证 POST https://my.mandarake.co.jp/customer/ajax/my/ 与 GET mypageTop
        """
        cookies = self._get_cookies_dict()
        self.cookie_count = len(cookies)
        if not self._has_mandarake_auth_cookies(cookies):
            self.is_logged_in = False
            self.account_name = ""
            self.last_login_check = time.time()
            self.last_login_message = "缺少有效登录 Cookie (mandarake_sessionId / mandarake_userId)"
            return {
                "success": False,
                "is_logged_in": False,
                "account_name": "",
                "message": self.last_login_message,
            }

        if not verify_online:
            self.is_logged_in = True
            self.account_name = self.account_name or cookies.get("mandarake_userId") or self.config.get("username") or "Mandarake 会员"
            self.last_login_check = time.time()
            self.last_login_message = f"本地会话就绪: {self.account_name}"
            return {
                "success": True,
                "is_logged_in": True,
                "account_name": self.account_name,
                "message": self.last_login_message,
            }

        try:
            r_ajax = self.session.post(
                LOGIN_STATUS_AJAX_URL,
                data={"lang": "ja", "device": "0", "referer": "https://order.mandarake.co.jp/order/"},
                headers={
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                    "Origin": "https://order.mandarake.co.jp",
                    "Referer": "https://order.mandarake.co.jp/order/",
                },
                timeout=10,
            )
            ajax_text = r_ajax.text or ""
            if "customer/login" in ajax_text and "logout" not in ajax_text and "mypageTop" not in ajax_text:
                self.is_logged_in = False
                self.account_name = ""
                self.last_login_check = time.time()
                self.last_login_message = "会话已过期或无效，请重新登录或导入最新 Cookie"
                return {
                    "success": False,
                    "is_logged_in": False,
                    "account_name": "",
                    "message": self.last_login_message,
                }

            # 进一步探测 mypageTop 是否发生 302 踢回主页
            r_my = self.session.get(MYPAGE_TOP_URL, timeout=10, allow_redirects=False)
            loc = r_my.headers.get("Location", "")
            if r_my.status_code in (301, 302, 303, 307) and (
                "customer/login" in loc or loc.rstrip("/") == "https://www.mandarake.co.jp"
            ):
                self.is_logged_in = False
                self.account_name = ""
                self.last_login_check = time.time()
                self.last_login_message = "MyPage 验证未通过（会话已失效），请重新登录"
                return {
                    "success": False,
                    "is_logged_in": False,
                    "account_name": "",
                    "message": self.last_login_message,
                }

            # 解析会员昵称/账号名
            extracted_name = ""
            if r_my.status_code == 200 and r_my.text:
                soup = BeautifulSoup(r_my.text, "html.parser")
                el = soup.select_one(".user_name, .member_name, .mypage_user, .customer_name, .account_name")
                if el:
                    extracted_name = el.get_text(strip=True)
            if not extracted_name and ajax_text:
                m_user = re.search(r"([A-Za-z0-9_.\-@]+)\s*様", ajax_text)
                if m_user:
                    extracted_name = m_user.group(1)

            self.account_name = (
                extracted_name
                or self.config.get("username")
                or cookies.get("mandarake_userId")
                or "Mandarake 会员"
            )
            self.is_logged_in = True
            self.last_login_check = time.time()
            self.last_login_message = f"已登录 Mandarake: {self.account_name}"
            return {
                "success": True,
                "is_logged_in": True,
                "account_name": self.account_name,
                "message": self.last_login_message,
            }
        except Exception as e:
            logger.warning(f"在线校验 Mandarake 登录状态异常: {e}")
            self.last_login_check = time.time()
            self.last_login_message = f"在线验活超时/网络异常 ({e})"
            return {
                "success": self.is_logged_in,
                "is_logged_in": self.is_logged_in,
                "account_name": self.account_name,
                "message": self.last_login_message,
            }

    def login_with_credentials(
        self,
        username: str,
        password: str,
        save_credentials: Optional[bool] = None,
        root_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        从 Web 面板使用 Mandarake 账号与密码直接登录。
        注意：Mandarake 登录页挂载了 Google reCAPTCHA v3；我们遵守合规边界不绕过验证，
        直接按标准表单提交流程尝试，若官方拦截则会明确提示使用「浏览器登录 + Cookie 导入」。
        """
        uid = str(username or "").strip()
        pwd = str(password or "")
        if not uid:
            uid = self.config.get("username", "").strip()
        if not pwd:
            pwd = self.config.get("password", "")

        if not uid or not pwd:
            return {"success": False, "message": "请输入 Mandarake 账号（ID/邮箱）与密码"}

        save_flag = self.config.get("save_credentials", False) if save_credentials is None else bool(save_credentials)
        self.update_config(
            {
                "username": uid,
                "save_credentials": save_flag,
                "password": pwd if save_flag else "",
            },
            root_config=root_config,
        )

        try:
            # 1. 获取登录页初始化会话与隐藏字段
            r_get = self.session.get(LOGIN_PAGE_URL, timeout=12)
            if r_get.status_code != 200:
                return {"success": False, "message": f"无法访问官网登录页 (HTTP {r_get.status_code})"}

            soup = BeautifulSoup(r_get.text, "html.parser")
            form = soup.select_one("form[name='login'], form[action*='login'], form")
            action_url = LOGIN_PAGE_URL
            payload: Dict[str, str] = {}
            if form:
                if form.get("action"):
                    action_url = urljoin(LOGIN_PAGE_URL, form["action"])
                for inp in form.find_all("input"):
                    n = inp.get("name")
                    if n:
                        payload[n] = inp.get("value", "")

            payload["loginId"] = uid
            payload["passwd"] = pwd
            payload.setdefault("doLogin", "")

            headers = {
                "User-Agent": DEFAULT_USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://my.mandarake.co.jp",
                "Referer": LOGIN_PAGE_URL,
            }
            r_post = self.session.post(action_url, data=payload, headers=headers, timeout=12, allow_redirects=True)

            # 2. 检查是否签发有效登录 Cookie 并验证在线状态
            if self._has_mandarake_auth_cookies():
                verify = self.check_login_status(verify_online=True)
                if verify.get("is_logged_in"):
                    self.account_name = verify.get("account_name") or uid
                    self.save_session()
                    self._emit_status_event()
                    logger.info(f"✅ Mandarake 账号登录成功: {self.account_name}")
                    return {
                        "success": True,
                        "is_logged_in": True,
                        "account_name": self.account_name,
                        "message": f"登录成功！当前账号: {self.account_name}",
                    }

            # 3. 解析页面错误提示或 reCAPTCHA 拦截提示
            err_msg = ""
            if r_post.text:
                err_soup = BeautifulSoup(r_post.text, "html.parser")
                err_el = err_soup.select_one(".error, .err, .alert, .message, p.error_msg")
                if err_el:
                    err_msg = err_el.get_text(" ", strip=True)

            if "recaptcha" in (r_post.text or "").lower() or "robot" in err_msg.lower():
                hint = (
                    "官网登录页启用了 Google reCAPTCHA v3 人机风控，服务端直连登录被官方拒绝。"
                    "请点击右侧「浏览器登录」在官方页面完成登录后，复制 Cookie 粘贴导入即可永久保持会话！"
                )
            elif err_msg:
                hint = f"官网返回提示: {err_msg}。若确认账密无误仍无法直登，请使用「浏览器登录 + Cookie 导入」。"
            else:
                hint = (
                    "未能通过直连表单获得官网登录 Cookie（通常因 reCAPTCHA 人机校验触发）。"
                    "请使用旁边的「浏览器登录 + Cookie 导入」完成授权。"
                )

            self.is_logged_in = False
            self.last_login_message = hint
            self._emit_status_event()
            return {
                "success": False,
                "is_logged_in": False,
                "requires_browser_login": True,
                "login_url": LOGIN_PAGE_URL,
                "message": hint,
            }
        except Exception as e:
            msg = f"账号登录请求异常: {e}"
            logger.error(msg)
            return {"success": False, "is_logged_in": False, "message": msg}

    @staticmethod
    def parse_cookie_input(
        cookie_input: str = "",
        session_id: str = "",
        user_id: str = "",
    ) -> Dict[str, str]:
        """
        多格式解析 Cookie：
        - 浏览器 document.cookie 字符串 (`k1=v1; k2=v2`)
        - HTTP 请求头 (`Cookie: k1=v1; k2=v2`)
        - JSON 字典 (`{"mandarake_sessionId": "...", "mandarake_userId": "..."}`)
        - EditThisCookie / Cookie-Editor JSON 数组 (`[{"name": "...", "value": "..."}]`)
        - 显式传入的 session_id / user_id
        """
        parsed: Dict[str, str] = {}
        raw = str(cookie_input or "").strip()

        if raw:
            # 尝试 JSON 解析
            if raw.startswith("{") or raw.startswith("["):
                try:
                    obj = json.loads(raw)
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if k and v is not None:
                                parsed[str(k).strip()] = str(v).strip()
                    elif isinstance(obj, list):
                        for item in obj:
                            if isinstance(item, dict) and item.get("name"):
                                parsed[str(item["name"]).strip()] = str(item.get("value", "")).strip()
                except Exception:
                    pass

            # 文本 Cookie 串 / Header 解析
            if not parsed:
                cleaned = raw
                if cleaned.lower().startswith("cookie:"):
                    cleaned = cleaned.split(":", 1)[1].strip()
                # 支持分号或换行分隔
                for part in re.split(r"[;\r\n]+", cleaned):
                    part = part.strip()
                    if "=" in part:
                        k, v = part.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"')
                        if k:
                            parsed[k] = v

        if session_id and str(session_id).strip():
            parsed["mandarake_sessionId"] = str(session_id).strip()
        if user_id and str(user_id).strip():
            parsed["mandarake_userId"] = str(user_id).strip()

        return parsed

    def import_cookies(
        self,
        cookie_input: str = "",
        session_id: str = "",
        user_id: str = "",
        verify_online: bool = True,
    ) -> Dict[str, Any]:
        """导入并验证 Mandarake 会话 Cookie"""
        parsed = self.parse_cookie_input(cookie_input=cookie_input, session_id=session_id, user_id=user_id)
        if not parsed:
            return {"success": False, "message": "未解析到有效的 Cookie 键值对，请检查输入格式"}

        if not self._has_mandarake_auth_cookies(parsed):
            return {
                "success": False,
                "message": (
                    "Cookie 中缺少登录态关键字段 `mandarake_sessionId` 或 `mandarake_userId`。"
                    "请确保已在浏览器中登录 Mandarake 后再复制 Cookie 或直接填写这两项！"
                ),
            }

        # 备份旧 Cookie 以便验活失败时回滚
        prev_cookies = self._get_cookies_dict()
        prev_account = self.account_name
        prev_logged_in = self.is_logged_in

        for k, v in parsed.items():
            self.session.cookies.set(k, v, domain=".mandarake.co.jp", path="/")

        verify = self.check_login_status(verify_online=verify_online)
        if verify.get("is_logged_in"):
            self.save_session()
            self._emit_status_event()
            logger.info(f"✅ Cookie 导入并通过验证，当前账号: {self.account_name}")
            return {
                "success": True,
                "is_logged_in": True,
                "account_name": self.account_name,
                "message": f"Cookie 导入成功并已通过官网验证！账号: {self.account_name}",
            }

        # 回滚
        self.session.cookies.clear()
        for k, v in prev_cookies.items():
            self.session.cookies.set(k, v, domain=".mandarake.co.jp", path="/")
        self.account_name = prev_account
        self.is_logged_in = prev_logged_in
        self._emit_status_event()
        return {
            "success": False,
            "is_logged_in": prev_logged_in,
            "message": verify.get("message") or "导入的 Cookie 验证未通过，请确认在官网处于有效登录状态后重新复制",
        }

    def launch_browser_login(self) -> Dict[str, Any]:
        """打开 Mandarake 官方登录页面以便用户登录后复制 Cookie"""
        if os.environ.get("IN_DOCKER") or sys.platform.startswith("linux"):
            return {
                "success": False,
                "login_url": LOGIN_PAGE_URL,
                "message": "当前运行在服务端/Docker 无头环境中，已为您准备官网登录地址：请在当前浏览器新标签页登录 Mandarake 后复制 Cookie 导入！",
            }

        try:
            chrome_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            ]
            browser_exe = next((p for p in chrome_paths if os.path.exists(p)), None)
            if browser_exe:
                profile_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "browser_profile")
                os.makedirs(profile_dir, exist_ok=True)
                subprocess.Popen(
                    [
                        browser_exe,
                        f"--user-data-dir={profile_dir}",
                        "--no-first-run",
                        "--no-default-browser-check",
                        LOGIN_PAGE_URL,
                    ]
                )
            else:
                webbrowser.open(LOGIN_PAGE_URL)
            return {
                "success": True,
                "login_url": LOGIN_PAGE_URL,
                "message": "已唤起浏览器打开 Mandarake 登录页！完成登录后按 F12 在 Console 执行 copy(document.cookie) 并粘贴到下方导入即可。",
            }
        except Exception as e:
            return {
                "success": False,
                "login_url": LOGIN_PAGE_URL,
                "message": f"唤起本地浏览器失败 ({e})，请手动打开官网登录页登录后导入 Cookie。",
            }

    # --- 购物车操作 (对齐 tools.mandarake.co.jp/basket/ API) ---

    def add_to_cart(self, item_id: str, count: int = 1) -> Tuple[bool, str, Optional[str]]:
        """将指定商品加入 Mandarake 购物车，返回 (是否成功, 提示信息, 结算入口URL)"""
        item_id = str(item_id).strip()
        detail_ref = f"https://order.mandarake.co.jp/order/detailPage/item?itemCode={item_id}&lang=ja"
        payload = {
            "request[id]": item_id,
            "request[count]": str(max(1, int(count))),
            "request[shopType]": "webshop",
            "request[langage]": "ja",
            "request[countryId]": "JP",
            "request[location]": f"/order/detailPage/item?itemCode={item_id}&lang=ja",
            "request[referer]": detail_ref,
        }
        headers = dict(DEFAULT_HEADERS)
        headers["Referer"] = detail_ref
        headers["Origin"] = "https://order.mandarake.co.jp"

        try:
            resp = self.session.post(BASKET_ADD_URL, data=payload, headers=headers, timeout=10)
            if resp.status_code != 200:
                msg = f"加购接口响应异常 (HTTP {resp.status_code})"
                logger.warning(msg)
                return False, msg, None

            html = resp.text or ""
            soup = BeautifulSoup(html, "html.parser")
            err_msgs = [p.get_text(" ", strip=True) for p in soup.select(".basket_message p, .error, .err") if p.get_text(strip=True)]
            joined_err = " | ".join(err_msgs)

            # 检查是否返回明确失败文案（库存不足/已售罄/购买上限等）
            fail_keywords = ["売り切れ", "在庫", "品切", "カートに入れることができません", "エラー", "購入できません", "上限"]
            if any(k in joined_err for k in fail_keywords) and f"item_webshop_{item_id}" not in html and item_id not in html:
                return False, f"加购被官网拒绝: {joined_err}", None

            # 尝试优先从当前加购商品所属店铺容器 (.block.shop) 提取对应的结算入口链接
            checkout_url = None
            item_box = soup.find(id=re.compile(rf"item_[^_]+_{re.escape(item_id)}"))
            if item_box is not None:
                shop_block = item_box.find_parent(class_=lambda c: c and "shop" in c.split())
                if shop_block is not None:
                    shop_link = shop_block.select_one("a[href*='inputOrder']")
                    if shop_link and shop_link.get("href"):
                        checkout_url = urljoin(CART_INPUT_URL, shop_link["href"])
            if not checkout_url:
                for a in soup.select("a[href*='inputOrder']"):
                    href = a.get("href") or ""
                    if href:
                        checkout_url = urljoin(CART_INPUT_URL, href)
                        break

            logger.info(f"🛒 商品 {item_id} 已加入 Mandarake 购物车")
            return True, joined_err or "加购成功", checkout_url
        except Exception as e:
            msg = f"加购网络请求异常: {e}"
            logger.error(msg)
            return False, msg, None

    def delete_from_cart(self, item_id: str) -> bool:
        """从购物车中移除指定商品"""
        item_id = str(item_id).strip()
        detail_ref = f"https://order.mandarake.co.jp/order/detailPage/item?itemCode={item_id}&lang=ja"
        payload = {
            "request[id]": item_id,
            "request[shopType]": "webshop",
            "request[langage]": "ja",
            "request[countryId]": "JP",
            "request[inCart]": "0",
            "request[referer]": detail_ref,
        }
        headers = dict(DEFAULT_HEADERS)
        headers["Referer"] = detail_ref
        try:
            resp = self.session.post(BASKET_DEL_URL, data=payload, headers=headers, timeout=10)
            return resp.status_code == 200
        except Exception:
            return False

    def get_cart_items(self) -> Dict[str, Any]:
        """查询当前 Mandarake 官网购物车内容"""
        payload = {
            "request[langage]": "ja",
            "request[countryId]": "JP",
            "request[referer]": "https://order.mandarake.co.jp/order/",
        }
        try:
            resp = self.session.post(BASKET_GET_URL, data=payload, headers=DEFAULT_HEADERS, timeout=10)
            if resp.status_code != 200:
                return {"success": False, "items": [], "count": 0, "message": f"获取购物车失败 (HTTP {resp.status_code})"}
            soup = BeautifulSoup(resp.text or "", "html.parser")
            items = []
            for box in soup.select(".box.item, [id^='item_webshop_']"):
                box_id = box.get("id", "")
                m = re.search(r"item_[^_]+_(\d+)", box_id)
                pid = m.group(1) if m else box_id
                title_el = box.select_one(".item_name, .title, a")
                price_el = box.select_one(".price, .item_price")
                items.append({
                    "id": pid,
                    "title": title_el.get_text(" ", strip=True) if title_el else pid,
                    "price_str": price_el.get_text(" ", strip=True) if price_el else "",
                    "shop": box.get("data-shop", ""),
                })
            return {"success": True, "items": items, "count": len(items), "message": f"当前购物车共 {len(items)} 件商品"}
        except Exception as e:
            return {"success": False, "items": [], "count": 0, "message": f"读取购物车异常: {e}"}

    def clear_cart(self) -> Dict[str, Any]:
        """清空购物车中所有商品"""
        res = self.get_cart_items()
        if not res.get("success"):
            return res
        removed = 0
        for it in res.get("items", []):
            if self.delete_from_cart(it["id"]):
                removed += 1
        return {"success": True, "removed": removed, "message": f"已从官网购物车移除 {removed} 件商品"}

    # --- Teeda / 结算表单智能解析与填充 ---

    @staticmethod
    def _element_context_text(el) -> str:
        """收集表单控件及其关联 label / 所在表格行/容器的可读文本，用于稳健字段识别"""
        chunks: List[str] = []
        el_id = el.get("id")
        if el_id:
            root = el
            while root.parent is not None:
                root = root.parent
            lbl = root.find("label", attrs={"for": el_id})
            if lbl:
                chunks.append(lbl.get_text(" ", strip=True))
        for p in el.parents:
            if p.name in ("label", "td", "th", "dd", "li", "tr", "div"):
                txt = p.get_text(" ", strip=True)
                if txt:
                    chunks.append(txt[:240])
                if p.name in ("tr", "li"):
                    break
        return " ".join(chunks)

    @staticmethod
    def _direct_option_text(el) -> str:
        """提取单个 radio 或 checkbox 的直属选项标签文本（避免串入整张表单文字）"""
        el_id = el.get("id")
        if el_id:
            root = el
            while root.parent is not None:
                root = root.parent
            lbl = root.find("label", attrs={"for": el_id})
            if lbl:
                return lbl.get_text(" ", strip=True)
        for p in el.parents:
            if p.name in ("label", "li", "dd", "div", "td"):
                txt = p.get_text(" ", strip=True)
                if txt and len(txt) <= 180:
                    return txt
                if p.name == "td":
                    break
        # 回退检查后续兄弟文本节点
        sib_texts = []
        for sib in el.next_siblings:
            if isinstance(sib, str):
                s = sib.strip()
                if s:
                    sib_texts.append(s)
            elif getattr(sib, "name", None) in ("label", "span", "strong", "b"):
                sib_texts.append(sib.get_text(" ", strip=True))
                break
            elif getattr(sib, "name", None) in ("input", "br"):
                if sib_texts:
                    break
        return " ".join(sib_texts).strip()

    @staticmethod
    def _find_checkout_form(soup: BeautifulSoup):
        forms = soup.find_all("form")
        if not forms:
            return None
        for selector in [
            "form#orderForm",
            "form[name='orderForm']",
            "form#cartForm",
            "form[name='cartForm']",
            "form[action*='confirmOrder']",
            "form[action*='inputPaymentMethod']",
            "form[action*='inputReceiver']",
            "form[action*='inputOrder']",
            "form[action*='confirm']",
        ]:
            f = soup.select_one(selector)
            if f is not None:
                return f
        # 选择输入控件最多的表单（排除仅搜索/登录小表单）
        return max(forms, key=lambda f: len(f.find_all(["input", "select", "textarea"])))

    @staticmethod
    def _extract_teeda_hidden_fields(html: str) -> Dict[str, str]:
        """提取 Seasar2 / Teeda 通过内联 <script> 动态注入的隐藏字段（如 te-conditions）"""
        fields: Dict[str, str] = {}
        if not html:
            return fields
        for m in re.finditer(
            r"<input\b[^>]*name\s*=\s*['\"](te-conditions)['\"][^>]*value\s*=\s*['\"]([^'\"]*)['\"]",
            html,
            re.I,
        ):
            fields[m.group(1)] = unescape(m.group(2))
        if "te-conditions" not in fields:
            m2 = re.search(
                r"<input\b[^>]*value\s*=\s*['\"]([^'\"]*)['\"][^>]*name\s*=\s*['\"](te-conditions)['\"]",
                html,
                re.I,
            )
            if m2:
                fields[m2.group(2)] = unescape(m2.group(1))
        return fields

    @classmethod
    def _extract_form_defaults(cls, form, html: str) -> Dict[str, str]:
        """提取表单当前默认字段值，并合并 Teeda 内联脚本注入的隐藏字段"""
        form_data: Dict[str, str] = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            itype = (inp.get("type") or "text").lower()
            if itype in ("submit", "button", "image", "reset", "file"):
                continue
            if itype == "radio":
                if inp.has_attr("checked"):
                    form_data[name] = inp.get("value", "")
            elif itype == "checkbox":
                if inp.has_attr("checked"):
                    form_data[name] = inp.get("value") or "on"
            else:
                form_data[name] = inp.get("value", "")

        for sel in form.find_all("select"):
            name = sel.get("name")
            if not name:
                continue
            selected_opt = sel.find("option", selected=True)
            if selected_opt is not None:
                form_data[name] = selected_opt.get("value", "")
            else:
                first_opt = sel.find("option")
                form_data[name] = first_opt.get("value", "") if first_opt else ""

        for ta in form.find_all("textarea"):
            name = ta.get("name")
            if name:
                form_data[name] = ta.get_text() or ""

        for k, v in cls._extract_teeda_hidden_fields(html).items():
            form_data.setdefault(k, v)
        return form_data

    @staticmethod
    def _extract_page_errors(soup: BeautifulSoup) -> List[str]:
        err_texts: List[str] = []
        seen = set()
        for el in soup.select(".error, .err, .errorMessage, .err_msg, ul.error li, span.error, #messages .error, #messages li"):
            txt = el.get_text(" ", strip=True)
            if txt and txt not in seen:
                seen.add(txt)
                err_texts.append(txt)
        return err_texts

    @staticmethod
    def _detect_real_sold_out(soup: BeautifulSoup, page_text: str, err_texts: List[str]) -> bool:
        """
        精准识别官网页面是否真的返回了商品售罄/无货错误。
        注意排除页头按钮「売り切れ商品を削除」以及条款中的「売り切れとなる場合」「在庫確認します」等常驻说明文案。
        """
        sold_tokens = ("売り切れ", "在庫がございません", "在庫がありません", "在庫切れ", "品切れ", "在庫不足")
        for err in err_texts:
            if any(tok in err for tok in sold_tokens):
                return True

        cleaned = page_text or ""
        for benign in (
            "売り切れ商品を削除",
            "売り切れとなる場合",
            "売り切れの場合",
            "売り切れの際",
            "在庫切れとなる場合",
            "在庫切れの場合",
            "在庫確認します",
            "店頭共通",
        ):
            cleaned = cleaned.replace(benign, " ")

        explicit_sold_phrases = (
            "売り切れました",
            "売り切れのため",
            "売り切れです",
            "在庫がございません",
            "在庫がありません",
            "在庫切れのため",
            "在庫切れです",
            "品切れです",
            "品切れのため",
        )
        if any(phrase in cleaned for phrase in explicit_sold_phrases):
            return True

        for el in soup.select("form .soldout, form .sold_out, .error .soldout, .cart_soldout"):
            if el.get_text(strip=True):
                return True
        return False

    @classmethod
    def _match_payment_choice(
        cls,
        choices: List[Dict[str, Any]],
        payment_code: str,
    ) -> Optional[Dict[str, Any]]:
        meta = PAYMENT_METHOD_MAP.get(payment_code)
        if not meta:
            return None

        def _is_usable(ch: Dict[str, Any]) -> bool:
            if ch.get("disabled"):
                return False
            ch_val = str(ch.get("value", "")).strip()
            if ch_val in ("016", "019"):  # Paidy / atone 实盘固定码
                return False
            haystack = f"{ch.get('text', '')} {ch_val} {ch.get('id', '')}".lower()
            if any(bad in haystack for bad in ("利用できません", "選択できません", "利用不可", "paidy", "ペイディ", "atone", "amazon pay")):
                return False
            return True

        usable = [ch for ch in choices if _is_usable(ch)]

        # 第一优先级：精确匹配官网单选框固定值（例如銀行振込=011, 代金引換=008, 信用卡=015, PayPal=005, 店頭=006）
        exact_vals = [str(v).strip() for v in meta.get("exact_values", []) if str(v).strip()]
        if exact_vals:
            for ch in usable:
                if str(ch.get("value", "")).strip() in exact_vals:
                    return ch

        # 第二优先级：关键词兜底匹配（防止 PayPal 误匹配到 クレジットカード/PayPalCredit）
        keywords = [kw.lower() for kw in meta.get("match_keywords", [])]
        for ch in usable:
            text_and_id = f"{ch.get('text', '')} {ch.get('id', '')}".lower()
            if payment_code == "paypal" and ("credit" in text_and_id or "クレジット" in text_and_id):
                continue
            haystack = f"{text_and_id} {ch.get('value', '')}".lower()
            for kw in keywords:
                if kw in haystack:
                    return ch
        return None

    def parse_cart_confirmation_form(self, html: str, base_url: str, item_id: str) -> Dict[str, Any]:
        """解析 Mandarake 结算第 1 步页面 (inputOrderJa.html - カート内容確認)"""
        soup = BeautifulSoup(html or "", "html.parser")
        page_text = soup.get_text(" ", strip=True)
        if "カートに商品がありません" in page_text or "買い物かごに商品が入っておりません" in page_text:
            return {"ok": False, "error": "购物车为空，可能商品在加购前已被他人买走"}

        err_texts = self._extract_page_errors(soup)
        if self._detect_real_sold_out(soup, page_text, err_texts):
            return {"ok": False, "sold_out": True, "error": "商品在购物车确认页显示已售罄（売り切れ）"}
        if err_texts:
            return {"ok": False, "error": f"购物车确认页提示错误: {' | '.join(err_texts[:3])}"}

        form = self._find_checkout_form(soup)
        if not form:
            return {"ok": False, "error": "未在购物车确认页定位到有效表单 (cartForm)"}

        form_data = self._extract_form_defaults(form, html)
        target_id = str(item_id or "").strip()
        shop_idx = None
        for k, v in form_data.items():
            m_it = re.match(r"cartForm:shopCartGoodsItems:(\d+):inCartGoodsItems:\d+:id-x$", k)
            if m_it and str(v).strip() == target_id:
                shop_idx = m_it.group(1)
                break
        if shop_idx is None:
            # 单门店场景兜底取索引 0
            if any(re.match(r"cartForm:shopCartGoodsItems:0:", k) for k in form_data):
                shop_idx = "0"
            else:
                return {"ok": False, "error": f"购物车确认页中未找到目标商品 {target_id}"}

        form_data["cartForm:clickShopId"] = form_data.get(f"cartForm:shopCartGoodsItems:{shop_idx}:shopId", "")
        form_data["cartForm:clickBackOrder"] = form_data.get(f"cartForm:shopCartGoodsItems:{shop_idx}:backOrder", "false")
        form_data["cartForm:clickIndex"] = form_data.get(f"cartForm:shopCartGoodsItems:{shop_idx}:inCartGoodsIndex", shop_idx)
        form_data["cartForm:doInputCustomer"] = ""

        return {
            "ok": True,
            "action_url": urljoin(base_url, form.get("action") or base_url),
            "form_data": form_data,
            "shop_id": form_data["cartForm:clickShopId"],
            "error": "",
        }

    def parse_and_fill_receiver_form(
        self,
        html: str,
        base_url: str,
        address_cfg: Optional[Dict[str, str]] = None,
        use_custom_address: bool = False,
        payment_method: str = "bank",
    ) -> Dict[str, Any]:
        """解析并填充 Mandarake 结算第 2 步页面 (inputReceiverJa.html - お届け先入力)"""
        soup = BeautifulSoup(html or "", "html.parser")
        page_text = soup.get_text(" ", strip=True)
        err_texts = self._extract_page_errors(soup)
        if self._detect_real_sold_out(soup, page_text, err_texts):
            return {"ok": False, "sold_out": True, "error": "商品在收货地址页阶段已被抢空（売り切れ）"}
        if err_texts:
            return {"ok": False, "error": f"收货地址页校验失败: {' | '.join(err_texts[:3])}"}

        form = self._find_checkout_form(soup)
        if not form:
            return {"ok": False, "error": "未在收货地址页定位到有效表单"}

        form_data = self._extract_form_defaults(form, html)
        action_url = urljoin(base_url, form.get("action") or base_url)
        has_read_member_btn = form.find("input", attrs={"name": "cartForm:doReadMenberInfo"}) is not None
        needs_read_member = bool(has_read_member_btn and not str(form_data.get("cartForm:inputLastName") or "").strip())

        if needs_read_member:
            read_member_data = dict(form_data)
            read_member_data["cartForm:doReadMenberInfo"] = ""
            return {
                "ok": True,
                "needs_read_member": True,
                "action_url": action_url,
                "read_member_data": read_member_data,
                "form_data": form_data,
                "applied_address_fields": [],
                "error": "",
            }

        addr = address_cfg or {}
        if payment_method == "store" or "店頭" in str(addr.get("delivery_method") or ""):
            form_data["cartForm:radioReceiveMethod"] = "_store"
        elif not form_data.get("cartForm:radioReceiveMethod"):
            form_data["cartForm:radioReceiveMethod"] = "_home"

        applied_fields: List[str] = []
        if use_custom_address and addr:
            last_name = str(addr.get("last_name") or "").strip()
            first_name = str(addr.get("first_name") or "").strip()
            last_kana = str(addr.get("last_name_kana") or "").strip()
            first_kana = str(addr.get("first_name_kana") or "").strip()
            postal = str(addr.get("postal_code") or "").strip()
            pref = str(addr.get("prefecture") or "").strip()
            addr1 = str(addr.get("address1") or "").strip()
            addr2 = str(addr.get("address2") or "").strip()
            phone = str(addr.get("phone") or "").strip()

            if last_name:
                form_data["cartForm:inputLastName"] = last_name
                applied_fields.append("name")
            if first_name:
                form_data["cartForm:inputFirstName"] = first_name
                if "name" not in applied_fields:
                    applied_fields.append("name")
            if last_kana:
                form_data["cartForm:inputReadLastName"] = last_kana
                applied_fields.append("kana")
            if first_kana:
                form_data["cartForm:inputReadFirstName"] = first_kana
                if "kana" not in applied_fields:
                    applied_fields.append("kana")
            if postal:
                z1, z2 = _split_postal_code(postal)
                form_data["cartForm:inputZipCode"] = f"{z1}-{z2}" if (z1 and z2) else postal
                applied_fields.append("postal_code")
            if pref:
                form_data["cartForm:inputPref"] = pref
                applied_fields.append("prefecture")
            if addr1:
                if "cartForm:inputAddress3" in form_data:
                    m_split = re.match(r"^(.+?[都道府県])?(.*?(?:[^市区町村\d]+[市郡])?[^区町村\d]+[区町村])(.+)$", addr1)
                    if m_split and m_split.group(3).strip():
                        city_part = f"{m_split.group(1) or ''}{m_split.group(2) or ''}".strip()
                        form_data["cartForm:inputAddress1"] = city_part
                        form_data["cartForm:inputAddress2"] = m_split.group(3).strip()[:25]
                    else:
                        form_data["cartForm:inputAddress2"] = addr1[:25]
                        if not form_data.get("cartForm:inputAddress1"):
                            form_data["cartForm:inputAddress1"] = addr1
                else:
                    form_data["cartForm:inputAddress1"] = addr1
                applied_fields.append("address1")
            if addr2:
                if "cartForm:inputAddress3" in form_data:
                    form_data["cartForm:inputAddress3"] = addr2[:25]
                else:
                    form_data["cartForm:inputAddress2"] = addr2[:25]
                applied_fields.append("address2")
            if phone:
                p1, p2, p3 = _split_phone(phone)
                if p1:
                    form_data["cartForm:inputPhone11"] = p1
                if p2:
                    form_data["cartForm:inputPhone12"] = p2
                if p3:
                    form_data["cartForm:inputPhone13"] = p3
                applied_fields.append("phone")

        # 同步官网前端 JS onClickInputReceiver()：合并 inputPhone11-12-13 写入 inputPhone1
        p11 = str(form_data.get("cartForm:inputPhone11") or "").strip()
        p12 = str(form_data.get("cartForm:inputPhone12") or "").strip()
        p13 = str(form_data.get("cartForm:inputPhone13") or "").strip()
        if "cartForm:inputPhone1" in form_data and (p11 or p12 or p13):
            form_data["cartForm:inputPhone1"] = "-".join([p11, p12, p13])

        form_data["cartForm:doInputReceiver"] = ""
        return {
            "ok": True,
            "needs_read_member": False,
            "action_url": action_url,
            "form_data": form_data,
            "applied_address_fields": sorted(set(applied_fields)),
            "error": "",
        }

    @staticmethod
    def _match_prefecture_option(select_el, pref_value: str) -> Optional[str]:
        if not pref_value:
            return None
        target = pref_value.strip()
        target_short = re.sub(r"[都道府県]$", "", target)
        jis_idx = None
        for idx, p in enumerate(JP_PREFECTURES, 1):
            if p == target or re.sub(r"[都道府県]$", "", p) == target_short:
                jis_idx = idx
                break

        for opt in select_el.find_all("option"):
            val = opt.get("value", "")
            txt = opt.get_text(" ", strip=True)
            if not val and not txt:
                continue
            if txt == target or txt.replace(" ", "") == target or re.sub(r"[都道府県]$", "", txt) == target_short:
                return val if val != "" else txt
            if jis_idx is not None and val in (str(jis_idx), f"{jis_idx:02d}"):
                return val
        return None

    def parse_and_fill_input_form(
        self,
        html: str,
        base_url: str,
        payment_method: str,
        fallback_payment_method: str = "cod",
        address_cfg: Optional[Dict[str, str]] = None,
        use_custom_address: bool = False,
    ) -> Dict[str, Any]:
        """解析 inputOrderJa.html 表单，智能选择免认证支付方式、配送信息与收货地址"""
        soup = BeautifulSoup(html or "", "html.parser")
        form = self._find_checkout_form(soup)
        if not form:
            return {"ok": False, "error": "未在结算页面中定位到有效订单表单"}

        page_text = soup.get_text(" ", strip=True)
        if "カートに商品がありません" in page_text or "買い物かごに商品が入っておりません" in page_text:
            return {"ok": False, "error": "购物车为空，可能商品在加购前已被他人买走"}

        action_url = urljoin(base_url, form.get("action") or base_url)
        form_data: Dict[str, str] = {}

        # 1. 收集默认已有控件值（保留 Teeda 状态隐藏字段、默认选中的 radio/checkbox/select）
        radio_groups: Dict[str, List[Dict[str, Any]]] = {}
        all_selects = form.find_all("select")
        all_inputs = form.find_all("input")
        all_textareas = form.find_all("textarea")

        for inp in all_inputs:
            name = inp.get("name")
            if not name:
                continue
            itype = (inp.get("type") or "text").lower()
            if itype in ("submit", "button", "image", "reset", "file"):
                continue
            if itype == "radio":
                disabled = inp.has_attr("disabled")
                opt_text = self._direct_option_text(inp)
                radio_groups.setdefault(name, []).append({
                    "name": name,
                    "id": inp.get("id", ""),
                    "value": inp.get("value", ""),
                    "text": opt_text,
                    "context": self._element_context_text(inp),
                    "disabled": disabled,
                    "checked": inp.has_attr("checked"),
                })
                if inp.has_attr("checked"):
                    form_data[name] = inp.get("value", "")
            elif itype == "checkbox":
                if inp.has_attr("checked"):
                    form_data[name] = inp.get("value") or "on"
            else:
                form_data[name] = inp.get("value", "")

        for sel in all_selects:
            name = sel.get("name")
            if not name:
                continue
            selected_opt = sel.find("option", selected=True)
            if selected_opt is not None:
                form_data[name] = selected_opt.get("value", "")
            else:
                first_opt = sel.find("option")
                form_data[name] = first_opt.get("value", "") if first_opt else ""

        for ta in all_textareas:
            name = ta.get("name")
            if name:
                form_data[name] = ta.get_text() or ""

        for k, v in self._extract_teeda_hidden_fields(html).items():
            form_data.setdefault(k, v)

        # 2. 识别并选择支付方式（优先主选，不可用时按备选降级）
        payment_radio_group = None
        for rname, choices in radio_groups.items():
            joined_ctx = " ".join(f"{c['text']} {c['context']} {rname}" for c in choices).lower()
            if any(k in joined_ctx for k in ["お支払", "支払方法", "決済方法", "payment", "kessai", "pay", "銀行振込", "代金引換"]):
                if len(choices) >= 1:
                    payment_radio_group = (rname, choices)
                    break

        payment_select = None
        if payment_radio_group is None:
            for sel in all_selects:
                sname = sel.get("name") or ""
                sctx = self._element_context_text(sel).lower()
                opt_txt = " ".join(o.get_text(" ", strip=True) for o in sel.find_all("option")).lower()
                if any(k in f"{sname.lower()} {sctx} {opt_txt}" for k in ["お支払", "支払方法", "決済", "payment", "kessai", "銀行振込", "代金引換"]):
                    choices = [
                        {
                            "name": sname,
                            "value": o.get("value", ""),
                            "text": o.get_text(" ", strip=True),
                            "disabled": o.has_attr("disabled"),
                        }
                        for o in sel.find_all("option")
                        if o.get("value", "") != "" or o.get_text(strip=True)
                    ]
                    if choices:
                        payment_select = (sname, choices)
                        break

        matched_payment_code = ""
        matched_payment_label = ""
        available_payments: List[str] = []

        target_group = payment_radio_group or payment_select
        if target_group is not None:
            ctrl_name, choices = target_group
            available_payments = [c["text"] for c in choices if not c.get("disabled") and c.get("text")]

            chosen = self._match_payment_choice(choices, payment_method)
            if chosen is not None:
                matched_payment_code = payment_method
            elif fallback_payment_method and fallback_payment_method != "none" and fallback_payment_method != payment_method:
                chosen = self._match_payment_choice(choices, fallback_payment_method)
                if chosen is not None:
                    matched_payment_code = fallback_payment_method
                    logger.warning(
                        f"主选支付方式 [{payment_method}] 在当前商品/门店不可用，已自动切换备选免认证方式 [{fallback_payment_method}]"
                    )

            if chosen is None:
                return {
                    "ok": False,
                    "available_payments": available_payments,
                    "error": (
                        f"当前商品/门店不支持所选免认证支付方式 ({payment_method}) "
                        f"及备选方式 ({fallback_payment_method})，可用方式为: {', '.join(available_payments) or '未知'}；已安全中止下单。"
                    ),
                }

            form_data[ctrl_name] = chosen["value"]
            matched_payment_label = PAYMENT_METHOD_MAP[matched_payment_code]["label"]
        else:
            # 某些会员单一步骤场景无支付方式选项，沿用默认
            matched_payment_code = payment_method
            matched_payment_label = PAYMENT_METHOD_MAP.get(payment_method, {}).get("label", payment_method)

        # 3. 配送方式与配送时间段
        addr = address_cfg or {}
        desired_delivery = str(addr.get("delivery_method") or "auto").strip()
        if matched_payment_code == "store":
            desired_delivery = "店頭受取"

        if desired_delivery and desired_delivery != "auto":
            deliv_kws = (
                ["店頭受取", "店頭", "店舗受取"]
                if "店頭" in desired_delivery
                else ([desired_delivery] if desired_delivery not in ("宅配便", "メール便") else (["宅配", "佐川"] if desired_delivery == "宅配便" else ["メール便", "ゆうパケット", "ネコポス"]))
            )
            for rname, choices in radio_groups.items():
                if payment_radio_group and rname == payment_radio_group[0]:
                    continue
                ctx = " ".join(f"{rname} {c['text']} {c['context']}" for c in choices).lower()
                if any(k in ctx for k in ["配送方法", "発送方法", "受け取り", "deliver", "haisou"]):
                    for ch in choices:
                        if not ch.get("disabled") and any(dk.lower() in ch["text"].lower() for dk in deliv_kws):
                            form_data[rname] = ch["value"]
                            break
            for sel in all_selects:
                sname = sel.get("name") or ""
                if payment_select and sname == payment_select[0]:
                    continue
                ctx = f"{sname} {self._element_context_text(sel)}".lower()
                if any(k in ctx for k in ["配送方法", "発送方法", "受け取り", "deliver", "haisou"]):
                    for o in sel.find_all("option"):
                        ot = o.get_text(" ", strip=True)
                        if any(dk.lower() in ot.lower() for dk in deliv_kws):
                            form_data[sname] = o.get("value", "")
                            break

        desired_time = str(addr.get("delivery_time") or "none").strip()
        if desired_time and desired_time != "none":
            time_tokens = [desired_time] + [t for t in desired_time.split("-") if t]

            def _time_matches(opt_text: str, opt_val: str) -> bool:
                if desired_time == opt_val:
                    return True
                if desired_time == "午前中" and (opt_val == "am" or "午前" in opt_text):
                    return True
                if len(time_tokens) >= 2 and all(tk in opt_text for tk in time_tokens[1:]):
                    return True
                if desired_time == "18-21" and opt_val in ("18-20", "19-21"):
                    return True
                return False

            for rname, choices in radio_groups.items():
                if payment_radio_group and rname == payment_radio_group[0]:
                    continue
                ctx = " ".join(f"{rname} {c['text']} {c['context']}" for c in choices).lower()
                if any(k in ctx for k in ["時間帯", "配達時間", "radioshipinfo", "deliverytime", "jikan", "time"]):
                    for ch in choices:
                        if not ch.get("disabled") and _time_matches(ch.get("text", ""), ch.get("value", "")):
                            form_data[rname] = ch["value"]
                            break

            for sel in all_selects:
                sname = sel.get("name") or ""
                ctx = f"{sname} {self._element_context_text(sel)}".lower()
                if any(k in ctx for k in ["時間帯", "配達時間", "deliverytime", "jikan", "time"]):
                    for o in sel.find_all("option"):
                        ot = o.get_text(" ", strip=True)
                        ov = o.get("value", "")
                        if _time_matches(ot, ov):
                            form_data[sname] = ov
                            break

        # 3.5 配送备注 / ご要望（即使用默认地址也允许写入注文備考）
        applied_address_fields: List[str] = []
        remarks_val = str(addr.get("remarks") or "")
        if remarks_val:
            for inp in all_inputs:
                nm = inp.get("name") or ""
                itype = (inp.get("type") or "text").lower()
                if nm and itype in ("text", "email", "tel"):
                    combined = f"{nm.lower()} {(inp.get('id') or '').lower()} {self._element_context_text(inp).lower()}"
                    if any(k in combined for k in ["備考", "ご要望", "memo", "note", "biko", "bikou"]):
                        form_data[nm] = remarks_val
                        applied_address_fields.append("remarks")
            for ta in all_textareas:
                tname = ta.get("name") or ""
                ctx = f"{tname} {self._element_context_text(ta)}".lower()
                if any(k in ctx for k in ["備考", "ご要望", "memo", "note", "biko", "bikou", "comment"]):
                    form_data[tname] = remarks_val
                    applied_address_fields.append("remarks")

        # 4. 自定义收货地址填写（仅在 use_custom_address=True 时覆盖非空字段）
        if use_custom_address and addr:
            last_name = str(addr.get("last_name") or "").strip()
            first_name = str(addr.get("first_name") or "").strip()
            last_kana = str(addr.get("last_name_kana") or "").strip()
            first_kana = str(addr.get("first_name_kana") or "").strip()
            postal = str(addr.get("postal_code") or "").strip()
            pref = str(addr.get("prefecture") or "").strip()
            addr1 = str(addr.get("address1") or "").strip()
            addr2 = str(addr.get("address2") or "").strip()
            phone = str(addr.get("phone") or "").strip()
            email = str(addr.get("email") or "").strip()

            text_inputs = [
                inp for inp in all_inputs
                if inp.get("name") and (inp.get("type") or "text").lower() in ("text", "email", "tel")
            ]

            # 识别拆分型字段 (zip1/zip2, tel1/tel2/tel3, nameSei/nameMei 等)
            zip_inputs = []
            tel_inputs = []
            kana_inputs = []
            name_inputs = []

            for inp in text_inputs:
                nm = (inp.get("name") or "").lower()
                iid = (inp.get("id") or "").lower()
                ctx = self._element_context_text(inp).lower()
                combined = f"{nm} {iid} {ctx}"

                if any(k in combined for k in ["郵便", "zip", "post", "yubin"]):
                    zip_inputs.append(inp)
                elif any(k in combined for k in ["電話", "tel", "phone"]):
                    tel_inputs.append(inp)
                elif any(k in combined for k in ["フリガナ", "カナ", "kana", "furigana"]):
                    kana_inputs.append(inp)
                elif (
                    any(k in combined for k in ["お名前", "氏名", "sei", "mei", "lastname", "firstname", "name"])
                    and not any(ex in combined for ex in ["mail", "user", "login", "card", "payment"])
                ):
                    name_inputs.append(inp)
                elif any(k in combined for k in ["mail", "email", "メール"]):
                    if email:
                        form_data[inp["name"]] = email
                        applied_address_fields.append("email")
                elif any(k in combined for k in ["建物", "マンション", "部屋", "building", "addr2", "address2", "addr_2"]):
                    if addr2:
                        form_data[inp["name"]] = addr2
                        applied_address_fields.append("address2")
                elif any(k in combined for k in ["市区町村", "番地", "住所", "address1", "addr1", "address", "addr"]):
                    if addr1:
                        form_data[inp["name"]] = addr1
                        applied_address_fields.append("address1")

            # 填充姓名
            if last_name or first_name:
                if len(name_inputs) >= 2:
                    if last_name:
                        form_data[name_inputs[0]["name"]] = last_name
                    if first_name:
                        form_data[name_inputs[1]["name"]] = first_name
                    applied_address_fields.append("name")
                elif len(name_inputs) == 1:
                    form_data[name_inputs[0]["name"]] = f"{last_name} {first_name}".strip()
                    applied_address_fields.append("name")

            # 填充片假名
            if last_kana or first_kana:
                if len(kana_inputs) >= 2:
                    if last_kana:
                        form_data[kana_inputs[0]["name"]] = last_kana
                    if first_kana:
                        form_data[kana_inputs[1]["name"]] = first_kana
                    applied_address_fields.append("kana")
                elif len(kana_inputs) == 1:
                    form_data[kana_inputs[0]["name"]] = f"{last_kana} {first_kana}".strip()
                    applied_address_fields.append("kana")

            # 填充邮编
            if postal:
                z1, z2 = _split_postal_code(postal)
                if len(zip_inputs) >= 2:
                    form_data[zip_inputs[0]["name"]] = z1
                    form_data[zip_inputs[1]["name"]] = z2
                    applied_address_fields.append("postal_code")
                elif len(zip_inputs) == 1:
                    maxlen = zip_inputs[0].get("maxlength")
                    form_data[zip_inputs[0]["name"]] = f"{z1}{z2}" if maxlen == "7" else postal
                    applied_address_fields.append("postal_code")

            # 填充电话
            if phone:
                t1, t2, t3 = _split_phone(phone)
                if len(tel_inputs) >= 3:
                    form_data[tel_inputs[0]["name"]] = t1
                    form_data[tel_inputs[1]["name"]] = t2
                    form_data[tel_inputs[2]["name"]] = t3
                    applied_address_fields.append("phone")
                elif len(tel_inputs) == 1:
                    form_data[tel_inputs[0]["name"]] = phone
                    applied_address_fields.append("phone")

            # 填充都道府県
            if pref:
                for sel in all_selects:
                    sname = sel.get("name") or ""
                    ctx = f"{sname} {self._element_context_text(sel)}".lower()
                    if any(k in ctx for k in ["都道府県", "pref", "todofuken"]):
                        matched_val = self._match_prefecture_option(sel, pref)
                        if matched_val is not None:
                            form_data[sname] = matched_val
                            applied_address_fields.append("prefecture")
                            break

        # 5. 自动勾选同意条款复选框（如有）
        for inp in all_inputs:
            name = inp.get("name")
            if name and (inp.get("type") or "").lower() == "checkbox":
                ctx = f"{name} {self._element_context_text(inp)}".lower()
                if any(k in ctx for k in ["同意", "承諾", "規約", "確認しました", "agree"]):
                    form_data[name] = inp.get("value") or "on"

        # 6. 选择进入确认页的 Submit 按钮（规避「戻る / 削除 / 変更 / 退回」）
        all_form_btns = form.find_all(["input", "button"])
        has_payment_submit = any((btn.get("name") or "") == "cartForm:doInputPaymentMethod" for btn in all_form_btns)
        submit_candidates = []
        for btn in all_form_btns:
            btype = (btn.get("type") or ("submit" if btn.name == "button" else "text")).lower()
            if btn.name == "input" and btype not in ("submit", "image"):
                continue
            if btn.name == "button" and btype in ("button", "reset"):
                continue
            bname = btn.get("name") or ""
            bval = btn.get("value") or btn.get_text(" ", strip=True) or ""
            label_lower = f"{bname} {bval}".lower()
            bad_words = [
                "戻る", "back", "cancel", "削除", "delete", "変更", "修正", "update", "再計算",
                "remove", "return", "doreturn", "doreadmenberinfo", "dosaveforlater", "domovetocart",
            ]
            if has_payment_submit:
                bad_words.append("doinputreceiver")
            if any(bad in label_lower for bad in bad_words):
                continue
            submit_candidates.append((bname, bval, label_lower))

        chosen_submit = None
        for bname, bval, label_lower in submit_candidates:
            if any(good in label_lower for good in ["doinputpaymentmethod", "doconfirm", "確認", "confirm", "次へ", "next", "proceed"]):
                chosen_submit = (bname, bval)
                break
        if chosen_submit is None and submit_candidates:
            chosen_submit = (submit_candidates[0][0], submit_candidates[0][1])

        if chosen_submit and chosen_submit[0]:
            form_data[chosen_submit[0]] = chosen_submit[1]

        return {
            "ok": True,
            "action_url": action_url,
            "form_data": form_data,
            "matched_payment_code": matched_payment_code,
            "matched_payment_label": matched_payment_label,
            "applied_address_fields": sorted(set(applied_address_fields)),
            "available_payments": available_payments,
            "error": "",
        }

    @classmethod
    def parse_confirm_page(cls, html: str, base_url: str) -> Dict[str, Any]:
        """解析确认页 (confirmOrderJa.html)，校验是否有错误并定位最终「注文する」提交表单"""
        soup = BeautifulSoup(html or "", "html.parser")
        page_text = soup.get_text(" ", strip=True)
        page_title = soup.title.get_text(strip=True) if soup.title else ""

        # 检查页面是否仍停留在报错态或真实售罄
        err_texts = cls._extract_page_errors(soup)
        if cls._detect_real_sold_out(soup, page_text, err_texts):
            return {"ok": False, "sold_out": True, "error": "商品在结算确认阶段已被抢空（売り切れ）"}
        if err_texts:
            return {"ok": False, "error": f"结算表单校验未通过: {' | '.join(err_texts[:3])}"}

        form = cls._find_checkout_form(soup)
        if not form:
            return {"ok": False, "error": "确认页面未找到最终下单提交表单"}

        # 确认是否真实抵达了最终确认页（而未停留在前几步）
        has_prev_step_btn = any(
            form.find(attrs={"name": nm}) is not None
            for nm in ("cartForm:doInputCustomer", "cartForm:doInputReceiver", "cartForm:doInputPaymentMethod")
        )
        has_final_marker = (
            form.find(attrs={"name": "cartForm:doOnceOrder"}) is not None
            or any(
                any(kw in f"{btn.get('name') or ''} {btn.get('value') or btn.get_text(' ', strip=True)}".lower()
                    for kw in ("doonceorder", "doorder", "docomplete", "注文する", "注文を確定"))
                for btn in form.find_all(["input", "button"])
            )
        )
        if has_prev_step_btn and not has_final_marker:
            return {"ok": False, "error": f"未能进入最终订单确认页 (当前页面: {page_title or base_url})"}

        action_url = urljoin(base_url, form.get("action") or base_url)
        form_data = cls._extract_form_defaults(form, html)

        # 查找最终下单提交按钮（规避「戻る / 修正 / return」）
        final_candidates = []
        for btn in form.find_all(["input", "button"]):
            btype = (btn.get("type") or ("submit" if btn.name == "button" else "text")).lower()
            if btn.name == "input" and btype not in ("submit", "image"):
                continue
            if btn.name == "button" and btype in ("button", "reset"):
                continue
            bname = btn.get("name") or ""
            bval = btn.get("value") or btn.get_text(" ", strip=True) or ""
            label_lower = f"{bname} {bval}".lower()
            if any(bad in label_lower for bad in ["doreturn", "return", "remove", "戻る", "back", "修正", "変更", "cancel"]):
                continue
            final_candidates.append((bname, bval, label_lower))

        chosen_final = None
        for bname, bval, label_lower in final_candidates:
            if any(good in label_lower for good in ["doonceorder", "注文する", "注文を確定", "購入", "確定", "doorder", "docomplete", "order", "submit"]):
                chosen_final = (bname, bval)
                break
        if chosen_final is None and final_candidates:
            chosen_final = (final_candidates[0][0], final_candidates[0][1])

        if chosen_final and chosen_final[0]:
            form_data[chosen_final[0]] = chosen_final[1]

        # 抓取合计金额提示（兼容「総合計 66,000 円(税込)」与「合計 12,800円」）
        total_match = re.search(r"(?:総合計|合計)[^\d]*([\d,]+)\s*円", page_text)
        total_str = f"{total_match.group(1)}円" if total_match else ""

        return {
            "ok": True,
            "action_url": action_url,
            "form_data": form_data,
            "total_str": total_str,
            "error": "",
        }

    @classmethod
    def parse_complete_page(cls, html: str, final_url: str) -> Dict[str, Any]:
        """解析最终提交结果页：识别成功订单号、外部支付认证跳转或售罄/系统错误"""
        url_str = str(final_url or "")
        parsed_url = urlparse(url_str)
        host = (parsed_url.netloc or "").lower()

        # 1. 若被重定向至非 Mandarake 域名或明确第三方支付网关，视为需人工完成认证
        if host and "mandarake.co.jp" not in host:
            return {
                "status": "NEED_PAYMENT_AUTH",
                "success": False,
                "payment_url": url_str,
                "order_number": "",
                "message": f"订单已推进至外部支付认证页面，请打开链接完成认证: {url_str}",
            }

        soup = BeautifulSoup(html or "", "html.parser")
        page_text = soup.get_text(" ", strip=True)

        # 2. 检查系统错误页
        if "error.html" in url_str or "システムエラー" in page_text:
            return {
                "status": "FAILED",
                "success": False,
                "sold_out": False,
                "order_number": "",
                "message": "提交订单时官网返回系统错误页面 (error.html)",
            }

        # 3. 优先提取订单编号并识别成功完成页（防止条款说明文字误报售罄）
        order_num = ""
        m_ord = re.search(r"(?:注文番号|オーダー番号|受付番号|Order\s*(?:No\.?|ID))\s*[：:\s#]*([A-Za-z0-9\-_]{5,32})", page_text, re.I)
        if m_ord:
            order_num = m_ord.group(1).strip()

        is_complete = (
            "completeOrder" in url_str
            or any(k in page_text for k in ["ご注文ありがとうございました", "ご注文を承りました", "注文が完了", "ご注文完了"])
        )
        if is_complete:
            return {
                "status": "SUCCESS",
                "success": True,
                "sold_out": False,
                "order_number": order_num,
                "message": f"下单成功！{f'订单号: {order_num}' if order_num else '订单已成功提交至 Mandarake'}",
            }

        # 4. 精准检查真实售罄与表单校验错误
        err_texts = cls._extract_page_errors(soup)
        if cls._detect_real_sold_out(soup, page_text, err_texts):
            return {
                "status": "FAILED",
                "success": False,
                "sold_out": True,
                "order_number": "",
                "message": "下单失败：该商品已被其他买家抢先买走（売り切れ）",
            }

        if err_texts and "completeOrder" not in url_str:
            return {
                "status": "FAILED",
                "success": False,
                "sold_out": False,
                "order_number": "",
                "message": f"提交订单未通过官网校验: {' | '.join(err_texts[:3])}",
            }

        # 5. 检查页面内是否要求跳往第三方支付链接
        for a in soup.select("a[href]"):
            href = a.get("href") or ""
            if any(gw in href.lower() for gw in ["3dsecure", "paypal.com", "amazon.co.jp", "paidy", "atone", "sbpayment", "gmo-pg", "veritrans"]):
                return {
                    "status": "NEED_PAYMENT_AUTH",
                    "success": False,
                    "payment_url": href,
                    "order_number": order_num,
                    "message": f"订单等待支付认证，请手动前往完成: {href}",
                }

        return {
            "status": "FAILED",
            "success": False,
            "sold_out": False,
            "order_number": order_num,
            "message": f"未能确认最终订单提交结果 (当前页: {url_str})，请前往官网 MyPage 核实",
        }

    # --- 核心抢购 / 一键下单执行 ---

    def _record_order(
        self,
        order_uuid: str,
        item_id: str,
        title: str,
        price: int,
        url: str,
        img_url: str,
        shop: str,
        status: str,
        is_dry: bool,
        trigger_type: str,
        message: str,
        duration_ms: int,
        extra_details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        details = dict(extra_details or {})
        details.setdefault("account", self.account_name)
        order_row = {
            "id": order_uuid,
            "item_id": item_id,
            "title": title,
            "price": int(price or 0),
            "price_str": f"{int(price):,}円" if price else "",
            "url": url,
            "img_url": img_url,
            "shop": shop,
            "status": status,
            "dry_run": 1 if is_dry else 0,
            "trigger_type": trigger_type,
            "message": message,
            "duration_ms": int(duration_ms),
            "created_at": int(time.time()),
            "raw_details": json.dumps(details, ensure_ascii=False),
        }
        self.db.add_order(order_row)
        order_row["parsed_details"] = details
        order_row["payment_method"] = details.get("payment_method", "")
        order_row["payment_label"] = details.get("payment_label", "")
        order_row["order_number"] = details.get("order_number", "")
        order_row["rule_name"] = details.get("rule_name", "")
        order_row["payment_url"] = details.get("payment_url", "")
        self._emit_order_event(order_row)
        return order_row

    def order_item(
        self,
        item_id: str,
        title: str = "",
        price: int = 0,
        img_url: str = "",
        shop: str = "",
        dry_run: Optional[bool] = None,
        payment_method: Optional[str] = None,
        trigger_type: str = "manual",
        rule_name: str = "",
        force_verify_login: bool = False,
    ) -> Dict[str, Any]:
        """
        执行一键抢购 / 自动抢购核心下单流程：
        加购 -> 获取结算页 -> 填写免认证支付方式与地址 -> 确认页 -> Dry-Run/实盘测试终步前拦截 或 Live 提交
        """
        start_ts = time.perf_counter()
        order_uuid = f"ord_{int(time.time())}_{str(uuid.uuid4())[:8]}"
        item_id = str(item_id or "").strip()
        steps: List[Dict[str, Any]] = []
        if not item_id:
            return {"success": False, "status": "FAILED", "order_id": order_uuid, "message": "商品 ID 不能为空", "steps": steps}

        # 若缺少元信息（含 img_url），尝试从本地归档库补齐
        if not title or not price or not shop or not img_url:
            db_item = self.db.get_product(item_id)
            if db_item:
                title = title or db_item.get("title", "")
                price = price or int(db_item.get("price", 0) or 0)
                img_url = img_url or db_item.get("img_url", "")
                shop = shop or db_item.get("shop", "")

        url = f"https://order.mandarake.co.jp/order/detailPage/item?itemCode={item_id}&lang=ja"
        is_dry = bool(self.config.get("dry_run", True)) if dry_run is None else bool(dry_run)

        pay_method = str(payment_method or self.config.get("default_payment_method") or "bank").strip().lower()
        if pay_method not in PAYMENT_METHOD_MAP:
            pay_method = "bank"
        fallback_pay = str(self.config.get("fallback_payment_method") or "cod").strip().lower()

        with self._order_lock:
            mode_label = (
                "LIVE-TEST 实盘链路终步前测试"
                if trigger_type == "live_test"
                else ("DRY-RUN 仿真演练" if is_dry else "LIVE 实盘抢购")
            )
            logger.info(
                f"⚡ [抢购引擎] 开始执行 [{mode_label}] | 商品: {title or item_id} (ID: {item_id}) | 支付: {pay_method} | 触发: {trigger_type}"
            )

            # 1. 自动抢购护栏检查（限额 / 防重 / 冷却）
            if trigger_type == "auto":
                max_price = int(self.config.get("auto_buy_max_price", 0) or 0)
                if max_price > 0 and int(price or 0) > max_price:
                    msg = f"自动抢购已跳过：商品价格 {int(price):,}円 超出全局限价 {max_price:,}円"
                    logger.info(f"🛡️ [抢购引擎] {msg}")
                    return {"success": False, "status": "SKIPPED", "order_id": order_uuid, "message": msg, "steps": steps}

                if self.db.has_recent_order(item_id, within_seconds=86400, statuses=["SUCCESS", "NEED_PAYMENT_AUTH"]):
                    msg = f"自动抢购已跳过：商品 {item_id} 24 小时内已有成功下单记录，防止重复下单"
                    logger.info(f"🛡️ [抢购引擎] {msg}")
                    return {"success": False, "status": "SKIPPED", "order_id": order_uuid, "message": msg, "steps": steps}

                cooldown = int(self.config.get("auto_buy_cooldown_seconds", 3600) or 3600)
                if is_dry and self.db.has_recent_order(item_id, within_seconds=cooldown, statuses=["DRY_RUN"]):
                    msg = f"自动抢购冷却跳过：商品 {item_id} 在 {cooldown}s 内已有演练记录"
                    logger.info(f"🛡️ [抢购引擎] {msg}")
                    return {"success": False, "status": "SKIPPED", "order_id": order_uuid, "message": msg, "steps": steps}

                daily_cap = int(self.config.get("auto_buy_max_orders_per_day", 5) or 5)
                today_cnt = self.db.count_today_orders(
                    trigger_type="auto",
                    dry_run=1 if is_dry else 0,
                    statuses=["DRY_RUN"] if is_dry else ["SUCCESS", "NEED_PAYMENT_AUTH"],
                )
                if today_cnt >= daily_cap:
                    msg = f"自动抢购已达今日上限 ({today_cnt}/{daily_cap})，已跳过保护资金安全"
                    logger.warning(f"🛡️ [抢购引擎] {msg}")
                    return {"success": False, "status": "SKIPPED", "order_id": order_uuid, "message": msg, "steps": steps}

            # 2. 登录态校验（实盘测试强制在线核验）
            if force_verify_login or trigger_type == "live_test" or not self.is_logged_in:
                verify = self.check_login_status(verify_online=True)
                if not verify.get("is_logged_in"):
                    err_msg = "尚未登录 Mandarake 账号或会话已失效，请前往【抢购设置】登录或导入 Cookie 后再抢购。"
                    steps.append({"step": "login", "label": "在线验证 Mandarake 登录态", "ok": False, "detail": verify.get("message") or err_msg})
                    duration_ms = int((time.perf_counter() - start_ts) * 1000)
                    logger.warning(f"⚠️ [抢购引擎] {err_msg}")
                    order_row = self._record_order(
                        order_uuid, item_id, title, price, url, img_url, shop,
                        "FAILED", is_dry, trigger_type, err_msg, duration_ms,
                        {"payment_method": pay_method, "rule_name": rule_name, "steps": steps},
                    )
                    return {"success": False, "status": "FAILED", "order_id": order_uuid, "message": err_msg, "order": order_row, "steps": steps}
                steps.append({"step": "login", "label": "在线验证 Mandarake 登录态", "ok": True, "detail": f"账号 {self.account_name} 会话有效"})
            else:
                steps.append({"step": "login", "label": "验证 Mandarake 登录态", "ok": True, "detail": f"账号 {self.account_name} 已就绪"})

            added_to_cart = False
            submitted_final = False

            def _post_checkout(url_target: str, payload: Dict[str, str], referer_url: str, step_name: str):
                resp = self.session.post(
                    url_target,
                    data=payload,
                    headers={**DEFAULT_HEADERS, "Referer": referer_url, "Origin": "https://cart.mandarake.co.jp"},
                    timeout=12,
                    allow_redirects=True,
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"{step_name}响应异常 (HTTP {resp.status_code})")
                if "customer/login" in (resp.url or ""):
                    self.is_logged_in = False
                    self._emit_status_event()
                    raise RuntimeError("结算页跳转回了登录页，当前登录会话已过期，请重新登录或导入 Cookie")
                return resp

            try:
                # 3. 加购
                ok_add, msg_add, checkout_url = self.add_to_cart(item_id=item_id, count=1)
                if not ok_add:
                    steps.append({"step": "add_to_cart", "label": "加入官网购物车 (basket/add)", "ok": False, "detail": msg_add})
                    duration_ms = int((time.perf_counter() - start_ts) * 1000)
                    if ("売り切れ" in msg_add or "在庫" in msg_add) and not is_dry:
                        self.db.mark_product_stock(item_id, is_in_stock=0)
                    order_row = self._record_order(
                        order_uuid, item_id, title, price, url, img_url, shop,
                        "FAILED", is_dry, trigger_type, msg_add, duration_ms,
                        {"payment_method": pay_method, "rule_name": rule_name, "steps": steps},
                    )
                    return {"success": False, "status": "FAILED", "order_id": order_uuid, "message": msg_add, "order": order_row, "steps": steps}

                added_to_cart = True
                steps.append({"step": "add_to_cart", "label": "加入官网购物车 (basket/add)", "ok": True, "detail": f"商品 {item_id} 加购成功"})
                target_cart_url = checkout_url or f"{CART_INPUT_URL}?referer={quote(url)}"

                # 4. 请求收银结算入口页 (inputOrderJa.html)
                r_cart = self.session.get(target_cart_url, headers=DEFAULT_HEADERS, timeout=12, allow_redirects=True)
                if r_cart.status_code != 200:
                    raise RuntimeError(f"访问结算输入页失败 (HTTP {r_cart.status_code})")

                if "customer/login" in (r_cart.url or ""):
                    self.is_logged_in = False
                    self._emit_status_event()
                    raise RuntimeError("结算页跳转回了登录页，当前登录会话已过期，请重新登录或导入 Cookie")

                addr_cfg = self.config.get("address", {})
                use_custom_addr = bool(self.config.get("use_custom_address", False))
                all_applied_addr_fields: List[str] = []
                payment_resp = r_cart

                # 识别 Mandarake 官方 4 步 Teeda 结算链路 vs 单页简化表单
                if "cartForm:doInputCustomer" in (r_cart.text or ""):
                    # 第 1 步：购物车确认页 -> 点击「商品の注文に進む」(cartForm:doInputCustomer)
                    cart_res = self.parse_cart_confirmation_form(r_cart.text, r_cart.url, item_id)
                    if not cart_res.get("ok"):
                        steps.append({"step": "cart_confirm", "label": "确认购物车并进入结算 (inputOrderJa)", "ok": False, "detail": cart_res.get("error") or "无法推进购物车"})
                        raise RuntimeError(cart_res.get("error") or "无法推进购物车")

                    payment_resp = _post_checkout(
                        cart_res["action_url"],
                        cart_res["form_data"],
                        r_cart.url,
                        "推进购物车确认页",
                    )
                    shop_note = f"门店 {cart_res['shop_id']}" if cart_res.get("shop_id") else "当前门店"
                    steps.append({"step": "cart_confirm", "label": "确认购物车并进入结算 (inputOrderJa)", "ok": True, "detail": f"已选定{shop_note}进入收货地址页"})

                    # 第 2 步：お届け先入力页 (inputReceiverJa.html)
                    if "cartForm:doInputReceiver" in (payment_resp.text or "") and "cartForm:radioPaymentMethod" not in (payment_resp.text or ""):
                        recv_res = self.parse_and_fill_receiver_form(
                            html=payment_resp.text,
                            base_url=payment_resp.url,
                            address_cfg=addr_cfg,
                            use_custom_address=use_custom_addr,
                            payment_method=pay_method,
                        )
                        if not recv_res.get("ok"):
                            steps.append({"step": "receiver_page", "label": "填写收货地址与配送 (inputReceiverJa)", "ok": False, "detail": recv_res.get("error") or "收货地址页处理失败"})
                            raise RuntimeError(recv_res.get("error") or "收货地址页处理失败")

                        if recv_res.get("needs_read_member"):
                            payment_resp = _post_checkout(
                                recv_res["action_url"],
                                recv_res["read_member_data"],
                                payment_resp.url,
                                "载入会员默认登记地址",
                            )
                            recv_res = self.parse_and_fill_receiver_form(
                                html=payment_resp.text,
                                base_url=payment_resp.url,
                                address_cfg=addr_cfg,
                                use_custom_address=use_custom_addr,
                                payment_method=pay_method,
                            )
                            if not recv_res.get("ok"):
                                steps.append({"step": "receiver_page", "label": "填写收货地址与配送 (inputReceiverJa)", "ok": False, "detail": recv_res.get("error") or "载入会员地址后校验失败"})
                                raise RuntimeError(recv_res.get("error") or "载入会员地址后校验失败")

                        all_applied_addr_fields.extend(recv_res.get("applied_address_fields", []))
                        recv_addr_note = (
                            f"自定义地址({', '.join(recv_res['applied_address_fields'])})"
                            if recv_res.get("applied_address_fields")
                            else "已载入会员默认地址"
                        )
                        payment_resp = _post_checkout(
                            recv_res["action_url"],
                            recv_res["form_data"],
                            payment_resp.url,
                            "提交收货地址进入支付方式页",
                        )
                        steps.append({"step": "receiver_page", "label": "填写收货地址与配送 (inputReceiverJa)", "ok": True, "detail": recv_addr_note})

                # 5. 第 3 步：选择免认证支付方式与配送时段 (inputPaymentMethodJa / 单步表单)
                input_step_label = (
                    "选择支付与配送方式 (inputPaymentMethodJa)"
                    if "cartForm:radioPaymentMethod" in (payment_resp.text or "")
                    else "解析结算页并填表 (inputOrderJa)"
                )
                fill_res = self.parse_and_fill_input_form(
                    html=payment_resp.text,
                    base_url=payment_resp.url,
                    payment_method=pay_method,
                    fallback_payment_method=fallback_pay,
                    address_cfg=addr_cfg,
                    use_custom_address=use_custom_addr,
                )
                if not fill_res.get("ok"):
                    steps.append({"step": "input_page", "label": input_step_label, "ok": False, "detail": fill_res.get("error") or "无法填充结算表单"})
                    raise RuntimeError(fill_res.get("error") or "无法填充结算表单")

                used_pay_code = fill_res["matched_payment_code"]
                used_pay_label = fill_res["matched_payment_label"]
                all_applied_addr_fields = sorted(set(all_applied_addr_fields + list(fill_res.get("applied_address_fields", []))))
                addr_desc = (
                    f"自定义覆盖({', '.join(all_applied_addr_fields)})"
                    if all_applied_addr_fields
                    else "会员默认地址"
                )
                steps.append({"step": "input_page", "label": input_step_label, "ok": True, "detail": f"支付: {used_pay_label} | {addr_desc}"})

                # 6. 提交进入最终确认页 (confirmOrderJa.html)
                r_confirm = _post_checkout(
                    fill_res["action_url"],
                    fill_res["form_data"],
                    payment_resp.url,
                    "提交进入订单确认页",
                )

                confirm_res = self.parse_confirm_page(r_confirm.text, r_confirm.url)
                if not confirm_res.get("ok"):
                    steps.append({"step": "confirm_page", "label": "到达订单确认页 (confirmOrderJa)", "ok": False, "detail": confirm_res.get("error") or "确认页校验失败"})
                    if confirm_res.get("sold_out") and not is_dry:
                        self.db.mark_product_stock(item_id, is_in_stock=0)
                    raise RuntimeError(confirm_res.get("error") or "确认页校验失败")

                total_note = f"总额 {confirm_res['total_str']}" if confirm_res.get("total_str") else "最终「注文する」表单已就绪"
                steps.append({"step": "confirm_page", "label": "到达订单确认页 (confirmOrderJa)", "ok": True, "detail": total_note})

                # 7. Dry-Run / 实盘终步前测试拦截（决不提交最终订单，也不污染本地商品售罄状态）
                if is_dry:
                    steps.append({"step": "intercept", "label": "最终下单前安全停止拦截", "ok": True, "detail": "已拦截向 completeOrderJa 的最终下单请求，0 真实订单/扣款"})
                    cleaned = self.delete_from_cart(item_id)
                    added_to_cart = False
                    steps.append({"step": "cleanup", "label": "清理官网测试购物车", "ok": bool(cleaned), "detail": "已移除测试商品" if cleaned else "移除购物车请求未确认"})
                    duration_ms = int((time.perf_counter() - start_ts) * 1000)
                    addr_note = (
                        f"自定义地址({', '.join(all_applied_addr_fields)})"
                        if all_applied_addr_fields
                        else "会员默认登记地址"
                    )
                    total_suffix = f" | 应付总额: {confirm_res['total_str']}" if confirm_res.get("total_str") else ""
                    prefix = "[实盘链路终步前测试成功]" if trigger_type == "live_test" else "[Dry-Run 演练成功]"
                    sim_msg = (
                        f"{prefix} 已完整走通加购→填表→确认页！账号: {self.account_name} | "
                        f"支付: {used_pay_label} | 地址: {addr_note}{total_suffix}。已按要求在最后一步下单前安全停止并清理测试购物车。"
                    )
                    logger.info(f"✅ [抢购引擎] {sim_msg}")
                    order_row = self._record_order(
                        order_uuid, item_id, title, price, url, img_url, shop,
                        "DRY_RUN", True, trigger_type, sim_msg, duration_ms,
                        {
                            "payment_method": used_pay_code,
                            "payment_label": used_pay_label,
                            "rule_name": rule_name,
                            "applied_address_fields": all_applied_addr_fields,
                            "steps": steps,
                        },
                    )
                    return {
                        "success": True,
                        "status": "DRY_RUN",
                        "order_id": order_uuid,
                        "message": sim_msg,
                        "duration_ms": duration_ms,
                        "order": order_row,
                        "steps": steps,
                    }

                # 8. Live 实盘提交最终订单
                submitted_final = True
                r_complete = self.session.post(
                    confirm_res["action_url"],
                    data=confirm_res["form_data"],
                    headers={**DEFAULT_HEADERS, "Referer": r_confirm.url, "Origin": "https://cart.mandarake.co.jp"},
                    timeout=15,
                    allow_redirects=True,
                )
                duration_ms = int((time.perf_counter() - start_ts) * 1000)

                if r_complete.status_code != 200:
                    raise RuntimeError(f"最终提交订单失败 (HTTP {r_complete.status_code})")

                comp_res = self.parse_complete_page(r_complete.text, r_complete.url)
                status = comp_res["status"]

                if status == "SUCCESS":
                    self.db.mark_product_stock(item_id, is_in_stock=0)
                    steps.append({"step": "complete", "label": "提交最终订单", "ok": True, "detail": comp_res["message"]})
                    succ_msg = (
                        f"🎉 【实盘抢购成功】{comp_res['message']} | "
                        f"支付方式: {used_pay_label} | 耗时: {duration_ms}ms"
                    )
                    logger.info(succ_msg)
                    order_row = self._record_order(
                        order_uuid, item_id, title, price, url, img_url, shop,
                        "SUCCESS", False, trigger_type, succ_msg, duration_ms,
                        {
                            "payment_method": used_pay_code,
                            "payment_label": used_pay_label,
                            "order_number": comp_res.get("order_number", ""),
                            "rule_name": rule_name,
                            "steps": steps,
                        },
                    )
                    return {
                        "success": True,
                        "status": "SUCCESS",
                        "order_id": order_uuid,
                        "order_number": comp_res.get("order_number", ""),
                        "message": succ_msg,
                        "duration_ms": duration_ms,
                        "order": order_row,
                        "steps": steps,
                    }

                if status == "NEED_PAYMENT_AUTH":
                    steps.append({"step": "complete", "label": "提交最终订单", "ok": False, "detail": comp_res["message"]})
                    auth_msg = f"⚠️ {comp_res['message']} (耗时: {duration_ms}ms)"
                    logger.warning(auth_msg)
                    order_row = self._record_order(
                        order_uuid, item_id, title, price, url, img_url, shop,
                        "NEED_PAYMENT_AUTH", False, trigger_type, auth_msg, duration_ms,
                        {
                            "payment_method": used_pay_code,
                            "payment_label": used_pay_label,
                            "payment_url": comp_res.get("payment_url", ""),
                            "order_number": comp_res.get("order_number", ""),
                            "rule_name": rule_name,
                            "steps": steps,
                        },
                    )
                    return {
                        "success": False,
                        "status": "NEED_PAYMENT_AUTH",
                        "order_id": order_uuid,
                        "payment_url": comp_res.get("payment_url", ""),
                        "message": auth_msg,
                        "duration_ms": duration_ms,
                        "order": order_row,
                        "steps": steps,
                    }

                # 明确失败
                if comp_res.get("sold_out"):
                    self.db.mark_product_stock(item_id, is_in_stock=0)
                fail_msg = comp_res.get("message") or "实盘提交未确认成功"
                steps.append({"step": "complete", "label": "提交最终订单", "ok": False, "detail": fail_msg})
                logger.error(f"❌ [抢购引擎] {fail_msg}")
                order_row = self._record_order(
                    order_uuid, item_id, title, price, url, img_url, shop,
                    "FAILED", False, trigger_type, fail_msg, duration_ms,
                    {"payment_method": used_pay_code, "payment_label": used_pay_label, "rule_name": rule_name, "steps": steps},
                )
                return {
                    "success": False,
                    "status": "FAILED",
                    "order_id": order_uuid,
                    "message": fail_msg,
                    "duration_ms": duration_ms,
                    "order": order_row,
                    "steps": steps,
                }

            except Exception as e:
                duration_ms = int((time.perf_counter() - start_ts) * 1000)
                err_msg = f"抢购结算中断: {e}"
                logger.error(f"❌ [抢购引擎] {err_msg}")
                if not steps or steps[-1].get("ok", True):
                    steps.append({"step": "error", "label": "结算流程异常", "ok": False, "detail": str(e)})
                if added_to_cart and not submitted_final:
                    self.delete_from_cart(item_id)
                    steps.append({"step": "cleanup", "label": "清理测试购物车", "ok": True, "detail": "异常中止后已移除测试加购商品"})
                order_row = self._record_order(
                    order_uuid, item_id, title, price, url, img_url, shop,
                    "FAILED", is_dry, trigger_type, err_msg, duration_ms,
                    {"payment_method": pay_method, "rule_name": rule_name, "steps": steps},
                )
                return {
                    "success": False,
                    "status": "FAILED",
                    "order_id": order_uuid,
                    "message": err_msg,
                    "duration_ms": duration_ms,
                    "order": order_row,
                    "steps": steps,
                }

    @staticmethod
    def extract_item_code(item_input: str) -> str:
        """从纯商品 ID 或 Mandarake 商品详情页 URL 中提取 itemCode"""
        raw = str(item_input or "").strip()
        if not raw:
            return ""
        if raw.startswith(("http://", "https://")) or "itemCode=" in raw:
            qs = parse_qs(urlparse(raw).query)
            if qs.get("itemCode") and qs["itemCode"][0].strip():
                return qs["itemCode"][0].strip()
            m = re.search(r"itemCode=([A-Za-z0-9_\-]+)", raw)
            if m:
                return m.group(1).strip()
        return raw

    def test_live_checkout(
        self,
        item_input: str = "",
        payment_method: Optional[str] = None,
        scraper: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        实盘抢购链路全真测试（强制在最后一步提交下单前停止拦截）：
        - 使用当前真实登录会话、自定义/默认地址与默认免认证支付方式
        - 强制传递 dry_run=True 与 trigger_type="live_test"，确保无论全局开关是否为实盘模式，都不会提交最终订单
        """
        target_id = self.extract_item_code(item_input)
        target_item: Optional[Dict[str, Any]] = None

        if target_id:
            target_item = self.db.get_product(target_id) or {"id": target_id}
        else:
            # 优先从本地归档库挑选近期在库商品，并用官网实时接口确认仍在库
            try:
                res = self.db.query_products(page=1, page_size=10, is_in_stock=1, sort_by="updated_at", sort_order="desc")
                candidates = res.get("items", []) if isinstance(res, dict) else []
            except Exception:
                candidates = []

            for cand in candidates:
                pid = str(cand.get("id") or "").strip()
                if not pid:
                    continue
                if scraper is not None and hasattr(scraper, "check_item_realtime_stock"):
                    real_stock = scraper.check_item_realtime_stock(pid)
                    if real_stock == 1:
                        target_item = cand
                        target_id = pid
                        break
                    elif real_stock == 0:
                        continue
                else:
                    target_item = cand
                    target_id = pid
                    break

            # 若库内近期候选均未确认有货，实时拉取官网第 1 页在库商品
            if not target_id and scraper is not None and hasattr(scraper, "fetch_category_page"):
                try:
                    live_items, _, _ = scraper.fetch_category_page(
                        category_code="020114",
                        page=1,
                        in_stock_only=True,
                        disp_count=48,
                        polite_delay=False,
                        bypass_cache=True,
                    )
                    for it in live_items:
                        if int(it.get("is_in_stock", 0)) == 1 and it.get("id"):
                            target_item = it
                            target_id = str(it["id"]).strip()
                            break
                except Exception as e:
                    logger.warning(f"[实盘链路测试] 实时拉取在库商品失败: {e}")

        if not target_id:
            return {
                "success": False,
                "status": "FAILED",
                "message": "未能自动找到可用的在库测试商品，请手动填入一个在库商品 ID 或官网链接后重试。",
                "steps": [],
            }

        result = self.order_item(
            item_id=target_id,
            title=str((target_item or {}).get("title") or ""),
            price=int((target_item or {}).get("price") or 0),
            img_url=str((target_item or {}).get("img_url") or ""),
            shop=str((target_item or {}).get("shop") or ""),
            dry_run=True,  # 硬编码安全锁：终步前必停，绝不提交最终订单
            payment_method=payment_method,
            trigger_type="live_test",
            force_verify_login=True,
        )
        result["tested_item"] = {
            "id": target_id,
            "title": str((target_item or {}).get("title") or f"商品 {target_id}"),
            "price": int((target_item or {}).get("price") or 0),
            "shop": str((target_item or {}).get("shop") or ""),
            "img_url": str((target_item or {}).get("img_url") or ""),
        }
        return result


# 全局单例
auto_order_engine = MandarakeAutoOrder()
