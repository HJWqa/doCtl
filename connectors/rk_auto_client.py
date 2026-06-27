"""
RK auto TCP JSON-line client.

This is the recommended 2D master-control path for automatic table calibration
and multi-object height measurement.
"""

from __future__ import annotations

from typing import Any

from config import RK_AUTO_HOST, RK_AUTO_PORT
from connectors.base import BaseTCPClient


class RkAutoClient(BaseTCPClient):
    def __init__(self, host: str = RK_AUTO_HOST, port: int = RK_AUTO_PORT):
        super().__init__("RK自动服务", host, port)

    def health(self) -> dict[str, Any] | None:
        return self.request({"cmd": "health", "request_id": "doctl-health"})

    def calibrate_table(self) -> dict[str, Any] | None:
        return self.request({"cmd": "calibrate_table", "request_id": "doctl-calib"})

    def measure_objects(self, objects: list[dict[str, Any]]) -> dict[str, Any] | None:
        return self.request({"cmd": "measure_objects", "request_id": "doctl-measure", "objects": objects})

    def measure_point(self, u: int, v: int, radius_px: int = 16) -> dict[str, Any] | None:
        return self.request({"cmd": "measure_point", "u": int(u), "v": int(v), "radius_px": int(radius_px)})

    def measure_roi(self, x: int, y: int, w: int, h: int) -> dict[str, Any] | None:
        return self.request({"cmd": "measure_roi", "x": int(x), "y": int(y), "w": int(w), "h": int(h)})

    def request(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.is_connected and not self.connect():
            return None
        if not self.send_json(payload):
            return None
        return self.recv_json()
