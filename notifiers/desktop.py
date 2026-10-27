"""
Windows 桌面原生气泡与横幅通知模块
点击通知可直接在浏览器中打开 Mandarake 商品详情页
"""
import sys
import html
import subprocess
import threading
import logging
from typing import List, Dict, Any
from notifiers.base import BaseNotifier

logger = logging.getLogger("mandarake.notifier.desktop")


class DesktopNotifier(BaseNotifier):
    def __init__(self, enabled: bool = True):
        super().__init__("desktop", enabled)

    def _show_windows_toast(self, title: str, message: str, url: str):
        if sys.platform != "win32":
            return

        clean_title = html.escape(title[:60])
        clean_msg = html.escape(message[:120])
        safe_url = html.escape(url)

        ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null

$template = @"
<toast activationType="protocol" launch="{safe_url}">
    <visual>
        <binding template="ToastGeneric">
            <text>{clean_title}</text>
            <text>{clean_msg}</text>
        </binding>
    </visual>
</toast>
"@

$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Mandarake").Show($toast)
"""
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                timeout=5,
                check=False
            )
        except Exception as e:
            logger.debug(f"发送 Windows Toast 弹窗通知异常: {e}")

    def notify(self, items: List[Dict[str, Any]], rule_name: str = "") -> bool:
        if not self.enabled or not items:
            return False

        first = items[0]
        title = f"【Mandarake 在库上新】{rule_name or '发现目标商品'}"
        msg = f"{first.get('title', '')}\n价格: {first.get('price_str', '')} | 店铺: {first.get('shop', '')}"
        if len(items) > 1:
            msg += f"\n(等共计 {len(items)} 件)"

        url = first.get("url", "https://order.mandarake.co.jp/")
        threading.Thread(target=self._show_windows_toast, args=(title, msg, url), daemon=True).start()
        return True

    def test(self) -> bool:
        t = "【Mandarake 测试通知】"
        m = "桌面通知连接正常！发现新商品时将在此弹出提醒。"
        u = "https://order.mandarake.co.jp/"
        threading.Thread(target=self._show_windows_toast, args=(t, m, u), daemon=True).start()
        return True
