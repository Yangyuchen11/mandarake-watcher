"""
声音提醒模块 (Windows winsound / 控制台蜂鸣)
"""
import sys
import threading
import logging
from typing import List, Dict, Any
from notifiers.base import BaseNotifier

logger = logging.getLogger("mandarake.notifier.sound")


class SoundNotifier(BaseNotifier):
    def __init__(self, enabled: bool = True):
        super().__init__("sound", enabled)

    def _play_beep_sequence(self):
        try:
            if sys.platform == "win32":
                import winsound
                # 播放多音阶欢快提示音
                winsound.Beep(1046, 120)  # C6
                winsound.Beep(1318, 120)  # E6
                winsound.Beep(1568, 150)  # G6
                winsound.Beep(2093, 200)  # C7
            else:
                sys.stdout.write("\a")
                sys.stdout.flush()
        except Exception as e:
            logger.debug(f"播放声音提示异常: {e}")

    def notify(self, items: List[Dict[str, Any]], rule_name: str = "") -> bool:
        if not self.enabled or not items:
            return False
        threading.Thread(target=self._play_beep_sequence, daemon=True).start()
        return True

    def test(self) -> bool:
        threading.Thread(target=self._play_beep_sequence, daemon=True).start()
        return True
