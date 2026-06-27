"""
Semicolon-separated TCP string protocol helpers.

The competition devices exchange lines such as:
  A;square;23.98;-12.3;

Fields are separated by ';'. A trailing ';' is accepted and preserved when
formatting outbound commands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class SemicolonMessage:
    raw: str
    fields: list[Any]

    @property
    def kind(self) -> str | None:
        return str(self.fields[0]) if self.fields else None

    @property
    def target(self) -> str | None:
        return str(self.fields[1]) if len(self.fields) > 1 else None


class ProtocolError(ValueError):
    pass


def parse_message(raw: str, *, cast_values: bool = True) -> SemicolonMessage:
    text = raw.strip()
    if not text:
        raise ProtocolError("empty message")
    parts = text.split(";")
    if parts and parts[-1] == "":
        parts = parts[:-1]
    if not parts:
        raise ProtocolError("message has no fields")
    fields = [_cast_field(item) for item in parts] if cast_values else parts
    return SemicolonMessage(raw=raw, fields=fields)


def format_message(fields: Iterable[Any]) -> str:
    return ";".join(_format_field(item) for item in fields) + ";"


def parse_xy_payload(
    raw: str,
    *,
    task: str,
    mode: str,
    object_fields: list[dict[str, Any]],
    min_field_count: int | None = None,
) -> list[dict[str, Any]]:
    msg = parse_message(raw)
    if msg.kind != task:
        raise ProtocolError(f"expected task {task}, got {msg.kind}")
    if msg.target != mode:
        raise ProtocolError(f"expected mode {mode}, got {msg.target}")

    values = msg.fields[2:]
    expected = sum(len(item.get("fields", ["x", "y"])) for item in object_fields)
    required = expected if min_field_count is None else min_field_count
    if len(values) < required:
        raise ProtocolError(f"expected at least {required} value fields, got {len(values)}")

    objects: list[dict[str, Any]] = []
    offset = 0
    for item in object_fields:
        names = list(item.get("fields", ["x", "y"]))
        if offset + len(names) > len(values):
            break
        obj = {
            "task": task,
            "type": item.get("type"),
            "label": item.get("label", item.get("type")),
        }
        for name in names:
            obj[name] = values[offset]
            offset += 1
        objects.append(obj)
    return objects


def _cast_field(value: str) -> str | int | float:
    value = value.strip()
    if value == "":
        return ""
    try:
        if value.lower().startswith(("0x", "-0x")):
            return value
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _format_field(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)
