"""Branch experiments and memory search against the synthetic emulator (no ROM needed).

The synthetic emulator serialises its full memory, frame count and held buttons in save_state, so restore
tests check real round-trips. These are mock-emulator tests: they prove controller logic, not PyBoy behaviour.
"""
import json
import threading

import pytest

from dmg_cartographer import search
from dmg_cartographer.core.controller import PyBoyController
from test_snapshots import PIECE_X, FRAME_COUNTER, SimPyBoy

FIRE_FLAG = 0xC300  # set to 7 by any frame that holds "a" (synthetic, game-agnostic test target)


class StatefulSim(SimPyBoy):
    def save_state(self, buffer):
        buffer.write(json.dumps({"memory": list(self.memory), "ticks": self.ticks,
                                 "held": sorted(self.held)}).encode())

    def load_state(self, buffer):
        data = json.loads(buffer.read())
        self.memory = data["memory"]
        self.ticks = data["ticks"]
        self.held = set(data["held"])
        self.draw()

    def tick(self, frames, render, sound):
        if "a" in self.held:
            self.memory[FIRE_FLAG] = 7
        return super().tick(frames, render, sound)


@pytest.fixture
def project(tmp_path):
    roms = tmp_path / ".dmg-cartographer" / "roms"
    roms.mkdir(parents=True)
    (roms / "puzzle.gb").write_bytes(b"puzzle rom")
    (roms / "other.gb").write_bytes(b"other rom")
    return str(tmp_path)


@pytest.fixture
def controller(project):
    c = PyBoyController(StatefulSim)
    c.load_game(project, "puzzle.gb")
    c.step(2)
    c.save_checkpoint("base")          # frame 2, piece at 80, nothing held
    c.step(4, press=["right"])         # frame 6, piece at 84, "right" held
    return c


def live(c):
    return {"frames": c.status()["timeline_frame"], "held": c.status()["held_buttons"],
            "piece": c._pyboy.memory[PIECE_X], "ticks": c._pyboy.ticks}


# ---------- branch_experiment ----------

LEFT = {"name": "left", "actions": [{"press": ["left"], "frames": 3}]}
RIGHT = {"name": "right", "actions": [{"press": ["right"], "frames": 3}]}
IDLE = {"name": "idle", "actions": [{"frames": 3}]}
FIRE = {"name": "fire", "actions": [{"press": ["a"], "frames": 1}]}


def test_branches_start_from_checkpoint_with_exact_frames(controller):
    result = controller.branch_experiment("base", [LEFT, RIGHT, IDLE])
    rows = {b["name"]: b for b in result["branches"]}
    for name in ("left", "right", "idle"):
        assert rows[name]["start_frame"] == 2
        assert rows[name]["end_frame"] == 5
        assert rows[name]["advanced_frames"] == 3
    assert rows["left"]["changes"]["WRAM"]  # memory compared
    left_piece = next(c for c in rows["left"]["changes"]["WRAM"] if c["address"] == "0xC100")
    right_piece = next(c for c in rows["right"]["changes"]["WRAM"] if c["address"] == "0xC100")
    assert (left_piece["diff"], right_piece["diff"]) == (-3, 3)
    assert all(c["address"] != "0xC100" for c in rows["idle"]["changes"]["WRAM"])
    assert result["restored"] is True


def test_live_state_is_restored_after_branches(controller):
    before = live(controller)
    assert before == {"frames": 6, "held": ["right"], "piece": 84, "ticks": 6}
    controller.branch_experiment("base", [LEFT, RIGHT, IDLE])
    assert live(controller) == before


def test_unique_changes_identify_branch_only_bytes(controller):
    result = controller.branch_experiment("base", [LEFT, FIRE, IDLE])
    fire = next(b for b in result["branches"] if b["name"] == "fire")
    assert {c["address"] for c in fire["unique_changes"]} == {"0xC300"}
    idle = next(b for b in result["branches"] if b["name"] == "idle")
    assert all(c["address"] != "0xC300" for c in idle["unique_changes"])
    assert result["shared_change_count"] > 0  # the frame counter changes in every branch


def test_failing_branch_still_restores_state(controller, monkeypatch):
    before = live(controller)
    real = controller.run_sequence
    calls = {"n": 0}

    def flaky(actions):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated failure in branch 2")
        return real(actions)

    monkeypatch.setattr(controller, "run_sequence", flaky)
    with pytest.raises(RuntimeError, match="simulated failure"):
        controller.branch_experiment("base", [LEFT, RIGHT, IDLE])
    assert live(controller) == before


def test_invalid_branch_input_is_rejected_before_running(controller):
    before = live(controller)
    ticks = controller._pyboy.ticks
    bad_inputs = [
        {"branches": [{"name": "x", "actions": [{"frames": 601}]}]},
        {"branches": [{"name": "x", "actions": [{"press": ["fire"]}]}]},
        {"branches": [LEFT, LEFT]},
        {"branches": [{"name": "snap-0001", "actions": [{"frames": 1}]}]},
        {"branches": [{"name": "x", "actions": [{"frames": 1}]}] * 9},
        {"branches": [IDLE], "compare": ["sprites"]},
        {"branches": [IDLE], "compare": ["hardware"]},
    ]
    for kwargs in bad_inputs:
        with pytest.raises(ValueError):
            controller.branch_experiment("base", **kwargs)
    assert controller._pyboy.ticks == ticks
    assert live(controller) == before


def test_missing_checkpoint_is_an_error_and_changes_nothing(controller):
    before = live(controller)
    with pytest.raises(ValueError, match="does not exist"):
        controller.branch_experiment("no_such_checkpoint", [IDLE])
    assert live(controller) == before


# ---------- search_memory: pure helpers ----------

def test_decode_endianness_and_signedness():
    data = [0x34, 0x12, 0xFF, 0xFF]
    assert search.decode(data, 0, "uint16", "little") == 0x1234
    assert search.decode(data, 0, "uint16", "big") == 0x3412
    assert search.decode(data, 2, "int16", "little") == -1
    assert search.decode(data, 2, "int8", "little") == -1
    assert search.decode(data, 2, "uint8", "big") == 255  # endianness is ignored for single bytes


def test_pattern_parsing_and_matching():
    assert search.parse_pattern("AB ?? ef") == [0xAB, None, 0xEF]
    data = [0, 0xAB, 0xCD, 0xEF]
    assert search.pattern_matches(data, 1, [0xAB, None, 0xEF])
    assert not search.pattern_matches(data, 2, [0xAB, None])
    for bad in ["", "GG", "123", "A" * 2 * 40]:
        with pytest.raises(ValueError):
            search.parse_pattern(bad)


def test_request_validation_messages():
    base = dict(dtype="uint8", endianness="little", exact=None, low=None, high=None, pattern=None,
                has_session=False)
    with pytest.raises(ValueError, match="earlier values"):
        search.validate_request(condition="decreased", **base)
    with pytest.raises(ValueError, match="integer"):
        search.validate_request(condition="exact", **{**base, "dtype": "int8", "exact": 200})
    with pytest.raises(ValueError, match="low must be"):
        search.validate_request(condition="range", **{**base, "low": 9, "high": 3})
    with pytest.raises(ValueError, match="uint8"):
        search.validate_request(condition="pattern", **{**base, "dtype": "uint16", "pattern": "00"})
    with pytest.raises(ValueError, match="only starts"):
        search.validate_request(condition="unknown", **{**base, "has_session": True})


# ---------- search_memory: controller ----------

def test_unknown_start_then_decreased_and_increased_narrows(controller):
    start = controller.search_memory(region="WRAM")
    assert start["candidates_before"] == 8192 and start["candidates_remaining"] == 8192
    assert start["eliminated"] == 0 and start["session"] == "search-0001"
    assert start["bank"]["banked"] == "cgb_only"

    controller.step(1, press=["left"], release=["right"])  # piece 84 -> 83
    down = controller.search_memory(session=start["session"], condition="decreased")
    assert [m["address"] for m in down["matches"]] == ["0xC100"]
    assert down["matches"][0]["previous"] == {"dec": 84, "hex": "0x54"}
    assert down["matches"][0]["current"] == {"dec": 83, "hex": "0x53"}
    assert down["eliminated"] == down["candidates_before"] - 1

    controller.step(1, press=["right"], release=["left"])  # piece 83 -> 84
    up = controller.search_memory(session=start["session"], condition="increased")
    assert [m["address"] for m in up["matches"]] == ["0xC100"]


def test_exact_range_and_pattern_filters_start_sessions(controller):
    controller._pyboy.memory[0xC400] = 0xF0
    exact = controller.search_memory(condition="exact", exact=240)
    assert "0xC400" in {m["address"] for m in exact["matches"]}
    signed = controller.search_memory(dtype="int8", condition="exact", exact=-16)
    assert "0xC400" in {m["address"] for m in signed["matches"]}
    ranged = controller.search_memory(dtype="uint8", condition="range", low=84, high=84)
    assert "0xC100" in {m["address"] for m in ranged["matches"]}
    controller._pyboy.memory[0xC500:0xC503] = [0xAB, 0xCD, 0xEF]
    pat = controller.search_memory(condition="pattern", pattern="AB ?? EF")
    assert [m["address"] for m in pat["matches"]] == ["0xC500"]


def test_endianness_changes_16_bit_matches(controller):
    # Piece is at 84 (0x54) and the next byte is 0x01: little-endian 0x0154, big-endian 0x5401.
    little = controller.search_memory(dtype="uint16", endianness="little", condition="exact", exact=0x0154)
    big = controller.search_memory(dtype="uint16", endianness="big", condition="exact", exact=0x0154)
    assert "0xC100" in {m["address"] for m in little["matches"]}
    assert "0xC100" not in {m["address"] for m in big["matches"]}


def test_hram_region_and_bounded_output(controller):
    hram = controller.search_memory(region="HRAM")
    assert hram["candidates_before"] == 127 and hram["bank"] == {"banked": "no", "note": "HRAM is not banked"}
    limited = controller.search_memory(condition="range", low=0, high=255, max_results=5)
    assert limited["matches_truncated"] is True and len(limited["matches"]) == 5


def test_search_never_advances_emulator(controller):
    ticks = controller._pyboy.ticks
    s = controller.search_memory()
    controller.search_memory(session=s["session"], condition="changed")
    assert controller._pyboy.ticks == ticks


def test_session_errors(controller, project):
    s = controller.search_memory()
    with pytest.raises(ValueError, match="unknown search session"):
        controller.search_memory(session="search-9999", condition="changed")
    with pytest.raises(ValueError, match="must match"):
        controller.search_memory(session=s["session"], region="HRAM", condition="changed")
    with pytest.raises(ValueError, match="only starts"):
        controller.search_memory(session=s["session"], condition="unknown")
    with pytest.raises(ValueError, match="earlier values"):
        controller.search_memory(condition="increased")


def test_rom_change_discards_search_sessions(controller, project):
    s = controller.search_memory()
    controller.load_game(project, "other.gb")
    with pytest.raises(ValueError, match="unknown search session"):
        controller.search_memory(session=s["session"], condition="changed")
    assert controller.list_search_sessions() == []


def test_search_sessions_are_retained_within_limit(project):
    c = PyBoyController(StatefulSim)
    c.load_game(project, "puzzle.gb")
    from dmg_cartographer.core.shared import MAX_SEARCH_SESSIONS
    for _ in range(MAX_SEARCH_SESSIONS + 2):
        c.search_memory(region="HRAM")
    sessions = c.list_search_sessions()
    assert len(sessions) == MAX_SEARCH_SESSIONS
    assert sessions[0]["session"] == "search-0003"


# ---------- concurrency ----------

def test_concurrent_steps_and_searches_keep_frame_count_exact(controller):
    errors = []
    start_ticks = controller._pyboy.ticks

    def stepper():
        try:
            for _ in range(20):
                controller.step(1)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    def searcher():
        try:
            for _ in range(20):
                controller.search_memory(region="HRAM")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=stepper), threading.Thread(target=searcher)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert controller._pyboy.ticks - start_ticks == 20
