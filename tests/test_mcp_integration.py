"""Runs against the real MCP SDK when installed. No commercial ROM needed."""
import asyncio

import pytest

mcp_module = pytest.importorskip("mcp")


def test_mcp_lists_registered_tools(tmp_path):
    from mcp import Client
    from dmg_cartographer.server import mcp

    (tmp_path / ".dmg-cartographer" / "roms").mkdir(parents=True)

    async def check():
        async with Client(mcp) as client:
            result = await client.list_tools()
            names = {tool.name for tool in result.tools}
            assert {"game", "step", "screen", "state", "memory", "inspect_video"} <= names
            assert {"experiment", "branch", "watch", "replay", "result", "discoveries", "export", "screen_changes"} <= names
            assert not {n for n in names if n.startswith("investigate_")}
            games = await client.call_tool("game", {"action": "list", "project_dir": str(tmp_path)})
            assert not games.is_error

    asyncio.run(check())
