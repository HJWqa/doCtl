#!/usr/bin/env python3
"""
dobotCtl — Dobot 智能拾取总控程序
带 Web 监控界面

用法:
    python main.py                # 默认启动
    python main.py --port 8080    # 指定端口
    python main.py --no-mock      # 真实设备模式
"""

import sys
import argparse
import threading

from flask import Flask

from config import WEB_HOST, WEB_PORT
from utils.logger import logger


def create_app(coordinator) -> Flask:
    app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
    app.config["SECRET_KEY"] = "dobot-ctl-2026"

    # 注册蓝图
    from web.routes import api, init_routes
    app.register_blueprint(api)
    init_routes(coordinator)

    # 初始化 SocketIO
    from web.socketio_handler import socketio, init_socketio
    socketio.init_app(app)
    init_socketio(coordinator)

    return app


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dobot 总控程序")
    parser.add_argument("--port", type=int, default=WEB_PORT, help=f"Web 端口 (默认 {WEB_PORT})")
    parser.add_argument("--no-mock", action="store_true", help="禁用模拟模式, 连接真实设备")
    parser.add_argument("--host", type=str, default=WEB_HOST, help=f"监听地址 (默认 {WEB_HOST})")
    args = parser.parse_args()

    # ★ 必须在创建 Coordinator 之前设置
    if args.no_mock:
        import config
        config.MOCK_MODE = False
        logger.info("真实设备模式已启用")

    # 创建协调器 (此时 config.MOCK_MODE 已确定)
    from coordinator import Coordinator
    coordinator = Coordinator()

    # 创建 Flask app
    app = create_app(coordinator)

    # 导入 socketio
    from web.socketio_handler import socketio

    logger.info(f"Web 界面: http://{args.host}:{args.port}")
    logger.info("启动后请在 Web 界面点击 '启动' 开始任务循环")

    # 启动 Flask + SocketIO
    socketio.run(app, host=args.host, port=args.port, debug=False, allow_unsafe_werkzeug=True)
