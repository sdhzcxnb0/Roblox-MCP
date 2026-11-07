"""Value encoding shared with the Studio plugin.

Wire format (JSON) tags Roblox datatypes with ``__type``:

    Vector3      {"__type": "Vector3", "x": 1, "y": 2, "z": 3}
    Vector2      {"__type": "Vector2", "x": 1, "y": 2}
    CFrame       {"__type": "CFrame", "position": [x, y, z], "look": [x, y, z]}
    Color3       {"__type": "Color3", "r": 1, "g": 0.5, "b": 0}
    UDim         {"__type": "UDim", "scale": 1, "offset": 0}
    UDim2        {"__type": "UDim2", "x": [...], "y": [...]}
    EnumItem     {"__type": "Enum", "enum": "Material", "name": "Plastic"}
    Instance     {"__type": "Instance", "path": "Workspace/Part"}
    BrickColor   {"__type": "BrickColor", "name": "Bright red"}
    NumberRange  {"__type": "NumberRange", "min": 0, "max": 1}
"""

from __future__ import annotations

import json
import re
from typing import Any

_LITERAL_RE = re.compile(r"^\s*([A-Za-z0-9_.]+)\s*\((.*)\)\s*$", re.S)


def to_text(value: Any, indent: int = 0) -> str:
    """Render a wire value as compact human-readable text."""
    pad = "  " * indent
    if isinstance(value, dict) and "__type" in value:
        kind = value["__type"]
        if kind == "Vector3":
            return f"Vector3({_num(value['x'])}, {_num(value['y'])}, {_num(value['z'])})"
        if kind == "Vector2":
            return f"Vector2({_num(value['x'])}, {_num(value['y'])})"
        if kind == "Color3":
            return f"Color3({_num(value['r'])}, {_num(value['g'])}, {_num(value['b'])})"
        if kind == "CFrame":
            pos = value.get("position") or [0, 0, 0]
            look = value.get("look")
            base = f"CFrame({_num(pos[0])}, {_num(pos[1])}, {_num(pos[2])})"
            return base + (f" lookAt {_vec(look)}" if look else "")
        if kind == "UDim":
            return f"UDim({_num(value.get('scale', 0))}, {_num(value.get('offset', 0))})"
        if kind == "UDim2":
            x, y = value.get("x") or [0, 0], value.get("y") or [0, 0]
            return f"UDim2({_num(x[0])}, {_num(x[1])}, {_num(y[0])}, {_num(y[1])})"
        if kind == "Enum":
            return f"Enum.{value.get('enum')}.{value.get('name')}"
        if kind == "Instance":
            return f"Instance({value.get('path')})"
        if kind == "BrickColor":
            return f"BrickColor({value.get('name')})"
        if kind == "NumberRange":
            return f"NumberRange({_num(value.get('min', 0))}, {_num(value.get('max', 1))})"
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        if len(value) > 12:
            head = ", ".join(to_text(v, indent) for v in value[:12])
            return f"[{head}, … +{len(value) - 12}]"
        return "[" + ", ".join(to_text(v, indent) for v in value) + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [f"{k} = {to_text(v, indent + 1)}" for k, v in value.items()]
        return "{" + ", ".join(items) + "}"
    if isinstance(value, str):
        return value
    if value is None:
        return "nil"
    return json.dumps(value, ensure_ascii=False)


def parse_literal(spec: str) -> Any:
    """Parse ``Vector3(1, 2, 3)`` / ``Enum.Material.Plastic`` style input.

    Plain JSON-compatible scalars and nested JSON-ish literals are accepted too;
    anything unrecognised is sent through as a string.
    """
    if not isinstance(spec, str):
        return spec
    text = spec.strip()
    if not text:
        return ""
    if text[0] in "[{\"" or text in ("true", "false", "null"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    if text[0] == "-" or text[0].isdigit():
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                pass
    match = _LITERAL_RE.match(text)
    if not match:
        return text
    ctor, raw_args = match.group(1), match.group(2)
    args = _split_args(raw_args)
    if ctor.startswith("Enum."):
        parts = ctor.split(".")
        return {"__type": "Enum", "enum": ".".join(parts[1:-1]), "name": parts[-1]}
    if ctor == "Vector3" and len(args) == 3:
        return {"__type": "Vector3", "x": _num(args[0]), "y": _num(args[1]), "z": _num(args[2])}
    if ctor == "Vector2" and len(args) == 2:
        return {"__type": "Vector2", "x": _num(args[0]), "y": _num(args[1])}
    if ctor == "Color3":
        if len(args) == 3:
            return {"__type": "Color3", "r": _num(args[0]), "g": _num(args[1]), "b": _num(args[2])}
        if len(args) == 1 and isinstance(args[0], str) and args[0].startswith("#"):
            hex_color = args[0].lstrip("#")
            r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))
            return {"__type": "Color3", "r": r, "g": g, "b": b}
    if ctor == "UDim2" and len(args) == 4:
        return {"__type": "UDim2", "x": [_num(args[0]), _num(args[1])], "y": [_num(args[2]), _num(args[3])]}
    if ctor == "UDim" and len(args) == 2:
        return {"__type": "UDim", "scale": _num(args[0]), "offset": _num(args[1])}
    if ctor == "CFrame":
        if len(args) == 3:
            return {"__type": "CFrame", "position": [_num(args[0]), _num(args[1]), _num(args[2])]}
        look = ctor.startswith("CFrame.lookAt") or ctor == "CFrame.lookAt"
        if look and len(args) == 6:
            return {
                "__type": "CFrame",
                "position": [_num(args[0]), _num(args[1]), _num(args[2])],
                "look": [_num(args[3]), _num(args[4]), _num(args[5])],
            }
    if ctor == "NumberRange" and len(args) == 2:
        return {"__type": "NumberRange", "min": _num(args[0]), "max": _num(args[1])}
    return text


def _split_args(raw: str) -> list[Any]:
    args: list[Any] = []
    depth = 0
    current = ""
    for ch in raw:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            args.append(_scalar(current))
            current = ""
        else:
            current += ch
    if current.strip():
        args.append(_scalar(current))
    return args


def _scalar(text: str) -> Any:
    text = text.strip().strip('"').strip("'")
    if text.startswith("#"):
        return text
    try:
        return _num(text)
    except (TypeError, ValueError):
        return text


def _num(text: Any) -> Any:
    if isinstance(text, (int, float)):
        return text
    try:
        return int(text)
    except (TypeError, ValueError):
        pass
    try:
        return round(float(text), 6)
    except (TypeError, ValueError):
        return text


def _vec(value: list[Any] | None) -> str:
    if not value:
        return "(?)"
    return "(" + ", ".join(_num(v) for v in value) + ")"
