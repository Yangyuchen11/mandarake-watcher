import os
import json
import shutil
import logging
from typing import Dict, Any

logger = logging.getLogger("mandarake.config")


def get_config_path() -> str:
    """
    智能获取配置文件路径：
    1. Docker 环境下（IN_DOCKER=1）：强制使用 data/config.json，以确保挂载卷能够持久化配置。
       若 data/config.json 尚不存在，则自动从根目录 config.json 或 config.example.json 拷贝初始化。
    2. 本地开发环境下：优先使用 data/config.json，其次根目录 config.json，最后从模板初始化。
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    data_cfg = os.path.join(data_dir, "config.json")
    root_cfg = os.path.join(base_dir, "config.json")
    example_cfg = os.path.join(base_dir, "config.example.json")

    # 1. 容器环境下的持久化保障
    if os.environ.get("IN_DOCKER"):
        if not os.path.isfile(data_cfg):
            source = root_cfg if os.path.isfile(root_cfg) else example_cfg
            if os.path.isfile(source):
                try:
                    shutil.copyfile(source, data_cfg)
                    logger.info(f"[Docker] 已自动初始化持久化配置至: {data_cfg} (来源: {source})")
                except Exception as e:
                    logger.warning(f"[Docker] 自动复制持久化配置失败: {e}")
        return data_cfg

    # 2. 本地/直接运行环境
    if os.path.isfile(data_cfg):
        return data_cfg
    if os.path.isfile(root_cfg):
        return root_cfg

    # 若均不存在，在 root_cfg 初始化
    if os.path.isfile(example_cfg):
        try:
            shutil.copyfile(example_cfg, root_cfg)
            logger.info(f"已自动从 config.example.json 初始化配置文件: {root_cfg}")
        except Exception as e:
            logger.warning(f"复制初始化配置文件失败: {e}")

    return root_cfg


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
    """持久化保存配置，并尽量保持 data 与根目录的配置一致性"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_cfg = os.path.join(base_dir, "data", "config.json")
    root_cfg = os.path.join(base_dir, "config.json")

    primary_path = get_config_path()
    success = False
    try:
        with open(primary_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        success = True
    except Exception as e:
        logger.warning(f"保存配置失败 ({primary_path}): {e}")
        return False

    # 若当前使用的是 data/config.json 且根目录下也有 config.json（例如本地挂载代码目录运行），尝试同步回根目录
    try:
        if primary_path == data_cfg and os.path.isfile(root_cfg):
            with open(root_cfg, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        elif primary_path == root_cfg and os.path.isdir(os.path.dirname(data_cfg)):
            with open(data_cfg, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return success
