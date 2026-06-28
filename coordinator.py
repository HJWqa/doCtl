"""
任务协调器
按序调度: 视觉检测 → 高度查询 → 坐标融合 → 机械臂执行

作为后台线程运行, 通过回调/事件与 Web 层通信
"""

import time
import threading

from connectors.vision_client import VisionClient
from connectors.camera3d_client import Camera3DClient
from connectors.three_d_http_client import ThreeDHttpClient
from connectors.rk_auto_client import RkAutoClient
from connectors.task3_bridge_client import Task3BridgeClient
from connectors.arm_client import ArmClient
from competition.script_service import ScriptService
from fusion import merge_pose
from config import CYCLE_INTERVAL_MIN, MOCK_MODE
from utils.logger import logger


class Coordinator:
    """任务协调器 (后台线程)"""

    def __init__(self):
        self.vision = VisionClient()
        self.three_d = ThreeDHttpClient()
        self.rk_auto = RkAutoClient()
        self.task3 = Task3BridgeClient()
        self.camera3d = Camera3DClient()
        self.arm = ArmClient()
        self.script = ScriptService()

        self._thread: threading.Thread | None = None
        self._running = False
        self._paused = False

        # 统计
        self.total_tasks = 0
        self.success_tasks = 0
        self.fail_tasks = 0
        self._stats_lock = threading.Lock()

        # 状态回调 (供 WebSocket 推送)
        self._state_callbacks = []

    # ---------- 生命周期 ----------

    def start(self):
        """启动协调器 (并行连接设备 + 启动主循环)"""
        logger.info("=" * 50)
        logger.info("Dobot 总控程序 v1.0 启动")
        if MOCK_MODE:
            logger.warn("当前为 **模拟模式**, 不会连接真实设备")

        # ★ 并行连接所有设备, 不阻塞
        threads = []
        for client in [self.vision, self.rk_auto, self.task3, self.camera3d, self.arm]:
            t = threading.Thread(target=client.connect, daemon=True)
            t.start()
            threads.append(t)

        # 等待连接完成 (最慢的连不上也只等 SOCKET_TIMEOUT 秒)
        for t in threads:
            t.join(timeout=6)

        # 机械臂回零
        if self.arm.is_connected:
            self.arm.home()

        # 启动后台循环
        self._running = True
        self._paused = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.success("协调器已启动")
        self._notify_state()

    def stop(self):
        """停止协调器"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        self.vision.disconnect()
        self.rk_auto.disconnect()
        self.task3.disconnect()
        self.camera3d.disconnect()
        self.arm.disconnect()
        logger.info("协调器已停止")

    def pause(self):
        self._paused = True
        logger.info("协调器已暂停")

    def resume(self):
        self._paused = False
        logger.info("协调器已恢复")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    # ---------- 状态获取 ----------

    def get_status(self) -> dict:
        with self._stats_lock:
            return {
                "running": self._running,
                "paused": self._paused,
                "total_tasks": self.total_tasks,
                "success_tasks": self.success_tasks,
                "fail_tasks": self.fail_tasks,
                "script": self.script.get_status(),
                "arm_busy": self.arm.is_busy,
            }

    def on_state_change(self, callback):
        """注册状态变更回调 callback(status_dict)"""
        self._state_callbacks.append(callback)

    def _notify_state(self):
        status = self.get_status()
        for cb in self._state_callbacks:
            try:
                cb(status)
            except Exception:
                pass

    def _three_d_http_ok(self) -> bool:
        try:
            data = self.three_d.health()
            return bool(data.get("ok"))
        except Exception:
            return False

    # ---------- 主循环 ----------

    def _loop(self):
        """主循环: 视觉 → 高度 → 执行"""
        logger.info("主循环已启动")

        while self._running:
            # 暂停检查
            if self._paused:
                time.sleep(0.2)
                continue

            # 连接检查 & 自动重连
            self._ensure_connections()

            # 检查机械臂是否繁忙
            if self.arm.is_busy:
                time.sleep(0.3)
                continue

            # ---- 步骤 1: 视觉检测 ----
            objects = self.vision.detect()
            self._notify_state()

            if objects is None:
                # 通信异常
                time.sleep(CYCLE_INTERVAL_MIN)
                continue

            if not objects:
                # 无物体, 等待后重试
                time.sleep(CYCLE_INTERVAL_MIN)
                continue

            # 取第一个物体
            obj = objects[0]
            logger.info(f"→ 处理物体 #{obj.get('id')}: X={obj['x']} Y={obj['y']}")

            # ---- 步骤 2: 高度查询 ----
            z = self._measure_robot_z(obj)
            self._notify_state()

            if z is None:
                logger.warn(f"物体 #{obj.get('id')} 高度获取失败, 跳过")
                with self._stats_lock:
                    self.total_tasks += 1
                    self.fail_tasks += 1
                self._notify_state()
                continue

            # ---- 步骤 3: 坐标融合 ----
            pose = merge_pose(obj["x"], obj["y"], z, obj.get("angle", 0))

            # ---- 步骤 4: 机械臂执行 ----
            result = self.arm.pick_and_place(pose)
            self._notify_state()

            with self._stats_lock:
                self.total_tasks += 1
                if result and result.get("status") == "ok":
                    self.success_tasks += 1
                else:
                    self.fail_tasks += 1
            self._notify_state()

            # 任务间短暂间隔
            time.sleep(0.3)

    def _ensure_connections(self):
        """检查并自动重连各设备"""
        for client in [self.vision, self.rk_auto, self.task3, self.camera3d, self.arm]:
            if not client.is_connected:
                client.reconnect()
                self._notify_state()

    def _measure_robot_z(self, obj: dict) -> float | None:
        """根据配置选择高度链路，返回给机器人使用的 Z 修正。"""
        import config

        if config.HEIGHT_SOURCE == "rk_auto":
            request_obj = self._build_rk_auto_object(obj)
            resp = self.rk_auto.measure_objects([request_obj])
            if not resp or not resp.get("ok"):
                logger.warn(f"RK auto 测高失败: {resp}")
                return None
            results = resp.get("results") or []
            if not results:
                logger.warn("RK auto 响应没有 results")
                return None
            first = results[0]
            z = first.get("robot_z_mm", first.get("height_mm"))
            logger.success(f"[RK auto] {request_obj} -> Z={z}")
            return float(z)

        return self.camera3d.get_height(obj["x"], obj["y"])

    def _build_rk_auto_object(self, obj: dict) -> dict:
        """把 VS 目标转换成 RK auto 可理解的测高对象。"""
        out = {
            "id": obj.get("id"),
            "class_id": obj.get("class_id"),
            "label": obj.get("label"),
        }
        for key in ("position_id", "u", "v", "radius_px", "x", "y", "w", "h"):
            if key in obj:
                out[key] = obj[key]
        if len([v for v in out.values() if v is not None]) <= 1:
            out["position_id"] = obj.get("id")
        return {k: v for k, v in out.items() if v is not None}

    # ---------- 配置热更新 ----------

    def get_config(self) -> dict:
        """导出可编辑配置项 (供 Web 界面)"""
        from config import get_editable_config
        return get_editable_config()

    def apply_config(self, updates: dict) -> dict:
        """
        保存配置并热重连。
        如果协调器正在运行, 会断开旧连接、创建新客户端、重新连接。
        返回: save_config 的结果
        """
        from config import save_config

        was_running = self._running

        # 先停止循环 (避免旧客户端继续收发)
        if was_running:
            self._running = False
            if self._thread:
                self._thread.join(timeout=3)

        # 断开旧连接
        self.vision.disconnect()
        self.rk_auto.disconnect()
        self.task3.disconnect()
        self.camera3d.disconnect()
        self.arm.disconnect()

        # 写入配置文件
        result = save_config(updates)

        if not result["ok"]:
            logger.error(f"配置保存失败: {result}")
            # 恢复
            if was_running:
                self._running = True
                self._thread = threading.Thread(target=self._loop, daemon=True)
                self._thread.start()
            self._notify_state()
            return result

        # 用新 host/port 重建客户端
        from config import VISION_HOST, VISION_PORT, \
            RK_AUTO_HOST, RK_AUTO_PORT, TASK3_HOST, TASK3_PORT, \
            CAMERA3D_HOST, CAMERA3D_PORT, ARM_HOST, ARM_PORT

        self.vision.host = VISION_HOST
        self.vision.port = VISION_PORT
        self.rk_auto.host = RK_AUTO_HOST
        self.rk_auto.port = RK_AUTO_PORT
        self.task3.host = TASK3_HOST
        self.task3.port = TASK3_PORT
        self.camera3d.host = CAMERA3D_HOST
        self.camera3d.port = CAMERA3D_PORT
        self.arm.host = ARM_HOST
        self.arm.port = ARM_PORT

        logger.success(f"配置已更新: {result['ok']}")

        # 重新连接
        self.vision.connect()
        self.rk_auto.connect()
        self.task3.connect()
        self.camera3d.connect()
        self.arm.connect()

        # 恢复运行
        if was_running:
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

        self._notify_state()
        return result
