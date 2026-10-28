import io
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from dmg_cartographer.core.controller import PyBoyController


class FakeTileMap:
    def __getitem__(self, xy):
        x, y = xy
        return x + y * 32


class FakeArea:
    def tolist(self):
        return [[1, 2], [3, 4]]


class FakeScreen:
    @property
    def image(self):
        return Image.new("RGBA", (160, 144), (50, 100, 100, 255))


class FakePyBoy:
    instances = []

    def __init__(self, rom, window, sound_emulated, ram_file=None, rtc_file=None):
        self.rom = rom
        self.window = window
        self.sound_emulated = sound_emulated
        self.loaded_ram = ram_file.read() if ram_file is not None else None
        self.cartridge_title = "FAKE GAME"
        self.screen = FakeScreen()
        self.tilemap_background = FakeTileMap()
        self.tilemap_window = FakeTileMap()
        self.memory = [i % 256 for i in range(65536)]
        self.actions = []
        self.stopped = False
        self.ticks = 0
        self.restored = False
        FakePyBoy.instances.append(self)

    def set_emulation_speed(self, speed):
        self.actions.append(("speed", speed))

    def button_press(self, button):
        self.actions.append(("press", button))

    def button_release(self, button):
        self.actions.append(("release", button))

    def tick(self, frames, render, sound):
        self.ticks += frames
        self.actions.append(("tick", frames, render, sound))
        return True

    def game_area(self):
        return FakeArea()

    def save_state(self, buffer):
        buffer.write(f"frames={self.ticks}".encode())

    def load_state(self, buffer):
        self.restored = buffer.read().startswith(b"frames=")

    def stop(self, save=True, ram_file=None, rtc_file=None):
        self.stopped = True
        if save and ram_file is not None:
            ram_file.write(b"SAVE")


@pytest.fixture
def project(tmp_path):
    roms = tmp_path / ".dmg-cartographer" / "roms"
    (roms / "nested").mkdir(parents=True)
    (roms / "test.gb").write_bytes(b"test rom placeholder")
    (roms / "second.gbc").write_bytes(b"test rom placeholder")
    (roms / "readme.txt").write_text("not a rom")
    (roms / "nested" / "deep.gbc").write_bytes(b"deep rom placeholder")
    return str(tmp_path)


@pytest.fixture
def controller():
    return PyBoyController(FakePyBoy)


def test_game_list_and_not_loaded(controller, project):
    assert controller.list_games(project) == ["nested/deep.gbc", "second.gbc", "test.gb"]
    assert controller.status()["loaded"] is False
    with pytest.raises(RuntimeError):
        controller.step(1)


def test_no_automatic_ticks_and_exact_frames(controller, project):
    controller.load_game(project, "test.gb")
    assert controller._pyboy.ticks == 0
    assert controller.step(7, press=["right"])["advanced_frames"] == 7
    assert controller.step(4)["held_buttons"] == ["right"]
    assert controller.step(1, release=["right"])["held_buttons"] == []
    assert controller._pyboy.ticks == 12
    assert controller._pyboy.actions.count(("press", "right")) == 1
    assert controller._pyboy.actions.count(("release", "right")) == 1
    assert controller.status()["timeline_frame"] == 12


def test_invalid_inputs_do_not_advance(controller, project):
    controller.load_game(project, "test.gb")
    for action in [
        {"frames": 0}, {"frames": 601}, {"frames": True},
        {"frames": 2, "press": ["fire"]},
        {"frames": 1, "press": ["a"], "release": ["a"]},
        {"frames": 1, "press": ["a", "a"]},
    ]:
        with pytest.raises(ValueError):
            controller.step(**action)
    assert controller._pyboy.ticks == 0


def test_sequence_validated_and_counts(controller, project):
    controller.load_game(project, "test.gb")
    with pytest.raises(ValueError):
        controller.run_sequence([{"frames": 5}, {"frames": 601}])
    assert controller._pyboy.ticks == 0
    result = controller.run_sequence([
        {"frames": 5, "press": ["right"]},
        {"frames": 10},
        {"frames": 2, "release": ["right"]},
    ])
    assert result["completed_actions"] == 3
    assert result["requested_frames"] == 17
    assert result["timeline_frame"] == 17
    assert result["held_buttons"] == []


def test_load_restricts_paths_and_symlinks(controller, project, tmp_path):
    (tmp_path / "outside.gb").write_bytes(b"out")
    (tmp_path / ".dmg-cartographer" / "roms" / "linked.gb").symlink_to(tmp_path / "outside.gb")
    for rom in ["../outside.gb", "linked.gb", "/tmp/outside.gb", "missing.gb"]:
        with pytest.raises(ValueError):
            controller.load_game(project, rom)
    assert controller.status()["loaded"] is False


def test_switch_stops_old_game(controller, project):
    controller.load_game(project, "test.gb")
    first = controller._pyboy
    controller.step(10, press=["a"])
    result = controller.load_game(project, "second.gbc")
    assert first.stopped is True
    assert result["timeline_frame"] == 0
    assert result["held_buttons"] == []
    controller.close_game()
    assert controller.status()["loaded"] is False


def test_screen_area_tilemap_memory(controller, project):
    controller.load_game(project, "test.gb")
    png = controller.screen_png(scale=2)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(io.BytesIO(png)) as im:
        assert im.size == (320, 288)
    assert controller.game_area()["tiles"] == [[1, 2], [3, 4]]
    assert controller.tilemap("background", 5, 6, 2, 2)["tiles"] == [[197, 198], [229, 230]]
    assert controller.read_memory(0xC000, 3)["bytes"] == [0, 1, 2]
    assert controller.read_memory("0xC000", 3)["bytes"] == [0, 1, 2]  # same string form as write and decode
    with pytest.raises(ValueError):
        controller.read_memory(65535, 2)
    with pytest.raises(ValueError, match="invalid address"):
        controller.read_memory("C000", 1)
    with pytest.raises(ValueError):
        controller.tilemap(width=33)


def test_checkpoint_roundtrip_and_name_validation(controller, project):
    controller.load_game(project, "test.gb")
    controller.step(23, press=["a"])
    controller.save_checkpoint("before_boss")
    assert controller.list_checkpoints() == ["before_boss"]
    controller.step(8, release=["a"])
    result = controller.load_checkpoint("before_boss")
    assert controller._pyboy.restored is True
    assert result["timeline_frame"] == 23
    assert result["held_buttons"] == []
    assert len([a for a in controller._pyboy.actions if a[0] == "release"]) >= 8
    for name in ["../no", "bad/name", "", "X" * 49]:
        with pytest.raises(ValueError):
            controller.save_checkpoint(name)


def test_checkpoint_is_game_specific(controller, project):
    controller.load_game(project, "test.gb")
    controller.save_checkpoint("start")
    controller.load_game(project, "second.gbc")
    assert controller.list_checkpoints() == []
    with pytest.raises(ValueError):
        controller.load_checkpoint("start")


def test_step_and_capture_is_one_exact_advance(controller, project):
    controller.load_game(project, "test.gb")
    png = controller.step_and_capture(9, press=["b"], scale=1)
    assert png.startswith(b"\x89PNG")
    assert controller._pyboy.ticks == 9
    assert controller.status()["held_buttons"] == ["b"]


def test_nested_rom_loads_by_relative_path_and_rejects_other_paths(controller, project, tmp_path):
    assert controller.load_game(project, "nested/deep.gbc")["rom"] == "nested/deep.gbc"
    assert (tmp_path / ".dmg-cartographer" / "roms").is_dir()
    assert controller.status()["rom_folder"].endswith("-" + controller._rom_sha1[:8])
    for bad in ["../test.gb", str(tmp_path / ".dmg-cartographer/roms/test.gb"), "missing.gb"]:
        with pytest.raises(ValueError):
            controller.load_game(project, bad)


def test_battery_save_is_written_to_the_rom_folder_and_restored(controller, project, tmp_path):
    controller.load_game(project, "test.gb")
    controller.close_game()
    saves = list((tmp_path / ".dmg-cartographer").glob("test-*/save.ram"))
    assert [p.read_bytes() for p in saves] == [b"SAVE"]

    controller.load_game(project, "test.gb")
    assert controller._pyboy.loaded_ram == b"SAVE"


def test_project_dir_must_be_an_existing_absolute_folder(controller, project, tmp_path):
    for bad in ["relative/project", ".", str(tmp_path / "missing")]:
        with pytest.raises(ValueError):
            controller.list_games(bad)
        with pytest.raises(ValueError):
            controller.load_game(bad, "test.gb")
    assert not (tmp_path / "missing").exists()


def test_new_project_gets_empty_pyboy_folder_and_status_reports_it(controller, tmp_path):
    other = tmp_path / "other-project"
    other.mkdir()
    assert controller.list_games(str(other)) == []
    assert sorted(p.name for p in (other / ".dmg-cartographer").iterdir()) == ["roms"]
    assert controller.status()["data_dir"] is None


def test_always_headless_and_unthrottled_across_reset(controller, project):
    controller.load_game(project, "test.gb")
    assert controller._pyboy.window == "null" and ("speed", 0) in controller._pyboy.actions
    controller.reset()
    assert controller._pyboy.window == "null"
