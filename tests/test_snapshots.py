"""Snapshots, comparisons and experiments against a tiny synthetic game (no ROM needed)."""
from types import SimpleNamespace

import pytest
from PIL import Image

from dmg_cartographer.core.controller import PyBoyController
from test_controller import FakePyBoy

PIECE_X = 0xC100     # synthetic "piece x" byte: moves while left/right is held
FRAME_COUNTER = 0xC200  # synthetic byte that changes every frame regardless of input


class SimPyBoy(FakePyBoy):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.held = set()
        self.memory[PIECE_X] = 80
        self.memory[FRAME_COUNTER] = 0
        self.screen = SimpleNamespace(image=None)
        self.draw()

    def button_press(self, button):
        super().button_press(button)
        self.held.add(button)

    def button_release(self, button):
        super().button_release(button)
        self.held.discard(button)

    def tick(self, frames, render, sound):
        for _ in range(frames):
            if "left" in self.held:
                self.memory[PIECE_X] -= 1
            if "right" in self.held:
                self.memory[PIECE_X] += 1
            self.memory[FRAME_COUNTER] = (self.memory[FRAME_COUNTER] + 1) % 256
        self.draw()
        return super().tick(frames, render, sound)

    def draw(self):
        image = Image.new("RGBA", (160, 144), (0, 0, 0, 255))
        x = self.memory[PIECE_X]
        image.paste((255, 255, 255, 255), (x, 40, x + 8, 48))
        self.screen.image = image


@pytest.fixture
def project(tmp_path):
    roms = tmp_path / ".pyboy-mcp" / "roms"
    roms.mkdir(parents=True)
    (roms / "puzzle.gb").write_bytes(b"puzzle rom")
    (roms / "other.gb").write_bytes(b"other rom")
    return str(tmp_path)


@pytest.fixture
def controller(project):
    c = PyBoyController(SimPyBoy)
    c.load_game(project, "puzzle.gb")
    return c


LEFT_TAP = [{"press": ["left"], "frames": 1}, {"release": ["left"], "frames": 1}]


def test_compare_screen_rejects_region_outside_screen(controller):
    snap = controller.capture_snapshot("start")
    with pytest.raises(ValueError):
        controller.compare_screen(snap["id"], region={"x": 150, "y": 0, "width": 20, "height": 5})


def test_snapshots_and_comparisons_never_tick(controller):
    controller.step(1)
    ticks = controller._pyboy.ticks
    snap = controller.capture_snapshot("start", include_tilemap=True)
    controller.list_snapshots()
    controller.compare_memory(snap["id"])
    controller.compare_memory(snap["id"], region="HRAM", filter="all")
    controller.compare_screen(snap["id"], include_image=True)
    controller.delete_snapshot(snap["id"])
    assert controller._pyboy.ticks == ticks
    assert controller.status()["timeline_frame"] == 1


def test_identical_state_has_zero_differences(controller):
    controller.step(1)
    snap = controller.capture_snapshot()
    memory = controller.compare_memory(snap["id"])
    assert memory["changed_count"] == 0 and memory["bytes_examined"] == 0x2000
    screen, _ = controller.compare_screen(snap["id"])
    assert screen["changed_pixels"] == 0 and "warning" not in screen


def test_snapshot_vs_snapshot_memory_and_screen(controller):
    controller.step(1)
    a = controller.capture_snapshot("a")
    controller.step(3, press=["right"])
    b = controller.capture_snapshot("b")
    memory = controller.compare_memory("a", current=False, target="b")
    changed = {c["address"]: c for c in memory["changes"]}
    assert changed["0xC100"]["diff"] == 3
    assert changed["0xC200"]["diff"] == 3
    assert memory["changed_count"] == 2
    screen, _ = controller.compare_screen(a["id"], current=False, target=b["id"])
    assert screen["bounding_box"] == {"x": 80, "y": 40, "width": 11, "height": 8}
    with pytest.raises(ValueError):
        controller.compare_memory("a", current=False)
    with pytest.raises(ValueError):
        controller.compare_memory("a", target="b")


def test_snapshot_lifecycle_ids_names_and_eviction(project):
    c = PyBoyController(SimPyBoy, max_snapshots=2)
    c.load_game(project, "puzzle.gb")
    first = c.capture_snapshot("same")
    second = c.capture_snapshot("same")
    assert (first["id"], second["id"]) == ("snap-0001", "snap-0002")
    with pytest.raises(ValueError, match="ambiguous"):
        c.compare_memory("same")
    third = c.capture_snapshot()
    assert third["evicted"] == ["snap-0001"]
    assert [s["id"] for s in c.list_snapshots()] == ["snap-0002", "snap-0003"]
    for bad in ["snap-0001", "nope", "", None]:
        with pytest.raises(ValueError):
            c.compare_memory(bad)
    for bad_name in ["snap-9", "exp-1", "", "x" * 65]:
        with pytest.raises(ValueError):
            c.capture_snapshot(bad_name)
    assert c.delete_snapshot("same")["deleted"] == "snap-0002"
    with pytest.raises(ValueError):
        c.capture_snapshot(include_memory=False, include_screen=False)


def test_stale_screen_is_flagged_until_a_frame_renders(controller):
    assert "warning" in controller.capture_snapshot()
    controller.step(1)
    assert "warning" not in controller.capture_snapshot()
    controller.save_checkpoint("cp")
    controller.load_checkpoint("cp")
    assert "warning" in controller.capture_snapshot()


def test_rom_change_discards_incompatible_snapshots(controller, project):
    snap = controller.capture_snapshot("old")
    controller.run_experiment("idle", [{"frames": 1}])
    same = controller.load_game(project, "puzzle.gb")  # emulator reset, same ROM: still comparable
    assert same["discarded_snapshots_and_experiments"] == 0
    controller.compare_memory(snap["id"])
    other = controller.load_game(project, "other.gb")
    assert other["discarded_snapshots_and_experiments"] == 4  # the "old" snapshot, the idle before/after snapshots, and the idle experiment
    with pytest.raises(ValueError, match="unknown snapshot"):
        controller.compare_memory(snap["id"])
    assert controller.list_snapshots() == [] and controller.list_experiments() == []


def test_unreadable_region_is_reported_not_fatal(controller):
    class PartlyBrokenMemory(list):
        def __getitem__(self, key):
            if isinstance(key, slice) and key.start == 0xFF80:
                raise IndexError("HRAM unavailable")
            return super().__getitem__(key)

    controller._pyboy.memory = PartlyBrokenMemory(controller._pyboy.memory)
    snap = controller.capture_snapshot()
    assert "HRAM" in snap["memory_errors"] and "WRAM" in snap["memory_regions"]
    with pytest.raises(ValueError, match="no HRAM data"):
        controller.compare_memory(snap["id"], region="HRAM")
    assert controller.compare_memory(snap["id"])["region"] == "WRAM"


def test_experiment_advances_exact_frames_and_reports_changes(controller):
    controller.step(1)
    result = controller.run_experiment("move_capsule_left", LEFT_TAP)
    assert result["id"] == "exp-0001"
    assert (result["start_frame"], result["end_frame"], result["advanced_frames"]) == (1, 3, 2)
    assert controller._pyboy.ticks == 3
    assert result["held_buttons"] == []
    changes = {c["address"]: c["diff"] for c in result["memory"]["WRAM"]["changes"]}
    assert changes == {"0xC100": -1, "0xC200": 2}
    assert result["screen"]["changed_pixels"] > 0
    assert [s["name"] for s in controller.list_snapshots()][-2:] == ["move_capsule_left:before",
                                                                    "move_capsule_left:after"]
    ticks = controller._pyboy.ticks
    with pytest.raises(ValueError):
        controller.run_experiment("bad", [{"frames": 601}])
    assert controller._pyboy.ticks == ticks


def test_candidates_found_across_repeated_experiments(controller):
    controller.step(1)
    controller.save_checkpoint("base")
    for name, actions in [("left", LEFT_TAP), ("left", LEFT_TAP),
                          ("right", [{"press": ["right"], "frames": 1}, {"release": ["right"], "frames": 1}]),
                          ("right", [{"press": ["right"], "frames": 1}, {"release": ["right"], "frames": 1}]),
                          ("idle", [{"frames": 2}]), ("idle", [{"frames": 2}])]:
        controller.load_checkpoint("base")
        controller.run_experiment(name, actions)

    # Without expectations, the piece position also moves on "right", so it is not unique to "left".
    contrast = controller.find_memory_candidates(["left", "right", "idle"])
    best_left = next(c for c in contrast["candidates"] if c["action"] == "left")
    assert best_left["address"] == "0xC100" and best_left["confidence"] == "low"
    assert {e["name"] for e in best_left["counterexamples"]} == {"right"}
    counter = next(c for c in contrast["candidates"] if c["address"] == "0xC200" and c["action"] == "left")
    assert counter["confidence"] == "low" and counter["counterexamples"]

    expected = controller.find_memory_candidates(
        ["left", "right", "idle"], {"left": "decrease", "right": "increase", "idle": "none"})
    top = expected["candidates"][0]
    assert top["address"] == "0xC100" and top["confidence"] == "high" and len(top["evidence"]) == 6
    frame_counter = next(c for c in expected["candidates"] if c["address"] == "0xC200")
    assert frame_counter["confidence"] == "low"
    with pytest.raises(ValueError):
        controller.find_memory_candidates(["exp-9999"])
