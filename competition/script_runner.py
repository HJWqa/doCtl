"""
比赛剧本执行器 — 将 TOML 配置 + VS 分号协议消息 → Bot 运动指令序列。

========== 核心概念 ==========

1. VS (Vision Studio) 发来的分号协议消息，例如:
     A;all;圆X;圆Y;正方体X;正方体Y;长方体X;长方体Y;
     B;扳手X;扳手Y;螺母X;螺母Y;

2. 本文件负责:
   - 解析 VS 消息中的坐标字段
   - 结合 TOML 里的任务配置 (抓取Z、放置XY、姿态RPY 等)
   - 生成发送给 Dobot Bot 的分号指令序列，例如:
     MovJ;X;Y;Z;Rx;Ry;Rz;
     MovL;X;Y;Z;Rx;Ry;Rz;
     Suck;1;
     GP;X;Y;Z;Rx;Ry;Rz;destX;destY;destZ;Rx;Ry;Rz;

3. "dry_run" 的含义: 本模块只生成指令字符串，不实际连接设备。
   实际的 TCP 收发由 ScriptService (script_service.py) 负责。

========== 协议规则 ==========

- 所有指令都是 分号分隔 + 结尾分号，例如: Suck;1;
- 数值自动转整数或浮点数
- 姿态用 RPY (Roll, Pitch, Yaw)，格式: Rx;Ry;Rz

========== 修改指南 ==========

- 改步序: 修改 _step_commands() 的 return 列表，增删/调整指令顺序
- 改 GP 模式: 修改 _gp_command() 的 fields 列表
- 改 VS 字段映射: 改 TOML 中 [[tasks.X.vision_fields]]，这里自动读取
- 改默认值: 改 TOML 中对应任务的配置项
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

# 项目根目录 (dobotCtl/)
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from protocols.semicolon import ProtocolError, format_message, parse_message, parse_xy_payload


# ============================================================
# 自定义异常
# ============================================================

class ScriptError(ValueError):
    """剧本配置错误：文件不存在、任务未定义、VS 消息格式不符等。"""
    pass


# ============================================================
# 剧本加载
# ============================================================

def load_script(path: Path) -> dict[str, Any]:
    """读取 TOML 剧本文件，返回字典。

    对应文件: configs/competition_script.toml
    返回结构: {
        "vision": {...},      # VS 连接配置
        "bot": {...},         # Bot 连接配置
        "three_d": {...},     # 3D 测高配置
        "tasks": {
            "A": {...},       # Task A 所有配置
            "B": {...},       # Task B 所有配置
        }
    }
    """
    if not path.exists():
        raise ScriptError(f"script file not found: {path}")
    with path.open("rb") as f:
        return tomllib.load(f)


# ============================================================
# 任务入口 (命令行 dry-run 用)
# ============================================================

def dry_run(script: dict[str, Any], *, task: str, mode: str,
            vs_message: str | None = None) -> dict[str, Any]:
    """命令行 dry-run 入口，按 task 分发到具体处理函数。

    Args:
        script: load_script() 返回的完整配置字典
        task:   "A" 或 "B"
        mode:   VS 消息中的模式字段，通常为 "all"
        vs_message: 模拟的 VS 消息 (为 None 时用 TOML 中的 sample_vs_message)

    Returns:
        {"ok": True, "task": "A", "bot_tx": [...], ...}
    """
    tasks = script.get("tasks", {})
    if task not in tasks:
        raise ScriptError(f"task {task!r} not found")
    cfg = tasks[task]
    if task == "A":
        return dry_run_task_a(script, cfg, mode=mode, vs_message=vs_message)
    if task == "B":
        return dry_run_task_b(script, cfg, mode=mode, vs_message=vs_message)
    raise ScriptError(f"unsupported task {task}")


# ============================================================
# Task A — 积木抓取/码垛
# ============================================================
#
# VS 消息格式 (在 TOML 的 [[tasks.A.vision_fields]] 定义):
#   A;all;圆X;圆Y;正方体X;正方体Y;长方体X;长方体Y;
#
# 步序 (bot_mode = "step"):
#   1. MovJ  XY 上方安全高度       ← 快速移动到抓取点上方
#   2. MovL  XY 抓取Z              ← 直线下降到抓取高度
#   3. Suck;1;                     ← 吸盘吸
#   4. MovJ  XY 上方安全高度       ← 抬回安全高度
#   5. MovJ  放置XY 安全高度       ← 快速移动到放置点上方
#   6. MovL  放置XY 放置Z          ← 直线下降到放置高度
#   7. Suck;0;                     ← 吸盘释放
#   8. MovL  放置XY (放置Z+抬升)   ← 释放后抬升，避免刮到物体
#   9. MovJ  停靠点 (如有 dock)    ← 回到停靠位姿
#
# 步序 (bot_mode = "gp"):
#   1. GP 抓取XY 抓取Z RPY 放置XY 放置Z RPY [停靠点]
#      (GP 一条指令完成整个抓放流程)
#
# TOML 配置项 ([tasks.A] 及其子表):
#   approach_z         = 150     # 安全高度 Z (快速移动用 MovJ)
#   pick_z             = 123     # 抓取高度 Z (下降用 MovL)
#   pick_rpy           = [...]   # 抓取姿态 [Rx, Ry, Rz]
#   place_rpy          = [...]   # 放置姿态 [Rx, Ry, Rz]
#   release_lift_mm    = 15      # 释放后向上抬升距离 (mm)
#   dock               = [...]   # 停靠点 [X,Y,Z,Rx,Ry,Rz]
#   multi_object       = true    # 是否处理 VS 消息中的所有物体
#   bot_mode           = "step"  # "step"=分步 或 "gp"=单指令
#
#   [tasks.A.stack]
#   base_z                    = 123   # 码垛基础 Z
#   default_block_height_mm   = 20    # 默认物块高度
#   default_place_xy          = [9,10]# 默认放置 XY
#
#   [tasks.A.objects.xxx]              # 按物块类型单独配置 (circle/square/rectangle)
#   height_mm   = 20                   # 物块高度
#   place_xy    = [9, 10]              # 放置 XY
#   approach_z  = 150                  # (可选) 覆写安全高度
#   pick_z      = 123                  # (可选) 覆写抓取高度

def dry_run_task_a(
    script: dict[str, Any],          # 完整剧本字典
    cfg: dict[str, Any],             # tasks.A 的配置
    *,
    mode: str,                       # VS 消息模式 ("all")
    vs_message: str | None,          # VS 发来的原始消息
) -> dict[str, Any]:
    # ---- 1. 解析 VS 消息中的物体坐标 ----
    start = f"start;A;{mode};"
    reply = start
    # 如果没传 vs_message，用 TOML 里的 sample_vs_message 做 dry-run
    raw = vs_message or str(cfg.get("sample_vs_message", "A;all;1;2;3;4;5;6;"))
    # parse_xy_payload 按 vision_fields 定义从消息中依次提取每个物体的 x,y
    objects = parse_xy_payload(
        raw,
        task="A",
        mode=mode,
        object_fields=list(cfg.get("vision_fields", [])),
        min_field_count=int(cfg.get("min_value_fields", 6)),
    )
    if not objects:
        raise ScriptError("no object parsed from VS message")

    # ---- 2. 决定处理哪些物体 ----
    # multi_object=true: 处理消息中的所有物体
    # multi_object=false: 只处理第一个
    multi = bool(cfg.get("multi_object", True))
    ordered = objects if multi else objects[:1]

    # ---- 3. 读取码垛配置 ----
    stack = dict(cfg.get("stack", {}))
    object_cfg = cfg.get("objects", {})      # 按物块类型的单独配置
    commands: list[str] = []

    # ---- 4. 为每个物体生成 Bot 指令 ----
    for index, obj in enumerate(ordered):
        typ = str(obj["type"])                # 物块类型: circle/square/rectangle
        per_obj = dict(object_cfg.get(typ, {}))  # 该类型的单独配置 (高度/放置点)
        # 放置 XY: 优先按类型，其次默认
        place = list(per_obj.get("place_xy", stack.get("default_place_xy", [0, 0])))
        # 物块高度: 优先按类型，其次默认
        block_height = float(per_obj.get("height_mm", stack.get("default_block_height_mm", 0)))
        # 码垛 Z 逐层叠加: base_z + index * block_height
        place_z = float(stack.get("base_z", 0)) + index * block_height

        # 按 bot_mode 选择步进模式或 GP 模式
        if cfg.get("bot_mode", "step") == "gp":
            commands.append(_gp_command(script, cfg, obj, per_obj, place, place_z))
        else:
            commands.extend(_step_commands(script, cfg, obj, per_obj, place, place_z))

    # ---- 5. 追加停靠点 (仅 step 模式) ----
    dock = cfg.get("dock")
    if dock and cfg.get("bot_mode", "step") == "step":
        # 停靠点格式: MovJ;dockX;dockY;dockZ;dockRx;dockRy;dockRz;
        commands.append(format_message(["MovJ", *dock]))

    # ---- 6. 返回结果 ----
    return {
        "ok": True,
        "task": "A",
        "start_rx": start,              # 收到的 start 消息
        "start_tx": reply,              # 回复的 start 消息
        "vs_rx": raw,                   # 原始 VS 消息
        "objects": objects,             # 所有解析出的物体
        "processed_objects": ordered,   # 实际处理的物体
        "bot_tx": commands,             # 要发送给 Bot 的指令列表
    }


# ============================================================
# Task B — 单物体抓取 (GP 模式)
# ============================================================
#
# 通信流程 (每条 VS 消息对应一个物体):
#   VS  → Ctl:  B;X;Y;              ← VS 给出一组平面坐标
#   Ctl → 3D:  B;start;            ← Ctl 向 3D 请求高度
#   3D  → Ctl: B;Z;destID;         ← 3D 返回物体高度 Z 和目的地 ID
#
# GP 指令:
#   GP;X;Y;actualZ;pickRpy;destX;destY;destZ;placeRpy;[dock]
#   其中 actualZ = base_table_z + Z
#
# TOML 配置项 ([tasks.B] 及其子表):
#   pre_pose          = [...]   # (可选) 前置位姿 [X,Y,Z,Rx,Ry,Rz]
#   base_table_z      = 100     # 桌面基础 Z，实际抓取Z = base_table_z + 3D返回的Z
#   pick_rpy          = [...]   # 抓取姿态
#   place_rpy         = [...]   # 放置姿态
#   default_dest_xy   = [...]   # 默认放置 XY（3D 返回的 destID 未匹配到时使用）
#   dock              = [...]   # 可选停靠点
#
#   [tasks.B.destinations.1]          # 目的地 1 (3D 返回 destID=1 时匹配)
#   xy           = [20, 30]           # 放置 XY
#   include_dock = false              # 是否追加停靠点
#
#   [tasks.B.destinations.2]          # 目的地 2
#   xy           = [50, 30]
#   include_dock = true

def dry_run_task_b(
    script: dict[str, Any],
    cfg: dict[str, Any],
    *,
    mode: str,
    vs_message: str | None,
) -> dict[str, Any]:
    """解析 VS 消息 B;X;Y; + 3D 返回值 B;Z;destID; → 生成单条 GP 指令。

    实际运行时 3D 的 Z/destID 由 _handle_task_b 注入 cfg["_3d_z"] / cfg["_3d_dest_id"]。
    dry-run 时使用 sample_3d_z 的前两个值。
    """
    # ---- 1. 解析 VS 消息: B;X;Y; ----
    raw = vs_message or str(cfg.get("sample_vs_message", "B;2;3;"))
    msg = parse_message(raw)
    if msg.kind != "B":
        raise ProtocolError(f"expected task B, got {msg.kind}")
    if len(msg.fields) < 3:
        raise ProtocolError(f"expected B;X;Y; at least 3 fields, got {len(msg.fields)}")
    x = msg.fields[1]  # 物体 X 坐标
    y = msg.fields[2]  # 物体 Y 坐标

    # ---- 2. 3D 相关 ----
    three_d = script.get("three_d", {})
    three_d_tx = str(three_d.get("task_b_request", "B;start;"))

    # 真实的 Z 和 destID（由 _handle_task_b 注入 cfg）
    if "_3d_z" in cfg:
        z_value = float(cfg["_3d_z"])
        dest_id = str(cfg["_3d_dest_id"])
    else:
        # dry-run：用 sample_3d_z = [Z, destID]
        sample = list(cfg.get("sample_3d_z", [15, 1]))
        z_value = float(sample[0]) if len(sample) > 0 else 15.0
        dest_id = str(sample[1]) if len(sample) > 1 else "1"
    three_d_rx = format_message(["B", z_value, int(dest_id)])

    # ---- 3. 查找目的地 ----
    destinations = cfg.get("destinations", {})
    dest_cfg = dict(destinations.get(dest_id, {}))
    dest = list(dest_cfg.get("xy", cfg.get("default_dest_xy", [0, 0])))
    dock = cfg.get("dock")

    # ---- 4. 生成单条 GP 指令 ----
    base_table_z = float(cfg.get("base_table_z", 105))
    actual_z = base_table_z + z_value  # 实际抓取 Z

    gp = [
        "GP",
        x, y, actual_z, *cfg.get("pick_rpy", [180, 0, 0]),
        *dest, *cfg.get("place_rpy", [180, 0, 0]),
    ]
    if dock and bool(dest_cfg.get("include_dock", True)):
        gp.extend(dock)
    commands = [format_message(gp)]

    # ---- 5. 返回 ----
    return {
        "ok": True,
        "task": "B",
        "vs_rx": raw,
        "x": x, "y": y,
        "z_value": z_value,
        "dest_id": dest_id,
        "actual_z": actual_z,
        "three_d_host": three_d.get("host", "192.168.173.2"),
        "three_d_port": three_d.get("port", 9303),
        "three_d_tx": three_d_tx,
        "three_d_rx": three_d_rx,
        "bot_tx": commands,
    }


# ============================================================
# Task A 步进模式指令生成 (bot_mode = "step")
# ============================================================
#
# 生成分步指令序列，每一步都是独立的 TCP 消息，需等待 Bot 回复 OK。
#
# 参数说明:
#   obj       - 当前物块信息: {"x": ..., "y": ..., "type": "circle", ...}
#   per_obj   - 该物块类型的单独配置 (来自 [tasks.A.objects.xxx])
#   place_xy  - 放置点 XY坐标 [x, y]
#   place_z   - 放置点 Z坐标 (已叠加码垛高度)
#
# 配置优先级: per_obj (物块单独) > cfg (任务全局) > 硬编码默认值

def _step_commands(
    script: dict[str, Any],
    cfg: dict[str, Any],
    obj: dict[str, Any],
    per_obj: dict[str, Any],
    place_xy: list[Any],
    place_z: float,
) -> list[str]:
    # ---- 抓取参数 ----
    x = obj["x"]                        # 抓取 X (来自 VS)
    y = obj["y"]                        # 抓取 Y (来自 VS)
    approach_z = per_obj.get("approach_z", cfg.get("approach_z", 150))  # 安全高度
    pick_z = per_obj.get("pick_z", cfg.get("pick_z", 123))              # 抓取下降高度

    # ---- 放置后抬升 ----
    release_lift_mm = float(per_obj.get("release_lift_mm", cfg.get("release_lift_mm", 15)))

    # ---- 姿态 ----
    pick_rpy = cfg.get("pick_rpy", [180, 0, 0])
    place_rpy = cfg.get("place_rpy", [180, 0, 0])

    # ---- 步序列表 (按执行顺序) ----
    # 增删步骤直接改下面的列表即可
    return [
        # 1. 快速移动到抓取点上方 (安全Z)
        format_message(["MovJ", x, y, approach_z, *pick_rpy]),

        # 2. 直线下降到抓取Z
        format_message(["MovL", x, y, pick_z, *pick_rpy]),

        # 3. 吸盘吸
        format_message(["Suck", 1]),

        # 4. 抬回安全高度
        format_message(["MovJ", x, y, approach_z, *pick_rpy]),

        # 5. 快速移动到放置点上方 (安全Z)
        format_message(["MovJ", *place_xy, approach_z, *place_rpy]),

        # 6. 直线下降到放置Z
        format_message(["MovL", *place_xy, place_z, *place_rpy]),

        # 7. 吸盘释放
        format_message(["Suck", 0]),

        # 8. 释放后向上抬升 (避免刮到已码放物体)
        format_message(["MovL", *place_xy, place_z + release_lift_mm, *place_rpy]),
    ]


# ============================================================
# Task A GP 模式指令生成 (bot_mode = "gp")
# ============================================================
#
# GP 是一条复合指令，Bot 内部完成整个抓放流程。
# 格式: GP;抓X;抓Y;抓Z;抓Rx;抓Ry;抓Rz;放X;放Y;放Z;放Rx;放Ry;放Rz;[停靠X;停靠Y;停靠Z;停靠Rx;停靠Ry;停靠Rz;]

def _gp_command(
    script: dict[str, Any],
    cfg: dict[str, Any],
    obj: dict[str, Any],
    per_obj: dict[str, Any],
    place_xy: list[Any],
    place_z: float,
) -> str:
    # 抓取 Z (GP 模式用 pick_z，不需要 approach_z)
    pick_z = per_obj.get("pick_z", cfg.get("pick_z", 123))
    dock = cfg.get("dock")

    # GP 字段: 命令字 + 抓取位姿(6) + 放置位姿(6) + 可选停靠(6)
    fields = [
        "GP",
        obj["x"], obj["y"], pick_z, *cfg.get("pick_rpy", [180, 0, 0]),
        *place_xy, place_z, *cfg.get("place_rpy", [180, 0, 0]),
    ]

    # 如果有停靠点，追加到 GP 末尾
    if dock:
        fields.extend(dock)

    return format_message(fields)


# ============================================================
# 命令行入口 (python script_runner.py configs/competition_script.toml --task A)
# ============================================================

def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="Dry-run configurable competition script.")
    parser.add_argument("script", nargs="?", default="configs/competition_script.toml",
                        help="TOML 剧本文件路径")
    parser.add_argument("--task", choices=["A", "B"], required=True,
                        help="任务: A 或 B")
    parser.add_argument("--mode", default="all",
                        help="VS 消息模式 (默认 all)")
    parser.add_argument("--vs-message", default=None,
                        help="模拟的 VS 消息 (不传则用 TOML 中的 sample_vs_message)")
    return parser.parse_args()


def main() -> int:
    """命令行 dry-run: 加载剧本 → 解析 VS 消息 → 打印生成的 Bot 指令。"""
    args = parse_args()
    path = Path(args.script)
    if not path.is_absolute():
        path = ROOT / path
    try:
        result = dry_run(load_script(path), task=args.task, mode=args.mode,
                         vs_message=args.vs_message)
    except (ScriptError, ProtocolError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
