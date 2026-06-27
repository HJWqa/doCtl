# doCtl 2D 主控架构设计

## 1. 比赛部署边界

两台电脑通过 Thunderbolt/USB4 点对点网络连接：

| 设备 | IP | 职责 |
|---|---|---|
| Windows/2D 主控机 | `192.168.173.1/24` | Vision Studio、机器人流程、doCtl 主控、赛场 UI |
| Fedora/3D 视觉机 | `192.168.173.2/24` | RK 3D 平台、RealSense、YOLO、3D 高度/Z 服务 |

稳定分工：

| 内容 | 负责方 |
|---|---|
| 2D 图像采集、N 点标定、机器人 X/Y | Vision Studio / 2D 主控 |
| 桌面深度标定、工件高度、`robot_z_mm` | RK 3D 平台 |
| 扳手/螺母 YOLO 类别 | RK 任务三桥接或 2D 侧识别结果 |
| 流程状态机、异常恢复、日志、调试命令 | doCtl |
| 机器人最终动作 | Vision Studio 或 Dobot 机器人接口，比赛时只保留一个主控源 |

关键原则：不要把 2D 机器人坐标直接当成 3D 图像坐标。若需要让 3D 按固定工位测高，使用 `position_id` 映射到 3D 侧预设 `u/v` 或 ROI。

## 2. 接口分层

```text
doCtl CLI / Web
  -> Coordinator 状态机
     -> VisionClient        触发/接收 VS 2D 结果
     -> RkAutoClient        2D 请求 3D 自动桌面标定、多物块测高
     -> Task3BridgeClient   请求 YOLO+3D 融合类别/Z
     -> ThreeDHttpClient    3D HTTP 健康、单点/ROI 测高
     -> ArmClient           机器人动作接口或 VS 动作代理
```

## 3. 推荐运行模式

### 3.1 比赛自动模式

适合：2D 侧统一触发，3D 侧按固定工位或 3D 坐标测高。

链路：

```text
doCtl -> RK auto :9200 -> RK 3D HTTP :8088
```

2D 发送：

```json
{"cmd":"calibrate_table"}
{"cmd":"measure_objects","objects":[{"position_id":"slot_1"},{"position_id":"slot_2"}]}
```

### 3.2 任务三桥接模式

适合：3D 图像内用 YOLO 匹配目标，返回 `wrench/nut + height_mm + robot_z_mm`。

链路：

```text
doCtl 或 Vision Studio -> RK task3 bridge :9103
```

请求：

```text
GET
GET 1
```

### 3.3 低层手动调试模式

适合：单独验证 3D 平台、桌面标定、点测高、ROI 测高。

链路：

```text
doCtl -> RK 3D HTTP :8088
```

## 4. 主控状态机

```text
IDLE
  -> PRECHECK        检查 3D/auto/task3/VS/arm 可达
  -> TABLE_CALIB     清空桌面后请求 RK 自动桌面标定
  -> WAIT_VISION     等待 VS 输出目标或 doCtl 主动触发
  -> MEASURE_Z       根据 position_id / u,v / ROI 请求 3D 测高
  -> FUSE            组合 VS X/Y/R + 3D robot_z_mm + class_id
  -> EXECUTE         发给 VS/机器人动作
  -> VERIFY          检查动作结果和安全状态
  -> DONE 或 RECOVER
```

异常策略：

| 异常 | 处理 |
|---|---|
| 3D HTTP 不通 | 停在 PRECHECK，提示检查雷电网络、`./rk 3d serve` |
| auto `9200` 不通 | 可退化到 `ThreeDHttpClient` 手动测高，比赛自动模式停止 |
| task3 `9103` 返回 `NG` | 不执行抓取，回安全点 |
| VS 未给目标 | WAIT_VISION 重试，不移动机器人 |
| 机器人动作失败 | 停止循环，记录位姿和错误，等待人工确认 |

## 5. CLI 调试入口

统一从项目根目录运行：

```bash
python ctl.py <模块> <动作> [参数]
```

必要调试命令：

```bash
python ctl.py network probe
python ctl.py 3d health
python ctl.py 3d calibrate-table
python ctl.py 3d measure-point --u 225 --v 240
python ctl.py rk-auto health
python ctl.py rk-auto calibrate-table
python ctl.py rk-auto measure-objects --objects-json '[{"position_id":"slot_1"}]'
python ctl.py task3 get --index 0
python ctl.py vision detect
python ctl.py arm home
python ctl.py flow dry-run
python ctl.py serve --host 0.0.0.0 --port 8080
```

CLI 是比赛排障的第一入口；Web UI 只做监控和手动控制，不承担唯一调试能力。
