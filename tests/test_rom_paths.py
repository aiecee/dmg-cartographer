"""Where ROMs and their data live: <project>/.dmg-cartographer/. Absolute ROM paths are copied in. Synthetic emulator."""
import hashlib
import json

import pytest

from dmg_cartographer import mcp_app
from dmg_cartographer.core.controller import PyBoyController
from dmg_cartographer.tools import raw
from test_inspection import BankedSim
from test_priority2 import make_rom


@pytest.fixture
def ctl(monkeypatch):
    c = PyBoyController(BankedSim)
    monkeypatch.setattr(mcp_app, "CONTROLLER", c)
    monkeypatch.setattr(raw, "CONTROLLER", c)
    return c


def rom_file(folder, name="puzzle.gb", data=None):
    folder.mkdir(parents=True, exist_ok=True)
    data = make_rom() if data is None else data
    (folder / name).write_bytes(data)
    return folder / name, hashlib.sha1(data).hexdigest()


def test_absolute_rom_is_copied_into_the_project_and_runs_from_the_copy(ctl, tmp_path):
    original, sha = rom_file(tmp_path / "downloads")
    project = tmp_path / "proj"
    project.mkdir()
    raw.game("load", project_dir=str(project), rom_path=str(original))
    copy = project / ".dmg-cartographer" / "roms" / "puzzle.gb"
    assert copy.read_bytes() == original.read_bytes()
    assert ctl._rom_path == copy
    assert ctl.status()["rom_folder"] == str(project / ".dmg-cartographer" / ("puzzle-" + sha[:8]))


def test_deleting_the_original_does_not_break_the_project(ctl, tmp_path):
    original, _ = rom_file(tmp_path / "downloads")
    project = tmp_path / "proj"
    project.mkdir()
    raw.game("load", project_dir=str(project), rom_path=str(original))
    ctl.close_game()
    original.unlink()
    assert raw.game("list", project_dir=str(project)).data.roms == ["puzzle.gb"]
    assert raw.game("load", project_dir=str(project), rom_name="puzzle.gb").data.loaded is True


def test_same_rom_again_is_reused_and_a_different_one_is_not_overwritten(ctl, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    first, _ = rom_file(tmp_path / "a")
    same, _ = rom_file(tmp_path / "b")
    clash, _ = rom_file(tmp_path / "c", data=make_rom(b"OTHER") + b"\x00" * 16)
    assert ctl.add_rom(str(project), str(first)) == "puzzle.gb"
    assert ctl.add_rom(str(project), str(same)) == "puzzle.gb"  # identical bytes: nothing copied, nothing changed
    with pytest.raises(ValueError, match="different contents"):
        ctl.add_rom(str(project), str(clash))
    assert (project / ".dmg-cartographer" / "roms" / "puzzle.gb").read_bytes() == first.read_bytes()


def test_rom_already_in_the_project_is_used_in_place(ctl, tmp_path):
    project = tmp_path / "proj"
    path, _ = rom_file(project / ".dmg-cartographer" / "roms" / "nested")
    assert ctl.add_rom(str(project), str(path)) == "nested/puzzle.gb"
    assert sorted(p.name for p in (project / ".dmg-cartographer" / "roms").iterdir()) == ["nested"]


def test_load_needs_a_project_and_exactly_one_rom_argument(ctl, tmp_path):
    original, _ = rom_file(tmp_path / "downloads")
    with pytest.raises(ValueError, match="needs project_dir"):
        raw.game("load", rom_path=str(original))
    with pytest.raises(ValueError, match="either rom_name or rom_path"):
        raw.game("load", project_dir=str(tmp_path), rom_name="puzzle.gb", rom_path=str(original))


def test_wrong_rom_paths_are_refused_and_nothing_is_copied(ctl, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    path, _ = rom_file(tmp_path / "downloads")
    (tmp_path / "downloads" / "notes.txt").write_text("not a rom")
    (tmp_path / "downloads" / "link.gb").symlink_to(path)
    for bad, message in [("downloads/puzzle.gb", "absolute path"), (str(tmp_path / "missing.gb"), "regular file"),
                         (str(tmp_path / "downloads" / "notes.txt"), r"\.gb or \.gbc"),
                         (str(tmp_path / "downloads" / "link.gb"), "regular file")]:
        with pytest.raises(ValueError, match=message):
            ctl.add_rom(str(project), bad)
    assert not (project / ".dmg-cartographer").exists()  # refused before anything is created


def legacy_layout(root, sha, key):
    """Files as earlier versions wrote them, for one ROM."""
    (root / "saves").mkdir(parents=True)
    (root / "saves" / f"{key}.ram").write_bytes(b"SAVE")
    (root / "checkpoints" / key).mkdir(parents=True)
    (root / "checkpoints" / key / "boot.state").write_bytes(b"STATE")
    (root / "checkpoints" / key / "boot.json").write_text(json.dumps({"frames": 3}))
    (root / "scenes" / key / "title").mkdir(parents=True)
    (root / "scenes" / key / "title" / "scene.json").write_text("{}")
    (root / "discoveries").mkdir()
    (root / "discoveries" / f"{sha}.json").write_text("{\"discoveries\": {}}")
    (root / "replays" / sha).mkdir(parents=True)
    (root / "replays" / sha / "r1.json").write_text("{}")
    (root / "history").mkdir()
    (root / "history" / f"{sha}.json").write_text("{\"version\": 1}")


def test_flat_layout_is_moved_into_the_rom_folder_once(ctl, tmp_path):
    _, sha = rom_file(tmp_path / "proj" / ".dmg-cartographer" / "roms")
    root = tmp_path / "proj" / ".dmg-cartographer"
    legacy_layout(root, sha, f"puzzle.gb-{sha[:12]}")
    ctl.load_game(str(tmp_path / "proj"), "puzzle.gb")
    rom_dir = root / ("puzzle-" + sha[:8])
    assert (rom_dir / "save.ram").read_bytes() == b"SAVE"
    assert (rom_dir / "checkpoints" / "boot.state").read_bytes() == b"STATE"
    assert (rom_dir / "scenes" / "title" / "scene.json").is_file()
    assert (rom_dir / "discoveries" / f"{sha}.json").is_file()
    assert (rom_dir / "replays" / sha / "r1.json").is_file()
    assert (rom_dir / "history.json").read_text() == "{\"version\": 1}"
    assert not (root / "saves" / f"puzzle.gb-{sha[:12]}.ram").exists()
    assert ctl.list_checkpoints() == ["boot"]

    ctl.close_game()
    ctl.load_game(str(tmp_path / "proj"), "puzzle.gb")  # a second load finds nothing left to move
    assert (rom_dir / "save.ram").read_bytes() == b"SAVE"


def test_migration_never_overwrites_a_file_already_in_the_rom_folder(ctl, tmp_path):
    _, sha = rom_file(tmp_path / "proj" / ".dmg-cartographer" / "roms")
    root = tmp_path / "proj" / ".dmg-cartographer"
    key = f"puzzle.gb-{sha[:12]}"
    legacy_layout(root, sha, key)
    rom_dir = root / ("puzzle-" + sha[:8])
    rom_dir.mkdir(parents=True)
    (rom_dir / "save.ram").write_bytes(b"NEWER")
    ctl.load_game(str(tmp_path / "proj"), "puzzle.gb")
    assert (rom_dir / "save.ram").read_bytes() == b"NEWER"
    assert (root / "saves" / f"{key}.ram").read_bytes() == b"SAVE"  # left in place for the user to decide
