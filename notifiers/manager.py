"""
统一通知中心调度器 (Notification Manager)
精简版：仅保留云端与远程友好的 Discord Webhook 与 SMTP 邮件通知
"""
import logging
from typing import List, Dict, Any

from notifiers.base import BaseNotifier
from notifiers.discord import DiscordNotifier
from notifiers.email_notify import EmailNotifier

logger = logging.getLogger("mandarake.notifier.manager")


class NotificationManager:
    def __init__(self, config: Dict[str, Any] = None):
        self.notifiers: Dict[str, BaseNotifier] = {}
        self.reload_config(config or {})

    def reload_config(self, config: Dict[str, Any]):
        n_cfg = config.get("notifiers", {})

        # 1. Discord Webhook 通知
        dc_cfg = n_cfg.get("discord", {})
        self.notifiers["discord"] = DiscordNotifier(
            webhook_url=dc_cfg.get("webhook_url", ""),
            enabled=dc_cfg.get("enabled", False)
        )

        # 2. Email (SMTP) 邮件通知
        email_cfg = n_cfg.get("email", {})
        self.notifiers["email"] = EmailNotifier(
            smtp_host=email_cfg.get("smtp_host", ""),
            smtp_port=email_cfg.get("smtp_port", 465),
            smtp_user=email_cfg.get("smtp_user", ""),
            smtp_password=email_cfg.get("smtp_password", ""),
            receiver_email=email_cfg.get("receiver_email", ""),
            use_ssl=email_cfg.get("use_ssl", True),
            enabled=email_cfg.get("enabled", False)
        )

    def dispatch(self, items: List[Dict[str, Any]], rule_name: str = ""):
        """分发新商品通知到所有已启用的通道"""
        if not items:
            return
        for name, notifier in self.notifiers.items():
            if notifier.enabled:
                try:
                    notifier.notify(items, rule_name)
                except Exception as e:
                    logger.error(f"通知渠道 [{name}] 发送失败: {e}")

    def test_notifier(self, name: str) -> bool:
        """测试指定通知渠道"""
        if name in self.notifiers:
            return self.notifiers[name].test()
        return False
