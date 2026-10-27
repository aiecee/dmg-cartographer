"""The skills name tools that the server registers."""
import asyncio
import re
from pathlib import Path

import pytest

from dmg_cartographer.server import mcp

SKILLS = sorted((Path(__file__).resolve().parents[1] / "skills").glob("*/SKILL.md"))


def tools_and_params():
    from mcp import Client

    async def fetch():
        async with Client(mcp) as client:
            return {t.name: set((t.input_schema or {}).get("properties", {})) for t in (await client.list_tools()).tools}

    return asyncio.run(fetch())


def test_there_are_exactly_four_skills_with_frontmatter():
    assert [p.parent.name for p in SKILLS] == ["code", "find-variable", "graphics", "navigate"]
    for path in SKILLS:
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\nname: " + path.parent.name + "\n"), path
        assert "\ndescription: " in text.split("---")[1]


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_skill_names_only_current_tools(path):
    tools = tools_and_params()
    text = path.read_text(encoding="utf-8")
    called = set(re.findall(r"`?\b([a-z_]+)\((?=\w+=|\)|\[|\d|')", text))
    for name in called:
        if name in tools or name in {"step", "game"}:
            continue
        # helper words that look like calls but are not tools
        assert name not in {"raw", "investigate"}, name
    for name in tools:
        if re.search(rf"\b{name}\(", text):
            assert name in tools
