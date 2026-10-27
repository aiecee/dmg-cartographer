"""The scene dataset format: round trip, version checks and the no-MCP import rule. No ROM needed."""
import ast
import json
from pathlib import Path

import pytest

from dmg_cartographer import dataset

SRC = Path(__file__).resolve().parents[1] / "src" / "dmg_cartographer" / "dataset.py"


def record(name, value):
    return {"schema_version": 1, "name": name, "rom": "game.gb", "rom_sha1": "ab" * 20, "frame": 12,
            "held_buttons": ["a"], "screen_fresh": True, "note": "", "values": {"coins": {"dec": value, "hex": "0x01"}}}


def make_sources(tmp_path, names):
    images, states = {}, {}
    for n in names:
        images[n] = tmp_path / f"{n}.png"
        images[n].write_bytes(b"png-" + n.encode())
        states[n] = tmp_path / f"{n}.state"
        states[n].write_bytes(b"state-" + n.encode())
    return images, states


def test_round_trip_and_order(tmp_path):
    images, states = make_sources(tmp_path, ["b", "a"])
    out = tmp_path / "ds"
    manifest = dataset.write_dataset(out, rom="game.gb", rom_sha1="ab" * 20,
                                     records=[record("b", 2), record("a", 1)],
                                     image_sources=images, state_sources=states)
    assert manifest["format_version"] == 1 and manifest["scene_count"] == 2 and manifest["fields"] == ["coins"]
    scenes = list(dataset.iter_scenes(out))
    assert [s.name for s in scenes] == ["a", "b"]
    assert scenes[0].values == {"coins": {"dec": 1, "hex": "0x01"}}
    assert scenes[0].image.read_bytes() == b"png-a" and scenes[0].state.read_bytes() == b"state-a"
    assert scenes[1].held_buttons == ["a"] and scenes[1].frame == 12


def test_states_are_optional(tmp_path):
    images, _ = make_sources(tmp_path, ["a"])
    dataset.write_dataset(tmp_path / "ds", rom="g", rom_sha1="x", records=[record("a", 1)],
                          image_sources=images, state_sources=None)
    (scene,) = dataset.iter_scenes(tmp_path / "ds")
    assert scene.state is None


def test_rewrite_removes_scenes_that_no_longer_exist(tmp_path):
    images, states = make_sources(tmp_path, ["a", "b"])
    out = tmp_path / "ds"
    dataset.write_dataset(out, rom="g", rom_sha1="x", records=[record("a", 1), record("b", 2)],
                          image_sources=images, state_sources=states)
    dataset.write_dataset(out, rom="g", rom_sha1="x", records=[record("a", 1)],
                          image_sources=images, state_sources=None)
    assert not (out / "images" / "b.png").exists()
    assert [s.name for s in dataset.iter_scenes(out)] == ["a"]


def test_reader_rejects_other_versions(tmp_path):
    images, _ = make_sources(tmp_path, ["a"])
    out = tmp_path / "ds"
    dataset.write_dataset(out, rom="g", rom_sha1="x", records=[record("a", 1)], image_sources=images,
                          state_sources=None)
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["format_version"] = 2
    (out / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(dataset.DatasetError, match="unsupported format_version 2"):
        list(dataset.iter_scenes(out))


def test_reader_rejects_other_formats_and_missing_files(tmp_path):
    images, _ = make_sources(tmp_path, ["a"])
    out = tmp_path / "ds"
    dataset.write_dataset(out, rom="g", rom_sha1="x", records=[record("a", 1)], image_sources=images,
                          state_sources=None)
    (out / "images" / "a.png").unlink()
    with pytest.raises(dataset.DatasetError, match="missing file"):
        list(dataset.iter_scenes(out))
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["format"] = "something-else"
    (out / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(dataset.DatasetError, match="not a dmg-cartographer-scenes dataset"):
        dataset.read_manifest(out)


def test_dataset_module_needs_no_mcp_or_emulator():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"mcp", "pyboy", "PIL", "numpy", "dmg_cartographer"}
