"""INTEGRATION tests against real PyBoy and a real Dr. Mario ROM.

These are the only tests that check real emulator behaviour. They skip when no ROM is present in .dmg-cartographer/roms/.
The ROM is copied into a temporary project, so nothing is written into the real project folder, and the ROM itself
is never committed (the .dmg-cartographer folder is gitignored).
"""
import shutil
from pathlib import Path

import pytest

from dmg_cartographer import cartridge
from dmg_cartographer import graphics
from dmg_cartographer.core.controller import PyBoyController

REPO = Path(__file__).resolve().parents[1]
ROMS = sorted((REPO / ".dmg-cartographer" / "roms").glob("Dr. Mario*.gb"))
pytestmark = pytest.mark.skipif(not ROMS, reason="no Dr. Mario ROM in .dmg-cartographer/roms; real-emulator tests skipped")

L = [{"press": ["left"], "frames": 1}, {"release": ["left"], "frames": 1}]
R = [{"press": ["right"], "frames": 1}, {"release": ["right"], "frames": 1}]
I = [{"frames": 2}]


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    project = tmp_path_factory.mktemp("drmario")
    rom_dir = project / ".dmg-cartographer" / "roms"
    rom_dir.mkdir(parents=True)
    shutil.copy(ROMS[0], rom_dir / ROMS[0].name)
    c = PyBoyController()
    c.load_game(str(project), ROMS[0].name)
    return c


@pytest.fixture(scope="module")
def gameplay(real):
    """Advance to gameplay (title, then menu, then start) and save a checkpoint."""
    real.step(300)
    real.step(1, press=["start"]); real.step(1, release=["start"]); real.step(300)
    real.step(1, press=["start"]); real.step(1, release=["start"]); real.step(300)
    real.save_checkpoint("gameplay")
    return "gameplay"


def test_header_and_title_match_the_real_emulator(real):
    checks = real.inspect_cartridge()["checks"]
    assert checks == {"header_valid": True, "header_checksum": True, "rom_size_matches_file": True,
                      "title_matches_emulator": True}


def test_sprite_decoding_matches_pyboy_for_every_slot(real, gameplay):
    real.load_checkpoint(gameplay)
    game = real._pyboy
    oam = bytes(int(v) for v in game.memory[graphics.OAM_START:graphics.OAM_END])
    lcdc = int(game.memory[graphics.LCDC])
    rows = {r["index"]: r for r in graphics.sprite_rows(oam, lcdc, cgb=False)}
    for i in range(graphics.SPRITE_COUNT):
        s, r = game.get_sprite(i), rows[i]
        assert (r["screen"]["x"], r["screen"]["y"]) == (s.x, s.y), i
        assert r["on_screen"] == s.on_screen, i
        assert r["priority_behind_background"] == bool(s.attr_obj_bg_priority), i
        assert r["flip"] == {"x": bool(s.attr_x_flip), "y": bool(s.attr_y_flip)}, i
        assert r["palette"]["dmg"] == s.attr_palette_number, i
        expected_tile = s.tile_identifier if not lcdc & 4 else s.tile_identifier & 0xFE
        assert r["tile_index"] == expected_tile, i


def test_entry_point_decodes_as_nop_then_jump(real):
    out = real.disassemble_rom(0, "0x0100", 2)["instructions"]
    assert (out[0]["mnemonic"], out[1]["mnemonic"], out[1]["targets"]) == ("NOP", "JP", ["0x0150"])


def test_branch_restores_the_real_live_state(real, gameplay):
    real.load_checkpoint(gameplay)
    real.step(1, press=["right"])                # live state: right held, one frame advanced
    before = (real.status()["timeline_frame"], real.status()["held_buttons"], int(real._pyboy.memory[0xC202]))
    real.branch_experiment(gameplay, [{"name": "left", "actions": L}, {"name": "idle", "actions": I}])
    after = (real.status()["timeline_frame"], real.status()["held_buttons"], int(real._pyboy.memory[0xC202]))
    assert after == before


def test_replay_reproduces_a_recorded_run_exactly(real, gameplay):
    real.load_checkpoint(gameplay)
    real.record_replay("it_left", "record", gameplay, L)
    first = real.record_replay("it_left", "replay")
    second = real.record_replay("it_left", "replay")
    assert first["matched"] and second["matched"]
    assert first["advanced_frames"] == 2 and first["frames_match_recording"]


def test_capsule_position_byte_is_supported_by_real_experiments(real, gameplay):
    """0xC202 tracks the falling capsule's X (the left half's OAM X). Found by the same generic tools; no game knowledge."""
    real.load_checkpoint(gameplay)
    game = real._pyboy
    result = real.investigate_validate("0xC202", gameplay, {"left": L, "right": R, "idle": I},
                                       {"left": "decrease", "right": "increase", "idle": "none"}, repeats=2)
    assert result["verdict"] == "supported_by_experiments", result["per_action"]


def test_write_test_separates_the_driver_from_a_mirror(real, gameplay):
    """Writing the driver moves the capsule and keeps the value; writing the mirror is overwritten by the game."""
    driver = real.investigate_validate("0xC202", gameplay, {"idle": I}, {"idle": "none"}, repeats=1,
                                       perturb={"value": 80, "frames": 2}, allow_writes=True)
    mirror = real.investigate_validate("0xC011", gameplay, {"idle": I}, {"idle": "none"}, repeats=1,
                                       perturb={"value": 80, "frames": 2}, allow_writes=True)
    assert driver["perturbation"]["reverted"] is False
    assert mirror["perturbation"]["reverted"] is True
