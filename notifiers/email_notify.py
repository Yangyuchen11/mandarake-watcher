"""
SMTP 邮件通知模块 (HTML 富文本图文清单)
支持标准 SMTP / SMTPS 协议 (QQ邮箱、163网易、Gmail、Outlook、企业邮箱等)
发现关键词匹配商品时向指定邮箱实时投递包含图片、价格、店铺及直达链接的 HTML 邮件
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
import logging
from typing import List, Dict, Any
from notifiers.base import BaseNotifier

logger = logging.getLogger("mandarake.notifier.email")


class EmailNotifier(BaseNotifier):
    def __init__(
        self,
        smtp_host: str = "",
        smtp_port: int = 465,
        smtp_user: str = "",
        smtp_password: str = "",
        receiver_email: str = "",
        use_ssl: bool = True,
        enabled: bool = False,
    ):
        super().__init__("email", enabled)
        self.smtp_host = smtp_host.strip()
        self.smtp_port = int(smtp_port) if smtp_port else 465
        self.smtp_user = smtp_user.strip()
        self.smtp_password = smtp_password.strip()
        self.receiver_email = receiver_email.strip() or self.smtp_user
        self.use_ssl = use_ssl

    def _build_html(self, items: List[Dict[str, Any]], rule_name: str) -> str:
        """构建现代响应式 HTML 邮件内容"""
        rows = []
        for it in items[:15]:
            img_src = it.get("img_url") or "https://order.mandarake.co.jp/content/img/icon/shortcut/192.png"
            img_tag = f'<img src="{img_src}" width="110" height="90" style="border-radius:6px;object-fit:cover;display:block;" />'
            price_val = it.get("price_str") or f"{it.get('price', 0):,}円"
            shop = it.get("shop") or "まんだらけ"
            cat = it.get("category_name") or "ドール"
            url = it.get("url") or f"https://order.mandarake.co.jp/order/detailPage/item?itemCode={it.get('id')}&lang=ja"

            rows.append(f"""
            <tr style="border-bottom:1px solid #2d3748;">
                <td style="padding:12px;text-align:center;width:120px;">{img_tag}</td>
                <td style="padding:12px;">
                    <div style="font-size:11px;color:#9ca3af;margin-bottom:4px;">{cat} · 发送店: {shop}</div>
                    <div style="font-size:14px;font-weight:bold;color:#f3f4f6;line-height:1.4;margin-bottom:6px;">{it.get('title')}</div>
                    <div style="font-size:12px;color:#9ca3af;">编号/ID: {it.get('item_no') or it.get('id')}</div>
                </td>
                <td style="padding:12px;font-size:16px;font-weight:bold;color:#ff5722;white-space:nowrap;text-align:right;">{price_val}</td>
                <td style="padding:12px;text-align:center;white-space:nowrap;width:100px;">
                    <a href="{url}" style="display:inline-block;padding:8px 16px;background:#ff5722;color:#ffffff;text-decoration:none;border-radius:6px;font-size:12px;font-weight:bold;">官网直达</a>
                </td>
            </tr>
            """)

        rows_html = "".join(rows)

        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body style="margin:0;padding:20px;background-color:#0f172a;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;">
            <div style="max-width:760px;margin:0 auto;background:#1e293b;border-radius:12px;overflow:hidden;border:1px solid #334155;">
                <div style="background:linear-gradient(135deg, #1e1e2f 0%, #0f172a 100%);color:#fff;padding:24px 28px;border-bottom:2px solid #ff5722;">
                    <h2 style="margin:0;font-size:20px;display:flex;align-items:center;color:#fff;">
                        <span style="color:#ff5722;margin-right:8px;">★</span> Mandarake 【ドール】 上新通知
                    </h2>
                    <div style="font-size:13px;color:#94a3b8;margin-top:6px;">
                        命中规则: <strong style="color:#f8fafc;">{rule_name or '默认规则'}</strong> | 发现 <strong style="color:#ff5722;">{len(items)}</strong> 件在库有货新商品
                    </div>
                </div>
                <table style="width:100%;border-collapse:collapse;">
                    <thead>
                        <tr style="background:#0f172a;color:#94a3b8;font-size:12px;border-bottom:1px solid #334155;">
                            <th style="padding:10px;text-align:center;">商品图</th>
                            <th style="padding:10px;text-align:left;">商品详情</th>
                            <th style="padding:10px;text-align:right;">售价</th>
                            <th style="padding:10px;text-align:center;">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>
                <div style="padding:16px 24px;background:#0f172a;color:#64748b;font-size:11px;text-align:center;border-top:1px solid #334155;">
                    此邮件由 Mandarake 【ドール】 智能监控机器人自动发送 · 定点监控与高速发现
                </div>
            </div>
        </body>
        </html>
        """
        return html

    def notify(self, items: List[Dict[str, Any]], rule_name: str = "") -> bool:
        if not self.enabled or not self.smtp_host or not self.smtp_user or not items:
            return False

        try:
            msg = MIMEMultipart("alternative")
            subject_str = f"【Mandarake上新】{rule_name or '目标商品'} 发现 {len(items)} 件新上架有货！"
            msg["Subject"] = Header(subject_str, "utf-8")
            msg["From"] = f"Mandarake Monitor <{self.smtp_user}>"
            msg["To"] = self.receiver_email

            html_content = self._build_html(items, rule_name)
            msg.attach(MIMEText(html_content, "html", "utf-8"))

            if self.use_ssl or self.smtp_port == 465:
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=12)
            else:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=12)
                server.starttls()

            server.login(self.smtp_user, self.smtp_password)
            server.sendmail(self.smtp_user, [self.receiver_email], msg.as_string())
            server.quit()
            logger.info(f"邮件通知已成功送达至: {self.receiver_email} (命中规则: {rule_name}, 商品数: {len(items)})")
            return True
        except Exception as e:
            logger.error(f"发送邮件通知失败: {e}")
            return False

    def test(self) -> bool:
        if not self.smtp_host or not self.smtp_user or not self.smtp_password:
            logger.warning("SMTP 配置未完整填写，无法测试邮件")
            return False
        try:
            msg = MIMEText("这是一封来自 Mandarake 【ドール】 监控机器人的测试邮件。如果您收到此邮件，说明 SMTP 邮件通道配置完全正确！", "plain", "utf-8")
            msg["Subject"] = Header("【Mandarake 监控系统】SMTP 邮件通知通道测试成功", "utf-8")
            msg["From"] = f"Mandarake Monitor <{self.smtp_user}>"
            msg["To"] = self.receiver_email

            if self.use_ssl or self.smtp_port == 465:
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=10)
            else:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10)
                server.starttls()

            server.login(self.smtp_user, self.smtp_password)
            server.sendmail(self.smtp_user, [self.receiver_email], msg.as_string())
            server.quit()
            logger.info(f"测试邮件已成功发送至: {self.receiver_email}")
            return True
        except Exception as e:
            logger.error(f"测试邮件发送失败: {e}")
            raise e
