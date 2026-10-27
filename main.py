"""
Mandarake 【ドール】 专区商品爬虫与自动下单系统 - 主程序入口
支持：
- Web 可视化控制看板 (默认): python main.py
- 单次即时检测: python main.py --check
- CLI 纯终端后台监控: python main.py --cli
- 全自动分类抓取: python main.py --crawl
"""
import os
import sys
import json
import time
import argparse
import webbrowser
import threading
import logging

from core.categories import category_mgr, ROOT_DOLL_CODE
from core.scraper import MandarakeScraper
from core.archive_db import archive_db
from core.archiver import archiver_engine
from core.monitor import MandarakeMonitor
from core.scheduler import MandarakeScheduler
from core.terminal import terminal_mgr
from notifiers.manager import NotificationManager
from hooks.auto_order import auto_order_engine
from web.app import create_app

# 配置基础控制台日志与 UTF-8 输出支持
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S"
)
class _QuietPollFilter(logging.Filter):
    # 屏蔽前端定时轮询 /api/stats 产生的 werkzeug 访问日志，避免刷屏
    def filter(self, record):
        return '/api/stats' not in record.getMessage()

logging.getLogger('werkzeug').addFilter(_QuietPollFilter())
logger = logging.getLogger("mandarake.main")

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__))), "config.json"


def load_config() -> dict:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "config.json")
    example_path = os.path.join(base_dir, "config.example.json")
    if not os.path.exists(config_path) and os.path.exists(example_path):
        import shutil
        try:
            shutil.copyfile(example_path, config_path)
            logger.info("未检测到 config.json，已自动从 config.example.json 初始化配置文件。")
        except Exception as e:
            logger.warning(f"初始化 config.json 失败: {e}")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"读取 config.json 失败: {e}")
    return {}


def main():
    default_host = os.environ.get("HOST", "0.0.0.0" if os.environ.get("IN_DOCKER") else "127.0.0.1")
    default_port = int(os.environ.get("PORT", 5005))

    parser = argparse.ArgumentParser(description="Mandarake 【ドール】 爬虫与自动下单系统")
    parser.add_argument("--host", default=default_host, help=f"Web 监听主机地址 (默认 {default_host})")
    parser.add_argument("--port", type=int, default=default_port, help=f"Web 端口号 (默认 {default_port})")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开默认浏览器")
    parser.add_argument("--check", action="store_true", help="单次执行规则检测后退出")
    parser.add_argument("--crawl", action="store_true", help="直接在终端启动全量分类抓取并入库")
    parser.add_argument("--cli", action="store_true", help="纯终端监控模式 (不启动 Web UI)")
    args = parser.parse_args()

    config = load_config()
    if args.port == 5005 and "port" in config:
        try:
            args.port = int(config["port"])
        except Exception:
            pass

    # 初始化通知中心与监控调度器
    notifier_mgr = NotificationManager(config)
    monitor = MandarakeMonitor(config, notifier_mgr)
    scheduler = MandarakeScheduler(monitor)

    # 1. 单次检测模式
    if args.check:
        logger.info("执行单次 Mandarake 【ドール】 在库与上新检测...")
        matches = monitor.check_once()
        print(f"\n==========================================")
        print(f"检测完成！命中在库新商品数: {len(matches)}")
        for idx, item in enumerate(matches, 1):
            print(f"[{idx}] {item.get('title')} | {item.get('price_str')} | 店铺: {item.get('shop')}")
            print(f"     链接: {item.get('url')}")
        print(f"==========================================\n")
        return

    # 2. 纯终端分类爬虫模式
    if args.crawl:
        logger.info("启动 CLI 全分类批量抓取模式...")
        archiver_engine.start(in_stock_only=True)
        try:
            while archiver_engine.is_running:
                st = archiver_engine.get_status()
                logger.info(f"进度: {st.get('status_message')} (已抓取: {st.get('total_crawled_count')} 件)")
                time.sleep(3.0)
        except KeyboardInterrupt:
            archiver_engine.stop()
        logger.info("抓取完毕！")
        return

    # 3. 纯终端后台监控模式
    if args.cli:
        logger.info("启动 CLI 纯终端后台监控模式 (Ctrl+C 退出)...")
        scheduler.start()
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            scheduler.stop()
        return

    # 4. Web 控制看板模式 (默认)
    # 挂载终端日志拦截
    terminal_mgr.install()

    # 启动后台监控调度
    scheduler.start()

    app = create_app(monitor, scheduler, notifier_mgr)

    # 自动打开浏览器
    if not args.no_browser and not os.environ.get("IN_DOCKER") and not os.environ.get("NO_BROWSER"):
        def open_browser():
            time.sleep(1.2)
            url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
            logger.info(f"正在自动在浏览器中打开 Web 看板: {url}")
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=open_browser, daemon=True).start()

    banner = f"""
========================================================================
       MANDARAKE 【ドール】 专区商品爬虫与自动下单控制看板
========================================================================
 * 运行环境: Python {sys.version.split()[0]}
 * 本地访问: http://{args.host}:{args.port}
 * 目标大类: 020114 (ドール 全下属分类深度覆盖)
 * 归档数据库: data/archive.db (SQLite WAL 高速模式)
 * 运行模式: {'🛡️ Dry-Run 仿真测试模式 (安全拦截扣款)' if auto_order_engine.dry_run else '⚡ 实盘下单模式'}
========================================================================
"""
    print(banner)

    try:
        app.run(host=args.host, port=args.port, debug=False, threaded=True)
    except KeyboardInterrupt:
        logger.info("收到退出信号，正在安全停止服务...")
    except Exception as e:
        logger.error(f"Web 控制台服务异常退出: {e}", exc_info=True)
    finally:
        scheduler.stop()
        archiver_engine.stop()


if __name__ == "__main__":
    main()
