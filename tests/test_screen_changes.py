"""investigate_screen_changes on the synthetic emulator (mocked, not real PyBoy).

The synthetic game draws only the piece (8x8 at x, 40), so pixel results have exact known values. Sprites come from
OAM written directly into the synthetic memory; background tiles come from a dict-backed tilemap stub.
"""
import io

import pytest
from PIL import Image

from dmg_cartographer import analysis
from dmg_cartographer.core.controller import PyBoyController
from test_inspection import BankedSim
from test_knowledge import make_rom


class Tiles:
    def __init__(self):
        self.cells = {}

    def __getitem__(self, xy):
        return self.cells.get(xy, 0)

    def tile(self, column, row):
        raise AssertionError("not used in these tests")


@pytest.fixture
def emu(tmp_path):
    roms = tmp_path / ".dmg-cartographer" / "roms"
    roms.mkdir(parents=True)
    (roms / "puzzle.gb").write_bytes(make_rom())
    c = PyBoyController(BankedSim)
    c.load_game(str(tmp_path), "puzzle.gb")
    mem = c._pyboy.memory.base
    mem[0xFE00:0xFEA0] = [0] * 160     # no visible sprites
    mem[0xFF40] = 0x91
    c._pyboy.tilemap_background = Tiles()
    c._pyboy.tilemap_window = Tiles()
    c.step(2)
    return c


def set_sprite(emu, slot, y, x, tile):
    emu._pyboy.memory.base[0xFE00 + slot * 4:0xFE00 + slot * 4 + 4] = [y, x, tile, 0]


# ---------- pure service ----------

def test_sprite_service_reports_direct_moves_and_inferred_identity():
    before = [{"index": 2, "tile_index": 5, "on_screen": True, "screen": {"x": 60, "y": 40}},
              {"index": 4, "tile_index": 9, "on_screen": True, "screen": {"x": 10, "y": 10}}]
    after = [{"index": 2, "tile_index": 5, "on_screen": True, "screen": {"x": 63, "y": 40}},
             {"index": 4, "tile_index": 7, "on_screen": True, "screen": {"x": 10, "y": 10}}]
    out = analysis.sprite_changes(before, after)
    assert out["moved_count"] == 1
    assert out["moved"][0]["delta"] == {"dx": 3, "dy": 0} and out["moved"][0]["inferred_same_object"] is True
    assert out["tile_changed_in_place_count"] == 1 and out["unchanged_on_screen"] == 0
    assert "inference" in out["identity_basis"]


# ---------- investigate_screen_changes ----------

def test_identical_frames_report_no_change(emu):
    a = emu.capture_snapshot("a", include_tilemap=True)
    b = emu.capture_snapshot("b", include_tilemap=True)
    out, png = emu.investigate_screen_changes("a", current=False, target="b", include_image=False)
    assert out["pixels"]["changed"] == 0 and out["rectangle_count"] == 0 and out["bounding_box"] is None
    assert out["sprites"]["moved_count"] == 0
    assert out["background_tiles"]["background"]["changed_tiles"] == 0
    assert out["summary"].startswith("0 pixels changed")
    assert png is None


def test_one_pixel_move_gives_exact_pixels_and_bounding_box(emu):
    emu.capture_snapshot("before")
    emu.step(1, press=["right"])                       # piece x 80 -> 81
    out, _ = emu.investigate_screen_changes("before", current=True)
    # The old column 80 turns off and the new column 88 turns on, 8 rows each.
    assert out["pixels"]["changed"] == 16
    assert out["bounding_box"] == {"x": 80, "y": 40, "width": 9, "height": 8}
    assert out["cells"]["changed_cells"] == 2
    assert out["basis"].startswith("direct")


def test_sprite_movement_and_disappearance_are_reported(emu):
    set_sprite(emu, 2, y=56, x=68, tile=5)             # screen (60, 40)
    emu.capture_snapshot("a")
    set_sprite(emu, 2, y=56, x=71, tile=5)             # screen (63, 40)
    emu.capture_snapshot("b")
    moved, _ = emu.investigate_screen_changes("a", current=False, target="b")
    assert moved["sprites"]["moved"][0]["delta"] == {"dx": 3, "dy": 0}
    assert moved["sprites"]["moved"][0]["inferred_same_object"] is True
    set_sprite(emu, 2, y=0, x=0, tile=5)               # hidden
    emu.capture_snapshot("c")
    gone, _ = emu.investigate_screen_changes("b", current=False, target="c")
    assert gone["sprites"]["disappeared"] == [{"slot": 2, "was_at": {"x": 63, "y": 40}}]


def test_background_tile_changes_need_tilemap_snapshots(emu):
    emu.capture_snapshot("t1", include_tilemap=True)
    emu._pyboy.tilemap_background.cells[(3, 2)] = 77
    emu.capture_snapshot("t2", include_tilemap=True)
    out, _ = emu.investigate_screen_changes("t1", current=False, target="t2")
    background = out["background_tiles"]["background"]
    assert background["changed_tiles"] == 1
    assert background["changes"][0] == {"x": 3, "y": 2, "previous": 0, "current": 77}
    emu.capture_snapshot("plain")  # captured without include_tilemap
    no_tiles, _ = emu.investigate_screen_changes("t1", current=False, target="plain")
    assert "unavailable" in no_tiles["background_tiles"]


def test_missing_sprite_data_is_reported_not_guessed(emu):
    old = emu.capture_snapshot("old")
    emu._snapshots[old["id"]]["oam"] = None           # as if captured before OAM was stored
    emu.capture_snapshot("new")
    out, _ = emu.investigate_screen_changes("old", current=False, target="new")
    assert "unavailable" in out["sprites"]


def test_region_limits_the_pixel_comparison(emu):
    emu.capture_snapshot("before")
    emu.step(1, press=["right"])
    out, _ = emu.investigate_screen_changes("before", region={"x": 0, "y": 0, "width": 40, "height": 40})
    assert out["pixels"]["changed"] == 0


def test_overlay_image_is_a_copy_and_framebuffer_is_untouched(emu):
    emu.capture_snapshot("before")
    emu.step(1, press=["right"])
    frame_before = emu._pyboy.screen.image.tobytes()
    out, png = emu.investigate_screen_changes("before", include_image=True)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (320, 288)
    assert emu._pyboy.screen.image.tobytes() == frame_before


def test_snapshot_without_screen_is_rejected(emu):
    emu.capture_snapshot("memory_only", include_screen=False)
    emu.capture_snapshot("with_screen")
    with pytest.raises(ValueError, match="without a screen"):
        emu.investigate_screen_changes("memory_only", current=False, target="with_screen")


def test_investigation_never_advances_frames(emu):
    emu.capture_snapshot("a")
    ticks = emu._pyboy.ticks
    emu.investigate_screen_changes("a")
    assert emu._pyboy.ticks == ticks
