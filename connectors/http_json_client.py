"""
Small standard-library HTTP JSON client for RK 3D platform calls.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class HttpJsonError(RuntimeError):
    pass


class HttpJsonClient:
    def __init__(self, host: str, port: int, name: str = "HTTP", timeout_s: float = 3.0):
        self.host = host
        self.port = int(port)
        self.name = name
        self.timeout_s = float(timeout_s)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def get(self, path: str) -> dict[str, Any]:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("POST", path, payload)

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                raw = response.read().decode("utf-8")
                data = json.loads(raw) if raw else {}
                if not isinstance(data, dict):
                    raise HttpJsonError(f"{url} returned non-object JSON")
                return data
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"ok": False, "error": raw or f"HTTP {exc.code}"}
            if isinstance(data, dict):
                data.setdefault("ok", False)
                data.setdefault("http_status", exc.code)
                return data
            return {"ok": False, "http_status": exc.code, "error": raw}
        except Exception as exc:
            raise HttpJsonError(str(exc)) from exc
