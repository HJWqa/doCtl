"""
坐标融合模块
将 Vision Studio 的 (X, Y, angle) + 3D 相机的 Z → 完整 Pose
"""

from config import ARM_CONFIG
from utils.logger import logger


def merge_pose(x: float, y: float, z: float, angle: float = 0.0) -> dict:
    """
    融合 2D 坐标 + 高度 → 6DoF 位姿

    参数:
        x, y   : Vision Studio 输出的物理坐标 (mm)
        z      : 3D 相机输出的高度 (mm)
        angle  : 物体旋转角度 (°), 映射到 rz (绕 Z 轴)

    返回:
        {"x": ..., "y": ..., "z": ..., "rx": 180, "ry": 0, "rz": angle}
    """
    # 拾取姿态: 末端垂直向下 (rx=180 绕 X 翻转), rz 跟随物体角度
    pose = {
        "x": round(x, 1),
        "y": round(y, 1),
        "z": round(z, 1),
        "rx": 180.0,
        "ry": 0.0,
        "rz": round(angle, 1),
    }
    logger.debug(f"[Fusion] { (x, y, z, angle) } → Pose {pose}")
    return pose


def compute_offset_place(pose: dict, offset_x: float = None) -> dict:
    """
    基于拾取位姿计算放置位姿 (沿用户 X 偏移)
    """
    dx = offset_x if offset_x is not None else ARM_CONFIG["place_offset"]
    place = dict(pose)
    place["x"] = round(pose["x"] + dx, 1)
    return place
