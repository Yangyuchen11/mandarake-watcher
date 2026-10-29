"""
Mandarake 【ドール】(Doll) 分类体系管理模块
封装大分类 020114 及其所有二级、三级子分类，提供树状结构解析与检索
"""
import os
import json
import logging
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("mandarake.categories")

ROOT_DOLL_CODE = "020114"
ROOT_DOLL_NAME = "ドール"

# 默认内置完整分类树，避免因外部文件缺失导致不可用
DEFAULT_DOLL_TREE = {
    "02011401": {
        "name": "書籍・雑誌",
        "children": {}
    },
    "02011402": {
        "name": "ひめかずら(ドールウィッグ)",
        "children": {
            "0201140201": "LLサイズ(10-11inch)",
            "0201140202": "Lサイズ(9-9.5inch)",
            "0201140203": "Mサイズ(8-8.5inch)",
            "0201140204": "MSサイズ(7-7.5inch)",
            "0201140205": "Sサイズ(6-7inch)",
            "0201140206": "5inch",
            "0201140207": "4.5inch",
            "0201140211": "スタイル系統：ショート",
            "0201140212": "スタイル系統：ロング",
            "0201140213": "スタイル系統：セミロング",
            "0201140214": "スタイル系統：ツーテール・ポニーテール",
            "0201140219": "スタイル系統：その他アレンジ系",
            "0201140221": "カラー系統：ブラウン・オレンジ・レッド",
            "0201140222": "カラー系統：ピンク・パープル",
            "0201140223": "カラー系統：ブロンド・イエロー",
            "0201140224": "カラー系統：ブラック・ホワイト・シルバー",
            "0201140225": "カラー系統：ブルー・グリーン",
            "0201140231": "バンス(つけ毛)",
            "0201140232": "ウェフティング(植毛用の毛束)"
        }
    },
    "02011403": {
        "name": "大型キャストドール(スーパードルフィーなど)",
        "children": {
            "0201140301": "60cm以上(SDなど)",
            "0201140302": "40cmサイズ(MSDなど)",
            "0201140303": "30cmサイズ(幼SDなど)",
            "0201140304": "30cm未満"
        }
    },
    "02011404": {
        "name": "大型ソフビドール(ドルフィードリームなど)",
        "children": {
            "0201140401": "60cm以上(DDなど)",
            "0201140402": "40cm～サイズ(MDD・オビツ50など)",
            "0201140499": "その他"
        }
    },
    "02011405": {
        "name": "1/6～1/12ドール(～30cm)",
        "children": {
            "0201140501": "ネオブライス",
            "0201140502": "ミディブライス",
            "0201140503": "プチブライス",
            "0201140504": "リカちゃん",
            "0201140505": "ジェニー",
            "0201140506": "バービー",
            "0201140507": "プーリップ",
            "0201140508": "momokoDOLL",
            "0201140509": "ピュアニーモ",
            "0201140510": "リビングデッドドールズ",
            "0201140511": "ベッツィー",
            "0201140512": "ワンダーフロッグ",
            "0201140599": "その他"
        }
    },
    "02011406": {
        "name": "創作人形",
        "children": {}
    },
    "02011429": {
        "name": "その他ドール",
        "children": {}
    },
    "02011431": {
        "name": "ドール衣装",
        "children": {
            "0201143101": "60cm以上(SD･DDなど)",
            "0201143102": "40～50cmサイズ(MSD・オビツ50など)",
            "0201143103": "30cmサイズ(幼SDなど)",
            "0201143104": "30cmサイズ(バービーなど)",
            "0201143105": "27cmサイズ(ジェニーなど)",
            "0201143106": "22cmサイズ(リカ・ネオブライスなど)",
            "0201143107": "1/12サイズ(プチブライス・オビツ11など)",
            "0201143199": "その他"
        }
    },
    "02011432": {
        "name": "中古ドールウィッグ",
        "children": {
            "0201143201": "SD・DDサイズ(約8-9inch)",
            "0201143202": "幼SDサイズ(約6-7inch)",
            "0201143203": "MSDサイズ(約7-7.5inch)",
            "0201143299": "その他"
        }
    },
    "02011433": {
        "name": "ドールアイ",
        "children": {}
    },
    "02011434": {
        "name": "ドール用小物・家具",
        "children": {}
    }
}


class DollCategoryManager:
    """Mandarake ドール分类管理器"""

    def __init__(self, data_file: Optional[str] = None):
        if data_file is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_file = os.path.join(base_dir, "data", "doll_categories.json")
        self.data_file = data_file
        self.tree: Dict[str, Any] = self._load_tree()

    def _load_tree(self) -> Dict[str, Any]:
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and len(data) > 0:
                        return data
            except Exception as e:
                logger.warning(f"读取分类文件 {self.data_file} 失败，使用内置默认数据: {e}")
        return DEFAULT_DOLL_TREE

    def get_tree(self) -> Dict[str, Any]:
        """获取完整的ドール下属分类树"""
        return self.tree

    def get_root_info(self) -> Dict[str, str]:
        """获取根节点ドール大分类信息"""
        return {"code": ROOT_DOLL_CODE, "name": ROOT_DOLL_NAME}

    def get_all_categories_flat(self) -> List[Dict[str, Any]]:
        """获取扁平化的分类列表（包含父子关系与层级）"""
        results = [
            {
                "code": ROOT_DOLL_CODE,
                "name": ROOT_DOLL_NAME,
                "level": 1,
                "parent_code": "",
                "parent_name": "",
                "full_path": ROOT_DOLL_NAME,
                "has_children": True
            }
        ]

        for sub_code, sub_info in sorted(self.tree.items()):
            sub_name = sub_info.get("name", "")
            children = sub_info.get("children", {})
            results.append({
                "code": sub_code,
                "name": sub_name,
                "level": 2,
                "parent_code": ROOT_DOLL_CODE,
                "parent_name": ROOT_DOLL_NAME,
                "full_path": f"{ROOT_DOLL_NAME} > {sub_name}",
                "has_children": len(children) > 0
            })

            for child_code, child_name in sorted(children.items()):
                results.append({
                    "code": child_code,
                    "name": child_name,
                    "level": 3,
                    "parent_code": sub_code,
                    "parent_name": sub_name,
                    "full_path": f"{ROOT_DOLL_NAME} > {sub_name} > {child_name}",
                    "has_children": False
                })

        return results

    def get_crawlable_categories(self) -> List[Dict[str, str]]:
        """
        获取用于爬取任务的推荐分类列表：
        包含所有二级分类，以及三级叶子分类，方便按细分板块分别抓取
        """
        items = []
        for sub_code, sub_info in sorted(self.tree.items()):
            sub_name = sub_info.get("name", "")
            children = sub_info.get("children", {})
            items.append({
                "code": sub_code,
                "name": sub_name,
                "level": "2",
                "full_name": f"{ROOT_DOLL_NAME} / {sub_name}"
            })
            for c_code, c_name in sorted(children.items()):
                items.append({
                    "code": c_code,
                    "name": c_name,
                    "level": "3",
                    "full_name": f"{ROOT_DOLL_NAME} / {sub_name} / {c_name}"
                })
        return items

    def get_category_name(self, code: str) -> str:
        """根据分类代码解析出全路径名称"""
        if not code or code == ROOT_DOLL_CODE:
            return ROOT_DOLL_NAME

        # 先查二级
        if code in self.tree:
            return f"{ROOT_DOLL_NAME} > {self.tree[code].get('name', code)}"

        # 再查三级
        for sub_code, sub_info in self.tree.items():
            children = sub_info.get("children", {})
            if code in children:
                return f"{ROOT_DOLL_NAME} > {sub_info.get('name', '')} > {children[code]}"

        return f"分类({code})"

    def is_valid_doll_code(self, code: str) -> bool:
        """校验是否为有效的ドール子分类代码"""
        if not code:
            return False
        return code.startswith(ROOT_DOLL_CODE)

    def get_leaf_category_codes(self) -> List[str]:
        """获取全站所有末级（叶子）分类代码，确保抓取时能精准覆盖所有三级细分板块"""
        leaves = []
        for sub_code, sub_info in sorted(self.tree.items()):
            children = sub_info.get("children", {})
            if children:
                for child_code in sorted(children.keys()):
                    leaves.append(child_code)
            else:
                leaves.append(sub_code)
        return leaves

    def infer_category(self, title: str, current_cat_code: str = "") -> Tuple[str, str]:
        """
        基于商品标题智能推断其具体的三级分类代码与全路径名称
        原则：
        1. 严格信赖官网叶子分类代码，若已有明确细分代码，直接返回，不进行二次猜测
        2. 若为二级分支代码，仅允许在其自身子分类树内细分，严禁跨大类篡改
        3. 仅在根分类 020114 兜底时按特征词智能归类（优先配件/假发/娃衣，后整娃）
        """
        if not title:
            return current_cat_code, self.get_category_name(current_cat_code)

        # 若已经是有效的叶子分类代码 (无子分类的分支或三级细分类)，直接返回对应官方全名
        if current_cat_code and (current_cat_code in self.get_leaf_category_codes() or len(current_cat_code) >= 10):
            return current_cat_code, self.get_category_name(current_cat_code)

        t = title.lower()
        code = current_cat_code or ROOT_DOLL_CODE

        # --- 针对具体二级分类的内部尺寸/系列细分 (绝不跨出当前大类) ---

        # 1. 【大型キャストドール】(02011403) 内部尺寸细分
        if code == "02011403":
            if "幼sd" in t or "幼天使" in title or "yousd" in t:
                return "0201140303", self.get_category_name("0201140303")
            if any(k in t for k in ("msd", "sdm", "sdミディ", "ミニスーパードルフィー")) or "40cm" in t:
                return "0201140302", self.get_category_name("0201140302")
            if any(k in t for k in ("sd13", "sd16", "sd17", "sdgr", "スーパードルフィー", "60cm", "65cm", "70cm", "1/3", "sd男", "sd女")):
                return "0201140301", self.get_category_name("0201140301")
            return "02011403", self.get_category_name("02011403")

        # 2. 【大型ソフビドール】(02011404) 内部尺寸细分
        if code == "02011404":
            if "mdd" in t or "オビツ50" in title or "ミニドルフィードリーム" in title:
                return "0201140402", self.get_category_name("0201140402")
            if any(k in t for k in ("dds", "dddy", "ddp", "ドルフィードリーム")) or "dd" in t or "60cm" in t:
                return "0201140401", self.get_category_name("0201140401")
            return "02011404", self.get_category_name("02011404")

        # 3. 【ドール衣装】(02011431) 内部尺寸细分
        if code == "02011431":
            if any(k in t for k in ("1/12", "オビツ11", "プチブライス", "ピコニーモ")):
                return "0201143107", self.get_category_name("0201143107")
            if any(k in t for k in ("22cm", "リカちゃん", "リカ", "ブライス", "licca")):
                return "0201143106", self.get_category_name("0201143106")
            if any(k in t for k in ("27cm", "ジェニー", "jenny")):
                return "0201143105", self.get_category_name("0201143105")
            if any(k in t for k in ("幼sd", "30cm")):
                return "0201143103", self.get_category_name("0201143103")
            if any(k in t for k in ("msd", "mdd", "sdm", "オビツ50", "40cm", "50cm", "1/4")):
                return "0201143102", self.get_category_name("0201143102")
            if any(k in t for k in ("60cm", "sd13", "sdgr", "sd17", "dd", "dds", "1/3")):
                return "0201143101", self.get_category_name("0201143101")
            return "02011431", self.get_category_name("02011431")

        # 4. 【中古ドールウィッグ】(02011432) 内部尺寸细分
        if code == "02011432":
            if any(k in t for k in ("6-7", "6～7", "6.5inch", "幼sd")):
                return "0201143202", self.get_category_name("0201143202")
            if any(k in t for k in ("7-8", "7～8", "7-7.5", "7～7.5", "msd", "mdd")):
                return "0201143203", self.get_category_name("0201143203")
            if any(k in t for k in ("8-9", "8～9", "9-10", "9～10", "8.5inch", "9inch", "sd", "dd")):
                return "0201143201", self.get_category_name("0201143201")
            return "02011432", self.get_category_name("02011432")

        # 5. 【ひめかずら】(02011402)
        if code == "02011402":
            if "llサイズ" in t or "10-11" in t or "10～11" in t:
                return "0201140201", self.get_category_name("0201140201")
            if "lサイズ" in t or "9-9.5" in t or "9～9.5" in t:
                return "0201140202", self.get_category_name("0201140202")
            if "mサイズ" in t or "8-8.5" in t or "8～8.5" in t:
                return "0201140203", self.get_category_name("0201140203")
            if "msサイズ" in t or "7-7.5" in t or "7～7.5" in t:
                return "0201140204", self.get_category_name("0201140204")
            if "sサイズ" in t or "6-7" in t or "6～7" in t:
                return "0201140205", self.get_category_name("0201140205")
            return "02011402", self.get_category_name("02011402")

        # 6. 【1/6～1/12ドール】(02011405) 内部品牌细分
        if code == "02011405":
            if "ミディブライス" in title or "middie blythe" in t:
                return "0201140502", self.get_category_name("0201140502")
            if "プチブライス" in title or "petite blythe" in t:
                return "0201140503", self.get_category_name("0201140503")
            if "ブライス" in title or "blythe" in t:
                return "0201140501", self.get_category_name("0201140501")
            if any(k in title for k in ("リカちゃん", "リカ", "キャッスル")) or "licca" in t:
                return "0201140504", self.get_category_name("0201140504")
            if "ジェニー" in title or "jenny" in t or "カレンダーガール" in title:
                return "0201140505", self.get_category_name("0201140505")
            if "バービー" in title or "barbie" in t:
                return "0201140506", self.get_category_name("0201140506")
            if any(k in title for k in ("プーリップ", "テヤン", "ダル", "イスル", "ビョル")) or "pullip" in t:
                return "0201140507", self.get_category_name("0201140507")
            if "momoko" in t or "モモコ" in title:
                return "0201140508", self.get_category_name("0201140508")
            if any(k in title for k in ("ピュアニーモ", "えっくす☆きゅーと", "アゾン")) or "pureneemo" in t or "azone" in t:
                return "0201140509", self.get_category_name("0201140509")
            if "リビングデッド" in title or "ldd" in t or "living dead" in t:
                return "0201140510", self.get_category_name("0201140510")
            if "ベッツィー" in title or "betsy" in t:
                return "0201140511", self.get_category_name("0201140511")
            if "ワンダーフロッグ" in title or "wonder frog" in t:
                return "0201140512", self.get_category_name("0201140512")
            return "02011405", self.get_category_name("02011405")

        # --- 仅在 code 仅为根分类 020114 时的全局兜底智能推断 ---
        if code == ROOT_DOLL_CODE:
            # 优先 1: 配件/假发/眼珠/娃衣 (防止被尺寸关键词误判为整娃)
            if any(k in title for k in ("ウィッグ", "wig", "かつら", "毛束", "バンス")):
                return "02011432", self.get_category_name("02011432")
            if any(k in title for k in ("グラスアイ", "レジンアイ", "ドールアイ")) or "アイ" in title and "ヘッド" not in title:
                return "02011433", self.get_category_name("02011433")
            if any(k in title for k in ("衣装", "ドレス", "服", "セット", "アウトフィット", "outfit", "着物", "袴", "パジャマ", "スカート", "パンツ", "ソックス", "靴下", "帽子", "キャップ")):
                return "02011431", self.get_category_name("02011431")
            if any(k in title for k in ("靴", "シューズ", "ブーツ", "スタンド", "家具", "キャリーバッグ", "小物")):
                return "02011434", self.get_category_name("02011434")

            # 优先 2: DD/MDD 软胶系列
            if any(k in t for k in ("mdd", "ddh", "ドルフィードリーム", "dollfie dream")) or "dd" in t and "sd" not in t:
                if "mdd" in t:
                    return "0201140402", self.get_category_name("0201140402")
                return "0201140401", self.get_category_name("0201140401")

            # 优先 3: SD 树脂系列
            if any(k in t for k in ("幼sd", "幼天使", "yousd")):
                return "0201140303", self.get_category_name("0201140303")
            if any(k in t for k in ("msd", "sdm", "sdミディ", "ミニスーパードルフィー")):
                return "0201140302", self.get_category_name("0201140302")
            if any(k in t for k in ("sd13", "sd16", "sd17", "sdgr", "スーパードルフィー", "super dollfie", "sd男", "sd女")):
                return "0201140301", self.get_category_name("0201140301")

            # 优先 4: 1/6~1/12 人形品牌
            if "ブライス" in title or "blythe" in t:
                return "0201140501", self.get_category_name("0201140501")
            if any(k in title for k in ("リカちゃん", "リカ")) or "licca" in t:
                return "0201140504", self.get_category_name("0201140504")
            if "ジェニー" in title or "jenny" in t:
                return "0201140505", self.get_category_name("0201140505")
            if "バービー" in title or "barbie" in t:
                return "0201140506", self.get_category_name("0201140506")

        return current_cat_code, self.get_category_name(current_cat_code)


# 全局单例
category_mgr = DollCategoryManager()
