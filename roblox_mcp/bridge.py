"""HTTP bridge between the MCP server and the Roblox Studio plugin.

The Studio plugin runs inside Studio, so the MCP server (stdio, started by the
MCP client) cannot reach Studio directly.  Instead the plugin long-polls this
tiny local HTTP server; the server keeps a queue of commands and resolves each
call with whatever the plugin posts back.

    MCP client --stdio--> roblox_mcp --HTTP(127.0.0.1:44755)--> Studio plugin

Only loopback is used; nothing is exposed to the network.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 44755
PLUGIN_TIMEOUT = 35.0  # seconds without a poll before the plugin counts as gone


class BridgeError(RuntimeError):
    """Raised when Studio cannot answer a command."""


@dataclass
class Command:
    id: str
    name: str
    args: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None


class Bridge:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 token: str | None = None) -> None:
        self.host = host
        self.port = port
        self.token = token
        self._cond = threading.Condition()
        self._queue: list[Command] = []
        self._pending: dict[str, Command] = {}
        self._plugin: dict[str, Any] | None = None
        self._last_seen: float = 0.0
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.logs: list[str] = []

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._httpd is not None:
            return
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:  # silence stderr spam
                return

            # -- helpers ------------------------------------------------------
            def _body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    data = json.loads(raw or b"{}")
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON body: {exc}") from exc
                if not isinstance(data, dict):
                    raise ValueError("JSON body must be an object")
                return data

            def _send(self, code: int, payload: Any = None) -> None:
                body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(code)
                if body:
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _authorized(self) -> bool:
                if not bridge.token:
                    return True
                return self.headers.get("X-Roblox-MCP-Token") == bridge.token

            # -- routes -------------------------------------------------------
            def do_GET(self) -> None:  # noqa: N802
                if not self._authorized():
                    self._send(401, {"error": "unauthorized"})
                    return
                url = urlparse(self.path)
                if url.path == "/health":
                    self._send(200, bridge.status())
                elif url.path == "/poll":
                    self._poll(url)
                elif url.path == "/commands":
                    self._send(200, [{"id": c.id, "name": c.name} for c in bridge._queue])
                else:
                    self._send(404, {"error": "not found"})

            def _poll(self, url) -> None:
                try:
                    wait = float(parse_qs(url.query).get("wait", ["20"])[0])
                except ValueError:
                    wait = 20.0
                wait = max(0.0, min(wait, 50.0))
                bridge.touch_plugin()
                deadline = time.monotonic() + wait
                with bridge._cond:
                    while not bridge._queue:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            bridge.touch_plugin()
                            self._send(204)
                            return
                        bridge._cond.wait(remaining)
                    cmd = bridge._queue.pop(0)
                self._send(200, {"id": cmd.id, "command": cmd.name, "args": cmd.args})

            def do_POST(self) -> None:  # noqa: N802
                if not self._authorized():
                    self._send(401, {"error": "unauthorized"})
                    return
                url = urlparse(self.path)
                try:
                    data = self._body()
                except ValueError as exc:
                    self._send(400, {"error": str(exc)})
                    return
                if url.path == "/hello":
                    bridge.register_plugin(data)
                    self._send(200, {"server": "roblox-mcp", "pollWait": 20})
                elif url.path == "/result":
                    bridge._resolve(data)
                    self._send(200, {"ok": True})
                elif url.path == "/log":
                    bridge._log(data)
                    self._send(200, {"ok": True})
                elif url.path == "/bye":
                    bridge.unregister_plugin(data)
                    self._send(200, {"ok": True})
                else:
                    self._send(404, {"error": "not found"})

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="roblox-mcp-bridge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread = None

    # ------------------------------------------------------------------ plugin state
    def touch_plugin(self) -> None:
        with self._cond:
            self._last_seen = time.monotonic()

    def register_plugin(self, info: dict[str, Any]) -> None:
        with self._cond:
            self._plugin = {
                "name": info.get("plugin") or "RobloxMCP",
                "studio": info.get("studio") or "unknown",
                "place": info.get("place") or "",
            }
            self._last_seen = time.monotonic()
            self._cond.notify_all()

    def unregister_plugin(self, info: dict[str, Any] | None = None) -> None:
        with self._cond:
            self._plugin = None
            self._last_seen = 0.0

    def _log(self, data: dict[str, Any]) -> None:
        line = f"[studio] {data.get('level', 'info')}: {data.get('message', '')}"
        self.logs.append(line)
        del self.logs[:-200]
        with self._cond:
            self._last_seen = time.monotonic()

    @property
    def connected(self) -> bool:
        with self._cond:
            return self._plugin is not None and (time.monotonic() - self._last_seen) < PLUGIN_TIMEOUT

    def status(self) -> dict[str, Any]:
        with self._cond:
            connected = self._plugin is not None and (time.monotonic() - self._last_seen) < PLUGIN_TIMEOUT
            return {
                "connected": connected,
                "plugin": self._plugin if connected else None,
                "queued": len(self._queue),
                "port": self.port,
                "lastPollAgo": round(time.monotonic() - self._last_seen, 1) if self._last_seen else None,
            }

    def wait_for_plugin(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.connected:
                return True
            time.sleep(0.1)
        return self.connected

    # ------------------------------------------------------------------ calls
    def _resolve(self, data: dict[str, Any]) -> None:
        cmd_id = data.get("id")
        with self._cond:
            self._last_seen = time.monotonic()
            cmd = self._pending.pop(cmd_id, None)
        if cmd is None:
            return
        cmd.result = {
            "ok": bool(data.get("ok", True)),
            "data": data.get("data"),
            "error": data.get("error"),
        }
        cmd.event.set()

    def call(self, name: str, args: dict[str, Any] | None = None, timeout: float = 60.0,
             require_plugin: bool = True) -> Any:
        args = args or {}
        if require_plugin and not self.wait_for_plugin(3.0):
            raise BridgeError(
                "Roblox Studio 插件未连接。请确认：1) Studio 已打开一个 place；"
                "2) 插件 RobloxMCP 已启用（Plugins 工具栏 → Manage Plugins，或在 3D 视口右键）；"
                "3) Game Settings → Security → Allow HTTP Requests 已开启。"
            )
        cmd = Command(id=uuid.uuid4().hex, name=name, args=args)
        with self._cond:
            self._queue.append(cmd)
            self._pending[cmd.id] = cmd
            self._cond.notify_all()
        try:
            if not cmd.event.wait(timeout):
                raise BridgeError(
                    f"命令 {name} 在 {timeout:.0f}s 内没有返回。Studio 可能正在忙、被弹窗挡住，或插件已停止轮询。"
                )
        finally:
            with self._cond:
                if cmd.id in self._pending:
                    del self._pending[cmd.id]
                if cmd in self._queue:
                    self._queue.remove(cmd)
        assert cmd.result is not None
        if not cmd.result["ok"]:
            raise BridgeError(str(cmd.result.get("error") or f"{name} 执行失败"))
        return cmd.result["data"]
