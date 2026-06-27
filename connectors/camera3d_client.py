"""
3D 深度相机 TCP Client (192.168.173.2:9551)
封装高度查询协议
"""

import random

from connectors.base import BaseTCPClient
from config import CAMERA3D_HOST, CAMERA3D_PORT, MOCK_MODE
from utils.logger import logger


class Camera3DClient(BaseTCPClient):
    """3D 相机客户端"""

    def __init__(self):
        super().__init__("3D相机", CAMERA3D_HOST, CAMERA3D_PORT)

    # ---------- API ----------

    def get_height(self, x: float, y: float) -> float | None:
        """
        查询 (x, y) 处物体表面高度
        返回: z (mm); 失败返回 None
        """
        if not self.is_connected:
            self._emit_err("未连接, 无法测高")
            logger.warn("[3D相机] 未连接, 无法测高")
            return None

        # ---- 模拟模式 ----
        if self._mock:
            z = round(random.uniform(10.0, 50.0), 1)
            self._emit_data("tx", f'{{"cmd":"get_height","x":{x},"y":{y}}}')
            self._emit_data("rx", f'{{"status":"ok","z":{z}}}')
            logger.success(f"[3D相机] MOCK: X={x} Y={y} → Z={z}")
            return z
        # -----------------

        ok = self.send_json({"cmd": "get_height", "x": x, "y": y})
        if not ok:
            return None

        resp = self.recv_json()
        if resp is None:
            return None

        if resp.get("status") == "ok":
            z = resp.get("z")
            logger.success(f"[3D相机] X={x} Y={y} → Z={z}")
            return z

        logger.warn(f"[3D相机] 错误响应: {resp.get('message')}")
        return None
