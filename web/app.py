"""
Mandarake Web 控制台与 REST / SSE API 服务
提供现代化可视化管理看板，支持分类树筛选、海量商品阅览、后台爬虫调度、一键下单与终端实时流
"""
import os
import json
import queue
import logging
import time
import threading
from typing import Dict, Any
from flask import Flask, render_template, jsonify, request, Response

from core.categories import category_mgr, ROOT_DOLL_CODE
from core.scraper import MandarakeScraper
from core.archive_db import archive_db
from core.archiver import archiver_engine
from core.monitor import MandarakeMonitor
from core.scheduler import MandarakeScheduler
from core.terminal import terminal_mgr
from hooks.auto_order import auto_order_engine
from notifiers.manager import NotificationManager

logger = logging.getLogger("mandarake.web")


def create_app(
    monitor: MandarakeMonitor,
    scheduler: MandarakeScheduler,
    notifier_mgr: NotificationManager
) -> Flask:
    template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "templates"))
    static_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "static"))

    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    app.config["JSON_AS_ASCII"] = False
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    # 挂载终端日志拦截器
    terminal_mgr.install()

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/favicon.ico")
    def favicon():
        return app.send_static_file("favicon.svg")

    # --- 分类体系接口 ---

    @app.route("/api/categories")
    def api_categories():
        return jsonify({
            "success": True,
            "root": category_mgr.get_root_info(),
            "tree": category_mgr.get_tree(),
            "flat_list": category_mgr.get_all_categories_flat(),
            "crawlable": category_mgr.get_crawlable_categories(),
            "counts": archive_db.get_category_counts()
        })

    @app.route("/api/categories/reclassify", methods=["POST"])
    def api_categories_reclassify():
        updated = archive_db.reclassify_all_products()
        return jsonify({
            "success": True,
            "updated_count": updated,
            "message": f"智能分类规整完成！已成功将 {updated} 件商品归入精确细分子分类。"
        })

    # --- 商品查询与归档检索 ---

    @app.route("/api/products")
    def api_products():
        page = request.args.get("page", 1, type=int)
        page_size = request.args.get("page_size", 40, type=int)
        cat_code = request.args.get("category_code", "").strip() or None
        stock_param = request.args.get("is_in_stock", "").strip()
        is_in_stock = int(stock_param) if stock_param in ("0", "1") else None
        keyword = request.args.get("keyword", "").strip() or None
        exclude_keyword = request.args.get("exclude_keyword", "").strip() or None
        preset_keywords = request.args.get("preset_keywords", "").strip() or None
        preset_mode = request.args.get("preset_mode", "or").strip().lower()
        shop = request.args.get("shop", "").strip() or None
        min_price = request.args.get("min_price", type=int)
        max_price = request.args.get("max_price", type=int)
        is_new = request.args.get("is_new", type=int)
        sort_by = request.args.get("sort_by", "updated_at").strip()
        sort_order = request.args.get("sort_order", "desc").strip()

        data = archive_db.query_products(
            page=page,
            page_size=page_size,
            category_code=cat_code,
            is_in_stock=is_in_stock,
            keyword=keyword,
            exclude_keyword=exclude_keyword,
            preset_keywords=preset_keywords,
            preset_mode=preset_mode,
            shop=shop,
            min_price=min_price,
            max_price=max_price,
            is_new=is_new,
            sort_by=sort_by,
            sort_order=sort_order
        )
        return jsonify({"success": True, **data})

    @app.route("/api/status")
    @app.route("/api/stats")
    def api_stats():
        stats = archive_db.get_stats()
        crawler_status = archiver_engine.get_status()
        sched_status = scheduler.get_status()
        auth_status = {
            "is_logged_in": auto_order_engine.is_logged_in,
            "account_name": auto_order_engine.account_name,
            "dry_run": auto_order_engine.dry_run,
        }
        return jsonify({
            "success": True,
            "status": "ok",
            "stats": stats,
            "crawler": crawler_status,
            "scheduler": sched_status,
            "auth": auth_status,
            "seen_count": len(monitor.seen_ids)
        })

    @app.route("/api/archive/clear", methods=["POST"])
    def api_archive_clear():
        ok = archive_db.clear_products()
        return jsonify({"success": ok, "message": "已清空全部商品档案与进度记录" if ok else "清空失败"})

    # --- 爬虫引擎控制接口 ---

    @app.route("/api/crawler/start", methods=["POST"])
    def api_crawler_start():
        body = request.get_json(silent=True) or {}
        cat_codes = body.get("category_codes", [])
        in_stock_only = body.get("in_stock_only", False)
        max_pages = body.get("max_pages")
        keyword = body.get("keyword")

        if max_pages is not None:
            try:
                max_pages = int(max_pages)
                if max_pages <= 0:
                    max_pages = None
            except Exception:
                max_pages = None

        ok = archiver_engine.start(
            category_codes=cat_codes,
            in_stock_only=in_stock_only,
            max_pages_per_cat=max_pages,
            keyword=keyword
        )
        return jsonify({
            "success": ok,
            "message": "爬虫已成功启动！" if ok else "爬虫正在运行中，请勿重复启动",
            "status": archiver_engine.get_status()
        })

    @app.route("/api/crawler/pause", methods=["POST"])
    def api_crawler_pause():
        ok = archiver_engine.pause()
        return jsonify({"success": ok, "message": "已暂停爬取" if ok else "操作失败"})

    @app.route("/api/crawler/resume", methods=["POST"])
    def api_crawler_resume():
        ok = archiver_engine.resume()
        return jsonify({"success": ok, "message": "已恢复爬取" if ok else "操作失败"})

    @app.route("/api/crawler/stop", methods=["POST"])
    def api_crawler_stop():
        ok = archiver_engine.stop()
        return jsonify({"success": ok, "message": "已中止爬取任务" if ok else "操作失败"})

    @app.route("/api/stock/sync_daily", methods=["POST"])
    def api_stock_sync_daily():
        if scheduler.is_syncing_daily:
            return jsonify({"success": False, "message": "每日库存与售罄巡检正在后台执行中，请勿重复触发"})
        threading.Thread(target=scheduler.run_daily_stock_sync, daemon=True, name="ManualDailyStockSyncThread").start()
        return jsonify({"success": True, "message": "已成功启动细分分类最新库存与售罄巡检（后台运行中）"})

    @app.route("/api/crawler/status")
    def api_crawler_status():
        return jsonify({"success": True, "status": archiver_engine.get_status()})

    @app.route("/api/crawler/progress_list")
    def api_crawler_progress_list():
        items = archive_db.get_all_crawl_progress()
        return jsonify({"success": True, "items": items})

    # --- 帐号认证与 Session 管理 ---

    @app.route("/api/auth/status")
    def api_auth_status():
        # 若距离上次检测超过 60 秒则重新在线检验一次
        if time.time() - auto_order_engine.last_login_check > 60:
            auto_order_engine.check_login_status()
        return jsonify({
            "success": True,
            "is_logged_in": auto_order_engine.is_logged_in,
            "account_name": auto_order_engine.account_name,
            "dry_run": auto_order_engine.dry_run,
            "payment_method": auto_order_engine.payment_method
        })

    @app.route("/api/auth/login_browser", methods=["POST"])
    def api_auth_login_browser():
        res = auto_order_engine.launch_browser_login()
        return jsonify(res)

    @app.route("/api/auth/sync_session", methods=["POST"])
    def api_auth_sync_session():
        is_logged = auto_order_engine.check_login_status()
        if is_logged:
            auto_order_engine.save_session()
        return jsonify({
            "success": is_logged,
            "is_logged_in": is_logged,
            "account_name": auto_order_engine.account_name,
            "message": f"登录态有效: {auto_order_engine.account_name}" if is_logged else "未检测到有效登录态，请在浏览器中完成登录后再点击同步"
        })

    @app.route("/api/auth/save_cookies", methods=["POST"])
    def api_auth_save_cookies():
        body = request.get_json(silent=True) or {}
        cookie_str = body.get("cookie_str", "")
        ok = auto_order_engine.import_cookie_string(cookie_str)
        return jsonify({
            "success": ok,
            "is_logged_in": ok,
            "account_name": auto_order_engine.account_name,
            "message": f"Cookie 导入成功！已识别帐号: {auto_order_engine.account_name}" if ok else "Cookie 验证未通过，请检查是否包含完整会话凭据"
        })

    @app.route("/api/auth/logout", methods=["POST"])
    def api_auth_logout():
        auto_order_engine.clear_session()
        return jsonify({"success": True, "message": "已注销并清空 Session"})

    @app.route("/api/auth/toggle_dry_run", methods=["POST"])
    def api_auth_toggle_dry_run():
        body = request.get_json(silent=True) or {}
        dry_run = body.get("dry_run", True)
        auto_order_engine.dry_run = bool(dry_run)
        auto_order_engine.save_config()
        return jsonify({
            "success": True,
            "dry_run": auto_order_engine.dry_run,
            "message": "已开启 Dry-Run 仿真测试模式 (不扣款安全演练)" if auto_order_engine.dry_run else "⚠️ 已切换为【实盘下单模式】，下单将向官网提交真实购买！"
        })

    # --- 下单执行与订单记录 ---

    @app.route("/api/order/buy", methods=["POST"])
    def api_order_buy():
        body = request.get_json(silent=True) or {}
        item_id = body.get("item_id")
        title = body.get("title", "")
        price = body.get("price", 0)
        img_url = body.get("img_url", "")
        shop = body.get("shop", "")
        dry_run = body.get("dry_run")
        payment_method = body.get("payment_method")

        if not item_id:
            return jsonify({"success": False, "message": "缺少商品 ID 参数"})

        res = auto_order_engine.order_item(
            item_id=item_id,
            title=title,
            price=price,
            img_url=img_url,
            shop=shop,
            dry_run=dry_run,
            payment_method=payment_method,
            trigger_type="manual"
        )
        if res.get("success"):
            archive_db.mark_product_stock(item_id, is_in_stock=0)
        return jsonify(res)

    @app.route("/api/orders")
    def api_orders():
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)
        items = archive_db.query_orders(limit=limit, offset=offset)
        return jsonify({"success": True, "items": items})

    @app.route("/api/orders/delete", methods=["POST"])
    def api_orders_delete():
        body = request.get_json(silent=True) or {}
        order_id = body.get("order_id")
        ok = archive_db.delete_order(order_id)
        return jsonify({"success": ok})

    @app.route("/api/orders/clear", methods=["POST"])
    def api_orders_clear():
        ok = archive_db.clear_orders()
        return jsonify({"success": ok, "message": "已清空所有订单记录"})

    # --- 系统终端日志 ---

    @app.route("/api/terminal/logs")
    def api_terminal_logs():
        since_id = request.args.get("since_id", 0, type=int)
        limit = request.args.get("limit", 300, type=int)
        level = request.args.get("level", "ALL").strip()
        kw = request.args.get("keyword", "").strip()
        logs = terminal_mgr.get_logs(since_id=since_id, limit=limit, level_filter=level, keyword=kw)
        return jsonify({"success": True, "logs": logs})

    @app.route("/api/terminal/clear", methods=["POST"])
    def api_terminal_clear():
        terminal_mgr.clear()
        return jsonify({"success": True, "message": "控制台日志已清空"})

    # --- 监控与调度操作 ---

    @app.route("/api/check_now", methods=["POST"])
    @app.route("/api/monitor/check_now", methods=["POST"])
    def api_monitor_check_now():
        matches = monitor.check_once()
        return jsonify({"success": True, "matches_count": len(matches), "matches": matches})

    @app.route("/api/trigger_burst", methods=["POST"])
    @app.route("/api/monitor/trigger_burst", methods=["POST"])
    def api_monitor_trigger_burst():
        scheduler.trigger_burst(duration_minutes=3)
        return jsonify({"success": True, "message": "已启动 3 分钟高频爆发检测模式"})

    @app.route("/api/monitor/init_baseline", methods=["POST"])
    def api_monitor_init_baseline():
        monitor.init_baseline()
        return jsonify({"success": True, "message": "已将当前所有在售商品加入基准线缓存"})

    # --- 监控规则与通知测试 ---

    from core.config_helper import save_config as save_cfg_helper
    def save_config_file(cfg=None):
        target_cfg = cfg if cfg is not None else monitor.config
        return save_cfg_helper(target_cfg)

    def normalize_rules(rules_list):
        modified = False
        for idx, r in enumerate(rules_list):
            if not r.get("id"):
                r["id"] = f"rule_{int(time.time())}_{idx}"
                modified = True
            if "enabled" not in r:
                r["enabled"] = True
                modified = True
        return modified

    @app.route("/api/monitor/rules", methods=["GET"])
    def api_get_rules():
        rules = monitor.config.get("rules", [])
        if normalize_rules(rules):
            save_config_file()
        return jsonify({"success": True, "rules": rules})

    @app.route("/api/monitor/rules/save", methods=["POST"])
    def api_save_rule():
        rule_data = request.get_json(silent=True) or {}
        rules = monitor.config.get("rules", [])
        rule_id = str(rule_data.get("id") or "").strip()
        rule_name = str(rule_data.get("name") or "").strip()
        if not rule_id or rule_id in ("undefined", "null"):
            rule_id = f"rule_{int(time.time())}"
        rule_data["id"] = rule_id
        if "enabled" not in rule_data:
            rule_data["enabled"] = True

        existing_idx = next(
            (i for i, r in enumerate(rules) if (r.get("id") and str(r.get("id")) == rule_id) or (rule_name and str(r.get("name")) == rule_name)),
            None
        )
        if existing_idx is not None:
            rules[existing_idx] = rule_data
        else:
            rules.append(rule_data)

        monitor.config["rules"] = rules
        save_config_file()

        return jsonify({"success": True, "message": "监控规则已保存并生效！", "rules": rules})

    @app.route("/api/monitor/rules/toggle", methods=["POST"])
    def api_toggle_rule():
        body = request.get_json(silent=True) or {}
        rule_id = str(body.get("id") or "").strip()
        rule_name = str(body.get("name") or "").strip()
        enabled = body.get("enabled", True)

        rules = monitor.config.get("rules", [])
        found = False
        for r in rules:
            if (rule_id and rule_id not in ("undefined", "null", "") and str(r.get("id", "")) == rule_id) or \
               (rule_name and str(r.get("name", "")) == rule_name):
                r["enabled"] = bool(enabled)
                found = True
                break

        if found:
            monitor.config["rules"] = rules
            save_config_file()
            return jsonify({"success": True, "message": "规则状态已更新", "rules": rules})
        return jsonify({"success": False, "message": "未找到指定规则", "rules": rules}), 404

    @app.route("/api/monitor/rules/delete", methods=["POST"])
    def api_delete_rule():
        body = request.get_json(silent=True) or {}
        rule_id = str(body.get("id") or "").strip()
        rule_name = str(body.get("name") or "").strip()

        rules = monitor.config.get("rules", [])

        def match_rule(r):
            if rule_id and rule_id not in ("undefined", "null", ""):
                if str(r.get("id", "")) == rule_id:
                    return True
            if rule_name and str(r.get("name", "")) == rule_name:
                return True
            return False

        rules = [r for r in rules if not match_rule(r)]
        monitor.config["rules"] = rules
        save_config_file()
        return jsonify({"success": True, "message": "规则已删除", "rules": rules})

    @app.route("/api/test_notify/email", methods=["POST"])
    def api_test_notify_email():
        body = request.get_json(silent=True) or {}
        if body.get("smtp_host"):
            from notifiers.email_notify import EmailNotifier
            temp_email = EmailNotifier(
                smtp_host=body.get("smtp_host", ""),
                smtp_port=int(body.get("smtp_port", 465)),
                smtp_user=body.get("smtp_user", ""),
                smtp_password=body.get("smtp_password", ""),
                receiver_email=body.get("receiver_email", ""),
                use_ssl=body.get("use_ssl", True),
                enabled=True
            )
            try:
                temp_email.test()
                return jsonify({"success": True, "message": f"测试邮件已成功发送至 {temp_email.receiver_email}！"})
            except Exception as e:
                return jsonify({"success": False, "message": f"发送测试邮件失败: {e}"})
        else:
            try:
                ok = notifier_mgr.test_notifier("email")
                return jsonify({"success": ok, "message": "测试邮件已发送！" if ok else "邮件测试失败，请检查 SMTP 配置"})
            except Exception as e:
                return jsonify({"success": False, "message": f"发送异常: {e}"})

    @app.route("/api/test_notify/discord", methods=["POST"])
    def api_test_notify_discord():
        body = request.get_json(silent=True) or {}
        webhook = body.get("webhook_url")
        if webhook:
            from notifiers.discord import DiscordNotifier
            temp_dc = DiscordNotifier(webhook_url=webhook, enabled=True)
            ok = temp_dc.test()
            return jsonify({"success": ok, "message": "Discord 测试消息已发送！" if ok else "Discord Webhook 测试失败，请核对 Webhook 链接"})
        else:
            ok = notifier_mgr.test_notifier("discord")
            return jsonify({"success": ok, "message": "Discord 测试消息已发送！" if ok else "测试失败，请检查 Webhook 配置"})

    @app.route("/api/config", methods=["GET", "POST"])
    def api_config():
        if request.method == "POST":
            new_cfg = request.get_json(silent=True) or {}
            # 递归/字段合并保存配置
            for k, v in new_cfg.items():
                if isinstance(v, dict) and isinstance(monitor.config.get(k), dict):
                    monitor.config[k].update(v)
                else:
                    monitor.config[k] = v
            notifier_mgr.reload_config(monitor.config)
            save_config_file()
            return jsonify({"success": True, "config": monitor.config})

        rules = monitor.config.get("rules", [])
        if normalize_rules(rules):
            save_config_file()
        return jsonify({"success": True, "config": monitor.config})

    # --- 实时 SSE 广播流 ---

    @app.route("/api/events")
    def api_events():
        def event_stream():
            msg_queue = queue.Queue(maxsize=100)

            def on_log(entry):
                try:
                    msg_queue.put_nowait({"type": "log", "data": entry})
                except queue.Full:
                    pass

            def on_progress(p_status):
                try:
                    msg_queue.put_nowait({"type": "progress", "data": p_status})
                except queue.Full:
                    pass

            terminal_mgr.add_listener(on_log)
            archiver_engine.add_progress_listener(on_progress)

            try:
                # 初始发送一次进度快照
                yield f"data: {json.dumps({'type': 'progress', 'data': archiver_engine.get_status()})}\n\n"
                while True:
                    try:
                        msg = msg_queue.get(timeout=15.0)
                        yield f"data: {json.dumps(msg)}\n\n"
                    except queue.Empty:
                        # 发送保活心跳包
                        yield f": keep-alive\n\n"
            finally:
                terminal_mgr.remove_listener(on_log)
                archiver_engine.remove_progress_listener(on_progress)

        resp = Response(event_stream(), mimetype="text/event-stream")
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Accel-Buffering"] = "no"
        resp.headers["Connection"] = "keep-alive"
        return resp

    return app
