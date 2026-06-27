"""
Flask HTTP 路由
"""

from flask import Blueprint, render_template, jsonify, request

api = Blueprint("api", __name__)

# coordinator 引用由 main.py 注入
_coordinator = None


def init_routes(coordinator):
    global _coordinator
    _coordinator = coordinator


@api.route("/")
def index():
    """主页面"""
    return render_template("index.html")


# ---------- REST API ----------

@api.route("/api/status")
def api_status():
    """获取系统状态"""
    if _coordinator is None:
        return jsonify({"error": "coordinator not initialized"}), 503
    return jsonify(_coordinator.get_status())


@api.route("/api/logs")
def api_logs():
    """获取最近日志"""
    from utils.logger import logger

    n = request.args.get("n", 100, type=int)
    return jsonify({"logs": logger.get_recent(n)})


@api.route("/api/control", methods=["POST"])
def api_control():
    """控制指令: start / stop / pause / resume / home"""
    if _coordinator is None:
        return jsonify({"error": "coordinator not initialized"}), 503

    data = request.get_json(silent=True) or {}
    cmd = data.get("cmd", "")

    handlers = {
        "start": lambda: _coordinator.start(),
        "stop": lambda: _coordinator.stop(),
        "pause": lambda: _coordinator.pause(),
        "resume": lambda: _coordinator.resume(),
        "home": lambda: _coordinator.arm.home(),
    }

    if cmd not in handlers:
        return jsonify({"error": f"unknown cmd: {cmd}"}), 400

    try:
        handlers[cmd]()
        return jsonify({"status": "ok", "cmd": cmd})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@api.route("/api/arm/move", methods=["POST"])
def api_arm_move():
    """手动控制机械臂移动到指定位姿"""
    if _coordinator is None:
        return jsonify({"error": "coordinator not initialized"}), 503

    data = request.get_json(silent=True) or {}
    pose = data.get("pose")
    if not pose or not all(k in pose for k in ["x", "y", "z", "rx", "ry", "rz"]):
        return jsonify({"error": "pose requires x,y,z,rx,ry,rz"}), 400

    result = _coordinator.arm.move(pose)
    return jsonify(result or {"status": "error", "message": "no response"})


# ---------- 配置管理 ----------

@api.route("/api/config")
def api_get_config():
    """获取可编辑配置项"""
    if _coordinator is None:
        return jsonify({"error": "coordinator not initialized"}), 503
    return jsonify(_coordinator.get_config())


@api.route("/api/config", methods=["POST"])
def api_save_config():
    """保存配置并热重连"""
    if _coordinator is None:
        return jsonify({"error": "coordinator not initialized"}), 503

    data = request.get_json(silent=True) or {}
    result = _coordinator.apply_config(data)
    return jsonify(result)


@api.route("/api/config/test", methods=["POST"])
def api_test_connection():
    """测试 TCP 连接是否可达 (不依赖协调器状态)"""
    import socket

    data = request.get_json(silent=True) or {}
    host = data.get("host", "").strip()
    port = data.get("port")

    if not host or not port:
        return jsonify({"error": "host and port required"}), 400

    try:
        port = int(port)
    except (TypeError, ValueError):
        return jsonify({"error": "port must be integer"}), 400

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        err = sock.connect_ex((host, port))
        sock.close()
        if err == 0:
            return jsonify({"host": host, "port": port, "reachable": True, "message": f"{host}:{port} 可达 ✓"})
        else:
            return jsonify({"host": host, "port": port, "reachable": False, "message": f"{host}:{port} 拒绝连接 (err={err})"})
    except socket.gaierror:
        return jsonify({"host": host, "port": port, "reachable": False, "message": f"无法解析主机名 {host}"})
    except Exception as e:
        return jsonify({"host": host, "port": port, "reachable": False, "message": str(e)})
