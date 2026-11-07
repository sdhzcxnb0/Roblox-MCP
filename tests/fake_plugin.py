"""A fake Roblox Studio plugin used by the tests.

It speaks the same HTTP bridge protocol as plugin/RobloxMCP.lua and keeps an
in-memory DataModel, so the whole MCP server can be exercised without Studio.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any


def _node(name: str, class_name: str, children: list[dict[str, Any]] | None = None,
          props: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "class": class_name, "children": children or [], "props": props or {}}


def default_model() -> dict[str, Any]:
    baseplate = _node("Baseplate", "Part", props={
        "Name": "Baseplate",
        "ClassName": "Part",
        "Size": {"__type": "Vector3", "x": 2048, "y": 4, "z": 2048},
        "Position": {"__type": "Vector3", "x": 0, "y": -2, "z": 0},
        "Anchored": True,
        "Color": {"__type": "Color3", "r": 0.4, "g": 0.4, "b": 0.4},
        "Transparency": 0,
        "Material": {"__type": "Enum", "enum": "Material", "name": "Plastic"},
        "Parent": {"__type": "Instance", "path": "Workspace"},
    })
    spawn = _node("SpawnLocation", "SpawnLocation", props={
        "Name": "SpawnLocation",
        "ClassName": "SpawnLocation",
        "Anchored": True,
        "Parent": {"__type": "Instance", "path": "Workspace"},
    })
    model = _node("House", "Model", children=[
        _node("Wall", "Part", props={
            "Name": "Wall",
            "ClassName": "Part",
            "Size": {"__type": "Vector3", "x": 8, "y": 6, "z": 1},
            "Parent": {"__type": "Instance", "path": "Workspace/House"},
        }),
    ])
    main_script = _node("Main", "Script", props={
        "Name": "Main",
        "ClassName": "Script",
        "Enabled": True,
        "Parent": {"__type": "Instance", "path": "ServerScriptService"},
    })
    config_module = _node("Config", "ModuleScript", props={
        "Name": "Config",
        "ClassName": "ModuleScript",
        "Parent": {"__type": "Instance", "path": "ReplicatedStorage"},
    })
    return _node("game", "DataModel", children=[
        _node("Workspace", "Workspace", children=[baseplate, spawn, model]),
        _node("Lighting", "Lighting"),
        _node("ServerScriptService", "ServerScriptService", children=[main_script]),
        _node("ReplicatedStorage", "ReplicatedStorage", children=[config_module]),
        _node("Players", "Players"),
    ])


def _join(path: str, name: str) -> str:
    """Path prefix without the leading 'game/' segment."""
    return name if path in ("", "game") else f"{path}/{name}"


SOURCES: dict[str, str] = {
    "ServerScriptService/Main": 'print("hello from Main")\n',
    "ReplicatedStorage/Config": "return { speed = 16 }\n",
}


class FakeStudio:
    def __init__(self, port: int) -> None:
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.root = default_model()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.commands: list[str] = []
        self.selection = [{"path": "Workspace/Baseplate", "className": "Part"}]

    # -- bridge ---------------------------------------------------------------
    def _request(self, path: str, method: str = "GET", payload: Any = None) -> tuple[int, Any]:
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            if resp.status == 204 or not body:
                return resp.status, None
            return resp.status, json.loads(body)

    def start(self, wait: float = 5.0) -> None:
        self.thread = threading.Thread(target=self._loop, name="fake-studio", daemon=True)
        self.thread.start()
        deadline = time.time() + wait
        while time.time() < deadline:
            try:
                status, _ = self._request("/health")
                if status == 200:
                    return
            except (urllib.error.URLError, OSError):
                time.sleep(0.05)
        raise TimeoutError("bridge did not come up")

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _loop(self) -> None:
        try:
            self._request("/hello", "POST", {"plugin": "FakeStudio", "studio": "0.000", "place": "TestPlace"})
        except (urllib.error.URLError, OSError):
            pass
        while not self.stop_event.is_set():
            try:
                status, message = self._request("/poll?wait=2")
            except (urllib.error.URLError, OSError):
                time.sleep(0.3)
                continue
            if status != 200 or not message:
                continue
            self.commands.append(message.get("command", ""))
            reply = self._handle(message.get("command"), message.get("args") or {})
            reply["id"] = message.get("id")
            try:
                self._request("/result", "POST", reply)
            except (urllib.error.URLError, OSError):
                pass

    # -- model helpers --------------------------------------------------------
    def find(self, path: str) -> dict[str, Any]:
        parts = [p for p in path.split("/") if p]
        if parts and parts[0].lower() == "game":
            parts.pop(0)
        node = self.root
        for part in parts:
            for child in node["children"]:
                if child["name"] == part:
                    node = child
                    break
            else:
                raise KeyError(f"no child {part!r} in {path}")
        return node

    def path_of(self, node: dict[str, Any], current: str = "") -> str:
        if node is self.root:
            return current
        for child in node["children"]:
            found = self.path_of(child, current + "/" + child["name"])
            if found:
                return found
        return ""

    # -- command dispatch -----------------------------------------------------
    def _handle(self, command: str | None, args: dict[str, Any]) -> dict[str, Any]:
        try:
            data = getattr(self, "cmd_" + str(command), None)
            if data is None:
                raise ValueError(f"unknown command {command}")
            return {"ok": True, "data": data(args)}
        except Exception as exc:  # noqa: BLE001 - mirror plugin behaviour
            return {"ok": False, "error": str(exc)}

    def cmd_get_tree(self, args: dict[str, Any]) -> dict[str, Any]:
        max_depth = int(args.get("maxDepth") or 2)
        max_nodes = int(args.get("maxNodes") or 300)
        counter = {"count": 0}

        def build(node: dict[str, Any], depth: int, path: str) -> dict[str, Any] | None:
            if counter["count"] >= max_nodes:
                return None
            counter["count"] += 1
            out = {"name": node["name"], "class": node["class"], "path": path}
            if depth < max_depth:
                children = []
                for child in node["children"]:
                    built = build(child, depth + 1, _join(path, child["name"]))
                    if built is None:
                        break
                    children.append(built)
                if children:
                    out["children"] = children
            elif node["children"]:
                out["leaf"] = len(node["children"])
            return out

        return {"root": args.get("root", "game"), "tree": build(self.root, 0, "game"),
                "nodes": counter["count"], "truncated": counter["count"] >= max_nodes}

    def cmd_get_instance(self, args: dict[str, Any]) -> dict[str, Any]:
        node = self.find(args["path"])
        props = node["props"]
        if args.get("properties"):
            props = {k: v for k, v in props.items() if k in args["properties"]}
        return {
            "path": args["path"],
            "className": node["class"],
            "properties": props,
            "attributes": node.get("attributes", {}),
            "children": [{"name": c["name"], "className": c["class"]} for c in node["children"]],
        }

    def cmd_search_instances(self, args: dict[str, Any]) -> dict[str, Any]:
        query = (args.get("query") or "").lower()
        class_name = (args.get("className") or "").lower()
        limit = int(args.get("maxResults") or 40)
        matches: list[dict[str, Any]] = []
        total = 0

        def visit(node: dict[str, Any], path: str) -> None:
            nonlocal total
            name = node["name"].lower()
            if (not query or query in name) and (not class_name or class_name in node["class"].lower()):
                total += 1
                if len(matches) < limit:
                    matches.append({"path": path, "className": node["class"], "name": node["name"]})
            for child in node["children"]:
                visit(child, _join(path, child["name"]))

        visit(self.root, "game")
        return {"matches": matches, "total": total, "truncated": total > len(matches)}

    def cmd_create_instance(self, args: dict[str, Any]) -> dict[str, Any]:
        parent_path = args.get("parent") or "Workspace"
        parent = self.find(parent_path)
        name = args.get("name") or args.get("className", "Instance")
        props = {"Name": name, "ClassName": args["className"],
                 "Parent": {"__type": "Instance", "path": parent_path}}
        for key, value in (args.get("properties") or {}).items():
            props[key] = value
        node = _node(name, args["className"], props=props)
        parent["children"].append(node)
        return {"path": f"{parent_path}/{name}", "className": args["className"]}

    def cmd_set_property(self, args: dict[str, Any]) -> dict[str, Any]:
        node = self.find(args["path"])
        node["props"][args["property"]] = args["value"]
        return {"path": args["path"], "value": node["props"][args["property"]]}

    def cmd_delete_instance(self, args: dict[str, Any]) -> dict[str, Any]:
        parts = [p for p in args["path"].split("/") if p]
        parent = self.find("/".join(parts[:-1]) or "game")
        for index, child in enumerate(parent["children"]):
            if child["name"] == parts[-1]:
                del parent["children"][index]
                return {"path": args["path"], "className": child["class"]}
        raise KeyError(args["path"])

    def cmd_insert_model(self, args: dict[str, Any]) -> dict[str, Any]:
        parent_path = args.get("parent") or "Workspace"
        parent = self.find(parent_path)
        node = _node(f"Asset_{args['assetId']}", "Model",
                     props={"Name": f"Asset_{args['assetId']}", "ClassName": "Model"})
        parent["children"].append(node)
        return {"path": f"{parent_path}/Asset_{args['assetId']}", "className": "Model", "count": 1}

    def cmd_execute_luau(self, args: dict[str, Any]) -> dict[str, Any]:
        first_line = args["code"].strip().splitlines()[0]
        return {"value": f"fake: {first_line}", "durationMs": 1}

    def cmd_list_scripts(self, args: dict[str, Any]) -> dict[str, Any]:
        scripts = []
        total = 0

        def visit(node: dict[str, Any], path: str) -> None:
            nonlocal total
            if node["class"] in ("Script", "LocalScript", "ModuleScript"):
                total += 1
                scripts.append({"path": path, "className": node["class"],
                                "lines": len(SOURCES.get(path, "").splitlines())})
            for child in node["children"]:
                visit(child, _join(path, child["name"]))

        visit(self.root, "game")
        return {"scripts": scripts, "total": total, "truncated": False}

    def cmd_read_script(self, args: dict[str, Any]) -> dict[str, Any]:
        path = args["path"]
        self.find(path)
        return {"path": path, "source": SOURCES.get(path, ""),
                "lines": len(SOURCES.get(path, "").splitlines())}

    def cmd_write_script(self, args: dict[str, Any]) -> dict[str, Any]:
        path = args["path"]
        self.find(path)
        SOURCES[path] = args["source"]
        return {"path": path, "lines": len(args["source"].splitlines())}

    def cmd_get_selection(self, _args: dict[str, Any]) -> dict[str, Any]:
        return {"selection": self.selection}
