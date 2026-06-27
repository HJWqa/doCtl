#!/usr/bin/env python3
"""
doCtl command line toolbox.

Every critical competition interface has a direct CLI command for debugging.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from typing import Any

from config import (
    ARM_HOST,
    ARM_PORT,
    RK_AUTO_HOST,
    RK_AUTO_PORT,
    TASK3_HOST,
    TASK3_PORT,
    THREE_D_HOST,
    THREE_D_HTTP_PORT,
    THREE_D_TCP_PORT,
    VISION_HOST,
    VISION_PORT,
    WEB_HOST,
    WEB_PORT,
)
from connectors.arm_client import ArmClient
from connectors.rk_auto_client import RkAutoClient
from connectors.task3_bridge_client import Task3BridgeClient
from connectors.three_d_http_client import ThreeDHttpClient
from connectors.vision_client import VisionClient


HELP_ARGS = ["-h", "--help", "-help"]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return int(args.func(args) or 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python ctl.py",
        description="doCtl 2D master-control CLI",
        add_help=False,
    )
    parser.add_argument(*HELP_ARGS, action="help", help="show help and exit")
    sub = parser.add_subparsers(dest="module")
    add_network(sub)
    add_3d(sub)
    add_rk_auto(sub)
    add_task3(sub)
    add_script(sub)
    add_vision(sub)
    add_arm(sub)
    add_flow(sub)
    add_serve(sub)
    return parser


def add_network(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("network", help="network probes", add_help=False)
    parser.add_argument(*HELP_ARGS, action="help", help="show network help and exit")
    actions = parser.add_subparsers(dest="action")
    probe = actions.add_parser("probe", help="probe all known competition ports")
    probe.set_defaults(func=cmd_network_probe)
    tcp = actions.add_parser("tcp", help="probe one TCP host:port")
    tcp.add_argument("--host", required=True)
    tcp.add_argument("--port", type=int, required=True)
    tcp.add_argument("--timeout-s", type=float, default=2.0)
    tcp.set_defaults(func=cmd_network_tcp)


def add_3d(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser(
        "3d",
        help="RK 3D HTTP debug commands",
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Debug RK 3D HTTP service on port 8088.",
        epilog="""Examples:
  python ctl.py 3d health
  python ctl.py 3d result
  python ctl.py 3d calibrate-table
  python ctl.py 3d measure-point --u 225 --v 240 --radius-px 18
  python ctl.py 3d measure-roi --x 190 --y 205 --w 80 --h 70
""",
    )
    parser.add_argument(*HELP_ARGS, action="help", help="show 3d help and exit")
    parser.add_argument("--host", default=THREE_D_HOST)
    parser.add_argument("--port", type=int, default=THREE_D_HTTP_PORT)
    actions = parser.add_subparsers(dest="action")
    for name, help_text, func in [
        ("health", "GET /api/health", cmd_3d_health),
        ("result", "GET /api/result", cmd_3d_result),
        ("calibrate-table", "POST /api/calibrate/table", cmd_3d_calibrate_table),
    ]:
        item = actions.add_parser(name, help=help_text)
        item.set_defaults(func=func)
    point = actions.add_parser("measure-point", help="POST /api/measure/point")
    point.add_argument("--u", type=int, required=True)
    point.add_argument("--v", type=int, required=True)
    point.add_argument("--radius-px", type=int, default=16)
    point.set_defaults(func=cmd_3d_measure_point)
    roi = actions.add_parser("measure-roi", help="POST /api/measure/roi")
    roi.add_argument("--x", type=int, required=True)
    roi.add_argument("--y", type=int, required=True)
    roi.add_argument("--w", type=int, required=True)
    roi.add_argument("--h", type=int, required=True)
    roi.set_defaults(func=cmd_3d_measure_roi)


def add_rk_auto(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("rk-auto", help="RK auto TCP JSON service", add_help=False)
    parser.add_argument(*HELP_ARGS, action="help", help="show rk-auto help and exit")
    parser.add_argument("--host", default=RK_AUTO_HOST)
    parser.add_argument("--port", type=int, default=RK_AUTO_PORT)
    actions = parser.add_subparsers(dest="action")
    health = actions.add_parser("health", help="send health command")
    health.set_defaults(func=cmd_auto_health)
    calib = actions.add_parser("calibrate-table", help="send calibrate_table command")
    calib.set_defaults(func=cmd_auto_calibrate_table)
    measure = actions.add_parser("measure-objects", help="send measure_objects command")
    measure.add_argument("--objects-json", required=True, help='JSON list, e.g. [{"position_id":"slot_1"}]')
    measure.set_defaults(func=cmd_auto_measure_objects)
    send = actions.add_parser("send", help="send arbitrary JSON object")
    send.add_argument("--json", required=True)
    send.set_defaults(func=cmd_auto_send)


def add_task3(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("task3", help="task3 bridge GET commands", add_help=False)
    parser.add_argument(*HELP_ARGS, action="help", help="show task3 help and exit")
    parser.add_argument("--host", default=TASK3_HOST)
    parser.add_argument("--port", type=int, default=TASK3_PORT)
    actions = parser.add_subparsers(dest="action")
    get = actions.add_parser("get", help="send GET or GET <index>")
    get.add_argument("--index", type=int, default=None)
    get.set_defaults(func=cmd_task3_get)


def add_script(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("script", help="semicolon competition script tools", add_help=False)
    parser.add_argument(*HELP_ARGS, action="help", help="show script help and exit")
    actions = parser.add_subparsers(dest="action")
    dry = actions.add_parser("dry-run", help="validate and preview Task A/B script commands")
    dry.add_argument("script", nargs="?", default="configs/competition_script.toml")
    dry.add_argument("--task", choices=["A", "B"], required=True)
    dry.add_argument("--mode", default="all", help="Vision mode field, e.g. all")
    dry.add_argument("--vs-message", default=None, help='raw semicolon VS line, e.g. "A;all;1;2;3;4;5;6;"')
    dry.set_defaults(func=cmd_script_dry_run)
    send3d = actions.add_parser("3d-send", help="send Task B semicolon request to RK script3d")
    send3d.add_argument("script", nargs="?", default="configs/competition_script.toml")
    send3d.add_argument("--host", default=None, help="override [three_d].host")
    send3d.add_argument("--port", type=int, default=None, help="override [three_d].port")
    send3d.add_argument("--message", default=None, help='override [three_d].task_b_request, e.g. "B;start;"')
    send3d.add_argument("--timeout-s", type=float, default=5.0)
    send3d.set_defaults(func=cmd_script_3d_send)
    serve = actions.add_parser("serve", help="connect to VS and run persistent semicolon script service", add_help=False)
    serve.add_argument(*HELP_ARGS, action="help", help="show script serve help and exit")
    serve.add_argument("script", nargs="?", default="configs/competition_script.toml")
    serve.set_defaults(func=cmd_script_serve)


def add_vision(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("vision", help="Vision Studio commands", add_help=False)
    parser.add_argument(*HELP_ARGS, action="help", help="show vision help and exit")
    parser.add_argument("--host", default=VISION_HOST)
    parser.add_argument("--port", type=int, default=VISION_PORT)
    actions = parser.add_subparsers(dest="action")
    detect = actions.add_parser("detect", help="trigger Vision Studio detect")
    detect.set_defaults(func=cmd_vision_detect)


def add_arm(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("arm", help="Dobot arm commands", add_help=False)
    parser.add_argument(*HELP_ARGS, action="help", help="show arm help and exit")
    parser.add_argument("--host", default=ARM_HOST)
    parser.add_argument("--port", type=int, default=ARM_PORT)
    actions = parser.add_subparsers(dest="action")
    home = actions.add_parser("home", help="send home command")
    home.set_defaults(func=cmd_arm_home)


def add_flow(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("flow", help="offline flow debug", add_help=False)
    parser.add_argument(*HELP_ARGS, action="help", help="show flow help and exit")
    actions = parser.add_subparsers(dest="action")
    dry = actions.add_parser("dry-run", help="run offline scripted flow")
    dry.add_argument("flow", nargs="?", default="flows/offline_pick_demo.toml")
    dry.add_argument("--repeat", type=int, default=None, help="override repeat in flow file")
    dry.add_argument("--no-output-jsonl", action="store_true", help="print only; do not write offline/runs/*.jsonl")
    dry.set_defaults(func=cmd_flow_dry_run)


def add_serve(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = sub.add_parser("serve", help="start doCtl Web UI")
    parser.add_argument("--host", default=WEB_HOST)
    parser.add_argument("--port", type=int, default=WEB_PORT)
    parser.add_argument("--no-mock", action="store_true")
    parser.set_defaults(func=cmd_serve)


def cmd_network_probe(args: argparse.Namespace) -> int:
    targets = [
        ("3d-http", THREE_D_HOST, THREE_D_HTTP_PORT),
        ("3d-tcp-json", THREE_D_HOST, THREE_D_TCP_PORT),
        ("rk-auto", RK_AUTO_HOST, RK_AUTO_PORT),
        ("task3", TASK3_HOST, TASK3_PORT),
        ("vision", VISION_HOST, VISION_PORT),
        ("arm", ARM_HOST, ARM_PORT),
    ]
    results = []
    for name, host, port in targets:
        ok, detail = probe_tcp(host, port, 2.0)
        results.append({"name": name, "host": host, "port": port, "ok": ok, "detail": detail})
    print_json({"ok": all(item["ok"] for item in results), "results": results})
    return 0 if all(item["ok"] for item in results) else 1


def cmd_network_tcp(args: argparse.Namespace) -> int:
    ok, detail = probe_tcp(args.host, args.port, args.timeout_s)
    print_json({"ok": ok, "host": args.host, "port": args.port, "detail": detail})
    return 0 if ok else 1


def cmd_3d_health(args: argparse.Namespace) -> int:
    return print_result(three_d(args).health())


def cmd_3d_result(args: argparse.Namespace) -> int:
    return print_result(three_d(args).result())


def cmd_3d_calibrate_table(args: argparse.Namespace) -> int:
    return print_result(three_d(args).calibrate_table())


def cmd_3d_measure_point(args: argparse.Namespace) -> int:
    return print_result(three_d(args).measure_point(args.u, args.v, args.radius_px))


def cmd_3d_measure_roi(args: argparse.Namespace) -> int:
    return print_result(three_d(args).measure_roi(args.x, args.y, args.w, args.h))


def cmd_auto_health(args: argparse.Namespace) -> int:
    return print_result(auto(args).health())


def cmd_auto_calibrate_table(args: argparse.Namespace) -> int:
    return print_result(auto(args).calibrate_table())


def cmd_auto_measure_objects(args: argparse.Namespace) -> int:
    objects = json.loads(args.objects_json)
    if not isinstance(objects, list):
        raise SystemExit("--objects-json must be a JSON list")
    return print_result(auto(args).measure_objects(objects))


def cmd_auto_send(args: argparse.Namespace) -> int:
    payload = json.loads(args.json)
    if not isinstance(payload, dict):
        raise SystemExit("--json must be a JSON object")
    return print_result(auto(args).request(payload))


def cmd_task3_get(args: argparse.Namespace) -> int:
    return print_result(task3(args).get(args.index))


def cmd_script_dry_run(args: argparse.Namespace) -> int:
    from competition.script_runner import ROOT, ScriptError, dry_run, load_script
    from protocols.semicolon import ProtocolError

    script_path = Path(args.script)
    if not script_path.is_absolute():
        script_path = ROOT / script_path
    try:
        result = dry_run(load_script(script_path), task=args.task, mode=args.mode, vs_message=args.vs_message)
    except (ScriptError, ProtocolError) as exc:
        print_json({"ok": False, "error": str(exc)})
        return 2
    print_json(result)
    return 0


def cmd_script_3d_send(args: argparse.Namespace) -> int:
    from competition.script_runner import ROOT, load_script

    script_path = Path(args.script)
    if not script_path.is_absolute():
        script_path = ROOT / script_path
    script = load_script(script_path)
    task_b = (script.get("tasks") or {}).get("B") or {}
    three_d = script.get("three_d", {})
    host = args.host or three_d.get("host", task_b.get("three_d_host", "192.168.173.2"))
    port = int(args.port or three_d.get("port", task_b.get("three_d_port", 9303)))
    message = str(args.message or three_d.get("task_b_request", task_b.get("three_d_request", "B;start;"))).rstrip()
    try:
        with socket.create_connection((host, port), timeout=args.timeout_s) as conn:
            stream = conn.makefile("rwb")
            stream.write((message + "\n").encode("utf-8"))
            stream.flush()
            response = stream.readline().decode("utf-8", errors="replace").strip()
    except Exception as exc:
        print_json({"ok": False, "host": host, "port": port, "tx": message, "error": str(exc)})
        return 1
    print_json({"ok": response.startswith("B;"), "host": host, "port": port, "tx": message, "rx": response})
    return 0 if response.startswith("B;") else 1


def cmd_script_serve(args: argparse.Namespace) -> int:
    from competition.script_service import ScriptService

    service = ScriptService(args.script)
    try:
        service.start()
        while service.is_running:
            import time

            time.sleep(0.5)
    except KeyboardInterrupt:
        service.stop()
        return 130
    return 0


def cmd_vision_detect(args: argparse.Namespace) -> int:
    client = VisionClient()
    client.host = args.host
    client.port = args.port
    if not client.connect():
        return 1
    try:
        result = client.detect()
        print_json({"ok": result is not None, "objects": result})
        return 0 if result is not None else 1
    finally:
        client.disconnect()


def cmd_arm_home(args: argparse.Namespace) -> int:
    client = ArmClient()
    client.host = args.host
    client.port = args.port
    if not client.connect():
        return 1
    try:
        return print_result(client.home())
    finally:
        client.disconnect()


def cmd_flow_dry_run(args: argparse.Namespace) -> int:
    from offline.run_flow import FlowError, ROOT, load_flow, run_flow

    flow_path = Path(args.flow)
    if not flow_path.is_absolute():
        flow_path = ROOT / flow_path
    try:
        flow = load_flow(flow_path)
        if args.no_output_jsonl:
            flow["output_jsonl"] = ""
        stats = run_flow(flow, repeat_override=args.repeat)
    except FlowError as exc:
        print_json({"ok": False, "error": str(exc)})
        return 2
    return 1 if stats.get("fail") else 0


def cmd_serve(args: argparse.Namespace) -> int:
    import main as web_main
    from coordinator import Coordinator

    if args.no_mock:
        import config

        config.MOCK_MODE = False
    coordinator = Coordinator()
    app = web_main.create_app(coordinator)
    from web.socketio_handler import socketio

    socketio.run(app, host=args.host, port=args.port, debug=False, allow_unsafe_werkzeug=True)
    return 0


def three_d(args: argparse.Namespace) -> ThreeDHttpClient:
    return ThreeDHttpClient(args.host, args.port)


def auto(args: argparse.Namespace) -> RkAutoClient:
    return RkAutoClient(args.host, args.port)


def task3(args: argparse.Namespace) -> Task3BridgeClient:
    return Task3BridgeClient(args.host, args.port)


def probe_tcp(host: str, port: int, timeout_s: float) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_s):
            return True, "connected"
    except Exception as exc:
        return False, str(exc)


def print_result(data: Any) -> int:
    if data is None:
        print_json({"ok": False, "error": "no response"})
        return 1
    print_json(data if isinstance(data, dict) else {"ok": True, "data": data})
    if isinstance(data, dict) and data.get("ok") is False:
        return 1
    return 0


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
