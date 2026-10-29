"""
Discord Webhook 富文本 Embed 通知模块
专为 Mandarake 【ドール】 监控定制，提供大图预览、官网直达超链接与核心参数排版
内置多商品智能安全分批（防止突破 Discord 6000 字符限制）、429 限流退避重试与合法性校验
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

    def _build_embed(self, item: Dict[str, Any]) -> Dict[str, Any]:
        title = str(item.get("title") or "未命名商品").strip()
        price = item.get("price_str") or f"{int(item.get('price') or 0):,}円"
        shop = str(item.get("shop") or "まんだらけ").strip()
        cat = str(item.get("category_name") or "ドール").strip()
        url = str(item.get("url") or "https://order.mandarake.co.jp/").strip()
        img_url = str(item.get("img_url") or "").strip()
        item_id = str(item.get("item_no") or item.get("id") or "").strip()
        is_soldout = item.get("instant_soldout") or (int(item.get("is_in_stock", 1)) == 0)
        is_restock = bool(item.get("is_restock"))

        if is_soldout:
            embed_title = f"⚡ [已秒切] {title[:190]}"
            embed_desc = f"🔴 **上新即秒切 (已售罄)** · [👉 查看 Mandarake 官网详情]({url})"
            color = 0xEF4444  # 醒目红
            status_text = "❌ 已售罄 (检测前已被买走)"
        elif is_restock:
            embed_title = f"🔄 [补货复活] {title[:190]}"
            embed_desc = f"🟢 **此前售罄商品重新在库** · [👉 点击进入 Mandarake 官网抢购]({url})"
            color = 0x3B82F6  # 蓝色 (补货)
            status_text = "🔄 重新在库"
        else:
            embed_title = f"🛒 {title[:190]}"
            embed_desc = f"🟢 **在库有货** · [👉 点击进入 Mandarake 官网购买]({url})"
            color = 0xFF5722  # Mandarake 品牌醒目橙红
            status_text = "🟢 在库有货"

        embed = {
            "title": embed_title,
            "url": url,
            "description": embed_desc,
            "color": color,
            "fields": [
                {"name": "💰 价格 (税込)", "value": f"**{price}**", "inline": True},
                {"name": "🏬 发送店舗", "value": shop or "未标明", "inline": True},
                {"name": "📦 当前状态", "value": status_text, "inline": True},
                {"name": "📂 分类目录", "value": cat or "ドール", "inline": True},
            ],
            "footer": {
                "text": f"商品编号: {item_id} · Mandarake Doll Monitor",
            },
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        # 仅当图片链接为标准 http/https 链接且无空格时附带大图，避免非法 URL 导致整个 Embed 报 400
        if img_url and (img_url.startswith("http://") or img_url.startswith("https://")) and " " not in img_url:
            embed["image"] = {"url": img_url}

        return embed

    def _send_payload(self, payload: Dict[str, Any], max_retries: int = 2) -> bool:
        """发送单批次 Payload 给 Discord Webhook，带 429 自动退避与详细错误日志"""
        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(self.webhook_url, json=payload, timeout=12)
                if resp.status_code in (200, 204):
                    return True

                # 应对 429 限流
                if resp.status_code == 429:
                    retry_after = 1.0
                    try:
                        retry_after = float(resp.json().get("retry_after", 1.0))
                    except Exception:
                        pass
                    logger.warning(
                        f"Discord Webhook 触发限流 (429)，等待 {retry_after:.2f}s 后重试 ({attempt + 1}/{max_retries + 1})..."
                    )
                    time.sleep(retry_after)
                    continue

                logger.error(f"Discord Webhook 响应非预期状态码 [HTTP {resp.status_code}]: {resp.text}")
                return False
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(1.0)
                    continue
                logger.error(f"发送 Discord Webhook 网络异常: {e}")
                return False
        return False

    def notify(self, items: List[Dict[str, Any]], rule_name: str = "") -> bool:
        if not self.enabled or not self.webhook_url or not items:
            return False

        try:
            # 1. 构建所有合法 Embed
            all_embeds = []
            for item in items:
                try:
                    emb = self._build_embed(item)
                    if emb:
                        all_embeds.append(emb)
                except Exception as e:
                    logger.warning(f"构建单件商品 Discord Embed 异常: {e}")

            if not all_embeds:
                return False

            # 2. 智能分批：每批最多 5 个 Embed，且总字符数严格限制在 5000 以内（彻底杜绝 Discord 6000 字符超限 400 报错）
            batches: List[List[Dict[str, Any]]] = []
            current_batch: List[Dict[str, Any]] = []
            current_chars = 0

            for emb in all_embeds:
                emb_chars = len(emb.get("title", "")) + len(emb.get("description", ""))
                emb_chars += sum(len(f.get("name", "")) + len(f.get("value", "")) for f in emb.get("fields", []))
                emb_chars += len(emb.get("footer", {}).get("text", ""))

                if len(current_batch) >= 5 or (current_chars + emb_chars) > 5000:
                    batches.append(current_batch)
                    current_batch = [emb]
                    current_chars = emb_chars
                else:
                    current_batch.append(emb)
                    current_chars += emb_chars

            if current_batch:
                batches.append(current_batch)

            total_batches = len(batches)
            all_success = True

            in_stock_cnt = sum(1 for it in items if int(it.get("is_in_stock", 1)) == 1 and not it.get("instant_soldout"))
            soldout_cnt = len(items) - in_stock_cnt
            stat_parts = []
            if in_stock_cnt > 0:
                stat_parts.append(f"🟢 在库 {in_stock_cnt} 件")
            if soldout_cnt > 0:
                stat_parts.append(f"🔴 秒切 {soldout_cnt} 件")
            stat_summary = f" ({', '.join(stat_parts)})" if stat_parts else ""

            for idx, batch_embeds in enumerate(batches, 1):
                batch_str = f" (批次 {idx}/{total_batches})" if total_batches > 1 else ""
                content = (
                    f"🎯 **【Mandarake 上新提醒】** 命中规则：`{rule_name or '默认规则'}`"
                    f"{stat_summary}{batch_str}："
                )

                payload = {
                    "username": "Mandarake 监控助手",
                    "content": content,
                    "embeds": batch_embeds,
                }

                ok = self._send_payload(payload)
                if not ok:
                    all_success = False

                # 批次间间隔 0.6 秒，平滑防止触发 Discord Webhook 频控
                if idx < total_batches:
                    time.sleep(0.6)

            return all_success
        except Exception as e:
            logger.error(f"处理 Discord 通知分发异常: {e}")
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
                "img_url": "https://img.mandarake.co.jp/shopimg/00/00/655/408/s_0000655408_1.jpg",
            }
            embed = self._build_embed(demo_item)
            payload = {
                "username": "Mandarake 监控助手",
                "content": "🔔 **【Discord Webhook 配置测试成功】**！收到此消息说明推送通道畅通，实际上架提醒效果如下：",
                "embeds": [embed],
            }
            return self._send_payload(payload)
        except Exception as e:
            logger.error(f"Discord 测试异常: {e}")
            return False
