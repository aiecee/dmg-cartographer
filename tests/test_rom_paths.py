"""Loading a ROM by absolute path, where its data goes, and moving data from the flat layout. Synthetic emulator."""
import hashlib
import json

import pytest

from dmg_cartographer.core.controller import PyBoyController
from test_inspection import BankedSim
from test_priority2 import make_rom


@pytest.fixture
def ctl():
    return PyBoyController(BankedSim)


def rom_file(folder, name="puzzle.gb"):
    folder.mkdir(parents=True, exist_ok=True)
    data = make_rom()
    (folder / name).write_bytes(data)
    return folder / name, hashlib.sha1(data).hexdigest()


def test_load_rom_takes_an_absolute_path_and_keeps_data_next_to_the_rom(ctl, tmp_path):
    path, sha = rom_file(tmp_path / "games")
    ctl.load_rom(str(path))
    assert ctl.status()["rom"] == "puzzle.gb"
    assert (tmp_path / "games" / ".dmg-cartographer").is_dir()
    assert ctl.status()["rom_folder"].endswith("puzzle-" + sha[:8])


def test_rom_inside_a_pyboy_mcp_project_keeps_that_root(ctl, tmp_path):
    path, _ = rom_file(tmp_path / "proj" / ".pyboy-mcp" / "roms")
    ctl.load_rom(str(path))
    assert ctl.status()["data_dir"] == str(tmp_path / "proj" / ".pyboy-mcp")


def test_data_dir_env_var_overrides_the_default(ctl, tmp_path, monkeypatch):
    path, _ = rom_file(tmp_path / "games")
    shared = tmp_path / "shared-data"
    monkeypatch.setenv("DMG_CARTOGRAPHER_DIR", str(shared))
    ctl.load_rom(str(path))
    assert ctl.status()["data_dir"] == str(shared)
    assert (shared / ("puzzle-" + ctl._rom_sha1[:8])).is_dir()


def test_relative_or_wrong_paths_are_refused(ctl, tmp_path):
    path, _ = rom_file(tmp_path / "games")
    for bad in ["games/puzzle.gb", str(tmp_path / "missing.gb"), str(tmp_path / "games")]:
        with pytest.raises(ValueError):
            ctl.load_rom(bad)
    text = tmp_path / "games" / "notes.txt"
    text.write_text("not a rom")
    with pytest.raises(ValueError, match=r"\.gb or \.gbc"):
        ctl.load_rom(str(text))
    link = tmp_path / "games" / "link.gb"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="regular file"):
        ctl.load_rom(str(link))


def test_list_roms_is_recursive_and_relative(ctl, tmp_path):
    rom_file(tmp_path / "lib")
    rom_file(tmp_path / "lib" / "deep", name="other.gbc")
    assert sorted(ctl.list_roms(str(tmp_path / "lib"))) == ["deep/other.gbc", "puzzle.gb"]
    with pytest.raises(ValueError, match="existing absolute folder"):
        ctl.list_roms("relative/folder")


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
    path, sha = rom_file(tmp_path / "proj" / ".pyboy-mcp" / "roms")
    root = tmp_path / "proj" / ".pyboy-mcp"
    legacy_layout(root, sha, f"puzzle.gb-{sha[:12]}")
    ctl.load_rom(str(path))
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
    ctl.load_rom(str(path))  # a second load finds nothing left to move and changes nothing
    assert (rom_dir / "save.ram").read_bytes() == b"SAVE"


def test_migration_never_overwrites_a_file_already_in_the_rom_folder(ctl, tmp_path):
    path, sha = rom_file(tmp_path / "proj" / ".pyboy-mcp" / "roms")
    root = tmp_path / "proj" / ".pyboy-mcp"
    key = f"puzzle.gb-{sha[:12]}"
    legacy_layout(root, sha, key)
    rom_dir = root / ("puzzle-" + sha[:8])
    rom_dir.mkdir(parents=True)
    (rom_dir / "save.ram").write_bytes(b"NEWER")
    ctl.load_rom(str(path))
    assert (rom_dir / "save.ram").read_bytes() == b"NEWER"
    assert (root / "saves" / f"{key}.ram").read_bytes() == b"SAVE"  # left in place for the user to decide
