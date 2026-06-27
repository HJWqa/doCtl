# 接口矩阵

## 网络端口

| 服务 | 地址 | 协议 | 方向 | 用途 | doCtl 命令 |
|---|---|---|---|---|---|
| RK 3D HTTP | `192.168.173.2:8088` | HTTP JSON | doCtl -> 3D | 健康、桌面标定、点/ROI 测高 | `python ctl.py 3d ...` |
| RK 3D TCP JSON | `192.168.173.2:9099` | TCP JSON 行流 | doCtl -> 3D | 低层持续 3D 结果流，通常由 RK 内部使用 | `python ctl.py network tcp --host ... --port 9099` |
| RK task3 bridge | `192.168.173.2:9103` | TCP 行协议 | doCtl/VS -> 3D | `GET` 返回 YOLO+3D 8 段或 JSON | `python ctl.py task3 get` |
| RK auto | `192.168.173.2:9200` | TCP JSON 行协议 | doCtl/VS -> 3D | 自动桌面标定、多物块测高 | `python ctl.py rk-auto ...` |
| Vision Studio | `127.0.0.1:9550` 或现场端口 | TCP JSON | doCtl -> VS | 2D 目标检测或动作代理 | `python ctl.py vision detect` |
| Dobot/E6 | `192.168.200.1:9552` | TCP JSON | doCtl -> robot | 机器人动作，若不用 VS 直接控制时启用 | `python ctl.py arm home` |
| doCtl Web | `0.0.0.0:8080` | HTTP/WebSocket | Browser -> doCtl | 监控、启动/停止、日志 | `python ctl.py serve` |

## RK auto JSON 协议

请求：

```json
{"cmd":"health","request_id":"h1"}
{"cmd":"calibrate_table","request_id":"c1"}
{"cmd":"measure_objects","objects":[{"position_id":"slot_1"},{"u":225,"v":240}]}
```

响应：

```json
{
  "ok": true,
  "cmd": "measure_objects",
  "results": [
    {
      "ok": true,
      "position_id": "slot_1",
      "height_mm": 42.0,
      "robot_z_mm": 40.0
    }
  ]
}
```

## task3 bridge VS 协议

请求：

```text
GET
GET 1
```

返回：

```text
status;class_id;label;object_type;u;v;height_mm;robot_z_mm
```

字段：

| 字段 | 说明 |
|---|---|
| `status` | `OK` 才允许抓取 |
| `class_id` | `0=wrench`, `1=nut` |
| `label` | 类别名称 |
| `object_type` | 3D 形状/目标类型 |
| `u`,`v` | 3D 图像中心，参考或匹配用 |
| `height_mm` | 3D 测量高度 |
| `robot_z_mm` | 机器人 Z 修正 |

## 3D HTTP 调试接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 3D 服务状态 |
| GET | `/api/result` | 当前检测对象 |
| POST | `/api/calibrate/table` | 桌面深度标定 |
| POST | `/api/measure/point` | 按点测高 |
| POST | `/api/measure/roi` | 按 ROI 测高 |

## 数据融合约定

| 字段 | 来源 | 用途 |
|---|---|---|
| `x`,`y` | VS N 点标定转换 | 机器人平面抓取位置 |
| `angle` / `rz` | VS 2D 定位 | 末端旋转 |
| `robot_z_mm` | RK 3D | 抓取 Z 修正 |
| `class_id` | YOLO 或 VS | 放置分支 |
| `position_id` | VS/doCtl 配置 | 固定工位测高映射 |

如果物块任意摆放，优先走 `task3 bridge` 的 3D 图像匹配；如果固定工位，优先走 `rk-auto measure_objects position_id`。
