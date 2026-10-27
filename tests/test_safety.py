"""Safety across both levels: read-only calls advance nothing, failed experiments restore, concurrent calls are safe.

Synthetic emulator (mocked, not real PyBoy).
"""
import threading

import pytest

from dmg_cartographer.core.controller import PyBoyController
from test_inspection import BankedSim
from test_priority2 import make_rom
from test_priority3 import HookSim

IDLE = [{"frames": 2}]


@pytest.fixture
def emu(tmp_path):
    roms = tmp_path / ".pyboy-mcp" / "roms"
    roms.mkdir(parents=True)
    (roms / "puzzle.gb").write_bytes(make_rom())
    c = PyBoyController(HookSim)
    c.load_game(str(tmp_path), "puzzle.gb")
    c._pyboy.memory.base[0xFE00:0xFEA0] = [0] * 160
    c._pyboy.memory.base[0xFF40] = 0x91
    c.step(2)
    c.save_checkpoint("base")
    c.step(3, press=["right"])   # right is held
    return c


READ_ONLY_CALLS = [
    lambda c: c.status(),
    lambda c: c.screen_png(),
    lambda c: c.game_area(),
    lambda c: c.tilemap("background", 0, 0, 4, 4),
    lambda c: c.read_memory(0xC100, 4),
    lambda c: c.inspect_cpu(),
    lambda c: c.inspect_rom(0, "0x0100", 8, cross_check=False),
    lambda c: c.disassemble_rom(0, "0x0100", 4),
    lambda c: c.decode_memory([{"name": "x", "type": "uint8"}], address="0xC100", length=1),
    lambda c: c.get_palette(),
    lambda c: c.inspect_cartridge(),
    lambda c: c.inspect_sprites(indices=[0]),
    lambda c: c.inspect_tiles(width=2, height=2),
    lambda c: (c.capture_snapshot("ro", include_tilemap=True), c.compare_memory("ro")),
    lambda c: c.list_snapshots(),
    lambda c: c.list_search_sessions(),
    lambda c: c.list_experiments(),
    lambda c: c.list_checkpoints(),
    lambda c: c.get_discoveries(),
    lambda c: c.annotate_screen(["pixels", "tiles"]),
]


@pytest.mark.parametrize("call", READ_ONLY_CALLS, ids=range(len(READ_ONLY_CALLS)))
def test_read_only_calls_advance_zero_frames_and_keep_buttons(emu, call):
    before = (emu._pyboy.ticks, emu.status()["timeline_frame"], emu.status()["held_buttons"])
    call(emu)
    assert (emu._pyboy.ticks, emu.status()["timeline_frame"], emu.status()["held_buttons"]) == before


def test_failed_experiment_restores_frames_and_buttons(emu, monkeypatch):
    before = (emu._pyboy.ticks, emu.status()["timeline_frame"], emu.status()["held_buttons"])
    real = emu.step
    calls = {"n": 0}

    def flaky(frames, press=None, release=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated failure mid-experiment")
        return real(frames, press, release)

    monkeypatch.setattr(emu, "step", flaky)
    with pytest.raises(RuntimeError, match="simulated"):
        emu.run_experiment("bad", [{"press": ["left"], "frames": 1}, {"frames": 1}])
    assert (emu._pyboy.ticks, emu.status()["timeline_frame"], emu.status()["held_buttons"]) == before


def test_concurrent_steps_and_reads_keep_the_frame_count_exact(emu):
    start_ticks = emu._pyboy.ticks
    errors = []

    def stepper():
        try:
            for _ in range(25):
                emu.step(1)
        except Exception as exc:  # reported below
            errors.append(exc)

    def reader():
        try:
            for _ in range(25):
                emu.status()
                emu.inspect_cpu()
                emu.capture_snapshot(include_memory=True, include_screen=False)
                emu.list_snapshots()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=stepper), threading.Thread(target=reader),
               threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert emu._pyboy.ticks - start_ticks == 25


def test_concurrent_branches_do_not_corrupt_live_state(emu):
    live_before = (emu.status()["timeline_frame"], emu.status()["held_buttons"], emu._pyboy.memory.base[0xC100])
    errors = []

    def branch():
        try:
            emu.branch_experiment("base", [{"name": "a", "actions": [{"press": ["left"], "frames": 2}]},
                                           {"name": "b", "actions": IDLE}])
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=branch) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert (emu.status()["timeline_frame"], emu.status()["held_buttons"], emu._pyboy.memory.base[0xC100]) == live_before
