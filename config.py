"""
dobotCtl 全局配置

注意：
- 比赛自动 Script 的 VS / RK 3D / Dobot Bot IP、端口、运动参数，以
  configs/competition_script.toml 为准。
- 本文件只保留 Web 服务端口、旧版调试命令和兼容客户端的默认值。
"""

import os
import re
import ast

_CONFIG_FILE = os.path.abspath(__file__)

# ============================================================
# 网络配置：旧版 CLI/调试客户端默认值，比赛 Script 不读取这些值。
# ============================================================
VISION_HOST = "172.29.64.1"
VISION_PORT = 7930

# Fedora/3D 视觉机 Thunderbolt/USB4 地址
THREE_D_HOST = "192.168.173.2"
THREE_D_HTTP_PORT = 8088
THREE_D_TCP_PORT = 9099

# 旧 RK auto 服务：保留给历史调试命令，比赛 Script 不直接连接它。
RK_AUTO_HOST = "192.168.173.2"
RK_AUTO_PORT = 9200

# 旧 task3 bridge：保留给历史调试命令，比赛 Script 只连接 [three_d]。
TASK3_HOST = "192.168.173.2"
TASK3_PORT = 9103

CAMERA3D_HOST = "192.168.173.2"
CAMERA3D_PORT = 9551

ARM_HOST = "192.168.200.1"
ARM_PORT = 9552

# Web 服务
WEB_HOST = "0.0.0.0"
WEB_PORT = 8080

# ============================================================
# 运动参数
# ============================================================
ARM_CONFIG = {
    "lift_z": 50,            # 提起高度 (mm)，沿工具 Z 方向
    "place_offset": 50,      # 放置点 X 偏移 (mm)，沿用户 X 方向
    "speed_approach": 50,    # 接近速度 (0,100]
    "speed_work": 30,        # 工作速度 (0,100]
    "acc": 50,               # 加速度 (0,100]
    "place_pose": [300, 0, 100, 180, 0, 0],  # 固定放置点
    "home_pose": [0, 0, 300, 180, 0, 0],     # 安全原点
}

# ============================================================
# 连接与重连
# ============================================================
RECONNECT_INITIAL = 1        # 首次重连间隔 (秒)
RECONNECT_MAX = 30           # 最大重连间隔 (秒)
RECONNECT_BACKOFF = 2        # 退避倍数
SOCKET_TIMEOUT = 3           # TCP 操作超时 (秒)

# ============================================================
# 任务参数
# ============================================================
CYCLE_INTERVAL_MIN = 0.5     # 主循环间隔 (秒) — 无物体时等待
COMMAND_TIMEOUT = 10         # 单次指令超时 (秒)
MAX_LOG_LINES = 500          # Web UI 日志最大行数

# ============================================================
# 模式
# ============================================================
MOCK_MODE = False

# 主控高度链路：rk_auto 为推荐比赛链路；legacy_9551 为旧 get_height 兼容链路。
HEIGHT_SOURCE = "rk_auto"


# ============================================================
# 配置导出 (供 Web 界面读取和修改)
# ============================================================

# 允许 Web 界面修改的配置项白名单。
# 比赛 Script 的通信参数不在这里修改，请直接改 configs/competition_script.toml。
_EDITABLE_KEYS = {
    "WEB_HOST", "WEB_PORT",
    "MOCK_MODE",
}

# 哪些是 int 类型 (需要转换)
_INT_KEYS = {
    "WEB_PORT",
}

# 哪些是 bool 类型
_BOOL_KEYS = {"MOCK_MODE"}


def get_editable_config() -> dict:
    """返回 Web 界面可编辑的配置项"""
    import sys
    mod = sys.modules[__name__]
    return {k: getattr(mod, k) for k in _EDITABLE_KEYS}


def save_config(updates: dict) -> dict:
    """
    将变更写入 config.py 并更新当前模块变量。
    返回: {"ok": [...], "rejected": [...]}
    """
    rejected = []
    applied = {}

    # 校验 & 类型转换
    for k, v in updates.items():
        if k not in _EDITABLE_KEYS:
            rejected.append(k)
            continue
        if k in _INT_KEYS:
            try:
                v = int(v)
            except (TypeError, ValueError):
                rejected.append(k)
                continue
        if k in _BOOL_KEYS:
            if isinstance(v, str):
                v = v.lower() in ("true", "1", "yes")
            else:
                v = bool(v)
        applied[k] = v

    # 写回文件
    try:
        _write_config_file(applied)
    except Exception as e:
        return {"ok": [], "rejected": list(applied.keys()), "error": str(e)}

    # 更新当前模块变量
    import sys
    mod = sys.modules[__name__]
    for k, v in applied.items():
        setattr(mod, k, v)

    return {"ok": list(applied.keys()), "rejected": rejected}


def _write_config_file(updates: dict):
    """用正则替换的方式写回 config.py, 保留注释和格式"""
    with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    for key, value in updates.items():
        if isinstance(value, bool):
            replacement = f"{key} = {value}"
        elif isinstance(value, int):
            replacement = f"{key} = {value}"
        elif isinstance(value, str):
            replacement = f'{key} = "{value}"'
        else:
            replacement = f"{key} = {repr(value)}"

        # 匹配: KEY = <anything>   (不捕获后续注释, 但保留前置缩进/注释)
        pattern = rf"^({key}\s*=\s*)(.+)$"
        content = re.sub(pattern, replacement, content, flags=re.MULTILINE)

    # 原子写入
    tmp = _CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, _CONFIG_FILE)
