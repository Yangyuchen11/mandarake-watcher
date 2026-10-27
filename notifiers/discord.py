"""
Discord Webhook 富文本 Embed 通知模块
专为 Mandarake 【ドール】 监控定制，提供大图预览、官网直达超链接与核心参数排版
"""
import time
import logging
from typing import List, Dict, Any
import requests
from notifiers.base import BaseNotifier

logger = logging.getLogger("mandarake.notifier.discord")

MANDARAKE_AVATAR = "https://order.mandarake.co.jp/content/img/icon/shortcut/192.png"


class DiscordNotifier(BaseNotifier):
    def __init__(self, webhook_url: str = "", enabled: bool = False):
        super().__init__("discord", enabled)
        self.webhook_url = webhook_url.strip()

    def _build_embed(self, item: Dict[str, Any]) -> Dict[str, Any]:
        title = item.get("title", "未命名商品")
        price = item.get("price_str") or f"{item.get('price', 0):,}円"
        shop = item.get("shop", "まんだらけ")
        cat = item.get("category_name", "ドール")
        url = item.get("url", "https://order.mandarake.co.jp/")
        img_url = item.get("img_url", "")
        item_id = item.get("item_no") or item.get("id", "")

        embed = {
            "title": f"🛒 {title[:240]}",
            "url": url,
            "description": f"🟢 **在库有货** · [👉 点击进入 Mandarake 官网购买]({url})",
            "color": 0xFF5722,  # Mandarake 品牌醒目橙红
            "fields": [
                {"name": "💰 价格 (税込)", "value": f"**{price}**", "inline": True},
                {"name": "🏬 发送店舗", "value": shop or "未标明", "inline": True},
                {"name": "📂 分类目录", "value": cat or "ドール", "inline": True},
            ],
            "footer": {
                "text": f"商品编号: {item_id} · Mandarake Doll Monitor",
                "icon_url": MANDARAKE_AVATAR
            },
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        # 大图展示：让娃的妆面、配件细节一览无余
        if img_url:
            embed["image"] = {"url": img_url}

        return embed

    def notify(self, items: List[Dict[str, Any]], rule_name: str = "") -> bool:
        if not self.enabled or not self.webhook_url or not items:
            return False

        try:
            # Discord 单次最多发送 10 个 embeds
            embeds = [self._build_embed(item) for item in items[:10]]
            content = f"🎯 **【Mandarake 在库新商品提醒】** 命中规则：`{rule_name or '默认规则'}`，共发现 **{len(items)}** 件新上架："
            payload = {
                "username": "Mandarake 监控助手",
                "avatar_url": MANDARAKE_AVATAR,
                "content": content,
                "embeds": embeds
            }
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error(f"发送 Discord Webhook 异常: {e}")
            return False

    def test(self) -> bool:
        """测试 Webhook，发送一条带有示例商品大图与格式的完整测试卡片"""
        if not self.webhook_url:
            return False
        try:
            demo_item = {
                "id": "1199999999",
                "item_no": "0100999999",
                "title": "【测试演示】ボークス SDGr女の子 ロリーナ ～甘い夢の乙女～ フルセット",
                "price": 128000,
                "price_str": "128,000円(税込)",
                "shop": "中野店",
                "category_name": "大型キャストドール(スーパードルフィーなど)",
                "url": "https://order.mandarake.co.jp/",
                "img_url": "https://order.mandarake.co.jp/content/img/icon/shortcut/192.png"
            }
            embed = self._build_embed(demo_item)
            payload = {
                "username": "Mandarake 监控助手",
                "avatar_url": MANDARAKE_AVATAR,
                "content": "🔔 **【Discord Webhook 配置测试成功】**！收到此消息说明推送通道畅通，实际上架提醒效果如下：",
                "embeds": [embed]
            }
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Discord 测试异常: {e}")
            return False
