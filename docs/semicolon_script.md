# 分号协议脚本设计

## 总原则

所有比赛设备统一按字符串行通信：

```text
字段1;字段2;字段3;
```

解析时允许字段是 `str`、`int`、`float`。发送时统一补尾部分号。

配置文件：

```text
configs/competition_script.toml
```

预览命令：

```bash
python3 ctl.py script dry-run --task A
python3 ctl.py script dry-run --task B
python3 ctl.py script dry-run --task A --vs-message 'A;all;1;2;3;4;5;6;'
python3 ctl.py script 3d-send
python3 ctl.py script serve
```

## Task 2 / A

启动握手：

```text
VS  -> Ctl: start;A;all;
Ctl -> VS : start;A;all;
VS  -> Ctl: A;all;1;2;3;4;5;6;
```

默认字段含义：

| 字段 | 含义 |
|---|---|
| `A` | Task 2 |
| `all` | 一次返回多个物块 |
| `1,2` | 圆柱体 X/Y |
| `3,4` | 正方体 X/Y |
| `5,6` | 长方体 X/Y |

如果 VS 多返回字段，在 `tasks.A.vision_fields` 里继续增加对象或字段即可。

机械臂分步模式：

```text
MovJ;x;y;150;180;0;0;
MovL;x;y;123;180;0;0;
Suck;1;
MovJ;x;y;150;180;0;0;
MovJ;place_x;place_y;150;180;0;0;
MovL;place_x;place_y;place_z;180;0;0;
Sucl;0;
```

`place_z` 会按物块高度逐次增加，也可以在每个物体配置里单独指定高度。

机械臂 GP 模式：

```text
GP;pick_x;pick_y;pick_z;180;0;0;place_x;place_y;place_z;180;0;0;dock_x;dock_y;dock_z;180;0;0;
```

## Task 3 / B

流程：

```text
VS  -> Ctl: B;1;2;3;4;
Ctl -> 3D : B;start;
3D  -> Ctl: B;7;8;
Ctl -> Bot: GP;1;2;107;180;0;0;20;30;180;0;0;
Ctl -> Bot: GP;3;4;108;180;0;0;50;30;180;0;0;11;12;150;180;0;0;
```

默认字段含义：

| 字段 | 含义 |
|---|---|
| `B` | Task 3 |
| `1,2` | 扳手 X/Y |
| `3,4` | 螺母 X/Y |
| `7,8` | 扳手/螺母 3D Z 增量 |

`base_table_z` 默认是 `100`，实际抓取 Z = `base_table_z + 3D 返回 Z`。

RK 3D 侧对应服务：

```bash
cd /home/shiro/Projects/RK
./rk script3d serve
```

doCtl 默认连接 `tasks.B.three_d_host:tasks.B.three_d_port`，当前是 `192.168.173.2:9303`。

连通测试：

```bash
python3 ctl.py script 3d-send
```

## 常驻运行

比赛时不需要反复运行 dry-run 或 3d-send。启动一次即可：

```bash
python3 ctl.py serve
```

然后在 Web 里点击“启动”，Script 主控会监听 `configs/competition_script.toml -> [listen]` 配置的 VS 分号协议端口。
之后 VS 发送：

```text
start;A;all;
A;1;2;3;4;5;6;
B;1;2;3;4;
```

doCtl 会在同一个常驻服务里自动完成握手、3D 查询和 Bot 指令发送。

如果不需要 Web，也可以直接：

```bash
python3 ctl.py script serve
```

## 校验策略

- Task A 必须以 `A;all;...` 开头。
- Task B 必须以 `B;...` 开头。
- 字段数少于配置要求时驳回，不执行机械臂动作。
- 多出来的字段默认忽略，除非在配置中声明。
- 超时和重发参数放在 `[global]` 里，Bot 超时应大于 VS 超时。
