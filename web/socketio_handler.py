"""
Flask-SocketIO 事件处理
推送实时状态 & 日志到 Web 前端
"""

from flask_socketio import SocketIO

socketio = SocketIO(async_mode="threading", cors_allowed_origins="*")

# 引用由 main.py 注入
_coordinator = None


def init_socketio(coordinator):
    global _coordinator
    _coordinator = coordinator

    # 日志推送
    from utils.logger import logger

    def on_log(level, msg, ts):
        socketio.emit("log", {"level": level, "msg": msg, "ts": ts})

    logger.on_log(on_log)

    # 状态变更推送
    def on_state(status):
        socketio.emit("status", status)

    coordinator.on_state_change(on_state)

    # 数据收发推送 (每个设备)
    for client, dev_id in [
        (coordinator.vision,    "vision"),
        (coordinator.camera3d,  "camera3d"),
        (coordinator.arm,       "arm"),
    ]:
        def make_handler(c, did):
            def on_data(direction, data_str):
                socketio.emit("data_traffic", {
                    "device": did,
                    "device_name": c.name,
                    "host": f"{c.host}:{c.port}",
                    "direction": direction,  # "tx" or "rx"
                    "data": data_str,
                })
            return on_data
        client.on_data(make_handler(client, dev_id))


@socketio.on("connect")
def handle_connect():
    """WebSocket 客户端连接"""
    if _coordinator:
        socketio.emit("status", _coordinator.get_status())
        from utils.logger import logger
        socketio.emit("log_batch", {"logs": logger.get_recent(50)})


@socketio.on("control")
def handle_control(data):
    """前端控制指令 (通过 WebSocket)"""
    if _coordinator is None:
        return

    cmd = data.get("cmd", "")
    handlers = {
        "start": lambda: _coordinator.start(),
        "stop": lambda: _coordinator.stop(),
        "pause": lambda: _coordinator.pause(),
        "resume": lambda: _coordinator.resume(),
        "home": lambda: _coordinator.arm.home(),
    }

    if cmd in handlers:
        try:
            handlers[cmd]()
        except Exception as e:
            socketio.emit("error", {"message": str(e)})
