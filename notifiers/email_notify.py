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
        """构建现代响应式 HTML 邮件内容 (移动端 & 桌面端双端完美适配)"""
        cards = []
        for it in items[:20]:
            img_src = it.get("img_url") or "https://order.mandarake.co.jp/content/img/icon/shortcut/192.png"
            price_val = it.get("price_str") or f"{int(it.get('price') or 0):,}円"
            shop = it.get("shop") or "まんだらけ"
            cat = it.get("category_name") or "ドール"
            url = it.get("url") or f"https://order.mandarake.co.jp/order/detailPage/item?itemCode={it.get('id')}&lang=ja"
            item_id = it.get("item_no") or it.get("id")
            title = it.get("title") or "未知商品"
            is_soldout = it.get("instant_soldout") or (int(it.get("is_in_stock", 1)) == 0)
            is_restock = bool(it.get("is_restock"))

            if is_soldout:
                status_tag = '<span style="display:inline-block;padding:2px 6px;background:#ef4444;color:#ffffff;border-radius:4px;font-size:10px;font-weight:bold;margin-left:4px;vertical-align:middle;">⚡ 秒切已售罄</span>'
                btn_style = "background:#475569;color:#e2e8f0;border:1px solid #64748b"
                btn_text = "查看详情"
            elif is_restock:
                status_tag = '<span style="display:inline-block;padding:2px 6px;background:#2563eb;color:#ffffff;border-radius:4px;font-size:10px;font-weight:bold;margin-left:4px;vertical-align:middle;">🔄 补货在库</span>'
                btn_style = "background:#2563eb;color:#ffffff"
                btn_text = "官网抢购"
            else:
                status_tag = '<span style="display:inline-block;padding:2px 6px;background:#059669;color:#ffffff;border-radius:4px;font-size:10px;font-weight:bold;margin-left:4px;vertical-align:middle;">🟢 在库有货</span>'
                btn_style = "background:#ff5722;color:#ffffff"
                btn_text = "官网抢购"

            # 采用 2 列表格/卡片布局：左侧商品图，右侧信息 + 价格与直达按钮
            # 杜绝多列表格在手机小屏幕下被挤爆、右侧价格与按钮出界无法点击的问题
            cards.append(f"""
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border-bottom:1px solid #334155;background:#1e293b;">
                <tr>
                    <td valign="top" class="item-img-cell" style="padding:12px 0 12px 12px;width:96px;min-width:90px;max-width:104px;text-align:center;">
                        <a href="{url}" target="_blank" style="text-decoration:none;display:inline-block;">
                            <img src="{img_src}" class="item-img" width="90" height="90" style="width:90px;height:90px;border-radius:8px;object-fit:cover;display:block;border:1px solid #475569;" alt="商品图" />
                        </a>
                    </td>
                    <td valign="top" class="item-info-cell" style="padding:12px 14px 12px 12px;">
                        <div style="font-size:11px;color:#94a3b8;line-height:1.4;margin-bottom:4px;">
                            <span>{cat}</span> · <span>发送店: {shop}</span>{status_tag}
                        </div>
                        <div style="margin-bottom:8px;">
                            <a href="{url}" target="_blank" style="font-size:14px;font-weight:600;color:#f8fafc;text-decoration:none;line-height:1.4;display:block;">
                                {title}
                            </a>
                        </div>
                        <!-- 底部价格与按钮行：全宽自适应，手机端也能一览无余 -->
                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-top:6px;">
                            <tr>
                                <td valign="middle" align="left">
                                    <div class="price-val" style="font-size:16px;font-weight:bold;color:#ff5722;line-height:1.2;">{price_val}</div>
                                    <div style="font-size:11px;color:#64748b;margin-top:2px;">ID: {item_id}</div>
                                </td>
                                <td valign="middle" align="right" style="white-space:nowrap;padding-left:8px;">
                                    <a href="{url}" target="_blank" class="action-btn" style="display:inline-block;padding:8px 16px;{btn_style};text-decoration:none;border-radius:6px;font-size:12px;font-weight:bold;text-align:center;box-shadow:0 2px 4px rgba(0,0,0,0.25);">
                                        {btn_text} →
                                    </a>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
            """)

        cards_html = "".join(cards)

        in_stock_cnt = sum(1 for it in items if int(it.get("is_in_stock", 1)) == 1 and not it.get("instant_soldout"))
        soldout_cnt = len(items) - in_stock_cnt

        stat_summary_html = f"发现 <strong style='color:#ff5722;'>{len(items)}</strong> 件新商品"
        if in_stock_cnt > 0 and soldout_cnt > 0:
            stat_summary_html += f" (<strong style='color:#10b981;'>{in_stock_cnt} 在库</strong> · <strong style='color:#ef4444;'>{soldout_cnt} 秒切已售</strong>)"
        elif soldout_cnt > 0:
            stat_summary_html += f" (<strong style='color:#ef4444;'>全部 {soldout_cnt} 件已秒切售罄</strong>)"

        more_notice_html = ""
        if len(items) > 20:
            more_notice_html = f"""
            <div style="padding:12px;text-align:center;color:#94a3b8;font-size:12px;background:#1e293b;border-bottom:1px solid #334155;">
                ⚡ 本次共捕获 <strong>{len(items)}</strong> 件商品，为保证邮件加载与排版流畅，以上仅展示前 20 件。更多商品请前往控制台或 Mandarake 官网查看。
            </div>
            """

        html = f"""
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
            <meta http-equiv="X-UA-Compatible" content="IE=edge">
            <title>Mandarake 上新通知</title>
            <style type="text/css">
                body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
                table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
                img {{ -ms-interpolation-mode: bicubic; }}
                body {{ margin: 0; padding: 0; width: 100% !important; min-width: 100%; }}
                @media only screen and (max-width: 480px) {{
                    .email-wrapper {{ padding: 6px 4px !important; }}
                    .email-container {{ border-radius: 8px !important; width: 100% !important; }}
                    .header-box {{ padding: 16px 14px !important; }}
                    .header-title {{ font-size: 17px !important; }}
                    .item-img {{ width: 80px !important; height: 80px !important; }}
                    .item-img-cell {{ width: 84px !important; min-width: 84px !important; padding: 10px 0 10px 8px !important; }}
                    .item-info-cell {{ padding: 10px 10px 10px 8px !important; }}
                    .price-val {{ font-size: 15px !important; }}
                    .action-btn {{ padding: 7px 12px !important; font-size: 11px !important; }}
                }}
            </style>
        </head>
        <body style="margin:0;padding:0;background-color:#0b1120;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
            <div class="email-wrapper" style="padding:20px 10px;background-color:#0b1120;">
                <div class="email-container" style="max-width:680px;margin:0 auto;background:#1e293b;border-radius:12px;overflow:hidden;border:1px solid #334155;box-shadow:0 8px 24px rgba(0,0,0,0.35);">
                    <!-- 头部标题栏 -->
                    <div class="header-box" style="background:linear-gradient(135deg, #1e1e2f 0%, #0f172a 100%);color:#fff;padding:20px 24px;border-bottom:2px solid #ff5722;">
                        <h2 class="header-title" style="margin:0;font-size:19px;display:flex;align-items:center;color:#ffffff;line-height:1.3;">
                            <span style="color:#ff5722;margin-right:8px;font-size:20px;">★</span> Mandarake 【ドール】 上新通知
                        </h2>
                        <div style="font-size:13px;color:#94a3b8;margin-top:6px;line-height:1.4;">
                            命中规则: <strong style="color:#f8fafc;">{rule_name or '默认规则'}</strong> | {stat_summary_html}
                        </div>
                    </div>

                    <!-- 商品卡片列表 (2 列流式布局) -->
                    <div style="width:100%;">
                        {cards_html}
                        {more_notice_html}
                    </div>

                    <!-- 底部说明栏 -->
                    <div style="padding:16px 20px;background:#0f172a;color:#64748b;font-size:11px;text-align:center;line-height:1.5;">
                        此邮件由 Mandarake 【ドール】 智能监控机器人自动发送 · 定点监控与高速发现
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        return html

    def _create_smtp_session(self, timeout: int = 12) -> smtplib.SMTP:
        """
        智能建立 SMTP 连接，自动适配 SMTPS (SSL) 与 STARTTLS 协议。
        彻底解决因端口与加密协议错配导致的 [SSL: WRONG_VERSION_NUMBER] 异常。
        """
        import ssl
        port = int(self.smtp_port)

        # 1. 若端口为 587 或 25，协议规范为 STARTTLS（明文握手后升级加密），直接使用 SMTP + starttls
        if port in (587, 25):
            server = smtplib.SMTP(self.smtp_host, port, timeout=timeout)
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except Exception as e:
                logger.debug(f"STARTTLS 升级跳过或失败 (端口 {port}): {e}")
            return server

        # 2. 若勾选了 SSL 或端口为 465，优先尝试 SMTP_SSL
        if self.use_ssl or port == 465:
            try:
                server = smtplib.SMTP_SSL(self.smtp_host, port, timeout=timeout)
                return server
            except (ssl.SSLError, smtplib.SMTPConnectError, OSError) as e:
                logger.warning(f"SMTP_SSL 连接失败 ({e})，正在自动尝试平滑降级为 STARTTLS 模式重试...")
                server = smtplib.SMTP(self.smtp_host, port, timeout=timeout)
                server.ehlo()
                try:
                    server.starttls()
                    server.ehlo()
                except Exception:
                    pass
                return server

        # 3. 其它默认明文/STARTTLS 模式
        try:
            server = smtplib.SMTP(self.smtp_host, port, timeout=timeout)
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except Exception:
                pass
            return server
        except (ssl.SSLError, smtplib.SMTPConnectError, OSError):
            return smtplib.SMTP_SSL(self.smtp_host, port, timeout=timeout)

    def notify(self, items: List[Dict[str, Any]], rule_name: str = "") -> bool:
        if not self.enabled or not self.smtp_host or not self.smtp_user or not items:
            return False

        try:
            in_stock_cnt = sum(1 for it in items if int(it.get("is_in_stock", 1)) == 1 and not it.get("instant_soldout"))
            soldout_cnt = len(items) - in_stock_cnt

            if in_stock_cnt > 0 and soldout_cnt > 0:
                subject_str = f"【Mandarake上新】{rule_name or '目标商品'} 在库 {in_stock_cnt} 件 / 秒切 {soldout_cnt} 件！"
            elif in_stock_cnt > 0:
                subject_str = f"【Mandarake上新】{rule_name or '目标商品'} 发现 {in_stock_cnt} 件新上架有货！"
            else:
                subject_str = f"【Mandarake秒切】{rule_name or '目标商品'} 发现 {soldout_cnt} 件上新已售罄商品！"

            msg = MIMEMultipart("alternative")
            msg["Subject"] = Header(subject_str, "utf-8")
            msg["From"] = f"Mandarake Monitor <{self.smtp_user}>"
            msg["To"] = self.receiver_email

            html_content = self._build_html(items, rule_name)
            msg.attach(MIMEText(html_content, "html", "utf-8"))

            server = self._create_smtp_session(timeout=12)
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

            server = self._create_smtp_session(timeout=10)
            server.login(self.smtp_user, self.smtp_password)
            server.sendmail(self.smtp_user, [self.receiver_email], msg.as_string())
            server.quit()
            logger.info(f"测试邮件已成功发送至: {self.receiver_email}")
            return True
        except Exception as e:
            logger.error(f"测试邮件发送失败: {e}")
            raise e
