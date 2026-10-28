"""Every raw tool action returns an Envelope whose data validates against its typed output model.

Runs the real tool functions against the synthetic emulator used by the other controller tests.
The CPU inspection needs registers the synthetic emulator does not provide, so it is not covered here.
"""
import pytest
from mcp.server.mcpserver import Image

from dmg_cartographer import mcp_app
from dmg_cartographer.core.controller import PyBoyController
from dmg_cartographer.tools import raw
from test_inspection import BankedSim
from test_knowledge import make_rom


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    roms = tmp_path / ".dmg-cartographer" / "roms"
    roms.mkdir(parents=True)
    (roms / "puzzle.gb").write_bytes(make_rom())
    c = PyBoyController(BankedSim)
    c.load_game(str(tmp_path), "puzzle.gb")
    monkeypatch.setattr(mcp_app, "CONTROLLER", c)
    monkeypatch.setattr(raw, "CONTROLLER", c)
    monkeypatch.setitem(mcp_app.SENSITIVE_ENABLED, "write", True)
    c.step(1)
    return c


def split(result):
    """Tools return either an Envelope or [Envelope, *Images]; return (envelope, images)."""
    if isinstance(result, list):
        return result[0], [r for r in result[1:] if isinstance(r, Image)]
    return result, []


def check(result, action, images=0):
    env, imgs = split(result)
    assert env.data.action == action
    if action != "close":  # after close no ROM is loaded, so there is no sha or frame
        assert env.rom_sha1 and env.frame is not None
    assert len(imgs) == images
    return env


def test_game_actions(ctl, tmp_path):
    assert check(raw.game("status"), "status").data.loaded is True
    assert check(raw.game("list", project_dir=str(tmp_path)), "list").data.roms == ["puzzle.gb"]
    check(raw.game("reset"), "reset")
    check(raw.game("load", project_dir=str(tmp_path), rom_name="puzzle.gb"), "load")
    check(raw.game("close"), "close")


def test_step_actions(ctl):
    assert check(raw.step(frames=3), "step").data.advanced_frames == 3
    check(raw.step(actions=[mcp_app.Action(frames=2)]), "sequence")
    check(raw.step(frames=2, capture=True), "step", images=1)
    check(raw.button("a", "press"), "button")
    check(raw.button("a", "release"), "button")


def test_screen_actions(ctl):
    check(raw.screen(), "screen", images=1)
    check(raw.screen(overlays=["pixels"]), "annotated", images=1)


def test_video_actions(ctl):
    check(raw.inspect_video("area"), "area", images=0)
    check(raw.inspect_video("tilemap", width=2, height=2), "tilemap")
    check(raw.inspect_video("tiles", width=2, height=2), "tiles")
    check(raw.inspect_video("sprites", max_results=5), "sprites")
    check(raw.inspect_video("palette"), "palette")


def test_memory_actions(ctl):
    assert check(raw.memory("read", address=0xC000, length=2), "read").data.bytes
    check(raw.memory("decode", address=0xC000, length=2,
                     fields=[{"name": "x", "type": "uint8", "offset": 0}]), "decode")
    check(raw.memory("write", address="0xC000", value=1, max_results=5), "write")


def test_state_actions(ctl):
    check(raw.state("save", name="cp1"), "save")
    check(raw.state("list"), "list")
    check(raw.state("load", name="cp1"), "load")


def test_snapshot_actions(ctl):
    snap = check(raw.snapshot("capture", name="a"), "capture").data
    check(raw.snapshot("list"), "list")
    check(raw.snapshot("delete", snapshot_id=snap.id), "delete")


def test_compare_and_search(ctl):
    base = raw.snapshot("capture", name="base").data.id
    check(raw.compare("memory", baseline=base), "memory")
    check(raw.compare("screen", baseline=base), "screen", images=0)
    check(raw.search_memory("search", condition="unknown"), "search")
    check(raw.search_memory("search", region="HRAM", condition="unknown"), "search")
    check(raw.search_memory("list"), "list")


def test_code_actions(ctl):
    check(raw.inspect_code("cartridge"), "cartridge")
    check(raw.inspect_code("rom", bank=0, address="0x0100", length=4), "rom")
    check(raw.inspect_code("disasm", bank=0, address="0x0150", count=2), "disasm")


def test_hook_list(ctl):
    check(raw.hook_execution("list"), "list")


def test_input_errors_reach_the_client_with_their_message(monkeypatch):
    import asyncio
    from mcp import Client

    unloaded = PyBoyController(BankedSim)
    monkeypatch.setattr(mcp_app, "CONTROLLER", unloaded)
    monkeypatch.setattr(raw, "CONTROLLER", unloaded)

    async def call():
        async with Client(mcp_app.mcp) as client:
            return await client.call_tool("step", {"frames": 1})

    result = asyncio.run(call())
    assert result.is_error
    assert "No game loaded" in result.content[0].text
