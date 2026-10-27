import os
import json
import shutil
import logging
from typing import Dict, Any

logger = logging.getLogger("mandarake.config")


def get_config_path() -> str:
    """
    智能获取配置文件路径：
    1. 优先读取 data/config.json (容器与云原生持久化卷推荐)
    2. 其次读取根目录 config.json
    3. 若均不存在，自动从 config.example.json 复制并返回
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    data_cfg = os.path.join(data_dir, "config.json")
    root_cfg = os.path.join(base_dir, "config.json")
    example_cfg = os.path.join(base_dir, "config.example.json")

    if os.path.isfile(data_cfg):
        return data_cfg
    if os.path.isfile(root_cfg):
        return root_cfg

    # 优先在 data 目录初始化以利于挂载卷持久化
    target_path = data_cfg if (os.environ.get("IN_DOCKER") or not os.path.exists(root_cfg)) else root_cfg
    if os.path.isfile(example_cfg):
        try:
            shutil.copyfile(example_cfg, target_path)
            logger.info(f"已自动从 config.example.json 初始化配置文件: {target_path}")
        except Exception as e:
            logger.warning(f"复制初始化配置文件失败: {e}")

    return target_path


def load_config() -> Dict[str, Any]:
    """读取并解析配置"""
    cfg_path = get_config_path()
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"读取配置失败 ({cfg_path}): {e}")
    return {}


def save_config(cfg: Dict[str, Any]) -> bool:
    """持久化保存配置"""
    cfg_path = get_config_path()
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.warning(f"保存配置失败 ({cfg_path}): {e}")
        return False
