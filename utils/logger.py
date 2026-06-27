"""
运行日志模块
支持环形缓冲, 供 Web UI 拉取
"""

import time
from collections import deque
import threading

from config import MAX_LOG_LINES


class Logger:
    """线程安全的环形缓冲日志"""

    def __init__(self, max_lines=MAX_LOG_LINES):
        self._lines = deque(maxlen=max_lines)
        self._lock = threading.Lock()
        self._callbacks = []  # 新日志回调 (供 WebSocket 推送)

    def info(self, msg: str):
        self._log("INFO", msg)

    def warn(self, msg: str):
        self._log("WARN", msg)

    def error(self, msg: str):
        self._log("ERROR", msg)

    def debug(self, msg: str):
        self._log("DEBUG", msg)

    def success(self, msg: str):
        self._log("OK", msg)

    def _log(self, level: str, msg: str):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] [{level}] {msg}"
        with self._lock:
            self._lines.append(line)
        for cb in self._callbacks:
            try:
                cb(level, msg, ts)
            except Exception:
                pass

    def get_all(self) -> list:
        with self._lock:
            return list(self._lines)

    def get_recent(self, n=50) -> list:
        with self._lock:
            return list(self._lines)[-n:]

    def on_log(self, callback):
        """注册新日志回调 callback(level, msg, ts)"""
        self._callbacks.append(callback)


# 全局单例
logger = Logger()
