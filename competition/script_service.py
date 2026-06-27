from __future__ import annotations

import socket
import socketserver
import threading
import time
from pathlib import Path
from typing import Any, Callable

from competition.script_runner import ROOT, ScriptError, dry_run_task_a, dry_run_task_b, load_script
from protocols.semicolon import ProtocolError, format_message, parse_message
from utils.logger import logger


TrafficCallback = Callable[[str, str], None]
StateCallback = Callable[[dict[str, Any]], None]


class ScriptService:
    """Persistent semicolon-protocol competition service.

    Vision Studio connects here once the service is started. The service handles:
      start;A;all; -> echo handshake
      A;all;x1;y1;x2;y2;x3;y3; -> Task A Bot commands
      B;x1;y1;x2;y2; -> Task B 3D request + Bot commands
    """

    def __init__(self, script_path: str | Path = "configs/competition_script.toml") -> None:
        self.script_path = _resolve_path(script_path)
        self._server: ThreadingTcpServer | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._paused = False
        self._state_callbacks: list[StateCallback] = []
        self._traffic_callbacks: list[TrafficCallback] = []
        self._stats_lock = threading.Lock()
        self.total_tasks = 0
        self.success_tasks = 0
        self.fail_tasks = 0
        self.last_error = ""
        self.last_rx = ""
        self.last_tx = ""

    def start(self) -> None:
        if self._running:
            return
        script = self._load_script()
        listen = script.get("listen", {})
        host = str(listen.get("host", "0.0.0.0"))
        port = int(listen.get("port", 7950))
        handler = self._make_handler()
        self._server = ThreadingTcpServer((host, port), handler)
        self._server.service = self
        self._running = True
        self._paused = False
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.success(f"[Script] 已监听 VS 分号协议 {host}:{port}")
        self._notify_state()

    def stop(self) -> None:
        self._running = False
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
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
        listen = script.get("listen", {}) if isinstance(script, dict) else {}
        with self._stats_lock:
            return {
                "running": self._running,
                "paused": self._paused,
                "listen": {
                    "host": listen.get("host", "0.0.0.0"),
                    "port": listen.get("port", 7950),
                },
                "script_path": str(self.script_path),
                "total_tasks": self.total_tasks,
                "success_tasks": self.success_tasks,
                "fail_tasks": self.fail_tasks,
                "last_error": self.last_error,
                "last_rx": self.last_rx,
                "last_tx": self.last_tx,
            }

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    def handle_line(self, line: str) -> str:
        self._emit("rx", line)
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
        return self._reply(["start", task, mode])

    def _handle_task_a(self, line: str) -> str:
        script = self._load_script()
        cfg = (script.get("tasks") or {}).get("A") or {}
        line = _normalize_task_a_line(line)
        mode = _task_mode(line, default="all")
        result = dry_run_task_a(script, cfg, mode=mode, vs_message=line)
        ok = self._send_bot_commands(result["bot_tx"])
        self._mark_task(ok, "" if ok else "bot command failed")
        return self._reply(["OK" if ok else "NG", "A", result.get("processed_objects") and len(result["processed_objects"])])

    def _handle_task_b(self, line: str) -> str:
        script = self._load_script()
        cfg = (script.get("tasks") or {}).get("B") or {}
        result = dry_run_task_b(script, cfg, mode="all", vs_message=line)
        z_rx = self._request_3d(cfg)
        z_values = _parse_task_b_z(z_rx)
        cfg = dict(cfg)
        cfg["sample_3d_z"] = z_values
        result = dry_run_task_b(script, cfg, mode="all", vs_message=line)
        ok = self._send_bot_commands(result["bot_tx"])
        self._mark_task(ok, "" if ok else "bot command failed")
        return self._reply(["OK" if ok else "NG", "B", len(result["bot_tx"])])

    def _request_3d(self, cfg: dict[str, Any]) -> str:
        host = str(cfg.get("three_d_host", "192.168.173.2"))
        port = int(cfg.get("three_d_port", 9303))
        message = str(cfg.get("three_d_request", "B;start;")).rstrip()
        timeout_s = float(cfg.get("three_d_timeout_s", 5.0))
        self._emit("tx", f"3D {host}:{port} {message}")
        with socket.create_connection((host, port), timeout=timeout_s) as conn:
            stream = conn.makefile("rwb")
            stream.write((message + "\n").encode("utf-8"))
            stream.flush()
            response = stream.readline().decode("utf-8", errors="replace").strip()
        self._emit("rx", f"3D {response}")
        return response

    def _send_bot_commands(self, commands: list[str]) -> bool:
        script = self._load_script()
        bot = script.get("bot", {})
        host = str(bot.get("host", "192.168.200.1"))
        port = int(bot.get("port", 9552))
        timeout_s = float(bot.get("timeout_s", 8.0))
        retry = int(bot.get("retry", 2))
        if bool(bot.get("dry_run", False)):
            for command in commands:
                self._emit("tx", f"BOT dry-run {command}")
                self._emit("rx", "BOT OK")
            return True
        with socket.create_connection((host, port), timeout=timeout_s) as conn:
            stream = conn.makefile("rwb")
            for command in commands:
                if not self._send_bot_command(stream, command, timeout_s, retry):
                    return False
        return True

    def _send_bot_command(self, stream: Any, command: str, timeout_s: float, retry: int) -> bool:
        for attempt in range(1, retry + 2):
            self._emit("tx", f"BOT {command}")
            stream.write((command.rstrip() + "\n").encode("utf-8"))
            stream.flush()
            started = time.monotonic()
            while time.monotonic() - started < timeout_s:
                try:
                    response = stream.readline().decode("utf-8", errors="replace").strip()
                except socket.timeout:
                    break
                if response:
                    self._emit("rx", f"BOT {response}")
                    if response.upper().startswith("OK"):
                        return True
                    break
            self._emit("err", f"BOT retry {attempt}/{retry + 1}: {command}")
        return False

    def _load_script(self) -> dict[str, Any]:
        return load_script(self.script_path)

    def _reply(self, fields: list[Any]) -> str:
        response = format_message(fields)
        self.last_tx = response
        self._emit("tx", response)
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
        self._notify_state()

    def _emit(self, direction: str, data: str) -> None:
        for callback in self._traffic_callbacks:
            try:
                callback(direction, data)
            except Exception:
                pass

    def _notify_state(self) -> None:
        status = self.get_status()
        for callback in self._state_callbacks:
            try:
                callback(status)
            except Exception:
                pass

    def _make_handler(self) -> type[socketserver.StreamRequestHandler]:
        class Handler(socketserver.StreamRequestHandler):
            def handle(inner_self) -> None:
                peer = f"{inner_self.client_address[0]}:{inner_self.client_address[1]}"
                logger.info(f"[Script] VS 已连接 {peer}")
                while True:
                    raw = inner_self.rfile.readline()
                    if not raw:
                        return
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    response = inner_self.server.service.handle_line(line)  # type: ignore[attr-defined]
                    inner_self.wfile.write((response + "\n").encode("utf-8"))
                    inner_self.wfile.flush()

        return Handler


class ThreadingTcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    service: ScriptService


def _resolve_path(path: str | Path) -> Path:
    item = Path(path)
    if item.is_absolute():
        return item
    return ROOT / item


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
