"""
实时运行终端日志管理模块 (Terminal Log Manager)
捕获系统全量运行日志并维护高性能环形内存缓冲 (Ring Buffer)，
支持动态分级检索、关键词即时过滤、100/300/1000 行限制切换与 SSE 实时广播。
"""
import sys
import time
import logging
import threading
from collections import deque
from typing import List, Dict, Optional, Callable, Any


LEVEL_WEIGHTS = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "WARN": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


class TerminalLogEntry:
    def __init__(self, entry_id: int, timestamp: float, level: str, logger_name: str, message: str, formatted: str):
        self.id = entry_id
        self.timestamp = timestamp
        self.level = level.upper()
        self.logger_name = logger_name
        self.message = message
        self.formatted = formatted

    def to_dict(self) -> Dict[str, Any]:
        time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))
        time_short = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "time": time_str,
            "time_short": time_short,
            "level": self.level,
            "logger": self.logger_name,
            "message": self.message,
            "formatted": self.formatted,
        }


class TerminalLogHandler(logging.Handler):
    """自定义日志处理器，将所有 Python 核心组件日志接入终端管理器"""

    def __init__(self, manager: "TerminalLogManager"):
        super().__init__()
        self.manager = manager
        self.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord):
        try:
            # 过滤 werkzeug 的常规 API 轮询日志（如 200 GET/POST），仅在发生 ERROR 及以上错误时记录
            if record.name == "werkzeug" and record.levelno < logging.ERROR:
                return
            formatted = self.format(record)
            self.manager.append_log(
                level=record.levelname,
                logger_name=record.name,
                message=record.getMessage(),
                formatted=formatted,
                timestamp=record.created
            )
        except Exception:
            self.handleError(record)


class TerminalLogManager:
    """终端管理器单例"""
    _instance = None
    _lock = threading.Lock()

    def __init__(self, max_capacity: int = 2000):
        self.max_capacity = max_capacity
        self._buffer: deque = deque(maxlen=max_capacity)
        self._id_counter = 0
        self._lock = threading.RLock()
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._installed = False

    @classmethod
    def get_instance(cls, max_capacity: int = 2000) -> "TerminalLogManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(max_capacity=max_capacity)
            return cls._instance

    def install(self):
        """挂载到 Python 根日志记录器，无缝拦截全系统的 logger 输出"""
        if self._installed:
            return
        handler = TerminalLogHandler(self)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        # 确保 werkzeug 日志仅在出错时记录与显示
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        if root_logger.level == logging.NOTSET or root_logger.level > logging.INFO:
            root_logger.setLevel(logging.INFO)
        self._installed = True
        self.append_log("INFO", "mandarake.terminal", "Mandarake 终端日志拦截器已成功装载", "[SYS] Mandarake 日志监控就绪")

    def append_log(self, level: str, logger_name: str, message: str, formatted: str, timestamp: Optional[float] = None):
        """写入单条日志并通知监听器"""
        ts = timestamp or time.time()
        with self._lock:
            self._id_counter += 1
            entry = TerminalLogEntry(
                entry_id=self._id_counter,
                timestamp=ts,
                level=level,
                logger_name=logger_name,
                message=message,
                formatted=formatted
            )
            self._buffer.append(entry)
            entry_dict = entry.to_dict()
            listeners_snapshot = list(self._listeners)

        for callback in listeners_snapshot:
            try:
                callback(entry_dict)
            except Exception:
                pass

    def add_listener(self, callback: Callable[[Dict[str, Any]], None]):
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[Dict[str, Any]], None]):
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def get_logs(
        self,
        since_id: int = 0,
        limit: int = 300,
        level_filter: Optional[str] = None,
        keyword: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """按条件检索日志"""
        with self._lock:
            entries = list(self._buffer)

        # 1. ID 增量
        if since_id > 0:
            entries = [e for e in entries if e.id > since_id]

        # 2. 等级筛选 (支持 "INFO 及以上" 阶梯过滤)
        if level_filter and level_filter.upper() != "ALL":
            min_weight = LEVEL_WEIGHTS.get(level_filter.upper(), 20)
            entries = [e for e in entries if LEVEL_WEIGHTS.get(e.level.upper(), 20) >= min_weight]

        # 3. 关键词检索
        if keyword and keyword.strip():
            kw = keyword.strip().lower()
            entries = [e for e in entries if kw in e.formatted.lower()]

        # 4. 截取最近行数
        if limit and limit > 0 and len(entries) > limit:
            entries = entries[-limit:]

        return [e.to_dict() for e in entries]

    def clear(self):
        """清空日志缓存"""
        with self._lock:
            self._buffer.clear()
            self._id_counter = 0


terminal_mgr = TerminalLogManager.get_instance()
