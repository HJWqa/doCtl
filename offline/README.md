# 离线流程脚本

这套方案用于离线调试流程，不连接真实设备。调试人员编辑 `flows/*.toml`，再运行 runner 查看 Vision、3D 相机、融合、机械臂各阶段的 TX/RX 和结果。

## 运行

```bash
python3 offline/run_flow.py flows/offline_pick_demo.toml
```

临时重复运行 3 次：

```bash
python3 offline/run_flow.py flows/offline_pick_demo.toml --repeat 3
```

如果要给自动化检查使用，并希望任一流程失败时返回非 0：

```bash
python3 offline/run_flow.py flows/offline_pick_demo.toml --strict-exit
```

## 编辑方式

每个 `[[cycles]]` 是一轮流程。

- `vision_objects = []` 表示 Vision 未检测到目标。
- `vision_objects = [{ id = 1, x = 250.0, y = 150.0, angle = 30.0 }]` 表示检测到目标。
- `camera_z = 24.5` 表示 3D 相机返回高度。
- `camera_status = "error"` 可模拟测高失败。
- `arm_status = "error"` 可模拟机械臂失败。
- `pose_override = { z = 50.0 }` 可覆盖融合后的位姿字段。

输出 JSONL 默认写到 `offline/runs/`，便于回放、对比或贴到问题单里。
