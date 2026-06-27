"""
RK 3D HTTP client.

Talks to the Fedora 3D platform on port 8088.
"""

from __future__ import annotations

from typing import Any

from config import THREE_D_HOST, THREE_D_HTTP_PORT, SOCKET_TIMEOUT
from connectors.http_json_client import HttpJsonClient


class ThreeDHttpClient(HttpJsonClient):
    def __init__(self, host: str = THREE_D_HOST, port: int = THREE_D_HTTP_PORT):
        super().__init__(host, port, name="RK-3D-HTTP", timeout_s=SOCKET_TIMEOUT)

    def health(self) -> dict[str, Any]:
        return self.get("/api/health")

    def result(self) -> dict[str, Any]:
        return self.get("/api/result")

    def calibrate_table(self) -> dict[str, Any]:
        return self.post("/api/calibrate/table")

    def measure_point(self, u: int, v: int, radius_px: int = 16) -> dict[str, Any]:
        return self.post("/api/measure/point", {"u": int(u), "v": int(v), "radius_px": int(radius_px)})

    def measure_roi(self, x: int, y: int, w: int, h: int) -> dict[str, Any]:
        return self.post("/api/measure/roi", {"x": int(x), "y": int(y), "w": int(w), "h": int(h)})
