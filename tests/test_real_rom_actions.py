"""Every raw tool action against a real Dr. Mario ROM, with each result validated by its typed output model.

The ROM is taken from DMG_TEST_ROM, or from the dr-mario-re project if that is present. It is copied into a
temporary project, so nothing is written next to the ROM. Skipped when no ROM is found.
"""
import os
import shutil
from pathlib import Path

import pytest

from dmg_cartographer import mcp_app
from dmg_cartographer.core.controller import PyBoyController
from dmg_cartographer.tools import raw

DEFAULT = Path(__file__).resolve().parents[1] / ".dmg-cartographer" / "roms" / "Dr. Mario (World) (Rev 1).gb"
ROM = Path(os.environ["DMG_TEST_ROM"]) if os.environ.get("DMG_TEST_ROM") else DEFAULT
pytestmark = pytest.mark.skipif(not ROM.is_file(), reason="no Dr. Mario ROM (set DMG_TEST_ROM)")


@pytest.fixture
def real(tmp_path, monkeypatch):
    rom_dir = tmp_path / ".dmg-cartographer" / "roms"
    rom_dir.mkdir(parents=True)
    shutil.copy(ROM, rom_dir / ROM.name)
    c = PyBoyController()
    monkeypatch.setattr(mcp_app, "CONTROLLER", c)
    monkeypatch.setattr(raw, "CONTROLLER", c)
    monkeypatch.setitem(mcp_app.SENSITIVE_ENABLED, "write", True)
    monkeypatch.setitem(mcp_app.SENSITIVE_ENABLED, "debug", True)
    raw.game("load", project_dir=str(tmp_path), rom_name=ROM.name)
    raw.step(frames=60)
    yield c, tmp_path
    raw.game("close")


def data_of(result):
    env = result[0] if isinstance(result, list) else result
    return env.data


def test_every_action_matches_its_model(real):
    _, proj = real
    assert data_of(raw.game("list", project_dir=str(proj))).roms == [ROM.name]
    assert data_of(raw.game("status")).cartridge_title == "DR.MARIO"
    assert data_of(raw.step(frames=3)).advanced_frames == 3
    assert data_of(raw.step(actions=[mcp_app.Action(frames=2, press=["start"]),
                                     mcp_app.Action(frames=2, release=["start"])])).completed_actions == 2
    assert data_of(raw.button("a", "press")).changed is True
    assert data_of(raw.button("a", "release")).changed is True
    raw.step(frames=1, capture=True)
    raw.screen()
    raw.screen(overlays=["pixels", "sprites"])
    assert data_of(raw.inspect_video("area")).width == 32
    assert data_of(raw.inspect_video("tilemap", width=4, height=4)).layer == "background"
    raw.inspect_video("tiles", width=2, height=2, include_images=True, include_attributes=True)
    assert data_of(raw.inspect_video("sprites", max_results=5)).count == 40
    assert set(data_of(raw.inspect_video("palette")).dmg_palettes.model_dump()) == {"BGP", "OBP0", "OBP1"}


def test_memory_state_and_snapshot_actions(real):
    c, _ = real
    assert data_of(raw.memory("read", address=0xC000, length=4)).bytes
    assert data_of(raw.memory("decode", address=0xC000, length=2,
                              fields=[{"name": "x", "type": "uint8", "offset": 0}])).source.length == 2
    fields = [
        {"name": "u8", "type": "uint8", "offset": 0}, {"name": "s8", "type": "int8", "offset": 0},
        {"name": "u16", "type": "uint16", "offset": 0}, {"name": "s16", "type": "int16", "offset": 0},
        {"name": "bits", "type": "bits", "offset": 0, "bit": 0, "width": 3},
        {"name": "bcd", "type": "bcd", "offset": 0, "length": 2},
        {"name": "text", "type": "string", "offset": 0, "length": 4},
        {"name": "arr", "type": "array", "offset": 0, "count": 2, "stride": 2, "item": {"type": "uint8"}},
        {"name": "pat", "type": "pattern", "pattern": "00 ??"},
    ]
    decoded = data_of(raw.memory("decode", address=0xC000, length=8, fields=fields)).fields
    assert set(decoded) == {f["name"] for f in fields}
    write = data_of(raw.memory("write", address="0xC000", value=1, experiment_frames=2))
    assert write.restored is True and write.frames_advanced == 2
    assert data_of(raw.state("save", name="cp1")).saved is True
    assert "cp1" in data_of(raw.state("list")).checkpoints
    assert data_of(raw.state("load", name="cp1")).checkpoint == "cp1"
    snap = data_of(raw.snapshot("capture", name="base")).id
    assert snap in [s.id for s in data_of(raw.snapshot("list")).snapshots]
    raw.step(frames=20)
    cm = data_of(raw.compare("memory", baseline=snap))
    assert cm.changed_count >= 0 and cm.baseline == snap
    assert data_of(raw.compare("memory", baseline=snap, group=False)).groups is None
    cs = data_of(raw.compare("screen", baseline=snap, mode="pixel"))
    assert cs.grid is None
    assert data_of(raw.compare("screen", baseline=snap, mode="grid")).grid is not None
    assert data_of(raw.snapshot("delete", snapshot_id=snap)).deleted == snap


def test_search_code_and_hook_actions(real):
    _, _ = real
    search = data_of(raw.search_memory("search", condition="unknown", max_results=5))
    raw.step(frames=5)
    narrowed = data_of(raw.search_memory("search", session=search.session, condition="changed", max_results=5))
    assert narrowed.session == search.session
    assert data_of(raw.search_memory("list")).sessions
    assert data_of(raw.inspect_code("cartridge")).all_checks_pass in (True, False)
    cpu = data_of(raw.inspect_code("cpu"))
    assert 0 <= cpu.registers.A <= 255
    assert data_of(raw.inspect_code("rom", bank=0, address="0x0100", length=4)).matches_emulator is True
    assert data_of(raw.inspect_code("disasm", bank=0, address="0x0150", count=3)).count == 3
    # Hook the instruction the CPU is on now: the game loops here, so it fires within a few frames.
    pc = data_of(raw.inspect_code("cpu")).registers.PC
    reg = data_of(raw.hook_execution("register", bank=0, address=f"0x{pc:04X}", label="loop"))
    assert reg.registered.key == f"0:{pc:04X}"
    raw.step(frames=3)
    hooks = data_of(raw.hook_execution("list"))
    assert hooks.count == 1 and hooks.hooks[0].observations[0].frame_note
    assert data_of(raw.hook_execution("remove", bank=0, address=f"0x{pc:04X}")).final_count >= 1  # final_count is the hook's hit count


def test_game_lifecycle_actions(real):
    _, proj = real
    assert data_of(raw.game("reset")).frames_advanced >= 0
    assert data_of(raw.game("close")).loaded is False
    assert data_of(raw.game("load", project_dir=str(proj), rom_name=ROM.name)).loaded is True
