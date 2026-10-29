"""
Mandarake 数据库官方分类全量纠偏与修复脚本
用于云端或本地一键修复历史商品分类污染，将全部商品 100% 对齐官网原始 categoryCode。
使用方法：
    本地: python reclassify.py
    Docker: docker compose exec mandarake-watcher python reclassify.py
"""
import sys
import time
import io

# 强制 UTF-8 控制台输出
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from core.archive_db import archive_db

def main():
    print("=" * 60)
    print("🚀 开始执行 Mandarake 商品数据库官方分类全量纠偏与修复...")
    print("=" * 60)

    t0 = time.time()
    try:
        updated_count = archive_db.reclassify_all_products()
        t1 = time.time()

        counts = archive_db.get_category_counts()
        print("\n" + "=" * 60)
        print(f"✅ 修复完成！耗时: {t1 - t0:.2f} 秒")
        print(f"📦 成功纠偏商品数量: {updated_count} 件")
        print("-" * 60)
        print("📊 核心分类当前最新商品数：")
        print(f"  · 大型キャストドール (02011403, 树脂整娃): {counts.get('02011403', 0)} 件")
        print(f"  · 大型ソフビドール   (02011404, 软胶整娃): {counts.get('02011404', 0)} 件")
        print(f"  · ドール衣装         (02011431, 娃衣配件): {counts.get('02011431', 0)} 件")
        print(f"  · ドール用小物・家具 (02011434, 小物家具): {counts.get('02011434', 0)} 件")
        print("=" * 60)
    except Exception as e:
        print(f"❌ 修复过程中发生异常: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
