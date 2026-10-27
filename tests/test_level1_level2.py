"""Level 1 input/reset/palette methods, and tool-group registration (real server, real MCP registration).

Controller tests use the synthetic emulator (mocked). The group tests start the real server module in a subprocess,
so they check what a client would actually see.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dmg_cartographer.core.controller import PyBoyController
from test_inspection import BankedSim
from test_priority2 import make_rom

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def emu(tmp_path):
    roms = tmp_path / ".dmg-cartographer" / "roms"
    roms.mkdir(parents=True)
    (roms / "puzzle.gb").write_bytes(make_rom())
    c = PyBoyController(BankedSim)
    c.load_game(str(tmp_path), "puzzle.gb")
    return c


def test_press_and_release_do_not_advance_frames(emu):
    ticks = emu._pyboy.ticks
    pressed = emu.press_button("left")
    assert pressed["changed"] is True and pressed["held_buttons"] == ["left"]
    assert pressed["frames_advanced"] == 0 and emu._pyboy.ticks == ticks
    assert emu.press_button("left")["changed"] is False          # already held: no change
    released = emu.release_button("left")
    assert released["held_buttons"] == [] and released["frames_advanced"] == 0
    assert emu.release_button("left")["changed"] is False        # not held: no error
    assert emu._pyboy.ticks == ticks


def test_press_takes_effect_on_the_next_frame_advance(emu):
    emu.press_button("right")
    emu.step(2)
    assert emu._pyboy.memory.base[0xC100] == 82  # piece started at 80 and moved right for 2 frames
    assert emu.status()["timeline_frame"] == 2
    assert emu.status()["held_buttons"] == ["right"]


def test_buttons_are_validated(emu):
    with pytest.raises(ValueError):
        emu.press_button("fire")
    with pytest.raises(ValueError):
        emu.release_button("start2")


def test_reset_restarts_from_frame_zero_and_keeps_checkpoints(emu):
    emu.step(5, press=["a"])
    emu.save_checkpoint("keep")
    result = emu.reset()
    assert result["timeline_frame"] == 0 and result["held_buttons"] == [] and result["frames_advanced"] == 0
    assert emu.list_checkpoints() == ["keep"]


def test_palette_reads_registers_as_observations(emu):
    emu._pyboy.memory.base[0xFF47] = 0b11100100      # identity mapping
    emu._pyboy.memory.base[0xFF48] = 0b00011011      # reversed
    result = emu.get_palette()
    assert result["dmg_palettes"]["BGP"]["shade_by_color_index"] == {"0": 0, "1": 1, "2": 2, "3": 3}
    assert result["dmg_palettes"]["OBP0"]["shade_by_color_index"] == {"0": 3, "1": 2, "2": 1, "3": 0}
    assert "not exposed" in result["cgb_palette_ram"]


def _tool_names(env_extra: dict) -> set[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DMG_CARTOGRAPHER_")}
    env.update(env_extra)
    code = (
        "import asyncio, json\n"
        "from dmg_cartographer import server\n"
        "from mcp import Client\n"
        "async def main():\n"
        "    async with Client(server.mcp) as c:\n"
        "        print(json.dumps(sorted(t.name for t in (await c.list_tools()).tools)))\n"
        "asyncio.run(main())\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    return set(json.loads(out.stdout.strip().splitlines()[-1]))


SENSITIVE = {"raw_hook_execution"}


def test_default_hides_sensitive_tools_but_keeps_everything_else():
    names = _tool_names({})
    assert not (names & SENSITIVE)
    assert {"button", "game", "inspect_video", "branch"} <= names


def test_sensitive_flags_enable_only_their_own_tools():
    debug = _tool_names({"DMG_CARTOGRAPHER_ENABLE_DEBUG": "1"})
    assert "hook_execution" in debug
    assert "hook_execution" not in _tool_names({})


def test_group_selection_removes_whole_groups():
    raw_only = _tool_names({"DMG_CARTOGRAPHER_GROUPS": "raw"})
    assert raw_only and "branch" not in raw_only
    investigate_only = _tool_names({"DMG_CARTOGRAPHER_GROUPS": "investigate"})
    assert investigate_only and "game" not in investigate_only
