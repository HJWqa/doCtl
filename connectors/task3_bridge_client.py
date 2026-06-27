"""
Task3 bridge TCP client.

Protocol:
  request: GET or GET <index>
  response: status;class_id;label;object_type;u;v;height_mm;robot_z_mm
"""

from __future__ import annotations

from typing import Any

from config import TASK3_HOST, TASK3_PORT
from connectors.base import BaseTCPClient


class Task3BridgeClient(BaseTCPClient):
    def __init__(self, host: str = TASK3_HOST, port: int = TASK3_PORT):
        super().__init__("任务三桥接", host, port)

    def get(self, index: int | None = None) -> dict[str, Any] | None:
        if not self.is_connected and not self.connect():
            return None
        request = "GET" if index is None else f"GET {int(index)}"
        if not self._send_raw(request):
            return None
        raw = self._recv_raw()
        if raw is None:
            return None
        return parse_task3_response(raw)


def parse_task3_response(raw: str) -> dict[str, Any]:
    fields = raw.strip().split(";")
    if len(fields) != 8:
        return {"ok": False, "status": "NG", "error": f"expected 8 fields, got {len(fields)}", "raw": raw}
    status, class_id, label, object_type, u, v, height_mm, robot_z_mm = fields
    return {
        "ok": status == "OK",
        "status": status,
        "class_id": class_id,
        "label": label,
        "object_type": object_type,
        "u": _int_or_zero(u),
        "v": _int_or_zero(v),
        "height_mm": _float_or_zero(height_mm),
        "robot_z_mm": _float_or_zero(robot_z_mm),
        "raw": raw,
    }


def _int_or_zero(value: str) -> int:
    try:
        return int(float(value))
    except ValueError:
        return 0


def _float_or_zero(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        return 0.0
