"""
Vision Studio TCP Client (127.0.0.1:9550)
封装 2D 视觉检测协议
"""

import json
import random

from connectors.base import BaseTCPClient
from config import VISION_HOST, VISION_PORT, COMMAND_TIMEOUT, MOCK_MODE
from utils.logger import logger


class VisionClient(BaseTCPClient):
    """Vision Studio 2D 视觉客户端"""

    def __init__(self):
        super().__init__("Vision", VISION_HOST, VISION_PORT)
        self._mock_objects = [
            {"id": 1, "x": 250.0, "y": 150.0, "angle": 30.0},
            {"id": 2, "x": 320.5, "y": 180.2, "angle": 45.0},
            {"id": 3, "x": 180.0, "y": 220.0, "angle": 0.0},
        ]
        self._mock_idx = 0

    # ---------- API ----------

    def detect(self) -> list[dict] | None:
        """
        触发目标检测
        返回: [{"id": int, "x": float, "y": float, "angle": float}, ...]
              无物体时返回 []
              连接/通信异常返回 None
        """
        if not self.is_connected:
            self._emit_err("未连接, 无法检测")
            logger.warn("[Vision] 未连接, 无法检测")
            return None

        # ---- 模拟模式 ----
        if self._mock:
            # 模拟: 有时返回物体, 有时空
            if random.random() < 0.25:
                self._emit_data("tx", '{"cmd":"detect","camera_id":0}')
                self._emit_data("rx", '{"status":"ok","objects":[]}')
                logger.debug("[Vision] MOCK: 本轮无物体")
                return []
            obj = self._mock_objects[self._mock_idx % len(self._mock_objects)]
            self._mock_idx += 1
            self._emit_data("tx", '{"cmd":"detect","camera_id":0}')
            resp_str = json.dumps({"status": "ok", "objects": [obj]}, ensure_ascii=False)
            self._emit_data("rx", resp_str)
            logger.success(f"[Vision] MOCK: 检测到物体 id={obj['id']} X={obj['x']} Y={obj['y']}")
            return [dict(obj)]
        # -----------------

        ok = self.send_json({"cmd": "detect", "camera_id": 0})
        if not ok:
            return None

        resp = self.recv_json()
        if resp is None:
            return None

        if resp.get("status") == "ok":
            objects = resp.get("objects", [])
            if objects:
                logger.success(f"[Vision] 检测到 {len(objects)} 个物体")
            else:
                logger.info("[Vision] 未检测到物体")
            return objects

        logger.warn(f"[Vision] 错误响应: {resp.get('message')}")
        return None
