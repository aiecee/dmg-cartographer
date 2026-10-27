"""Experiment and result history survives a restart (synthetic emulator; the restart is a new controller)."""
import json

import pytest

from dmg_cartographer.core.controller import PyBoyController
from dmg_cartographer.knowledge import history
from test_inspection import BankedSim
from test_priority2 import make_rom

LEFT = [{"press": ["left"], "frames": 1}, {"release": ["left"], "frames": 1}]
IDLE = [{"frames": 2}]


@pytest.fixture
def project(tmp_path):
    roms = tmp_path / ".pyboy-mcp" / "roms"
    roms.mkdir(parents=True)
    (roms / "puzzle.gb").write_bytes(make_rom())
    (roms / "other.gb").write_bytes(make_rom(b"OTHER") + b"\x00" * 16)
    return str(tmp_path)


def fresh(project, rom="puzzle.gb"):
    c = PyBoyController(BankedSim)
    c.load_game(project, rom)
    return c


def test_experiments_and_results_survive_a_restart(project):
    first = fresh(project)
    first._pyboy.memory.base[0xFE00:0xFEA0] = [0] * 160
    first.save_checkpoint("base")
    exp = first.run_experiment("left", LEFT)
    res = first.branch_experiment("base", [{"name": "idle", "actions": IDLE}])
    before_experiments = [e["id"] for e in first.list_experiments()]

    second = fresh(project)                       # a new controller: nothing in memory
    assert [e["id"] for e in second.list_experiments()] == before_experiments
    restored = second.get_result(res["result_id"])
    assert restored["kind"] == "branch" and restored["branches"][0]["name"] == "idle"
    # the restored experiment still has its memory changes, so candidate search works on it
    second.run_experiment("idle", IDLE)
    out = second.find_memory_candidates([exp["id"], "idle"])
    assert out["addresses_considered"] > 0


def test_ids_continue_after_restart_and_are_never_reused(project):
    first = fresh(project)
    first.run_experiment("idle", IDLE)
    first.run_experiment("idle", IDLE)
    second = fresh(project)
    new = second.run_experiment("idle", IDLE)
    assert new["id"] == "exp-0003"


def test_history_is_per_rom(project):
    first = fresh(project)
    first_id = first.run_experiment("idle", IDLE)["id"]
    other = fresh(project, "other.gb")
    assert other.list_experiments() == []
    other_id = other.run_experiment("idle", IDLE)["id"]
    back = fresh(project)
    assert [e["id"] for e in back.list_experiments()] == [first_id]
    assert first_id == "exp-0001" and other_id == "exp-0001"  # counters are per ROM history


def test_history_file_is_readable_json_named_by_rom_hash(project):
    c = fresh(project)
    c.run_experiment("idle", IDLE)
    path = history.history_path(c._rom_dir, c._rom_sha1)
    payload = json.loads(path.read_text())
    assert payload["version"] == history.HISTORY_VERSION
    assert payload["experiments"][0]["name"] == "idle"
    assert payload["experiments"][0]["changes"] is not None


def test_search_sessions_are_memory_only(project):
    first = fresh(project)
    first.search_memory(region="HRAM")
    assert first.list_search_sessions()
    assert fresh(project).list_search_sessions() == []


def test_unsupported_history_version_is_refused(project):
    c = fresh(project)
    c.run_experiment("idle", IDLE)
    path = history.history_path(c._rom_dir, c._rom_sha1)
    payload = json.loads(path.read_text())
    payload["version"] = 99
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="unsupported version"):
        fresh(project)


def test_invalid_rom_hash_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="invalid ROM hash"):
        history.history_path(tmp_path, "../escape")


def test_discoveries_export_writes_structured_file(project):
    c = fresh(project)
    c.save_discovery("piece_x", "0xC100", "WRAM", description="hypothesis")
    out = c.export_discoveries("disc1")
    payload = json.loads(open(out["file"]).read())
    assert out["count"] == 1 and payload["discoveries"][0]["name"] == "piece_x"
    assert payload["rom"]["sha1"] == c._rom_sha1


def test_unified_discoveries_tool_dispatches_and_rejects_bad_actions():
    import asyncio
    from types import SimpleNamespace
    from dmg_cartographer import server
    from mcp import Client

    async def call(args):
        async with Client(server.mcp) as client:
            try:
                return await client.call_tool("investigate_discoveries", args)
            except Exception as exc:  # the SDK raises on tool errors in this version
                return SimpleNamespace(is_error=True, error=str(exc))

    bad = asyncio.run(call({"action": "delete"}))
    assert bad.is_error
    no_name = asyncio.run(call({"action": "save"}))
    assert no_name.is_error


def test_verified_discovery_cannot_cite_an_experiment_that_contradicted_it(project):
    c = fresh(project)
    c.save_checkpoint("base")
    c._pyboy.memory.base[0xFE00:0xFEA0] = [0] * 160
    # idle does not change the byte, so "decrease" is contradicted by every run
    v = c.investigate_validate("0xC100", "base", {"idle": IDLE}, {"idle": "decrease"}, repeats=2)
    assert v["verdict"] == "contradicted"
    bad_ref = v["runs"][0]["experiment"]
    with pytest.raises(ValueError, match="contradicted validation"):
        c.save_discovery("claim", "0xC100", "WRAM", status="verified", evidence=[bad_ref])
    c.save_discovery("claim", "0xC100", "WRAM", status="hypothesis", evidence=[bad_ref])  # recording is still fine


def test_perturbation_result_is_stored_and_citable(project):
    c = fresh(project)
    c._pyboy.memory.base[0xFE00:0xFEA0] = [0] * 160
    c.save_checkpoint("base")
    p = c.investigate_validate("0xC100", "base", {"idle": IDLE}, {"idle": "none"}, repeats=1,
                               perturb={"value": 80, "frames": 2}, allow_writes=True)
    rid = p["perturbation"]["result_id"]
    assert p["perturbation"]["value_after"] == 80 and p["perturbation"]["reverted"] is False
    assert c.get_result(rid)["kind"] == "perturbation"
    d = c.save_discovery("piece_x", "0xC100", "WRAM", status="verified", evidence=[rid])
    assert d["status"] == "verified"
