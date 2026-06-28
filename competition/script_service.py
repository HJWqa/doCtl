from __future__ import annotations

import socket
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from competition.script_runner import ROOT, ScriptError, dry_run_task_a, dry_run_task_b, load_script
from protocols.semicolon import ProtocolError, format_message, parse_message
from utils.logger import logger


TrafficCallback = Callable[[str, str, str], None]
StateCallback = Callable[[dict[str, Any]], None]


class ScriptService:
    """Persistent semicolon-protocol competition service.

    The controller connects to Vision Studio as a TCP client and keeps that
    connection open for the whole match. The service handles:
      start;A;all; -> echo handshake
      A;all;x1;y1;x2;y2;x3;y3; -> Task A Bot commands
      B;x1;y1;x2;y2; -> Task B 3D request + Bot commands
    """

    def __init__(self, script_path: str | Path = "configs/competition_script.toml") -> None:
        self.script_path = _resolve_path(script_path)
        self._conn: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._health_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False
        self._paused = False
        self._vision_connected = False
        self._vision_target: dict[str, Any] = {}
        self._three_d_last_ok = False
        self._bot_last_ok = False
        self._state_callbacks: list[StateCallback] = []
        self._traffic_callbacks: list[TrafficCallback] = []
        self._stats_lock = threading.Lock()
        self.total_tasks = 0
        self.success_tasks = 0
        self.fail_tasks = 0
        self.last_error = ""
        self.last_rx = ""
        self.last_tx = ""
        self.current_step = "待机"
        self._events = deque(maxlen=200)

    def start(self) -> None:
        if self._running:
            return
        script = self._load_script()
        self._vision_target = _endpoint(script, "vision", "127.0.0.1", 7930)
        self._running = True
        self._paused = False
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._vision_loop, daemon=True)
        self._thread.start()
        self._health_thread = threading.Thread(target=self._health_loop, daemon=True)
        self._health_thread.start()
        logger.success(
            f"[Script] 已启动，作为 Client 连接 VS {self._vision_target['host']}:{self._vision_target['port']}"
        )
        self._notify_state()

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._conn:
            try:
                self._conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        if self._health_thread:
            self._health_thread.join(timeout=3)
            self._health_thread = None
        self._vision_connected = False
        self._three_d_last_ok = False
        self._bot_last_ok = False
        logger.info("[Script] 已停止")
        self._notify_state()

    def pause(self) -> None:
        self._paused = True
        logger.info("[Script] 已暂停")
        self._notify_state()

    def resume(self) -> None:
        self._paused = False
        logger.info("[Script] 已恢复")
        self._notify_state()

    def on_state_change(self, callback: StateCallback) -> None:
        self._state_callbacks.append(callback)

    def on_data(self, callback: TrafficCallback) -> None:
        self._traffic_callbacks.append(callback)

    def get_status(self) -> dict[str, Any]:
        script = {}
        try:
            script = self._load_script()
        except Exception as exc:
            self.last_error = str(exc)
        vision = _endpoint(script, "vision", "127.0.0.1", 7930) if isinstance(script, dict) else {}
        three_d = _endpoint(script, "three_d", "192.168.173.2", 9303) if isinstance(script, dict) else {}
        bot = _endpoint(script, "bot", "192.168.200.1", 9552) if isinstance(script, dict) else {}
        with self._stats_lock:
            return {
                "running": self._running,
                "paused": self._paused,
                "vision": {
                    "host": vision.get("host", "127.0.0.1"),
                    "port": vision.get("port", 7930),
                    "connected": self._vision_connected,
                    "mode": "client",
                },
                "three_d": {
                    "host": three_d.get("host", "192.168.173.2"),
                    "port": three_d.get("port", 9303),
                    "last_ok": self._three_d_last_ok,
                },
                "bot": {
                    "host": bot.get("host", "192.168.200.1"),
                    "port": bot.get("port", 9552),
                    "last_ok": self._bot_last_ok,
                },
                "script_path": str(self.script_path),
                "total_tasks": self.total_tasks,
                "success_tasks": self.success_tasks,
                "fail_tasks": self.fail_tasks,
                "last_error": self.last_error,
                "last_rx": self.last_rx,
                "last_tx": self.last_tx,
                "current_step": self.current_step,
                "events": list(self._events),
            }

    @property
    def is_running(self) -> bool:
        """外部 routes.py / ctl.py 会读，判断主控是否运行中。"""
        return self._running

    def handle_line(self, line: str) -> str:
        self._record("VS 收到", line)
        self._emit("vision", "rx", line)
        self.last_rx = line
        if self._paused:
            return self._reply(["NG", "paused"])
        try:
            msg = parse_message(line)
            if msg.kind == "start":
                return self._handle_start(msg)
            if msg.kind == "A":
                return self._handle_task_a(line)
            if msg.kind == "B":
                return self._handle_task_b(line)
            return self._reply(["NG", "unknown", msg.kind or ""])
        except (ProtocolError, ScriptError, ValueError, OSError) as exc:
            self._mark_task(False, str(exc))
            return self._reply(["NG", type(exc).__name__, str(exc)])

    def _handle_start(self, msg: Any) -> str:
        task = str(msg.fields[1]) if len(msg.fields) > 1 else ""
        mode = str(msg.fields[2]) if len(msg.fields) > 2 else "all"
        if task not in {"A", "B"}:
            return self._reply(["NG", "start", "bad_task", task])
        self._record("握手", f"Task {task} / {mode}")
        return self._reply(["start", task, mode])

    def _handle_task_a(self, line: str) -> str:
        script = self._load_script()
        cfg = (script.get("tasks") or {}).get("A") or {}
        line = _normalize_task_a_line(line)
        mode = _task_mode(line, default="all")
        result = dry_run_task_a(script, cfg, mode=mode, vs_message=line)
        self._record("Task A", f"解析 {len(result['processed_objects'])} 个物块")
        ok = self._send_bot_commands(result["bot_tx"])
        self._mark_task(ok, "" if ok else "bot command failed")
        return self._reply(["OK" if ok else "NG", "A", result.get("processed_objects") and len(result["processed_objects"])])

    def _handle_task_b(self, line: str) -> str:
        script = self._load_script()
        cfg = (script.get("tasks") or {}).get("B") or {}
        # ---- 前置动作：移动到观测位姿，再发 3D start ----
        pre_pose = cfg.get("pre_pose")
        if pre_pose and isinstance(pre_pose, list) and len(pre_pose) >= 6:
            move_cmd = format_message(["MovJ", *pre_pose[:6]])
            self._record("Task B", f"前置移动 → {pre_pose[:6]}")
            if not self._send_bot_commands([move_cmd]):
                self._mark_task(False, "前置移动失败")
                return self._reply(["NG", "B", "pre_move_failed"])
        # ---- 请求 3D 高度 ----
        result = dry_run_task_b(script, cfg, mode="all", vs_message=line)
        self._record("Task B", "请求 3D 高度")
        z_rx = self._request_3d(cfg)
        z_values = _parse_task_b_z(z_rx)
        cfg = dict(cfg)
        cfg["sample_3d_z"] = z_values
        result = dry_run_task_b(script, cfg, mode="all", vs_message=line)
        self._record("Task B", f"3D 返回 Z={z_values}")
        ok = self._send_bot_commands(result["bot_tx"])
        self._mark_task(ok, "" if ok else "bot command failed")
        return self._reply(["OK" if ok else "NG", "B", len(result["bot_tx"])])

    def _request_3d(self, cfg: dict[str, Any]) -> str:
        script = self._load_script()
        three_d = script.get("three_d", {})
        host = str(three_d.get("host", cfg.get("three_d_host", "192.168.173.2")))
        port = int(three_d.get("port", cfg.get("three_d_port", 9303)))
        message = str(three_d.get("task_b_request", cfg.get("three_d_request", "B;start;"))).rstrip()
        timeout_s = float(three_d.get("timeout_s", cfg.get("three_d_timeout_s", 5.0)))
        self._emit("camera3d", "tx", f"{host}:{port} {message}")
        try:
            with socket.create_connection((host, port), timeout=timeout_s) as conn:
                stream = conn.makefile("rwb")
                stream.write((message + "\n").encode("utf-8"))
                stream.flush()
                response = stream.readline().decode("utf-8", errors="replace").strip()
            self._three_d_last_ok = True
        except OSError:
            self._three_d_last_ok = False
            raise
        self._emit("camera3d", "rx", response)
        return response

    def _send_bot_commands(self, commands: list[str]) -> bool:
        """向 Bot 发送指令序列。每条指令独立建连，重试时新建连接。"""
        script = self._load_script()
        bot = script.get("bot", {})
        host = str(bot.get("host", "192.168.200.1"))
        port = int(bot.get("port", 9552))
        timeout_s = float(bot.get("timeout_s", 8.0))
        retry = int(bot.get("retry", 2))
        if bool(bot.get("dry_run", False)):
            for command in commands:
                self._record("Bot dry-run", command)
                self._emit("arm", "tx", f"dry-run {command}")
                self._emit("arm", "rx", "OK")
            self._bot_last_ok = True
            return True
        for command in commands:
            if not self._send_bot_command_reconnect(host, port, command, timeout_s, retry):
                self._bot_last_ok = False
                return False
        self._bot_last_ok = True
        return True

    def _send_bot_command_reconnect(
        self, host: str, port: int, command: str, timeout_s: float, retry: int,
    ) -> bool:
        """发送单条指令，支持重试。每次尝试新建 TCP 连接，避免复用超时后的坏 socket。"""
        retry_delay_s = 0.5
        for attempt in range(1, retry + 2):
            self._emit("arm", "tx", command)
            self._record("Bot 发送", command)
            try:
                with socket.create_connection((host, port), timeout=timeout_s) as conn:
                    conn.settimeout(timeout_s)
                    stream = conn.makefile("rwb")
                    stream.write((command.rstrip() + "\n").encode("utf-8"))
                    stream.flush()
                    started = time.monotonic()
                    while time.monotonic() - started < timeout_s:
                        try:
                            raw = stream.readline()
                        except (socket.timeout, OSError, ValueError) as e:
                            self._emit("arm", "err",
                                       f"读取异常 (attempt {attempt}): {e}")
                            break
                        if not raw:
                            self._emit("arm", "err", "Bot 连接已关闭 (EOF)")
                            break
                        response = raw.decode("utf-8", errors="replace").strip()
                        if response:
                            self._emit("arm", "rx", response)
                            if response.upper().startswith("OK"):
                                self._record("Bot OK", command)
                                return True
                            self._emit("arm", "err", f"Bot 返回非 OK: {response}")
                            break
                    else:
                        self._emit("arm", "err",
                                   f"无响应 (attempt {attempt}/{retry + 1}, waited {timeout_s}s): {command[:60]}")
            except OSError as e:
                self._emit("arm", "err", f"连接失败 (attempt {attempt}): {e}")
            if attempt <= retry:
                time.sleep(retry_delay_s)
        return False

    def _load_script(self) -> dict[str, Any]:
        return load_script(self.script_path)

    def _reply(self, fields: list[Any]) -> str:
        response = format_message(fields)
        self.last_tx = response
        self._emit("vision", "tx", response)
        self._notify_state()
        return response

    def _mark_task(self, ok: bool, error: str) -> None:
        with self._stats_lock:
            self.total_tasks += 1
            if ok:
                self.success_tasks += 1
                self.last_error = ""
            else:
                self.fail_tasks += 1
                self.last_error = error
        self._record("完成" if ok else "失败", error or "任务完成", "ok" if ok else "error")
        self._notify_state()

    def _emit(self, device: str, direction: str, data: str) -> None:
        for callback in self._traffic_callbacks:
            try:
                callback(device, direction, data)
            except Exception:
                pass

    def _notify_state(self) -> None:
        status = self.get_status()
        for callback in self._state_callbacks:
            try:
                callback(status)
            except Exception:
                pass

    def _record(self, step: str, detail: str, level: str = "info") -> None:
        self.current_step = step
        event = {
            "time": time.strftime("%H:%M:%S"),
            "step": step,
            "detail": detail,
            "level": level,
        }
        self._events.append(event)
        self._notify_state()

    def _vision_loop(self) -> None:
        while self._running and not self._stop_event.is_set():
            try:
                script = self._load_script()
                vision = _endpoint(script, "vision", "127.0.0.1", 7930)
                self._vision_target = vision
                host = str(vision["host"])
                port = int(vision["port"])
                timeout_s = float(vision.get("timeout_s", 3.0))
                reconnect_s = float(vision.get("reconnect_s", 1.0))
                self._record("连接 VS", f"{host}:{port}")
                with socket.create_connection((host, port), timeout=timeout_s) as conn:
                    conn.settimeout(1.0)
                    self._conn = conn
                    self._vision_connected = True
                    self._record("VS 已连接", f"{host}:{port}", "ok")
                    self._recv_vision(conn)
            except OSError as exc:
                if self._running and not self._stop_event.is_set():
                    self._record("VS 连接失败", str(exc), "error")
                    logger.warn(f"[Script] VS 连接失败: {exc}")
                    self._stop_event.wait(reconnect_s if "reconnect_s" in locals() else 1.0)
            except (ScriptError, ValueError) as exc:
                self._record("Script 配置错误", str(exc), "error")
                logger.error(f"[Script] 配置错误: {exc}")
                self._stop_event.wait(1.0)
            finally:
                self._vision_connected = False
                self._conn = None
                self._notify_state()

    def _recv_vision(self, conn: socket.socket) -> None:
        buffer = b""
        while self._running and not self._stop_event.is_set():
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                # 超时后处理缓冲区中残留的数据
                stripped = buffer.strip()
                if stripped:
                    line = stripped.decode("utf-8", errors="replace").strip()
                    buffer = b""
                    if not line.endswith(";"):
                        self._emit("vision", "err",
                                   f"VS 消息缺少结尾分号，仍尝试处理: {line[:80]}")
                    response = self.handle_line(line)
                    conn.sendall((response + "\n").encode("utf-8"))
                continue
            if not chunk:
                raise ConnectionError("VS closed connection")
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                response = self.handle_line(line)
                conn.sendall((response + "\n").encode("utf-8"))

    def _health_loop(self) -> None:
        """后台健康检查：探测 VS / 3D / Bot 的 TCP 连通性。

        只在设备当前标记为不可达时才探测（避免频繁建连干扰设备）。
        首次启动时主动探测一轮建立初始状态。
        """
        HEALTH_INTERVAL = 5.0    # 不可达时重探间隔（秒）
        PROBE_TIMEOUT = 2.0      # 探测超时（秒）
        INITIAL_PROBED = False   # 是否已完成首次探测

        while self._running and not self._stop_event.is_set():
            try:
                script = self._load_script()
            except Exception:
                self._stop_event.wait(HEALTH_INTERVAL)
                continue

            need_probe_3d = (not INITIAL_PROBED) or (not self._three_d_last_ok)
            need_probe_bot = (not INITIAL_PROBED) or (not self._bot_last_ok)
            # VS 只做首次探测 + _vision_loop 未连接时探测，避免干扰 _vision_loop 的长连接
            need_probe_vs = (not INITIAL_PROBED) or (not self._vision_connected)

            if not INITIAL_PROBED:
                INITIAL_PROBED = True

            # ---- 探测 VS (vision) ----
            if need_probe_vs:
                vision = script.get("vision", {})
                vs_host = str(vision.get("host", "127.0.0.1"))
                vs_port = int(vision.get("port", 7930))
                vs_ok = self._probe_tcp(vs_host, vs_port, PROBE_TIMEOUT)
                if vs_ok != self._vision_connected:
                    self._emit("vision", "info",
                               f"VS {vs_host}:{vs_port} {'可达 ✓' if vs_ok else '不可达 ✗'}")

            # ---- 探测 3D (three_d) ----
            if need_probe_3d:
                three_d = script.get("three_d", {})
                td_host = str(three_d.get("host", "192.168.173.2"))
                td_port = int(three_d.get("port", 9303))
                td_ok_3d = self._probe_tcp(td_host, td_port, PROBE_TIMEOUT)
                if td_ok_3d != self._three_d_last_ok:
                    self._three_d_last_ok = td_ok_3d
                    self._emit("camera3d", "info",
                               f"3D {td_host}:{td_port} {'可达 ✓' if td_ok_3d else '不可达 ✗'}")
                    self._notify_state()

            # ---- 探测 Bot ----
            if need_probe_bot:
                bot = script.get("bot", {})
                bot_host = str(bot.get("host", "192.168.200.1"))
                bot_port = int(bot.get("port", 9552))
                bot_ok = self._probe_tcp(bot_host, bot_port, PROBE_TIMEOUT)
                if bot_ok != self._bot_last_ok:
                    self._bot_last_ok = bot_ok
                    self._emit("arm", "info",
                               f"Bot {bot_host}:{bot_port} {'可达 ✓' if bot_ok else '不可达 ✗'}")
                    self._notify_state()

            self._stop_event.wait(HEALTH_INTERVAL)

    @staticmethod
    def _probe_tcp(host: str, port: int, timeout: float) -> bool:
        """尝试 TCP 连接，成功返回 True。"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            err = sock.connect_ex((host, port))
            sock.close()
            return err == 0
        except Exception:
            return False


def _resolve_path(path: str | Path) -> Path:
    item = Path(path)
    if item.is_absolute():
        return item
    return ROOT / item


def _endpoint(script: dict[str, Any], section: str, default_host: str, default_port: int) -> dict[str, Any]:
    cfg = script.get(section, {}) if isinstance(script, dict) else {}
    return {
        "host": str(cfg.get("host", default_host)),
        "port": int(cfg.get("port", default_port)),
        **{k: v for k, v in cfg.items() if k not in {"host", "port"}},
    }


def _task_mode(line: str, default: str) -> str:
    msg = parse_message(line)
    if len(msg.fields) > 1:
        return str(msg.fields[1])
    return default


def _normalize_task_a_line(line: str) -> str:
    msg = parse_message(line)
    if msg.kind != "A":
        return line
    if len(msg.fields) > 1 and isinstance(msg.fields[1], str) and msg.fields[1] == "all":
        return line
    return format_message(["A", "all", *msg.fields[1:]])


def _parse_task_b_z(line: str) -> list[float]:
    msg = parse_message(line)
    if msg.kind != "B":
        raise ProtocolError(f"expected 3D response B;z..., got {msg.kind}")
    values = []
    for item in msg.fields[1:]:
        values.append(float(item))
    if not values:
        raise ProtocolError("3D response has no z values")
    return values
