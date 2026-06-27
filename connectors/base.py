"""
TCP Client 基类
提供: connect / send / recv / reconnect / 数据监控
"""

import socket
import json
import queue
import time
import threading

from config import RECONNECT_INITIAL, RECONNECT_MAX, RECONNECT_BACKOFF, SOCKET_TIMEOUT
from utils.logger import logger


class BaseTCPClient:
    """TCP 客户端基类"""

    def __init__(self, name: str, host: str, port: int):
        self.name = name
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._connected = False
        self._running = False
        self._reconnect_delay = RECONNECT_INITIAL
        self._data_callbacks = []  # (direction, data_str) -> None
        self._rx_thread: threading.Thread | None = None
        self._rx_queue: queue.Queue[str] = queue.Queue(maxsize=500)
        self._rx_monitor_buffer = ""

    # ---------- Mock 判断 (动态读取 config, 支持 --no-mock) ----------

    @property
    def _mock(self) -> bool:
        import config
        return config.MOCK_MODE

    # ---------- 连接管理 ----------

    def connect(self) -> bool:
        """建立 TCP 连接"""
        if self._mock:
            self._emit_info(f"模拟模式 - 跳过连接 {self.host}:{self.port}")
            logger.info(f"[{self.name}] 模拟模式 - 跳过连接 {self.host}:{self.port}")
            self._connected = True
            self._running = True
            return True

        self._emit_info(f"正在连接 {self.host}:{self.port} ...")
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(SOCKET_TIMEOUT)
            self.sock.connect((self.host, self.port))
            self._connected = True
            self._running = True
            self._reconnect_delay = RECONNECT_INITIAL
            self._start_rx_thread()
            self._emit_info(f"connected {self.host}:{self.port}")
            logger.success(f"[{self.name}] 已连接 {self.host}:{self.port}")
            return True
        except Exception as e:
            self._emit_err(f"连接失败 {self.host}:{self.port} - {e}")
            logger.error(f"[{self.name}] 连接失败 {self.host}:{self.port} - {e}")
            self._connected = False
            return False

    def disconnect(self):
        """断开连接"""
        self._running = False
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=1)
        self._connected = False
        self._emit_info(f"已断开 {self.host}:{self.port}")
        logger.info(f"[{self.name}] 已断开")

    def reconnect(self) -> bool:
        """指数退避重连"""
        if self._mock:
            self._connected = True
            return True

        self._emit_info(f"重连等待 {self._reconnect_delay}s ...")
        logger.info(f"[{self.name}] 重连等待 {self._reconnect_delay}s ...")
        time.sleep(self._reconnect_delay)
        if self.connect():
            return True
        self._reconnect_delay = min(
            self._reconnect_delay * RECONNECT_BACKOFF, RECONNECT_MAX
        )
        return False

    @property
    def is_connected(self) -> bool:
        if self._mock:
            return True
        return self._connected

    # ---------- 数据回调 (供 Web UI 实时显示) ----------

    def on_data(self, callback):
        """注册数据回调 callback(direction, data_str)
           direction: 'tx'=发送 'rx'=接收 'info'=系统事件 'err'=错误"""
        self._data_callbacks.append(callback)

    def _emit_data(self, direction: str, data: str):
        for cb in self._data_callbacks:
            try:
                cb(direction, data)
            except Exception:
                pass

    def _emit_info(self, msg: str):
        self._emit_data("info", msg)

    def _emit_err(self, msg: str):
        self._emit_data("err", msg)

    # ---------- 收发 ----------

    def _send_raw(self, data: str) -> bool:
        """发送字符串, 自动加换行"""
        logger.debug(f"[{self.name}] TX -> {data[:200]}")
        self._emit_data("tx", data)

        if self._mock:
            return True

        with self._lock:
            try:
                self.sock.sendall((data + "\n").encode("utf-8"))
                return True
            except Exception as e:
                self._emit_err(f"发送失败 - {e}")
                logger.error(f"[{self.name}] 发送失败 - {e}")
                self._connected = False
                return False

    def _recv_raw(self) -> str | None:
        """接收一行字符串"""
        if self._mock:
            return None

        if self._rx_thread and self._rx_thread.is_alive():
            try:
                return self._rx_queue.get(timeout=SOCKET_TIMEOUT)
            except queue.Empty:
                self._emit_err(f"接收超时 ({SOCKET_TIMEOUT}s)")
                logger.warn(f"[{self.name}] 接收超时")
                return None

        return self._read_socket_line(emit_timeout=True)

    def _read_socket_line(self, emit_timeout: bool = False) -> str | None:
        """从 socket 读取一行。后台监听超时时保持安静，请求等待超时才上报。"""
        try:
            self.sock.settimeout(SOCKET_TIMEOUT)
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise ConnectionError("连接已关闭")
                buf += chunk
            data = buf.decode("utf-8").strip()
            logger.debug(f"[{self.name}] RX <- {data[:200]}")
            self._emit_data("rx", data)
            return data
        except socket.timeout:
            if emit_timeout:
                self._emit_err(f"接收超时 ({SOCKET_TIMEOUT}s)")
                logger.warn(f"[{self.name}] 接收超时")
            return None
        except Exception as e:
            if self._running:
                self._emit_err(f"接收失败 - {e}")
                logger.error(f"[{self.name}] 接收失败 - {e}")
            self._connected = False
            return None

    def _start_rx_thread(self):
        """统一后台接收，支持设备主动推送，同时把响应排队给 recv_json 使用。"""
        if self._rx_thread and self._rx_thread.is_alive():
            return

        self._rx_monitor_buffer = ""
        while True:
            try:
                self._rx_queue.get_nowait()
            except queue.Empty:
                break

        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx_thread.start()

    def _rx_loop(self):
        while self._running and self.sock:
            try:
                self.sock.settimeout(SOCKET_TIMEOUT)
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise ConnectionError("连接已关闭")
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    self._emit_err(f"接收失败 - {e}")
                    logger.error(f"[{self.name}] 接收失败 - {e}")
                self._connected = False
                break

            data = chunk.decode("utf-8", errors="replace")
            if not data:
                continue

            logger.debug(f"[{self.name}] RX <- {data[:200]}")
            self._emit_data("rx", data.strip() or data)

            self._rx_monitor_buffer += data
            lines = self._rx_monitor_buffer.splitlines(keepends=True)
            self._rx_monitor_buffer = ""

            for item in lines:
                if item.endswith(("\n", "\r")):
                    self._queue_rx_data(item.strip())
                else:
                    self._rx_monitor_buffer = item

            if len(self._rx_monitor_buffer) > 8192:
                self._queue_rx_data(self._rx_monitor_buffer.strip())
                self._rx_monitor_buffer = ""

    def _queue_rx_data(self, data: str):
        if not data:
            return
        try:
            self._rx_queue.put_nowait(data)
        except queue.Full:
            try:
                self._rx_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._rx_queue.put_nowait(data)
            except queue.Full:
                pass

    def send_json(self, obj: dict) -> bool:
        """发送 JSON 对象"""
        return self._send_raw(json.dumps(obj, ensure_ascii=False))

    def recv_json(self) -> dict | None:
        """接收并解析 JSON"""
        raw = self._recv_raw()
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            self._emit_err(f"JSON 解析失败: {raw[:100]}")
            logger.warn(f"[{self.name}] JSON 解析失败: {raw[:100]} - {e}")
            return None
