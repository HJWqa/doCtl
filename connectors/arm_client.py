"""
Dobot 机械臂 TCP Client (192.168.200.1:9552)
封装运动指令协议
"""

import time

from connectors.base import BaseTCPClient
from config import ARM_HOST, ARM_PORT, MOCK_MODE
from utils.logger import logger


class ArmClient(BaseTCPClient):
    """Dobot 机械臂客户端"""

    def __init__(self):
        super().__init__("机械臂", ARM_HOST, ARM_PORT)
        self._busy = False  # 执行中标志

    @property
    def is_busy(self) -> bool:
        return self._busy

    # ---------- API ----------

    def pick_and_place(self, pose: dict) -> dict | None:
        """
        执行拾放任务
        pose: {"x": ..., "y": ..., "z": ..., "rx": ..., "ry": ..., "rz": ...}
        返回: {"status": "ok"|"error", "message": str}
        """
        return self._exec_cmd("pick_and_place", pose)

    def move(self, pose: dict) -> dict | None:
        """纯移动 (不拾取)"""
        return self._exec_cmd("move", pose)

    def home(self) -> dict | None:
        """回零"""
        return self._exec_cmd("home")

    def _exec_cmd(self, cmd: str, pose: dict | None = None) -> dict | None:
        """通用指令执行"""
        if not self.is_connected:
            self._emit_err("未连接, 无法执行")
            logger.warn("[机械臂] 未连接")
            return None

        payload = {"cmd": cmd}
        if pose:
            payload["pose"] = pose

        # ---- 模拟模式 ----
        if self._mock:
            import json
            self._emit_data("tx", json.dumps(payload, ensure_ascii=False))
            self._busy = True
            logger.info(f"[机械臂] MOCK: 执行 {cmd}")
            time.sleep(1.5)  # 模拟机械臂运动时间
            self._busy = False
            resp = {"status": "ok", "message": "task done (mock)"}
            self._emit_data("rx", json.dumps(resp, ensure_ascii=False))
            logger.success(f"[机械臂] MOCK: {cmd} 完成")
            return resp
        # -----------------

        self._busy = True
        ok = self.send_json(payload)
        if not ok:
            self._busy = False
            return None

        resp = self.recv_json()
        self._busy = False

        if resp is None:
            logger.error("[机械臂] 无响应")
            return None

        if resp.get("status") == "ok":
            logger.success(f"[机械臂] {cmd} 完成")
        else:
            logger.error(f"[机械臂] {cmd} 失败: {resp.get('message')}")

        return resp
