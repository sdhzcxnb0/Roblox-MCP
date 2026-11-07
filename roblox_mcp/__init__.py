"""Roblox MCP — bridge Roblox Studio to any Model Context Protocol client."""

from .server import VERSION, build_server, main

__all__ = ["VERSION", "build_server", "main"]
__version__ = VERSION
