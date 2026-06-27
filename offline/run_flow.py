#!/usr/bin/env python3
"""
Run an editable offline pick-flow script.

The script does not connect to real devices. It emits the same kind of
TX/RX/process records a human needs when checking the workflow offline.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fusion import merge_pose  # noqa: E402


DEVICE_LABELS = {
    "vision": "Vision",
    "camera3d": "3D相机",
    "fusion": "Fusion",
    "arm": "机械臂",
    "flow": "Flow",
}


class FlowError(ValueError):
    pass


class Recorder:
    def __init__(self, jsonl_path: str | None):
        self.jsonl_path = Path(jsonl_path) if jsonl_path else None
        self._fp = None

    def __enter__(self) -> "Recorder":
        if self.jsonl_path:
            path = self.jsonl_path
            if not path.is_absolute():
                path = ROOT / path
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fp = path.open("w", encoding="utf-8")
            self.jsonl_path = path
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._fp:
            self._fp.close()

    def emit(self, device: str, direction: str, data: Any, cycle: str | None = None):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        label = DEVICE_LABELS.get(device, device)
        prefix = f"[{ts}] [{label} {direction.upper()}]"
        if cycle:
            prefix += f" [{cycle}]"
        print(f"{prefix} {payload}")

        if self._fp:
            self._fp.write(json.dumps({
                "ts": ts,
                "device": device,
                "direction": direction,
                "cycle": cycle,
                "data": data,
            }, ensure_ascii=False) + "\n")


def load_flow(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FlowError(f"flow file not found: {path}")
    with path.open("rb") as f:
        flow = tomllib.load(f)

    cycles = flow.get("cycles")
    if not isinstance(cycles, list) or not cycles:
        raise FlowError("flow must contain at least one [[cycles]] item")

    for idx, cycle in enumerate(cycles, start=1):
        if not isinstance(cycle, dict):
            raise FlowError(f"cycle #{idx} must be a table")
        objects = cycle.get("vision_objects", [])
        if not isinstance(objects, list):
            raise FlowError(f"cycle #{idx}: vision_objects must be a list")
        for obj in objects:
            for key in ("id", "x", "y"):
                if key not in obj:
                    raise FlowError(f"cycle #{idx}: each vision object requires {key}")

    return flow


def run_flow(flow: dict[str, Any], repeat_override: int | None = None):
    repeat = repeat_override if repeat_override is not None else int(flow.get("repeat", 1))
    interval = float(flow.get("cycle_interval_sec", 0.0))
    stop_on_error = bool(flow.get("stop_on_error", False))
    jsonl_path = flow.get("output_jsonl") or None

    stats = {
        "total": 0,
        "success": 0,
        "fail": 0,
        "skipped": 0,
    }

    with Recorder(jsonl_path) as rec:
        rec.emit("flow", "info", {
            "name": flow.get("name", "offline_flow"),
            "description": flow.get("description", ""),
            "repeat": repeat,
            "cycles": len(flow["cycles"]),
        })

        for rep in range(1, repeat + 1):
            for idx, cycle in enumerate(flow["cycles"], start=1):
                cycle_name = str(cycle.get("name") or f"cycle-{idx}")
                label = f"{rep}.{idx} {cycle_name}" if repeat > 1 else cycle_name
                ok = run_cycle(cycle, label, rec)

                if ok is True:
                    stats["total"] += 1
                    stats["success"] += 1
                elif ok is False:
                    stats["total"] += 1
                    stats["fail"] += 1
                    if stop_on_error:
                        rec.emit("flow", "err", {"message": "stop_on_error triggered"}, label)
                        rec.emit("flow", "summary", stats)
                        return stats
                else:
                    stats["skipped"] += 1

                if interval > 0:
                    time.sleep(interval)

        rec.emit("flow", "summary", stats)
        if rec.jsonl_path:
            print(f"\nJSONL: {rec.jsonl_path}")
    return stats


def run_cycle(cycle: dict[str, Any], label: str, rec: Recorder) -> bool | None:
    note = cycle.get("note")
    rec.emit("flow", "start", {"note": note or ""}, label)

    vision_status = str(cycle.get("vision_status", "ok"))
    rec.emit("vision", "tx", {"cmd": "detect", "camera_id": cycle.get("camera_id", 0)}, label)

    if vision_status != "ok":
        rec.emit("vision", "rx", {
            "status": "error",
            "message": cycle.get("vision_message", "vision error"),
        }, label)
        return False

    objects = cycle.get("vision_objects", [])
    rec.emit("vision", "rx", {"status": "ok", "objects": objects}, label)

    if not objects:
        rec.emit("flow", "skip", {"reason": "no vision object"}, label)
        return None

    obj = objects[0]
    x = float(obj["x"])
    y = float(obj["y"])
    angle = float(obj.get("angle", 0.0))

    camera_status = str(cycle.get("camera_status", "ok"))
    rec.emit("camera3d", "tx", {"cmd": "get_height", "x": x, "y": y}, label)

    if camera_status != "ok":
        rec.emit("camera3d", "rx", {
            "status": "error",
            "message": cycle.get("camera_message", "camera3d error"),
        }, label)
        return False

    if "camera_z" not in cycle:
        rec.emit("camera3d", "rx", {
            "status": "error",
            "message": "camera_z missing in offline script",
        }, label)
        return False

    z = float(cycle["camera_z"])
    rec.emit("camera3d", "rx", {"status": "ok", "z": z}, label)

    pose = merge_pose(x, y, z, angle)
    if isinstance(cycle.get("pose_override"), dict):
        pose.update(cycle["pose_override"])
    rec.emit("fusion", "pose", pose, label)

    arm_payload = {"cmd": cycle.get("arm_cmd", "pick_and_place"), "pose": pose}
    rec.emit("arm", "tx", arm_payload, label)

    arm_status = str(cycle.get("arm_status", "ok"))
    arm_resp = {
        "status": arm_status,
        "message": cycle.get("arm_message", "task done" if arm_status == "ok" else "arm error"),
    }
    rec.emit("arm", "rx", arm_resp, label)

    return arm_status == "ok"


def parse_args():
    parser = argparse.ArgumentParser(description="Run an offline editable pick-flow script.")
    parser.add_argument(
        "flow",
        nargs="?",
        default="flows/offline_pick_demo.toml",
        help="TOML flow file path",
    )
    parser.add_argument("--repeat", type=int, default=None, help="override repeat in flow file")
    parser.add_argument(
        "--strict-exit",
        action="store_true",
        help="return exit code 1 when any scripted cycle fails",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    flow_path = Path(args.flow)
    if not flow_path.is_absolute():
        flow_path = ROOT / flow_path

    try:
        flow = load_flow(flow_path)
        stats = run_flow(flow, args.repeat)
    except FlowError as e:
        print(f"flow error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130

    return 1 if args.strict_exit and stats["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
