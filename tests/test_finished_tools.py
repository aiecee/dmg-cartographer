"""Finished investigation tools: multi-monitor watch, suggested validation, stored results, replay divergence.

Synthetic emulator (mocked, not real PyBoy). The piece at 0xC100 moves with held left/right and is the only thing
drawn on screen; the frame counter at 0xC200 changes every frame.
"""
import pytest

from dmg_cartographer import analysis
from dmg_cartographer.core.controller import PyBoyController
from test_inspection import BankedSim
from test_priority2 import make_rom

PIECE = 0xC100
LEFT = [{"press": ["left"], "frames": 1}, {"release": ["left"], "frames": 1}]
RIGHT = [{"press": ["right"], "frames": 1}, {"release": ["right"], "frames": 1}]
IDLE = [{"frames": 2}]


@pytest.fixture
def emu(tmp_path):
    roms = tmp_path / ".dmg-cartographer" / "roms"
    roms.mkdir(parents=True)
    (roms / "puzzle.gb").write_bytes(make_rom())
    c = PyBoyController(BankedSim)
    c.load_game(str(tmp_path), "puzzle.gb")
    c._pyboy.memory.base[0xFE00:0xFEA0] = [0] * 160
    c._pyboy.memory.base[0xFF40] = 0x91
    c.step(2)
    c.save_checkpoint("base")
    return c


# ---------- investigate_watch with more monitor kinds ----------

def test_watch_cpu_reports_register_changes_with_exact_frames(emu):
    from types import SimpleNamespace
    emu._pyboy.register_file = SimpleNamespace(A=0, F=0, B=0, C=0, D=0, E=0, HL=0, SP=0, PC=0)
    original_tick = emu._pyboy.tick

    def tick(frames, render, sound):
        emu._pyboy.register_file.A += 1     # the synthetic CPU's accumulator advances each frame
        return original_tick(frames, render, sound)

    emu._pyboy.tick = tick
    out = emu.investigate_watch({"cpu": True}, 3, actions=[{"frames": 3}])
    assert out["advanced_frames"] == 3 and out["requested_frames"] == 3
    a_events = [e for e in out["events"] if e["register"] == "A"]
    assert [(e["previous"], e["current"]) for e in a_events] == [(0, 1), (1, 2), (2, 3)]
    assert out["monitors"]["cpu"] is True


def test_watch_sprite_slot_reports_moves_during_the_frames(emu):
    mem = emu._pyboy.memory.base
    mem[0xFE00 + 8:0xFE00 + 12] = [56, 68, 5, 0]         # slot 2: screen (60, 40), tile 5
    original_tick = emu._pyboy.tick

    def tick(frames, render, sound):
        result = original_tick(frames, render, sound)
        emu._pyboy.memory.base[0xFE00 + 9] += 1          # the sprite moves right one pixel per frame
        return result

    emu._pyboy.tick = tick
    out = emu.investigate_watch({"sprites": [2]}, 2, actions=[{"frames": 2}])
    sprite_events = [e for e in out["events"] if e["kind"] == "sprite"]
    assert [(e["previous"]["x"], e["current"]["x"]) for e in sprite_events] == [(60, 61), (61, 62)]
    assert all(e["slot"] == 2 for e in sprite_events)


def test_watch_tile_rectangle_reports_changed_tiles(emu):
    class Tiles:
        def __init__(self):
            self.cells = {}

        def __getitem__(self, xy):
            return self.cells.get(xy, 0)

    emu._pyboy.tilemap_background = Tiles()

    class Writer(BankedSim):
        pass

    def change_tile_after_one_frame():
        emu._pyboy.tilemap_background.cells[(1, 1)] = 9

    original_tick = emu._pyboy.tick

    def tick(frames, render, sound):
        result = original_tick(frames, render, sound)
        change_tile_after_one_frame()
        return result

    emu._pyboy.tick = tick
    out = emu.investigate_watch({"tiles": {"layer": "background", "x": 0, "y": 0, "width": 2, "height": 2}}, 2,
                                actions=[{"frames": 2}])
    tile_events = [e for e in out["events"] if e["kind"] == "tile"]
    assert tile_events and tile_events[0]["tilemap"] == {"col": 1, "row": 1}
    assert tile_events[0]["previous"] == 0 and tile_events[0]["current"] == 9


def test_watch_screen_region_counts_changed_pixels(emu):
    out = emu.investigate_watch({"screen_region": {"x": 60, "y": 36, "width": 40, "height": 16}}, 1,
                                actions=[{"press": ["right"], "frames": 1}])
    assert out["change_count"] == 1
    event = out["events"][0]
    assert event["kind"] == "screen_region" and event["changed_pixels"] == 16  # 80->81 moves 16 pixels in region


def test_watch_limits_and_monitor_validation(emu):
    with pytest.raises(ValueError, match="at least one"):
        emu.investigate_watch({}, 1)
    with pytest.raises(ValueError, match="unknown monitor"):
        emu.investigate_watch({"sound": True}, 1)
    with pytest.raises(ValueError):
        emu.investigate_watch({"sprites": list(range(9))}, 1)
    with pytest.raises(ValueError):
        emu.investigate_watch({"tiles": {"layer": "background", "x": 0, "y": 0, "width": 9, "height": 9}}, 1)
    with pytest.raises(ValueError):
        emu.investigate_watch({"tiles": {"layer": "sprites", "x": 0, "y": 0, "width": 1, "height": 1}}, 1)


def test_memory_only_watch_keeps_its_old_shape(emu):
    out = emu.watch_memory(["0xC100"], frames=2, actions=[{"press": ["left"], "frames": 2}])
    assert out["events"] and out["events"][0]["address"] == "0xC100"
    assert set(out) >= {"start_frame", "end_frame", "advanced_frames", "samples", "change_count", "events"}


# ---------- suggested validation from memory-variable candidates ----------

def run_three_each(emu):
    for name, actions in [("left", LEFT), ("right", RIGHT), ("idle", IDLE)]:
        for _ in range(2):
            emu.load_checkpoint("base")
            emu.run_experiment(name, actions)


def test_memory_variable_suggests_a_runnable_validation(emu):
    run_three_each(emu)
    out = emu.find_memory_candidates(["left", "right", "idle"])
    suggestions = {s["address"]: s for s in out["suggested_validation"]}
    piece = suggestions["0xC100"]
    assert piece["expected"] == {"left": "decrease", "right": "increase", "idle": "none"}
    assert piece["experiments"]["left"] == LEFT and piece["repeats"] == 2
    assert "not a verified discovery" in piece["next"]
    # the suggestion is directly runnable
    check = emu.investigate_validate(piece["address"], "base", piece["experiments"], piece["expected"], repeats=2)
    assert check["verdict"] == "supported_by_experiments"


def test_opposite_directions_under_one_name_give_change_not_a_direction(emu):
    # Two runs under one name move the piece in opposite directions: every run changed it, so the honest rule is "change"
    emu.load_checkpoint("base")
    emu.run_experiment("move", LEFT)
    emu.load_checkpoint("base")
    emu.run_experiment("move", RIGHT)
    emu.load_checkpoint("base")
    emu.run_experiment("idle", IDLE)
    out = emu.find_memory_candidates(["move", "idle"])
    piece = [s for s in out["suggested_validation"] if s["address"] == "0xC100"]
    assert piece and piece[0]["expected"] == {"move": "change", "idle": "none"}


def test_branch_result_is_stored_and_retrievable(emu):
    out = emu.branch_experiment("base", [{"name": "left", "actions": LEFT}, {"name": "idle", "actions": IDLE}])
    rid = out["result_id"]
    stored = emu.get_result(rid)
    assert stored["kind"] == "branch" and stored["result_id"] == rid
    assert [b["name"] for b in stored["branches"]] == ["left", "idle"]
    with pytest.raises(ValueError, match="unknown result"):
        emu.get_result("result-9999")


def test_replay_result_is_stored_with_divergence_report(emu):
    emu.record_replay("left_check", "record", "base", [{"press": ["left"], "frames": 3}])
    ok = emu.record_replay("left_check", "replay")
    assert ok["completed"] is True and ok["matched"] is True
    assert ok["advanced_frames"] == 3 and ok["frames_match_recording"] is True
    assert ok["possible_sources"] and "repeatability" in ok["possible_sources"][0]
    assert emu.get_result(ok["result_id"])["kind"] == "replay"


def test_replay_mismatch_names_its_likely_source(emu):
    emu.record_replay("moved", "record", "base", [{"press": ["left"], "frames": 2}])
    emu.step(4)
    emu.save_checkpoint("base")       # the checkpoint the replay starts from no longer matches the recording
    out = emu.record_replay("moved", "replay")
    assert out["matched"] is False
    assert any("starting state differs" in s for s in out["possible_sources"])


def test_results_are_dropped_when_a_different_rom_loads(emu, tmp_path):
    rid = emu.branch_experiment("base", [{"name": "idle", "actions": IDLE}])["result_id"]
    (tmp_path / ".dmg-cartographer" / "roms" / "other.gb").write_bytes(make_rom(b"OTHER"))
    emu.load_game(str(tmp_path), "other.gb")
    with pytest.raises(ValueError, match="unknown result"):
        emu.get_result(rid)


def test_divergence_sources_for_memory_only_difference():
    import dmg_cartographer.core.knowledge as knowledge
    sources = knowledge.KnowledgeMixin._divergence_sources(
        [{"field": "end.regions.WRAM", "recorded": "a", "observed": "b"}])
    assert any("WRAM/HRAM differ while frames and buttons match" in s for s in sources)
    assert "no divergence" in knowledge.KnowledgeMixin._divergence_sources([])[0]
