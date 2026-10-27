"""investigate_validate and investigate_correlations on the synthetic emulator (mocked, not real PyBoy).

In the synthetic game the piece at 0xC100 moves with held left/right and is the only thing drawn; the frame
counter at 0xC200 changes every frame. So visual change and memory change can be checked against known answers.
"""
import pytest

from dmg_cartographer import analysis
from dmg_cartographer.core.controller import PyBoyController
from test_inspection import BankedSim
from test_priority2 import make_rom

PIECE = 0xC100
COUNTER = 0xC200
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
    c.save_checkpoint("base")  # piece at 80, nothing held
    return c


def run_three_times_each(c):
    for name, actions in [("left", LEFT), ("right", RIGHT), ("idle", IDLE)]:
        for _ in range(2):
            c.load_checkpoint("base")
            c.run_experiment(name, actions)


# ---------- pure correlation service (synthetic records) ----------

def test_correlation_service_on_synthetic_records():
    records = [
        {"id": "e1", "changes": {0x10: (5, 4), 0x20: (1, 2)}, "screen_changed": True},
        {"id": "e2", "changes": {0x10: (4, 5)}, "screen_changed": True},
        {"id": "e3", "changes": {0x20: (2, 3)}, "screen_changed": False},
        {"id": "e4", "changes": {}, "screen_changed": False},
    ]
    out = analysis.correlate_with_screen(records)
    top = out["candidates"][0]
    assert top["address"] == "0x0010" and top["agreement"] == 1.0 and top["confidence"] == "low"
    assert top["counts"] == {"memory_and_screen": 2, "memory_only": 0, "screen_only": 0, "neither": 2}
    assert top["evidence"] == ["e1", "e2"] and top["direction_when_both"] == {"+": 1, "-": 1}
    second = out["candidates"][1]
    # e3: memory changed with the screen static; e2: the screen changed while this byte did not
    assert second["address"] == "0x0020" and second["counterexample_count"] == 2
    assert {"experiment": "e3", "kind": "memory changed, screen static"} in second["counterexamples"]
    assert {"experiment": "e2", "kind": "screen changed, memory unchanged"} in second["counterexamples"]
    assert "not proof" not in out["note"] and "Correlation" in out["note"]


def test_correlation_needs_two_samples():
    with pytest.raises(ValueError):
        analysis.correlate_with_screen([{"id": "e1", "changes": {}, "screen_changed": False}])


# ---------- investigate_correlations ----------

def test_correlations_rank_the_piece_above_the_counter(emu):
    run_three_times_each(emu)
    out = emu.investigate_correlations(["left", "right", "idle"])
    assert out["samples"] == 6 and out["frame_counts"] and set(out["frame_counts"].values()) == {2}
    by_address = {c["address"]: c for c in out["candidates"]}
    piece = by_address["0xC100"]
    assert piece["agreement"] == 1.0 and piece["pattern"] == "changes exactly when the screen changes"
    assert piece["counts"] == {"memory_and_screen": 4, "memory_only": 0, "screen_only": 0, "neither": 2}
    assert piece["direction_when_both"] == {"+": 2, "-": 2}           # left moves it down, right moves it up
    assert len(piece["evidence"]) == 4 and piece["confidence"] == "low"  # six samples are too few for more
    # the frame counter changes in every experiment: in idle that is change without a visible change
    counter = by_address["0xC200"]
    assert counter["counts"] == {"memory_and_screen": 4, "memory_only": 2, "screen_only": 0, "neither": 0}
    assert counter["pattern"] == "mixed" and counter["agreement"] == 0.667
    assert out["candidates"][0]["address"] == "0xC100"
    assert out["input_events"]["%s" % out["experiments"][0]] == LEFT


def test_experiments_without_screen_are_excluded_not_guessed(emu):
    emu.load_checkpoint("base")
    no_screen = emu.run_experiment("left", LEFT, compare_screen=False)["id"]
    emu.load_checkpoint("base")
    with_screen = emu.run_experiment("right", RIGHT)["id"]
    with pytest.raises(ValueError, match="at least 2"):
        emu.investigate_correlations([no_screen, with_screen])
    emu.load_checkpoint("base")
    emu.run_experiment("idle", IDLE)
    out = emu.investigate_correlations([no_screen, with_screen, "idle"])
    assert out["excluded"][0]["id"] == no_screen and no_screen not in out["experiments"]


# ---------- investigate_validate ----------

EXPECTED = {"left": "decrease", "right": "increase", "idle": "none"}
SCREEN = {"left": "changes", "right": "changes", "idle": "static"}
EXPERIMENTS = {"left": LEFT, "right": RIGHT, "idle": IDLE}


def live(c):
    return {"frames": c.status()["timeline_frame"], "held": c.status()["held_buttons"],
            "piece": c._pyboy.memory.base[PIECE], "ticks": c._pyboy.ticks}


def test_validate_supports_a_correct_hypothesis_and_restores_state(emu):
    emu.step(1, press=["up"])  # make the live state distinct from the checkpoint
    before = live(emu)
    out = emu.investigate_validate("0xC100", "base", EXPERIMENTS, EXPECTED, repeats=2, screen_expected=SCREEN)
    assert out["verdict"] == "supported_by_experiments"
    assert out["per_action"]["left"]["runs"] == 2 and out["contradictions"] == []
    assert out["frames_advanced_total"] == 12              # 3 experiments x 2 repeats x 2 frames
    assert live(emu) == before                              # live state restored, buttons included
    assert "not verified" in " ".join(out["limitations"]) or "verified" in " ".join(out["limitations"])


def test_validate_reports_contradictions_for_a_wrong_address(emu):
    out = emu.investigate_validate("0xC200", "base", EXPERIMENTS, {"left": "decrease"}, repeats=2)
    assert out["verdict"] == "contradicted"
    assert len(out["contradictions"]) == 2 and out["contradictions"][0]["diff"] == 2


def test_validate_is_inconclusive_with_one_run_each(emu):
    out = emu.investigate_validate("0xC100", "base", EXPERIMENTS, EXPECTED, repeats=1)
    assert out["verdict"] == "inconclusive"


def test_exact_zero_expectation_counts_as_a_match(emu):
    out = emu.investigate_validate("0xC100", "base", {"idle": IDLE}, {"idle": 0}, repeats=2)
    assert out["verdict"] == "supported_by_experiments"


def test_screen_expectations_can_contradict(emu):
    out = emu.investigate_validate("0xC100", "base", {"idle": IDLE}, {"idle": "none"}, repeats=2,
                                   screen_expected={"idle": "changes"})
    assert out["verdict"] == "contradicted"
    assert out["contradictions"][0]["check"] == "screen"


def test_perturbation_needs_explicit_authorisation(emu):
    with pytest.raises(ValueError, match="allow_writes"):
        emu.investigate_validate("0xC100", "base", EXPERIMENTS, EXPECTED,
                                 perturb={"value": 0x20, "frames": 0})


def test_perturbation_is_reversible_and_reports_the_value(emu):
    original = emu._pyboy.memory.base[PIECE]
    out = emu.investigate_validate("0xC100", "base", {"idle": IDLE}, {"idle": "none"}, repeats=2,
                                   perturb={"value": 0x20, "frames": 1}, allow_writes=True)
    assert out["perturbation"]["value_written"] == 0x20 and out["perturbation"]["value_after"] == 0x20
    assert emu._pyboy.memory.base[PIECE] == original        # restored after the observation


def test_validate_input_errors_change_nothing(emu):
    ticks = emu._pyboy.ticks
    bad_calls = [
        dict(address="0x8000", checkpoint="base", experiments=EXPERIMENTS, expected=EXPECTED),
        dict(address="0xC100", checkpoint="base", experiments=EXPERIMENTS, expected={"jump": "change"}),
        dict(address="0xC100", checkpoint="base", experiments=EXPERIMENTS, expected={"left": "sideways"}),
        dict(address="0xC100", checkpoint="base", experiments=EXPERIMENTS, expected=EXPECTED, repeats=0),
        dict(address="0xC100", checkpoint="no_such", experiments=EXPERIMENTS, expected=EXPECTED),
    ]
    for kwargs in bad_calls:
        with pytest.raises(ValueError):
            emu.investigate_validate(**kwargs)
    assert emu._pyboy.ticks == ticks
