"""Discoveries, replays, cartridge info, annotation and export. Synthetic emulator; real ROM header bytes.

The synthetic emulator reports the title "FAKE GAME", so header-vs-emulator title checks are expected to fail
here. That is a real check that the mismatch is detected, not a bug to hide.
"""
import hashlib
import json

import pytest
from PIL import Image

from dmg_cartographer import cartridge
from dmg_cartographer.knowledge import discoveries
from dmg_cartographer.knowledge import export
from dmg_cartographer.knowledge import replay
from dmg_cartographer.core.controller import PyBoyController
from test_inspection import BankedSim
from test_snapshots import PIECE_X


def make_rom(title: bytes = b"PUZZLE", bad_checksum: bool = False) -> bytes:
    rom = bytearray(32 * 1024)
    rom[0x134:0x134 + len(title)] = title
    rom[0x143] = 0x00           # DMG only
    rom[0x147] = 0x03           # MBC1+RAM+BATTERY
    rom[0x148] = 0x00           # 32 KiB
    rom[0x149] = 0x02           # 8 KiB RAM
    rom[0x14D] = cartridge.header_checksum(bytes(rom)) ^ (0xFF if bad_checksum else 0)
    return bytes(rom)


@pytest.fixture
def project(tmp_path):
    roms = tmp_path / ".pyboy-mcp" / "roms"
    roms.mkdir(parents=True)
    (roms / "puzzle.gb").write_bytes(make_rom())
    (roms / "other.gb").write_bytes(make_rom(b"OTHER") + b"\x00" * 16)  # different ROM, different hash
    return str(tmp_path)


@pytest.fixture
def emu(project):
    c = PyBoyController(BankedSim)
    c.load_game(project, "puzzle.gb")
    mem = c._pyboy.memory.base
    mem[0xFF40] = 0x91
    mem[0xFE00:0xFEA0] = [0] * 160
    c.step(2)
    c.save_checkpoint("base")
    c.step(4, press=["right"])
    return c


RIGHT = [{"press": ["right"], "frames": 1}, {"release": ["right"], "frames": 1}]
LEFT = [{"press": ["left"], "frames": 1}, {"release": ["left"], "frames": 1}]


def run_left_experiment(c):
    c.load_checkpoint("base")
    return c.run_experiment("left", LEFT)["id"]


# ---------- pure helpers ----------

def test_header_parsing_and_checksum():
    rom = make_rom()
    h = cartridge.parse_header(rom, file_size=len(rom))
    assert h["title"] == "PUZZLE" and h["header_checksum"]["valid"] is True
    assert h["cartridge_type"]["name"] == "MBC1+RAM+BATTERY" and h["battery"] is True
    assert h["ram_size"]["bytes"] == 8192 and h["rom_size"]["matches_file"] is True
    bad = cartridge.parse_header(make_rom(bad_checksum=True))
    assert bad["header_checksum"]["valid"] is False
    short = cartridge.parse_header(b"\x00" * 100)
    assert short["valid_header"] is False


def test_title_comparison_ignores_padding_and_case():
    assert cartridge.title_matches("PUZZLE", "puzzle ") is True
    assert cartridge.title_matches("PUZZLE", "FAKE GAME") is False
    assert cartridge.title_matches("PUZZLE", None) is None


def test_discovery_name_and_status_rules():
    base = {"name": "falling_x", "address": 0xC100, "region": "WRAM"}
    ok = discoveries.validate_discovery(base, existing=None, checkable_refs=set())
    assert ok["address"] == "0xC100" and ok["status"] == "hypothesis"
    for bad in [{"name": "exp-0001"}, {"name": "1abc"}, {"name": "x", "address": 0xC100},
                {"name": "x", "region": "VRAM"}, {"name": "x", "confidence": "sure"}]:
        with pytest.raises(ValueError):
            discoveries.validate_discovery(bad, existing=None, checkable_refs=set())
    # a name that merely starts with "exp" is fine
    discoveries.validate_discovery({"name": "experiment_left"}, existing=None, checkable_refs=set())


def test_replay_fingerprint_and_compare():
    regions = {"WRAM": b"\x01\x02", "HRAM": b"\x03"}
    a = replay.fingerprint(regions, 5, ["left"])
    assert a == replay.fingerprint(regions, 5, ["left"])          # deterministic
    assert replay.compare(a, a) == []
    b = replay.fingerprint({"WRAM": b"\x01\x09", "HRAM": b"\x03"}, 5, ["left"])
    fields = [m["field"] for m in replay.compare(a, b)]
    assert "digest" in fields and "regions.WRAM" in fields


# ---------- discoveries ----------

def test_saving_does_not_verify_and_verification_needs_an_experiment(emu):
    with pytest.raises(ValueError, match="does not verify|saving a claim"):
        emu.save_discovery("falling_x", "0xC100", "WRAM", "uint8", status="verified")
    saved = emu.save_discovery("falling_x", "0xC100", "WRAM", "uint8", confidence="medium",
                               description="horizontal position", evidence=["hunch"])
    assert saved["status"] == "hypothesis" and saved["evidence_count"] == 1
    exp = run_left_experiment(emu)
    verified = emu.save_discovery("falling_x", "0xC100", "WRAM", "uint8", status="verified", evidence=[exp],
                                  update=True)
    assert verified["status"] == "verified"


def test_duplicate_names_and_merging(emu):
    emu.save_discovery("pos", "0xC100", "WRAM", evidence=["a"])
    with pytest.raises(ValueError, match="already exists"):
        emu.save_discovery("pos", "0xC100", "WRAM")
    merged = emu.save_discovery("pos", "0xC100", "WRAM", evidence=["b", "a"], update=True)
    assert merged["evidence"] == ["a", "b"]


def test_disproven_needs_a_real_counterexample(emu):
    exp = run_left_experiment(emu)
    with pytest.raises(ValueError, match="disproven needs"):
        emu.save_discovery("nope", "0xC200", "WRAM", status="disproven", counterexamples=["vibes"])
    d = emu.save_discovery("nope", "0xC200", "WRAM", status="disproven", counterexamples=[exp])
    assert d["status"] == "disproven" and d["counterexample_count"] == 1


def test_support_and_contradiction_cannot_be_the_same_reference(emu):
    exp = run_left_experiment(emu)
    with pytest.raises(ValueError, match="both support and contradict"):
        emu.save_discovery("both", "0xC100", "WRAM", status="verified", evidence=[exp], counterexamples=[exp])


def test_get_discoveries_filters_and_evidence(emu):
    exp = run_left_experiment(emu)
    emu.save_discovery("pos_a", "0xC100", "WRAM", confidence="high", status="verified", evidence=[exp],
                       labels=["player"])
    emu.save_discovery("pos_b", "0xC101", "WRAM", confidence="low", evidence=["note"])
    emu.save_discovery("hram_x", "0xFF90", "HRAM", labels=["timer"])
    everything = emu.get_discoveries()
    assert everything["count"] == 3 and "evidence" not in everything["discoveries"][0]
    assert [d["name"] for d in emu.get_discoveries(address="0xC100")["discoveries"]] == ["pos_a"]
    assert [d["name"] for d in emu.get_discoveries(region="HRAM")["discoveries"]] == ["hram_x"]
    assert [d["name"] for d in emu.get_discoveries(label="player")["discoveries"]] == ["pos_a"]
    # hram_x was saved without a confidence, so it takes the default "low"
    assert [d["name"] for d in emu.get_discoveries(confidence="low")["discoveries"]] == ["hram_x", "pos_b"]
    verified = emu.get_discoveries(status="verified", include_evidence=True)["discoveries"][0]
    assert verified["evidence"] == [exp]


def test_discoveries_persist_and_are_isolated_per_rom(emu, project):
    emu.save_discovery("persisted", "0xC100", "WRAM")
    store_dir = emu._rom_dir / "discoveries"
    files = list(store_dir.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text())
    assert payload["discoveries"]["persisted"]["address"] == "0xC100"

    fresh = PyBoyController(BankedSim)
    fresh.load_game(project, "puzzle.gb")
    assert [d["name"] for d in fresh.get_discoveries()["discoveries"]] == ["persisted"]

    other_sha = hashlib.sha1((emu._data_dir / "roms" / "other.gb").read_bytes()).hexdigest()
    fresh.load_game(project, "other.gb")
    assert fresh.get_discoveries()["count"] == 0
    assert fresh.get_discoveries(rom_sha1=files[0].stem)["count"] == 1
    assert other_sha != files[0].stem


def test_invalid_discovery_inputs_leave_store_unchanged(emu):
    emu.save_discovery("keep", "0xC100", "WRAM")
    for kwargs in [dict(name="bad name!", address="0xC100", region="WRAM"),
                   dict(name="x", address="0xC100", region="WRAM", bank=9),
                   dict(name="y", address="0x10000", region="WRAM"),
                   dict(name="z", address="0xC100", region="WRAM", evidence=["a"] * 51)]:
        with pytest.raises(ValueError):
            emu.save_discovery(**kwargs)
    assert [d["name"] for d in emu.get_discoveries()["discoveries"]] == ["keep"]


# ---------- replays ----------

def test_record_then_replay_matches_exactly(emu):
    recorded = emu.record_replay("left_check", "record", "base", [{"press": ["left"], "frames": 3}])
    assert recorded["stored"] is True and recorded["evidence_ref"] == "replay:left_check"
    before = (emu.status()["timeline_frame"], emu._pyboy.memory.base[PIECE_X])
    replayed = emu.record_replay("left_check", "replay")
    assert replayed["matched"] is True and replayed["mismatch_count"] == 0
    assert (emu.status()["timeline_frame"], emu._pyboy.memory.base[PIECE_X]) == before


def test_replay_reports_changed_starting_checkpoint(emu):
    emu.record_replay("left_check", "record", "base", [{"press": ["left"], "frames": 3}])
    emu.step(5)
    emu.save_checkpoint("base")         # same name, different state: the recording no longer applies
    result = emu.record_replay("left_check", "replay")
    assert result["matched"] is False
    assert any(m["field"].startswith("start.") for m in result["mismatches"])


def test_replay_expect_values_are_checked(emu):
    # the base checkpoint has the piece at 80; two frames of left give 78
    emu.record_replay("exp_check", "record", "base", [{"press": ["left"], "frames": 2}], expect={"0xC100": 78})
    assert emu.record_replay("exp_check", "replay")["matched"] is True
    with pytest.raises(ValueError, match="did not hold"):
        emu.record_replay("exp_wrong", "record", "base", [{"frames": 1}], expect={"0xC100": 7})
    assert replay.ReplayStore(emu._rom_dir / "replays").read(emu._rom_sha1, "exp_wrong") is None  # nothing stored


def test_replay_errors_and_restoration(emu):
    with pytest.raises(ValueError, match="no replay"):
        emu.record_replay("missing", "replay")
    with pytest.raises(ValueError, match="checkpoint is required|needs actions"):
        emu.record_replay("x", "record", None, [{"frames": 1}])
    with pytest.raises(ValueError, match="does not exist"):
        emu.record_replay("x", "record", "no_such", [{"frames": 1}])
    frames, held = emu.status()["timeline_frame"], emu.status()["held_buttons"]
    emu.record_replay("r1", "record", "base", [{"press": ["left"], "frames": 2}])
    emu.record_replay("r1", "replay")
    assert emu.status()["timeline_frame"] == frames and emu.status()["held_buttons"] == held


def test_replay_can_back_a_verified_discovery(emu):
    emu.record_replay("left_check", "record", "base", [{"press": ["left"], "frames": 3}])
    d = emu.save_discovery("piece_x", "0xC100", "WRAM", status="verified", evidence=["replay:left_check"])
    assert d["status"] == "verified"


# ---------- cartridge ----------

def test_inspect_cartridge_reports_header_and_checks(emu):
    info = emu.inspect_cartridge()
    assert info["header"]["title"] == "PUZZLE"
    assert info["header"]["cartridge_type"]["name"] == "MBC1+RAM+BATTERY"
    assert info["checks"]["header_checksum"] is True and info["checks"]["rom_size_matches_file"] is True
    assert info["checks"]["title_matches_emulator"] is False   # emulator says FAKE GAME
    assert info["all_checks_pass"] is False
    assert info["rom_sha1"] == hashlib.sha1(emu._rom_path.read_bytes()).hexdigest()


def test_inspect_cartridge_flags_a_bad_header(project, tmp_path):
    (tmp_path / ".pyboy-mcp" / "roms" / "bad.gb").write_bytes(make_rom(bad_checksum=True))
    c = PyBoyController(BankedSim)
    c.load_game(project, "bad.gb")
    assert c.inspect_cartridge()["checks"]["header_checksum"] is False


# ---------- annotation ----------

def test_annotation_returns_png_and_leaves_framebuffer_alone(emu):
    before = emu._pyboy.screen.image.copy().tobytes()
    png, meta = emu.annotate_screen(["pixels", "tiles", "sprites", "regions"],
                                    [{"x": 10, "y": 10, "width": 20, "height": 20, "label": "piece"}], scale=2)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and meta["framebuffer_modified"] is False
    assert emu._pyboy.screen.image.tobytes() == before
    with Image.open(__import__("io").BytesIO(png)) as image:
        assert image.size == (320, 288)
    for bad in [dict(overlays=["grid"]), dict(overlays=[]), dict(scale=5)]:
        with pytest.raises(ValueError):
            emu.annotate_screen(**bad)


# ---------- export ----------

def test_export_writes_bounded_json_markdown_and_screenshots(emu, tmp_path):
    exp = run_left_experiment(emu)
    emu.load_checkpoint("base")
    emu.run_experiment("idle", [{"frames": 1}])  # candidate search needs two experiment names
    emu.save_discovery("piece_x", "0xC100", "WRAM", status="verified", evidence=[exp], labels=["player"])
    emu.record_replay("left_check", "record", "base", [{"press": ["left"], "frames": 2}])
    result = emu.export_analysis("run1", candidates=["left", "idle"], include_screenshots=True)
    files = result["files"]
    report = json.loads(open(files["json"]).read())
    assert report["rom"]["sha1"] == hashlib.sha1(emu._rom_path.read_bytes()).hexdigest()
    assert [d["name"] for d in report["discoveries"]] == ["piece_x"]
    assert report["experiments"][0]["id"] == exp and "screen_summary" in report["experiments"][0]
    assert report["candidates"]["candidate_count"] >= 1
    assert report["replays"][0]["name"] == "left_check"
    assert "Investigation" in open(files["markdown"]).read()
    assert len(files["screenshots"]) == 1 and open(files["screenshots"][0], "rb").read(4) == b"\x89PNG"
    assert result["counts"]["discoveries"] == 1


def test_export_rejects_oversize_and_bad_names(emu, monkeypatch):
    monkeypatch.setattr(export, "MAX_JSON_BYTES", 50)
    with pytest.raises(ValueError, match="over the"):
        emu.export_analysis("big")
    monkeypatch.setattr(export, "MAX_JSON_BYTES", 2 * 1024 * 1024)
    for name in ["../escape", "", "a" * 70]:
        with pytest.raises(ValueError):
            emu.export_analysis(name)
