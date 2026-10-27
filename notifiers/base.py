"""
通知基类定义
"""
import abc
import logging
from typing import List, Dict, Any

logger = logging.getLogger("mandarake.notifier.base")


class BaseNotifier(abc.ABC):
    def __init__(self, name: str, enabled: bool = True):
        self.name = name
        self.enabled = enabled

    @abc.abstractmethod
    def notify(self, items: List[Dict[str, Any]], rule_name: str = "") -> bool:
        """发送新商品提醒"""
        pass

    @abc.abstractmethod
    def test(self) -> bool:
        """测试通知通道是否可用"""
        pass
