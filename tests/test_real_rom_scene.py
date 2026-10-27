"""The scene tool against a real Dr. Mario ROM: save, list and read, validated by the typed scene models.

Same ROM rules as the other real-ROM tests. Skipped when no ROM is present.
"""
import os
import shutil
from pathlib import Path

import pytest

from dmg_cartographer import mcp_app
from dmg_cartographer.core.controller import PyBoyController
from dmg_cartographer.tools import raw, scene

DEFAULT = Path(__file__).resolve().parents[1] / ".dmg-cartographer" / "roms" / "Dr. Mario (World) (Rev 1).gb"
ROM = Path(os.environ["DMG_TEST_ROM"]) if os.environ.get("DMG_TEST_ROM") else DEFAULT
pytestmark = pytest.mark.skipif(not ROM.is_file(), reason="no Dr. Mario ROM (set DMG_TEST_ROM)")

FIELDS = [{"name": "score_tile", "type": "uint8", "offset": 0}, {"name": "bits", "type": "bits", "offset": 1,
                                                                  "bit": 0, "width": 2}]


@pytest.fixture
def real(tmp_path, monkeypatch):
    rom_dir = tmp_path / ".dmg-cartographer" / "roms"
    rom_dir.mkdir(parents=True)
    shutil.copy(ROM, rom_dir / ROM.name)
    c = PyBoyController()
    monkeypatch.setattr(mcp_app, "CONTROLLER", c)
    monkeypatch.setattr(raw, "CONTROLLER", c)
    monkeypatch.setattr(scene, "CONTROLLER", c)
    raw.game("load", project_dir=str(tmp_path), rom_name=ROM.name)
    raw.step(frames=60)
    yield c, tmp_path
    raw.game("close")


def data(result):
    return result[0].data if isinstance(result, list) else result.data


def test_save_list_read_scene(real):
    c, proj = real
    saved = data(scene.scene("save", name="title", address=0xC000, length=2, fields=FIELDS, note="boot"))
    assert saved.action == "save" and saved.schema_version == 1 and saved.note == "boot"
    assert set(saved.values) == {"score_tile", "bits"}
    folder = Path(saved.folder)
    assert (folder / "scene.json").is_file() and (folder / "capture.png").is_file() and (folder / "state.state").is_file()
    assert data(scene.scene("list")).scenes == ["title"]
    read = data(scene.scene("read", name="title"))
    assert read.frame == saved.frame and read.rom_sha1 == saved.rom_sha1
    assert read.values == saved.values


def test_save_is_read_only_for_the_emulator(real):
    c, _ = real
    before = c._frames
    scene.scene("save", name="still", address=0xC000, length=1, fields=FIELDS[:1])
    assert c._frames == before


def test_export_round_trips_through_the_plain_reader(real):
    from dmg_cartographer import dataset
    c, _ = real
    scene.scene("save", name="one", address=0xC000, length=2, fields=FIELDS, note="first")
    raw.step(frames=10)
    scene.scene("save", name="two", address=0xC000, length=2, fields=FIELDS)
    exported = data(scene.scene("export", name="train-v1", include_states=True))
    assert exported.scene_count == 2 and exported.format_version == 1
    scenes = list(dataset.iter_scenes(exported.folder))
    assert [s.name for s in scenes] == ["one", "two"]
    assert scenes[0].note == "first" and scenes[0].state.is_file() and scenes[0].image.is_file()
    assert scenes[0].values == c.read_scene("one")["values"]
    assert set(scenes[0].values) == {"score_tile", "bits"}
    assert scenes[1].frame > scenes[0].frame
