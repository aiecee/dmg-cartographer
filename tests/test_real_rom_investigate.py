"""Every investigate tool action against a real Dr. Mario ROM, validated by its typed output models.

Same ROM rules as test_real_rom_actions.py: DMG_TEST_ROM, or the dr-mario-re project. Skipped when absent.
"""
import os
import shutil
from pathlib import Path

import pytest
from mcp.server.mcpserver import Image

from dmg_cartographer import mcp_app
from dmg_cartographer.core.controller import PyBoyController
from dmg_cartographer.tools import investigation as inv
from dmg_cartographer.tools import raw

DEFAULT = Path(__file__).resolve().parents[1] / ".dmg-cartographer" / "roms" / "Dr. Mario (World) (Rev 1).gb"
ROM = Path(os.environ["DMG_TEST_ROM"]) if os.environ.get("DMG_TEST_ROM") else DEFAULT
pytestmark = pytest.mark.skipif(not ROM.is_file(), reason="no Dr. Mario ROM (set DMG_TEST_ROM)")

LEFT = [mcp_app.Action(frames=10, press=["left"]), mcp_app.Action(frames=10, release=["left"])]
IDLE = [mcp_app.Action(frames=10)]


@pytest.fixture
def real(tmp_path, monkeypatch):
    rom_dir = tmp_path / ".dmg-cartographer" / "roms"
    rom_dir.mkdir(parents=True)
    shutil.copy(ROM, rom_dir / ROM.name)
    c = PyBoyController()
    for mod in (mcp_app, raw, inv):
        monkeypatch.setattr(mod, "CONTROLLER", c)
    monkeypatch.setitem(mcp_app.SENSITIVE_ENABLED, "write", True)
    raw.game("load", project_dir=str(tmp_path), rom_name=ROM.name)
    raw.step(frames=60)
    raw.state("save", name="start")
    yield c
    raw.game("close")


def data(result):
    env = result[0] if isinstance(result, list) else result
    return env.data


def test_experiment_actions(real):
    assert data(inv.experiment("run", name="left", actions=LEFT)).id.startswith("exp-")
    raw.state("load", name="start")
    inv.experiment("run", name="idle", actions=IDLE)
    raw.state("load", name="start")
    assert len(data(inv.experiment("list")).experiments) == 2
    rank = data(inv.experiment("rank", names=["left", "idle"], expected_change={"left": "change", "idle": "none"}))
    assert len(rank.experiments) == 2  # rank and correlate report the stored experiment ids
    corr = data(inv.experiment("correlate", names=["left", "idle"]))
    assert len(corr.experiments) == 2
    raw.state("load", name="start")
    verdict = data(inv.experiment("validate", address="0xC000", checkpoint="start",
                                  experiments={"left": LEFT, "idle": IDLE}, expected={"left": "change", "idle": "none"}))
    assert verdict.verdict in ("supported_by_experiments", "contradicted", "inconclusive")


def test_branch_watch_replay_result(real):
    raw.state("load", name="start")
    from dmg_cartographer.mcp_app import BranchSpec  # noqa: F401  (validated by the tool signature)
    br = data(inv.branch("start", [BranchSpec(name="left", actions=LEFT), BranchSpec(name="idle", actions=IDLE)],
                         compare=["memory"]))
    assert br.restored is True
    stored = data(inv.result(br.result_id))
    assert stored.kind == "branch"
    raw.state("load", name="start")
    w = data(inv.watch(addresses=["0xC000"], cpu=True, frames=5))
    assert w.advanced_frames == 5
    raw.state("load", name="start")
    rec = data(inv.replay("r1", "record", "start", LEFT))
    assert rec.mode == "record" and rec.stored is True
    raw.state("load", name="start")
    rep = data(inv.replay("r1", "replay"))
    assert rep.mode == "replay" and rep.matched is True
    assert data(inv.result(rep.result_id)).kind == "replay"


def test_discoveries_export_and_screen_changes(real, tmp_path):
    raw.state("load", name="start")
    inv.experiment("run", name="left", actions=LEFT)
    saved = data(inv.discoveries("save", name="left_var", address="0xC000", region="WRAM", data_type="uint8",
                                 confidence="medium", evidence=["exp-0001"]))
    assert saved.action == "save" and saved.status == "hypothesis"
    listed = data(inv.discoveries("list"))
    assert listed.count >= 1
    assert data(inv.discoveries("export", name="probe-export")).count >= 1
    report = data(inv.export("probe-export", None, False, True, 20))
    assert report.counts.experiments >= 1
    raw.state("load", name="start")
    snap = raw.snapshot("capture", name="base").data.id
    raw.step(frames=20)
    result = inv.screen_changes(snap, True, None, "both", 0, None, True, True, False, 5)
    assert data(result).baseline == snap
    assert not any(isinstance(r, Image) for r in result[1:])


def test_narrowed_fields_on_real_output(real):
    raw.state("load", name="start")
    contra = data(inv.experiment("validate", address="0xC000", checkpoint="start",
                                 experiments={"left": LEFT, "idle": IDLE},
                                 expected={"left": "decrease", "idle": "none"}))
    assert contra.contradictions and contra.contradictions[0].expected == "decrease"
    raw.state("load", name="start")
    pert = data(inv.experiment("validate", address="0xC000", checkpoint="start",
                               experiments={"left": LEFT}, expected={"left": "change"},
                               perturb=mcp_app.Perturb(value=5, frames=3), allow_writes=True))
    assert pert.perturbation.value_written == 5
    raw.state("load", name="start")
    inv.experiment("run", name="mem_only", actions=IDLE, compare_screen=False)
    raw.state("load", name="start")
    inv.experiment("run", name="both", actions=IDLE)
    raw.state("load", name="start")
    inv.experiment("run", name="both2", actions=LEFT)
    raw.state("load", name="start")
    corr = data(inv.experiment("correlate", names=["mem_only", "both", "both2"]))
    assert len(corr.excluded) == 1 and "compare_screen" in corr.excluded[0].reason
    raw.state("load", name="start")
    rec = data(inv.replay("mm", "record", "start", LEFT))
    assert rec.stored is True
    raw.step(frames=7)
    raw.state("save", name="start")  # overwrite the checkpoint so the replay starts from different state
    raw.state("load", name="start")
    rep = data(inv.replay("mm", "replay"))
    assert rep.matched is False and rep.mismatches and rep.mismatches[0].field
