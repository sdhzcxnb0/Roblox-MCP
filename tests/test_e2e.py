"""End-to-end test: MCP client → roblox_mcp server → fake Studio plugin.

Run with:  python tests/test_e2e.py      (or `pytest tests/test_e2e.py`)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_plugin import FakeStudio  # noqa: E402

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("TEST_PORT", "44801"))


def text_of(result) -> str:
    parts = []
    for item in result.content:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(text)
    return "\n".join(parts)


async def main() -> None:
    studio = FakeStudio(PORT)

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "roblox_mcp"],
        cwd=REPO_ROOT,
        env={**os.environ, "ROBLOX_MCP_PORT": str(PORT)},
    )
    checks: list[str] = []

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {tool.name for tool in (await session.list_tools()).tools}
            expected = {
                "studio_status", "get_tree", "get_instance", "search_instances", "create_instance",
                "set_property", "delete_instance", "insert_model", "execute_luau",
                "list_scripts", "read_script", "write_script", "get_selection",
            }
            assert expected <= tools, f"missing tools: {expected - tools}"
            checks.append(f"{len(tools)} tools listed")

            # the bridge is only up once the server process starts, so the fake plugin joins now
            await asyncio.to_thread(studio.start)
            await asyncio.sleep(0.2)
            status = text_of(await session.call_tool("studio_status", {}))
            assert "connected" in status, status
            assert "TestPlace" in status, status
            checks.append("status connected")

            tree = text_of(await session.call_tool("get_tree", {"max_depth": 2}))
            assert "Workspace" in tree and "Baseplate" in tree, tree
            checks.append("tree")

            detail = text_of(await session.call_tool("get_instance", {"path": "Workspace/Baseplate"}))
            assert "Size" in detail and "Vector3(2048, 4, 2048)" in detail, detail
            checks.append("get_instance")

            found = text_of(await session.call_tool("search_instances", {"query": "wall"}))
            assert "Workspace/House/Wall" in found, found
            checks.append("search")

            created = text_of(await session.call_tool("create_instance", {
                "class_name": "Part", "parent": "Workspace", "name": "McpPart",
                "properties": json.dumps({"Size": "Vector3(1,2,3)", "Anchored": "true"}),
            }))
            assert "Workspace/McpPart" in created, created
            checks.append("create_instance")

            changed = text_of(await session.call_tool("set_property", {
                "path": "Workspace/McpPart", "property": "Transparency", "value": "0.5",
            }))
            assert "Transparency" in changed and "0.5" in changed, changed
            checks.append("set_property")

            luau = text_of(await session.call_tool("execute_luau", {"code": "return 1 + 1"}))
            assert "fake" in luau, luau
            checks.append("execute_luau")

            scripts = text_of(await session.call_tool("list_scripts", {}))
            assert "ServerScriptService/Main" in scripts, scripts
            checks.append("list_scripts")

            source = text_of(await session.call_tool("read_script", {"path": "ServerScriptService/Main"}))
            assert "hello from Main" in source, source
            checks.append("read_script")

            written = text_of(await session.call_tool("write_script", {
                "path": "ReplicatedStorage/Config", "source": "return { speed = 32 }\nprint('x')\n",
            }))
            assert "2 lines" in written, written
            after = text_of(await session.call_tool("read_script", {"path": "ReplicatedStorage/Config"}))
            assert "speed = 32" in after and "print('x')" in after, after
            checks.append("write_script")

            inserted = text_of(await session.call_tool("insert_model", {"asset_id": 123, "parent": "Workspace"}))
            assert "Asset_123" in inserted, inserted
            checks.append("insert_model")

            selection = text_of(await session.call_tool("get_selection", {}))
            assert "Workspace/Baseplate" in selection, selection
            checks.append("get_selection")

            deleted = text_of(await session.call_tool("delete_instance", {"path": "Workspace/McpPart"}))
            assert "Deleted" in deleted, deleted
            tree_after = text_of(await session.call_tool("get_tree", {"root": "Workspace", "max_depth": 1}))
            assert "McpPart" not in tree_after, tree_after
            checks.append("delete_instance")

    studio.stop()
    print("OK")
    for line in checks:
        print("  ✓", line)


if __name__ == "__main__":
    asyncio.run(main())
