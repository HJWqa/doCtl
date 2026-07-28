"""
Werkzeug + simple-websocket 兼容补丁。

现象:
  GET /socket.io/?EIO=4&transport=websocket  ->  日志 500
  AssertionError: write() before start_response

原因:
  截至 python-engineio 4.13.3，threading 驱动的 SimpleWebSocketWSGI
  用 simple_websocket.Server 在原始 socket 上直接写 101，从不调用
  WSGI start_response。Werkzeug 开发服务器收尾时对空体 write(b"")
  触发断言。WebSocket 往往已连上，但服务端刷假 500。
  上游跟踪: https://github.com/miguelgrinberg/python-engineio/issues/457

处理:
  若检测到上游仍未调用 start_response，则替换 __call__：
  在 WebSocket 应用返回前调用一次 start_response('101 ...')，仅更新
  WSGI 状态；真实握手仍由 simple_websocket 完成。

用法:
  必须在创建 SocketIO 之前调用 apply_werkzeug_websocket_patch()。
  上游若已正式修复，本补丁会自动 no-op。
"""

from __future__ import annotations

import inspect

_applied = False
_skipped_reason: str | None = None


def apply_werkzeug_websocket_patch() -> bool:
    """幂等打补丁。返回 True 表示已应用，False 表示跳过/不需要。"""
    global _applied, _skipped_reason
    if _applied:
        return _skipped_reason is None

    try:
        import engineio.async_drivers._websocket_wsgi as wsgi_ws
        import simple_websocket
    except ImportError as exc:
        _skipped_reason = f"import failed: {exc}"
        _applied = True
        return False

    original = wsgi_ws.SimpleWebSocketWSGI.__call__
    if getattr(original, "_doct_ws_patched", False):
        _applied = True
        return True

    # 上游若已在 __call__ 里真正使用 start_response 参数（不仅是签名），则跳过。
    # 当前 4.13.3 签名含 start_response 但函数体从不调用，仍需补丁。
    try:
        src = inspect.getsource(original)
    except (OSError, TypeError):
        src = ""
    body_uses_start_response = (
        "start_response(" in src
        or "start_response (" in src
    )
    if body_uses_start_response:
        _skipped_reason = "upstream already calls start_response"
        _applied = True
        return False

    def _call(self, environ, start_response):  # type: ignore[no-untyped-def]
        self.ws = simple_websocket.Server(environ, **self.server_args)
        # 真实 101 头已由 simple_websocket 写到 socket；这里只让 Werkzeug
        # 认为响应已开始，避免收尾 write(b"") 断言失败。
        start_response(
            "101 Switching Protocols",
            [
                ("Upgrade", "websocket"),
                ("Connection", "Upgrade"),
            ],
        )
        try:
            self.app(self)
        finally:
            try:
                self.close()
            except Exception:
                pass
        if self.ws.mode == "gunicorn":
            raise StopIteration()
        return []

    _call._doct_ws_patched = True  # type: ignore[attr-defined]
    wsgi_ws.SimpleWebSocketWSGI.__call__ = _call  # type: ignore[method-assign]
    _skipped_reason = None
    _applied = True
    return True


def patch_status() -> dict[str, object]:
    """调试用：补丁是否已应用、是否跳过。"""
    return {
        "applied_once": _applied,
        "active": _applied and _skipped_reason is None,
        "skipped_reason": _skipped_reason,
    }
