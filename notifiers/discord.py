"""
Discord Webhook 富文本 Embed 通知模块
"""
import time
import logging
from typing import List, Dict, Any
import requests
from notifiers.base import BaseNotifier

logger = logging.getLogger("mandarake.notifier.discord")


class DiscordNotifier(BaseNotifier):
    def __init__(self, webhook_url: str = "", enabled: bool = False):
        super().__init__("discord", enabled)
        self.webhook_url = webhook_url.strip()

    def notify(self, items: List[Dict[str, Any]], rule_name: str = "") -> bool:
        if not self.enabled or not self.webhook_url or not items:
            return False

        try:
            embeds = []
            for item in items[:10]:
                title = item.get("title", "未命名商品")
                price = item.get("price_str") or f"{item.get('price', 0):,}円"
                shop = item.get("shop", "")
                cat = item.get("category_name", "")
                url = item.get("url", "")
                img_url = item.get("img_url", "")

                embed = {
                    "title": title[:250],
                    "url": url,
                    "color": 0xFF5722,  # Mandarake 醒目橙红色
                    "fields": [
                        {"name": "💰 价格", "value": price, "inline": True},
                        {"name": "🏬 发送店舗", "value": shop or "未标明", "inline": True},
                        {"name": "📂 类别", "value": cat or "ドール", "inline": True},
                    ],
                    "footer": {"text": "Mandarake Doll Monitor Bot"},
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                if img_url:
                    embed["thumbnail"] = {"url": img_url}
                embeds.append(embed)

            content = f"🎯 **【Mandarake 在库新商品提醒】** 命中规则：`{rule_name or '默认规则'}`，共发现 {len(items)} 件："
            payload = {
                "username": "Mandarake Bot",
                "content": content,
                "embeds": embeds
            }
            resp = requests.post(self.webhook_url, json=payload, timeout=8)
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error(f"发送 Discord Webhook 异常: {e}")
            return False

    def test(self) -> bool:
        if not self.webhook_url:
            return False
        try:
            payload = {
                "username": "Mandarake Bot",
                "content": "🔔 **Mandarake 通知测试**：Discord Webhook 通道测试成功！"
            }
            resp = requests.post(self.webhook_url, json=payload, timeout=8)
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Discord 测试异常: {e}")
            return False
