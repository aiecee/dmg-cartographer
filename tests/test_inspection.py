"""Sprite/tile inspection, memory watching and writing. Synthetic emulator unless a test says otherwise.

test_sprite_decoding_matches_pyboy_sprite_class runs PyBoy's own Sprite class (real PyBoy code) on a stub memory
view, so it checks the decoder against PyBoy without a ROM.
"""
from types import SimpleNamespace

import pytest
from PIL import Image

from dmg_cartographer import graphics
from dmg_cartographer import memwrite
from dmg_cartographer.core.controller import PyBoyController
from test_toolkit import StatefulSim, project  # noqa: F401  (project fixture reused)
from test_snapshots import PIECE_X


class Mem:
    """Memory view that also accepts (bank, address) tuples, like PyBoy's memory view."""

    def __init__(self, base):
        self.base = base
        self.banks = {0: base, 1: [0] * 0x10000}

    def __getitem__(self, key):
        if isinstance(key, tuple):
            bank, address = key
            return self.banks[bank][address]
        return self.base[key]

    def __setitem__(self, key, value):
        self.base[key] = value

    def __iter__(self):
        return iter(self.base)


class TileMapStub:
    def __getitem__(self, xy):
        x, y = xy
        return x + y * 32

    def tile(self, column, row):
        return SimpleNamespace(image=Image.new("RGB", (8, 8), (column * 8 % 256, row * 8 % 256, 0)))


class BankedSim(StatefulSim):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.memory = Mem(self.memory)
        self.tilemap_background = TileMapStub()
        self.tilemap_window = TileMapStub()

    def load_state(self, buffer):
        super().load_state(buffer)
        self.memory = Mem(self.memory)

    def get_sprite(self, index):
        return SimpleNamespace(tiles=[SimpleNamespace(image=Image.new("RGB", (8, 8), (index, index, index)))])


@pytest.fixture
def emu(project):
    c = PyBoyController(BankedSim)
    c.load_game(project, "puzzle.gb")
    mem = c._pyboy.memory.base
    mem[0xFE00:0xFEA0] = [0] * 160     # empty OAM
    mem[0xFF40] = 0x91                 # LCD on, 8x8 sprites, background map 9800, window map 9800
    mem[0xFF42] = 0                    # SCY
    mem[0xFF43] = 0                    # SCX
    mem[0xFF4A] = 0                    # WY
    mem[0xFF4B] = 7                    # WX (window x = 0)
    mem[0x0143] = 0x43                 # DMG-only header
    c.step(2)
    c.save_checkpoint("base")          # frame 2, piece at 80
    c.step(4, press=["right"])         # frame 6, piece at 84, right held
    return c


def set_sprite(mem, index, y, x, tile, attr):
    mem[0xFE00 + index * 4: 0xFE00 + index * 4 + 4] = [y, x, tile, attr]


# ---------- pure helpers ----------

def test_tall_sprites_use_even_tile_pair_and_region_filters():
    oam = bytearray(160)
    oam[0:4] = bytes([40, 60, 0x13, 0x00])   # 8x16: first tile is the even index 0x12
    oam[4:8] = bytes([40, 130, 0x20, 0x00])  # at x=122
    rows = graphics.sprite_rows(bytes(oam), 0x04, cgb=False, indices=[0, 1])
    assert rows[0]["tile_indices"] == [0x12, 0x13] and rows[0]["screen"]["height"] == 16
    only_right = graphics.sprite_rows(bytes(oam), 0x04, cgb=False,
                                      region={"x": 100, "y": 0, "width": 60, "height": 144})
    assert [r["index"] for r in only_right] == [1]


def test_tile_placement_distinguishes_tilemap_pixel_and_viewport():
    # Scrolled 4 px right: column 0 starts 4 px left of the screen, so it is partly visible.
    partly = graphics.tile_placement("background", 0, 0, scx=4, scy=0, wx=7, wy=0)
    assert partly["viewport"] == {"x": -4, "y": 0} and partly["pixel"] == {"x": 0, "y": 0}
    assert partly["tilemap"] == {"col": 0, "row": 0}
    gone = graphics.tile_placement("background", 31, 0, scx=0, scy=0, wx=7, wy=0)
    assert gone["visible"] is False and gone["viewport"] is None
    window = graphics.tile_placement("window", 0, 0, scx=99, scy=99, wx=7, wy=0)
    assert window["viewport"] == {"x": 0, "y": 0}      # window ignores scroll
    assert window["visible"] is True


def test_cgb_header_and_attribute_decoding():
    assert graphics.cartridge_is_cgb(0x80)["cgb_compatible"] is True
    assert graphics.cartridge_is_cgb(0xC0)["cgb_only"] is True
    assert graphics.cartridge_is_cgb(0x43)["cgb_compatible"] is False
    attr = graphics.decode_tile_attribute(0xE9)  # priority, y flip, bank 1, palette 1
    assert attr["cgb_palette"] == 1 and attr["vram_bank"] == 1 and attr["priority"] is True


def test_grid_overlay_is_a_copy():
    base = Image.new("RGB", (160, 144), (0, 0, 0))
    grid = graphics.annotate(base, tiles=True)
    assert grid.size == (320, 288)
    assert base.getpixel((8, 8)) == (0, 0, 0)  # original untouched


def test_address_policy():
    assert memwrite.parse_address("0xC123") == 0xC123 and memwrite.parse_address("49443") == 49443
    for bad in ["0xZZ", 0x10000, True, -1, "0x10000"]:
        with pytest.raises(ValueError):
            memwrite.parse_address(bad)
    assert memwrite.check_write(0xC000, 3, allow_sensitive=False) == "WRAM"
    assert memwrite.check_write(0xFF80, 2, allow_sensitive=False) == "HRAM"
    for address, length, sensitive in [(0x0100, 1, False), (0xE000, 1, False), (0x8000, 1, False),
                                       (0xFF40, 1, False), (0xFE00, 1, False), (0xBFFF, 2, True)]:
        with pytest.raises(ValueError):
            memwrite.check_write(address, length, allow_sensitive=sensitive)
    assert memwrite.check_write(0x8000, 1, allow_sensitive=True) == "VRAM"
    with pytest.raises(ValueError, match="several regions"):
        memwrite.check_write(0xBFFF, 2, allow_sensitive=True)  # cart RAM edge spills into WRAM


# ---------- inspect_sprites ----------

def test_inspect_sprites_reports_raw_and_screen_values(emu):
    set_sprite(emu._pyboy.memory.base, 3, y=40, x=60, tile=0x12, attr=0xF0)
    result, pngs = emu.inspect_sprites(indices=[3])
    row = result["sprites"][0]
    assert row["raw"] == {"y": 40, "x": 60, "tile": 0x12, "attributes": "0xF0"}
    assert row["screen"] == {"x": 52, "y": 24, "width": 8, "height": 8}
    assert row["flip"] == {"x": True, "y": True} and row["priority_behind_background"] is True
    assert row["palette"] == {"dmg": 1}
    assert pngs == []


def test_inspect_sprites_images_and_limits(emu):
    mem = emu._pyboy.memory.base
    for i in range(9):
        set_sprite(mem, i, y=40, x=20 + i, tile=i, attr=0)
    result, pngs = emu.inspect_sprites(indices=list(range(4)), include_images=True)
    assert len(pngs) == 4 and pngs[0][:8] == b"\x89PNG\r\n\x1a\n"
    with pytest.raises(ValueError, match="at most 8"):
        emu.inspect_sprites(include_images=True)
    with pytest.raises(ValueError):
        emu.inspect_sprites(indices=[40])
    with pytest.raises(ValueError):
        emu.inspect_sprites(region={"x": 150, "y": 0, "width": 20, "height": 5})


def test_inspect_sprites_never_advances(emu):
    ticks = emu._pyboy.ticks
    emu.inspect_sprites()
    emu.inspect_tiles()
    assert emu._pyboy.ticks == ticks


# ---------- inspect_tiles ----------

def test_inspect_tiles_returns_ids_and_placement(emu):
    result, images, grid = emu.inspect_tiles(layer="background", x=2, y=1, width=3, height=2)
    cell = next(t for t in result["tiles"] if t["tilemap"] == {"col": 3, "row": 2})
    assert cell["tile_id"] == 3 + 2 * 32
    assert cell["pixel"] == {"x": 24, "y": 16}
    assert cell["viewport"] == {"x": 24, "y": 16}
    assert result["scroll"] == {"scx": 0, "scy": 0} and images == [] and grid is None
    assert result["map_base"] == "0x9800"


def test_inspect_tiles_window_scroll_and_map_base(emu):
    emu._pyboy.memory.base[0xFF40] = 0x91 | 0x40  # window map 9C00
    result, _, _ = emu.inspect_tiles(layer="window", width=2, height=1)
    assert result["map_base"] == "0x9C00" and result["scroll"] == {"wx": 7, "wy": 0}


def test_inspect_tiles_attributes_need_cgb_header(emu):
    result, _, _ = emu.inspect_tiles(width=1, height=1, include_attributes=True)
    assert "unavailable" in result["tiles"][0]["attributes"]
    mem = emu._pyboy.memory
    mem.base[0x0143] = 0x80
    mem.banks[1][0x9800] = 0xE9
    result, _, _ = emu.inspect_tiles(width=1, height=1, include_attributes=True)
    attr = result["tiles"][0]["attributes"]
    assert attr["cgb_palette"] == 1 and attr["vram_bank"] == 1 and attr["priority"] is True


def test_inspect_tiles_images_grid_and_limits(emu):
    result, images, grid = emu.inspect_tiles(width=2, height=2, include_images=True, include_grid=True)
    assert len(images) == 4 and grid[:8] == b"\x89PNG\r\n\x1a\n"
    with pytest.raises(ValueError):
        emu.inspect_tiles(include_images=True)  # 20x18 is too many for images
    for bad in [dict(x=-1), dict(width=33), dict(layer="sprite"), dict(x=30, width=3)]:
        with pytest.raises(ValueError):
            emu.inspect_tiles(**bad)


# ---------- watch_memory ----------

def test_watch_records_exact_changes_and_inputs(emu):
    # buttons stay held until released, so release "left" at the start of the idle part
    actions = [{"press": ["left"], "release": ["right"], "frames": 3}, {"release": ["left"], "frames": 7}]
    result = emu.watch_memory(["0xC100"], frames=10, actions=actions)
    assert result["advanced_frames"] == 10 and result["requested_frames"] == 10
    assert result["change_count"] == 3
    assert [(e["frame"], e["previous"], e["current"]) for e in result["events"]] == [(1, 84, 83), (2, 83, 82),
                                                                                    (3, 82, 81)]
    assert result["input_events"] == [{"frame": 0, "press": ["left"], "release": ["right"]},
                                      {"frame": 3, "press": [], "release": ["left"]}]
    assert result["addresses"] == [{"address": "0xC100", "region": "WRAM"}]
    assert "frame-sampled" in result["limitation"]


def test_watch_sampling_window_and_bounded_events(emu):
    # right is still held from the fixture, so release it first to observe a truly idle piece
    idle = emu.watch_memory(["0xC100"], frames=10, sample_every=5, actions=[{"release": ["right"], "frames": 10}])
    assert idle["samples"] == 2 and idle["change_count"] == 0 and idle["events"] == []
    bounded = emu.watch_memory(["0xC100"], frames=5, max_events=2,
                               actions=[{"press": ["left"], "release": ["right"], "frames": 5}])
    assert len(bounded["events"]) == 2 and bounded["events_truncated"] is True
    assert bounded["change_count"] == 5 and bounded["advanced_frames"] == 5


def test_watch_validation(emu):
    with pytest.raises(ValueError, match="exactly"):
        emu.watch_memory(["0xC100"], frames=5, actions=[{"frames": 4}])
    for kwargs in [dict(addresses=[]), dict(addresses=["0xC100", "0xC100"]), dict(addresses=["0x10000"]),
                   dict(addresses=["0xC100"], frames=0), dict(addresses=["0xC100"], frames=5, sample_every=6)]:
        params = {"addresses": ["0xC100"], "frames": 5, **kwargs}
        with pytest.raises(ValueError):
            emu.watch_memory(**params)


# ---------- write_memory ----------

def test_plain_write_persists_and_does_not_advance(emu):
    ticks = emu._pyboy.ticks
    before = emu._pyboy.memory.base[0xC050]
    result = emu.write_memory("0xC050", 0x77)
    assert result["previous"] == [before]
    assert emu._pyboy.memory.base[0xC050] == 0x77
    assert result["frames_advanced"] == 0 and result["restored"] is False
    assert emu._pyboy.ticks == ticks and "persists" in result["warning"]


def test_expected_value_guards_the_write(emu):
    before = emu._pyboy.memory.base[0xC050]
    with pytest.raises(ValueError, match="nothing was written"):
        emu.write_memory("0xC050", 0x11, expected=before ^ 0xFF)
    assert emu._pyboy.memory.base[0xC050] == before
    emu.write_memory("0xC050", [1, 2], expected=[before, emu._pyboy.memory.base[0xC051]])
    assert emu._pyboy.memory.base[0xC050:0xC052] == [1, 2]


def test_forbidden_targets_are_refused(emu):
    for address, sensitive in [(0x0150, False), (0xE000, False), (0x8000, False), (0xFF40, False),
                               (0xFE00, False), (0xA000, False)]:
        with pytest.raises(ValueError):
            emu.write_memory(address, 1, allow_sensitive=sensitive)
    with pytest.raises(ValueError, match="ROM"):
        emu.write_memory(0x0150, 1, allow_sensitive=True)
    emu.write_memory(0x8000, 1, allow_sensitive=True)  # explicit opt-in only
    assert emu._pyboy.memory.banks[0][0x8000] == 1


def test_experiment_write_restores_state_and_reports_observations(emu):
    mem = emu._pyboy.memory.base
    before_frames, before_held = emu.status()["timeline_frame"], emu.status()["held_buttons"]
    ticks = emu._pyboy.ticks
    result = emu.write_memory("0xC100", 16, experiment_frames=2)
    assert result["mode"] == "experiment" and result["restored"] is True
    assert result["observations"]["frames_advanced"] == 2
    rows = result["observations"]["memory"]["WRAM"]["changes"]
    piece = next(r for r in rows if r["address"] == "0xC100")
    # The written 16 is followed by 2 frames with "right" still held, so the piece reaches 18 during the experiment.
    assert (piece["previous"]["dec"], piece["current"]["dec"]) == (84, 18)
    assert emu._pyboy.memory.base[PIECE_X] == 84       # restored
    assert emu.status()["timeline_frame"] == before_frames
    assert emu.status()["held_buttons"] == before_held
    assert emu._pyboy.ticks == ticks


def test_experiment_restores_after_failure(emu, monkeypatch):
    before = (emu.status()["timeline_frame"], emu._pyboy.memory.base[PIECE_X])

    def broken(frames, press=None, release=None):
        raise RuntimeError("simulated step failure")

    monkeypatch.setattr(emu, "step", broken)
    with pytest.raises(RuntimeError, match="simulated"):
        emu.write_memory("0xC100", 16, experiment_frames=2)
    # re-read: restoring reloads the emulator, so an earlier memory reference would be stale
    assert (emu.status()["timeline_frame"], emu._pyboy.memory.base[PIECE_X]) == before


def test_write_value_validation(emu):
    for value in [256, -1, [], [1] * 17, "x", True]:
        with pytest.raises(ValueError):
            emu.write_memory("0xC000", value)
    with pytest.raises(ValueError):
        emu.write_memory("0xC000", 1, experiment_frames=601)
