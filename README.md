# doCtl

2D 主控程序。比赛部署中，doCtl 运行在 Windows/2D 主控机，统一调度 Vision Studio、RK 3D 视觉机、任务三桥接和机器人动作。

## 当前架构文档

- `docs/architecture.md`：2D 主控总体架构、状态机、比赛部署边界
- `docs/interface_matrix.md`：所有接口、端口、协议和 CLI 调试命令

## CLI 调试入口

所有关键接口都有独立命令：

```bash
python ctl.py -help
python ctl.py network probe
python ctl.py 3d health
python ctl.py 3d calibrate-table
python ctl.py 3d measure-point --u 225 --v 240 --radius-px 18
python ctl.py rk-auto health
python ctl.py rk-auto calibrate-table
python ctl.py rk-auto measure-objects --objects-json '[{"position_id":"slot_1"}]'
python ctl.py task3 get --index 0
python ctl.py vision detect
python ctl.py arm home
```

## Web 主控

```bash
python ctl.py serve --host 0.0.0.0 --port 8080
```

## 网络默认值

| 服务 | 默认地址 |
|---|---|
| 2D 主控机 | `192.168.173.1/24` |
| 3D 视觉机 | `192.168.173.2/24` |
| RK 3D HTTP | `192.168.173.2:8088` |
| RK 3D TCP JSON | `192.168.173.2:9099` |
| RK task3 bridge | `192.168.173.2:9103` |
| RK auto | `192.168.173.2:9200` |
| Dobot/E6 | `192.168.200.1:9552` |
