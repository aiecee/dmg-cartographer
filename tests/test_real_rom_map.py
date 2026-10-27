"""The state map on a real Dr. Mario ROM: validate, read, and refuse a map for another ROM.

Same ROM rules as the other real-ROM tests. The example map is examples/dr-mario-rev1.map.json.
"""
import os
import shutil
from pathlib import Path

import pytest

from dmg_cartographer import mcp_app
from dmg_cartographer.core.controller import PyBoyController
from dmg_cartographer.tools import raw, state_map

REPO = Path(__file__).resolve().parents[1]
DEFAULT = Path.home() / "projects" / "dr-mario-re" / ".pyboy-mcp" / "roms" / "Dr. Mario (World) (Rev 1).gb"
ROM = Path(os.environ["DMG_TEST_ROM"]) if os.environ.get("DMG_TEST_ROM") else DEFAULT
pytestmark = pytest.mark.skipif(not ROM.is_file(), reason="no Dr. Mario ROM (set DMG_TEST_ROM)")
EXAMPLE = REPO / "examples" / "dr-mario-rev1.map.json"


@pytest.fixture
def real(tmp_path, monkeypatch):
    rom_dir = tmp_path / ".pyboy-mcp" / "roms"
    rom_dir.mkdir(parents=True)
    shutil.copy(ROM, rom_dir / ROM.name)
    c = PyBoyController()
    monkeypatch.setattr(mcp_app, "CONTROLLER", c)
    monkeypatch.setattr(raw, "CONTROLLER", c)
    monkeypatch.setattr(state_map, "CONTROLLER", c)
    raw.game("load", rom_path=str(tmp_path / ".pyboy-mcp" / "roms" / ROM.name))
    raw.step(frames=60)
    yield c
    raw.game("close")


def data(result):
    return result[0].data if isinstance(result, list) else result.data


def test_example_map_validates_and_reads(real):
    checked = data(state_map.state_map("validate", str(EXAMPLE)))
    assert checked.field_count == 2 and checked.rom_sha1 == "d31d67d0682515c7c85deaa1752b02231150e5bf"
    values = data(state_map.state_map("read", str(EXAMPLE))).values
    assert len(values["board"]) == 16 and all(len(row) == 8 for row in values["board"])
    assert len(values["score_bytes"]) == 4


def test_labelled_read_matches_raw_when_the_map_has_no_lookup(real):
    raw_values = data(state_map.state_map("read", str(EXAMPLE))).values
    labelled = data(state_map.state_map("read", str(EXAMPLE), labelled=True)).values
    assert labelled == raw_values  # the example map has no lookup table yet


def test_map_for_another_rom_is_refused(real, tmp_path):
    other = tmp_path / "other.map.json"
    other.write_text(EXAMPLE.read_text().replace("d31d67d0682515c7c85deaa1752b02231150e5bf", "cd" * 20))
    with pytest.raises(Exception, match="map is for ROM"):
        state_map.state_map("read", str(other))
