"""Roblox MCP server — exposes Roblox Studio to any MCP client.

Run it over stdio from an MCP client (Claude Desktop, Claude Code, DSH, Cursor…).
The companion Roblox Studio plugin lives in ``plugin/RobloxMCP.lua``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

from .bridge import DEFAULT_HOST, DEFAULT_PORT, Bridge, BridgeError
from .values import parse_literal, to_text

SERVER_NAME = "roblox-mcp"
VERSION = "0.1.0"

_bridge: Bridge | None = None

INSTRUCTIONS = """\
You are connected to a running Roblox Studio session through the RobloxMCP plugin.

Answer questions about the open place by inspecting it before you change it:
  get_tree → search_instances → get_instance → read_script.

Address every instance with a path such as `Workspace/Baseplate` or
`ServerScriptService/Main/Init`. `game` (or `game/Workspace`) is the root.

Mutating tools (create_instance, set_property, delete_instance, insert_model,
write_script, execute_luau) act on the real Studio place. Each change is wrapped
in a Studio undo recording, so the user can press Ctrl+Z. Prefer the smallest
change that answers the request, tell the user what you are about to change when
it is destructive, and never guess a path — look it up first.

execute_luau runs arbitrary Luau inside Studio (plugin identity). Use it only
when no dedicated tool covers the job, and keep the snippet small.
"""


def server() -> MCPServer:
    return MCPServer(
        name=SERVER_NAME,
        version=VERSION,
        instructions=INSTRUCTIONS.strip(),
        debug=os.environ.get("ROBLOX_MCP_DEBUG") == "1",
    )


def get_bridge() -> Bridge:
    if _bridge is None:
        raise BridgeError("bridge not started")
    return _bridge


# --------------------------------------------------------------------------- helpers
def _fmt(value: Any) -> str:
    return to_text(value)


def _tree_lines(node: dict[str, Any], prefix: str = "", is_last: bool = True, is_root: bool = True) -> list[str]:
    name = node.get("name") or "?"
    class_name = node.get("class") or node.get("className") or "?"
    leaf = node.get("leaf")
    label = f"{name}  [{class_name}]"
    if leaf:
        label += f"  …+{leaf}"
    if is_root:
        lines = [label]
        child_prefix = ""
    else:
        lines = [prefix + ("└─ " if is_last else "├─ ") + label]
        child_prefix = prefix + ("   " if is_last else "│  ")
    children = node.get("children") or []
    for index, child in enumerate(children):
        lines.extend(_tree_lines(child, child_prefix, index == len(children) - 1, is_root=False))
    return lines


def _call(name: str, args: dict[str, Any] | None = None, timeout: float = 60.0) -> Any:
    return get_bridge().call(name, args or {}, timeout=timeout)


def _error_text(exc: Exception) -> str:
    return f"Error: {exc}"


# --------------------------------------------------------------------------- tools
def build_server() -> MCPServer:
    app = server()

    @app.tool(
        description="Check whether the Roblox Studio plugin is connected and report the open place, port and queue state.",
        structured_output=False,
    )
    def studio_status() -> str:
        status = get_bridge().status()
        if not status["connected"]:
            return (
                "Roblox Studio plugin: NOT CONNECTED\n"
                f"Listening on http://{get_bridge().host}:{status['port']}\n"
                "Open Studio, make sure the RobloxMCP plugin is enabled and 'Allow HTTP Requests' is on in "
                "Game Settings → Security."
            )
        plugin = status["plugin"] or {}
        return (
            "Roblox Studio plugin: connected\n"
            f"  place:  {plugin.get('place') or '(unknown)'}\n"
            f"  studio: {plugin.get('studio')}\n"
            f"  bridge: http://{get_bridge().host}:{status['port']}  (last poll {status['lastPollAgo']}s ago)\n"
            f"  queued: {status['queued']}"
        )

    @app.tool(
        description=(
            "Print the instance tree of the open place as text. root is a path like 'Workspace' or 'game' "
            "(default), max_depth limits recursion (default 2)."
        ),
        structured_output=False,
    )
    def get_tree(root: str = "game", max_depth: int = 2, max_nodes: int = 300) -> str:
        try:
            data = _call("get_tree", {"root": root, "maxDepth": int(max_depth), "maxNodes": int(max_nodes)})
        except BridgeError as exc:
            return _error_text(exc)
        tree = data.get("tree") or {}
        lines = [f"root: {root}"]
        lines.extend(_tree_lines(tree))
        meta = [f"nodes={data.get('nodes', 0)}"]
        if data.get("truncated"):
            meta.append("truncated (raise max_depth/max_nodes or narrow root)")
        lines.append("(" + ", ".join(meta) + ")")
        return "\n".join(lines)

    @app.tool(
        description=(
            "Read one instance: class, curated properties, attributes and children. "
            "path is like 'Workspace/Baseplate'. properties is an optional comma list of property names."
        ),
        structured_output=False,
    )
    def get_instance(path: str, properties: str = "") -> str:
        props = [p.strip() for p in properties.split(",") if p.strip()]
        try:
            data = _call("get_instance", {"path": path, "properties": props})
        except BridgeError as exc:
            return _error_text(exc)
        lines = [f"{data.get('path', path)}  [{data.get('className', '?')}]"]
        for key, value in (data.get("properties") or {}).items():
            lines.append(f"  {key:<16}= {_fmt(value)}")
        attrs = data.get("attributes") or {}
        if attrs:
            lines.append("  attributes:")
            for key, value in attrs.items():
                lines.append(f"    {key:<14}= {_fmt(value)}")
        children = data.get("children") or []
        if children:
            sample = ", ".join(f"{c['name']} [{c.get('className', '?')}]" for c in children[:25])
            more = f" … +{len(children) - 25}" if len(children) > 25 else ""
            lines.append(f"  children ({len(children)}): {sample}{more}")
        return "\n".join(lines)

    @app.tool(
        description="Find instances by name substring and/or exact class name. Returns paths with class.",
        structured_output=False,
    )
    def search_instances(query: str, class_name: str = "", root: str = "game", max_results: int = 40) -> str:
        try:
            data = _call("search_instances", {
                "query": query,
                "className": class_name,
                "root": root,
                "maxResults": int(max_results),
            })
        except BridgeError as exc:
            return _error_text(exc)
        matches = data.get("matches") or []
        if not matches:
            return f"No instance matches query={query!r} className={class_name!r}."
        lines = [f"{data.get('total', len(matches))} match(es)" + (" (truncated)" if data.get("truncated") else "")]
        for item in matches:
            lines.append(f"  {item.get('path')}  [{item.get('className')}]")
        return "\n".join(lines)

    @app.tool(
        description=(
            "Create a new instance. class_name is a Roblox class such as 'Part', 'Script', 'Model'. "
            "properties is an optional JSON object like {\"Size\": \"Vector3(4,1,2)\", \"Anchored\": true}."
        ),
        structured_output=False,
    )
    def create_instance(class_name: str, parent: str = "Workspace", name: str = "", properties: str = "") -> str:
        try:
            props = json.loads(properties) if properties.strip() else {}
        except json.JSONDecodeError as exc:
            return f"Error: properties must be a JSON object ({exc})"
        if not isinstance(props, dict):
            return "Error: properties must be a JSON object"
        parsed = {key: parse_literal(val) if isinstance(val, str) else val for key, val in props.items()}
        try:
            data = _call("create_instance", {
                "className": class_name,
                "parent": parent,
                "name": name,
                "properties": parsed,
            })
        except BridgeError as exc:
            return _error_text(exc)
        return f"Created {data.get('path')}  [{data.get('className')}]"

    @app.tool(
        description=(
            "Set one property on an instance. Values accept plain JSON scalars or literals such as "
            "'Vector3(4,1,2)', 'Color3(1,0,0)', 'Enum.Material.Neon', 'CFrame(0,5,0)'."
        ),
        structured_output=False,
    )
    def set_property(path: str, property: str, value: str) -> str:
        parsed = parse_literal(value)
        try:
            data = _call("set_property", {"path": path, "property": property, "value": parsed})
        except BridgeError as exc:
            return _error_text(exc)
        return f"{data.get('path')}.{property} = {_fmt(data.get('value', parsed))}"

    @app.tool(description="Delete (Destroy) an instance by path. Wrapped in an undo recording.", structured_output=False)
    def delete_instance(path: str) -> str:
        try:
            data = _call("delete_instance", {"path": path})
        except BridgeError as exc:
            return _error_text(exc)
        return f"Deleted {data.get('path')}  [{data.get('className')}]"

    @app.tool(
        description=(
            "Insert a model or asset from the Roblox catalog into the place via InsertService. "
            "asset_id is the catalog asset id; position is optional like 'Vector3(0,5,0)'."
        ),
        structured_output=False,
    )
    def insert_model(asset_id: int, parent: str = "Workspace", position: str = "") -> str:
        args: dict[str, Any] = {"assetId": int(asset_id), "parent": parent}
        if position.strip():
            args["position"] = parse_literal(position)
        try:
            data = _call("insert_model", args, timeout=120.0)
        except BridgeError as exc:
            return _error_text(exc)
        return f"Inserted {data.get('path')}  [{data.get('className')}]  ({data.get('count', 1)} instance(s))"

    @app.tool(
        description=(
            "Run a Luau snippet inside Studio and return its return value plus printed output. "
            "Use sparingly: dedicated tools are safer. The snippet runs as a temporary ModuleScript."
        ),
        structured_output=False,
    )
    def execute_luau(code: str) -> str:
        try:
            data = _call("execute_luau", {"code": code}, timeout=120.0)
        except BridgeError as exc:
            return _error_text(exc)
        parts = []
        if data.get("output"):
            parts.append(str(data["output"]))
        if data.get("value") is not None:
            parts.append("returned: " + _fmt(data["value"]))
        if not parts:
            parts.append("(no output)")
        return "\n".join(parts)

    @app.tool(description="List all scripts in the place (path, class, line count).", structured_output=False)
    def list_scripts(root: str = "game", max_results: int = 200) -> str:
        try:
            data = _call("list_scripts", {"root": root, "maxResults": int(max_results)})
        except BridgeError as exc:
            return _error_text(exc)
        scripts = data.get("scripts") or []
        if not scripts:
            return "No scripts found."
        lines = [f"{data.get('total', len(scripts))} script(s)" + (" (truncated)" if data.get("truncated") else "")]
        for item in scripts:
            lines.append(f"  {item.get('path')}  [{item.get('className')}]  {item.get('lines', 0)} lines")
        return "\n".join(lines)

    @app.tool(description="Read the source of a Script/LocalScript/ModuleScript by path.", structured_output=False)
    def read_script(path: str) -> str:
        try:
            data = _call("read_script", {"path": path})
        except BridgeError as exc:
            return _error_text(exc)
        return (data.get("source") or "").rstrip() or "(empty script)"

    @app.tool(
        description="Replace the source of a Script/LocalScript/ModuleScript by path (undoable).",
        structured_output=False,
    )
    def write_script(path: str, source: str) -> str:
        try:
            data = _call("write_script", {"path": path, "source": source}, timeout=90.0)
        except BridgeError as exc:
            return _error_text(exc)
        return f"Updated {data.get('path')}  ({data.get('lines', 0)} lines)"

    @app.tool(description="Show what the user currently has selected in Studio.", structured_output=False)
    def get_selection() -> str:
        try:
            data = _call("get_selection", {})
        except BridgeError as exc:
            return _error_text(exc)
        items = data.get("selection") or []
        if not items:
            return "Nothing selected in Studio."
        return "\n".join(f"  {item.get('path')}  [{item.get('className')}]" for item in items)

    return app


# --------------------------------------------------------------------------- entrypoint
def main(argv: list[str] | None = None) -> None:
    global _bridge
    parser = argparse.ArgumentParser(prog="roblox-mcp", description="MCP server for Roblox Studio")
    parser.add_argument("--host", default=os.environ.get("ROBLOX_MCP_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ROBLOX_MCP_PORT", DEFAULT_PORT)))
    parser.add_argument("--token", default=os.environ.get("ROBLOX_MCP_TOKEN") or None,
                        help="optional shared secret the plugin must send in X-Roblox-MCP-Token")
    parser.add_argument("--version", action="version", version=f"{SERVER_NAME} {VERSION}")
    args = parser.parse_args(argv)

    bridge = Bridge(host=args.host, port=args.port, token=args.token)
    bridge.start()
    _bridge = bridge
    try:
        build_server().run("stdio")
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()


if __name__ == "__main__":
    main(sys.argv[1:])
