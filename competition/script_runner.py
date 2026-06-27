"""
Configurable dry-run runner for Vision Studio / 3D / Dobot string protocols.

This runner is intentionally conservative: it validates incoming semicolon
messages and emits the exact outbound command strings we expect to send.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from protocols.semicolon import ProtocolError, format_message, parse_message, parse_xy_payload


class ScriptError(ValueError):
    pass


def load_script(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ScriptError(f"script file not found: {path}")
    with path.open("rb") as f:
        return tomllib.load(f)


def dry_run(script: dict[str, Any], *, task: str, mode: str, vs_message: str | None = None) -> dict[str, Any]:
    tasks = script.get("tasks", {})
    if task not in tasks:
        raise ScriptError(f"task {task!r} not found")
    cfg = tasks[task]
    if task == "A":
        return dry_run_task_a(script, cfg, mode=mode, vs_message=vs_message)
    if task == "B":
        return dry_run_task_b(script, cfg, mode=mode, vs_message=vs_message)
    raise ScriptError(f"unsupported task {task}")


def dry_run_task_a(
    script: dict[str, Any],
    cfg: dict[str, Any],
    *,
    mode: str,
    vs_message: str | None,
) -> dict[str, Any]:
    start = f"start;A;{mode};"
    reply = start
    raw = vs_message or str(cfg.get("sample_vs_message", "A;all;1;2;3;4;5;6;"))
    objects = parse_xy_payload(
        raw,
        task="A",
        mode=mode,
        object_fields=list(cfg.get("vision_fields", [])),
        min_field_count=int(cfg.get("min_value_fields", 6)),
    )
    if not objects:
        raise ScriptError("no object parsed from VS message")

    multi = bool(cfg.get("multi_object", True))
    ordered = objects if multi else objects[:1]
    stack = dict(cfg.get("stack", {}))
    object_cfg = cfg.get("objects", {})
    commands: list[str] = []

    for index, obj in enumerate(ordered):
        typ = str(obj["type"])
        per_obj = dict(object_cfg.get(typ, {}))
        place = list(per_obj.get("place_xy", stack.get("default_place_xy", [0, 0])))
        block_height = float(per_obj.get("height_mm", stack.get("default_block_height_mm", 0)))
        place_z = float(stack.get("base_z", 0)) + index * block_height

        if cfg.get("bot_mode", "step") == "gp":
            commands.append(_gp_command(script, cfg, obj, per_obj, place, place_z))
        else:
            commands.extend(_step_commands(script, cfg, obj, per_obj, place, place_z))

    dock = cfg.get("dock")
    if dock and cfg.get("bot_mode", "step") == "step":
        commands.append(format_message(["MovJ", *dock]))

    return {
        "ok": True,
        "task": "A",
        "start_rx": start,
        "start_tx": reply,
        "vs_rx": raw,
        "objects": objects,
        "processed_objects": ordered,
        "bot_tx": commands,
    }


def dry_run_task_b(
    script: dict[str, Any],
    cfg: dict[str, Any],
    *,
    mode: str,
    vs_message: str | None,
) -> dict[str, Any]:
    raw = vs_message or str(cfg.get("sample_vs_message", "B;1;2;3;4;"))
    msg = parse_message(raw)
    if msg.kind != "B":
        raise ProtocolError(f"expected task B, got {msg.kind}")
    values = msg.fields[1:]
    fields = list(cfg.get("vision_fields", []))
    required = sum(len(item.get("fields", ["x", "y"])) for item in fields)
    if len(values) < required:
        raise ProtocolError(f"expected at least {required} value fields, got {len(values)}")

    objects = []
    offset = 0
    for item in fields:
        names = list(item.get("fields", ["x", "y"]))
        obj = {"task": "B", "type": item.get("type"), "label": item.get("label", item.get("type"))}
        for name in names:
            obj[name] = values[offset]
            offset += 1
        objects.append(obj)

    three_d = script.get("three_d", {})
    three_d_tx = str(three_d.get("task_b_request", cfg.get("three_d_request", format_message(["B", "start"]))))
    z_values = list(cfg.get("sample_3d_z", [7, 8]))
    three_d_rx = format_message(["B", *z_values])
    destinations = cfg.get("destinations", {})
    dock = cfg.get("dock")
    commands = []
    base_table_z = float(cfg.get("base_table_z", 100))

    for index, obj in enumerate(objects):
        z_delta = float(z_values[index]) if index < len(z_values) else 0.0
        per_obj = dict(destinations.get(str(obj["type"]), {}))
        dest = list(per_obj.get("xy", cfg.get("default_dest_xy", [0, 0])))
        z = base_table_z + z_delta
        gp = ["GP", obj["x"], obj["y"], z, *cfg.get("pick_rpy", [180, 0, 0]), *dest, *cfg.get("place_rpy", [180, 0, 0])]
        if dock and bool(per_obj.get("include_dock", index == len(objects) - 1)):
            gp.extend(dock)
        commands.append(format_message(gp))

    return {
        "ok": True,
        "task": "B",
        "vs_rx": raw,
        "objects": objects,
        "three_d_host": three_d.get("host", cfg.get("three_d_host", "192.168.173.2")),
        "three_d_port": three_d.get("port", cfg.get("three_d_port", 9303)),
        "three_d_tx": three_d_tx,
        "three_d_rx": three_d_rx,
        "bot_tx": commands,
    }


def _step_commands(
    script: dict[str, Any],
    cfg: dict[str, Any],
    obj: dict[str, Any],
    per_obj: dict[str, Any],
    place_xy: list[Any],
    place_z: float,
) -> list[str]:
    x = obj["x"]
    y = obj["y"]
    approach_z = per_obj.get("approach_z", cfg.get("approach_z", 150))
    pick_z = per_obj.get("pick_z", cfg.get("pick_z", 123))
    pick_rpy = cfg.get("pick_rpy", [180, 0, 0])
    place_rpy = cfg.get("place_rpy", [180, 0, 0])
    return [
        format_message(["MovJ", x, y, approach_z, *pick_rpy]),
        format_message(["MovL", x, y, pick_z, *pick_rpy]),
        format_message(["Suck", 1]),
        format_message(["MovJ", x, y, approach_z, *pick_rpy]),
        format_message(["MovJ", *place_xy, approach_z, *place_rpy]),
        format_message(["MovL", *place_xy, place_z, *place_rpy]),
        format_message(["Sucl", 0]),
    ]


def _gp_command(
    script: dict[str, Any],
    cfg: dict[str, Any],
    obj: dict[str, Any],
    per_obj: dict[str, Any],
    place_xy: list[Any],
    place_z: float,
) -> str:
    pick_z = per_obj.get("pick_z", cfg.get("pick_z", 123))
    dock = cfg.get("dock")
    fields = [
        "GP",
        obj["x"], obj["y"], pick_z, *cfg.get("pick_rpy", [180, 0, 0]),
        *place_xy, place_z, *cfg.get("place_rpy", [180, 0, 0]),
    ]
    if dock:
        fields.extend(dock)
    return format_message(fields)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dry-run configurable competition script.")
    parser.add_argument("script", nargs="?", default="configs/competition_script.toml")
    parser.add_argument("--task", choices=["A", "B"], required=True)
    parser.add_argument("--mode", default="all")
    parser.add_argument("--vs-message", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = Path(args.script)
    if not path.is_absolute():
        path = ROOT / path
    try:
        result = dry_run(load_script(path), task=args.task, mode=args.mode, vs_message=args.vs_message)
    except (ScriptError, ProtocolError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
