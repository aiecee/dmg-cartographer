"""EmulatorMixin: the emulator operations of PyBoyController."""
from __future__ import annotations

import hashlib
import io
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dmg_cartographer.core.shared import BUTTONS, MAX_ACTIONS, MAX_BATCH_FRAMES, MAX_FRAMES, ROM_EXTENSIONS, atomic_write
from dmg_cartographer import analysis
from dmg_cartographer.core import migration
from dmg_cartographer.knowledge import replay


class EmulatorMixin:
    """Methods for the emulator part of the emulator controller."""

    @staticmethod
    def _data_dir_for(project_dir: str) -> Path:
        """Return <project_dir>/.dmg-cartographer, creating roms/, saves/ and checkpoints/ inside it."""
        if not isinstance(project_dir, str) or not os.path.isabs(project_dir):
            raise ValueError(f"project_dir must be an absolute path to your project folder, got {project_dir!r}")
        project = Path(project_dir).resolve()
        if not project.is_dir():
            raise ValueError(f"project_dir does not exist or is not a folder: {project_dir!r}")
        data_dir = project / ".dmg-cartographer"
        (data_dir / "roms").mkdir(parents=True, exist_ok=True)
        return data_dir


    @staticmethod
    def _rom_folder_name(rom_file: str, sha1: str) -> str:
        """One folder per ROM: a readable slug of the file name, then the first 8 characters of its SHA-1."""
        slug = re.sub(r"[^A-Za-z0-9]+", "-", Path(rom_file).stem).strip("-")[:40] or "rom"
        return f"{slug}-{sha1[:8]}"


    @staticmethod
    def _roms(rom_dir: Path) -> dict[str, Path]:
        """Map relative POSIX path (e.g. 'pokemon/red.gb') to ROM file, skipping symlinks."""
        found: dict[str, Path] = {}
        for root, dirs, files in os.walk(rom_dir):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
            for name in files:
                path = Path(root) / name
                if path.is_symlink() or path.suffix.lower() not in ROM_EXTENSIONS:
                    continue
                found[path.relative_to(rom_dir).as_posix()] = path
        return dict(sorted(found.items(), key=lambda item: item[0].lower()))


    def list_games(self, project_dir: str) -> list[str]:
        with self._lock:
            return list(self._roms(self._data_dir_for(project_dir) / "roms"))

    def _require_game(self) -> Any:
        if self._pyboy is None:
            raise RuntimeError("No game loaded. Call game(action='load', rom_path=<absolute path to a .gb or .gbc file>).")
        return self._pyboy


    @staticmethod
    def _rom_key_for(path: Path) -> str:
        digest = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
        return f"{path.name}-{digest}"


    @staticmethod
    def _save_paths(rom_dir: Path) -> tuple[Path, Path]:
        return rom_dir / "save.ram", rom_dir / "save.rtc"


    def load_game(self, project_dir: str, rom_name: str) -> dict[str, Any]:
        with self._lock:
            # Accept only a relative path discovered under the project's ROM directory.
            # No absolute paths, traversal, symlinks, or files outside the allowlist.
            data_dir = self._data_dir_for(project_dir)
            allowed = self._roms(data_dir / "roms")
            if rom_name not in allowed:
                raise ValueError(f"ROM not found in {data_dir / 'roms'}: {rom_name!r}. Available: {list(allowed)}")
            return self._open_rom(allowed[rom_name], rom_name, data_dir)


    def add_rom(self, project_dir: str, rom_path: str) -> str:
        """Copy a ROM into <project>/.dmg-cartographer/roms/ and return its name there.

        The game then runs from the copy, so moving or deleting the original doesn't break it. A ROM already in that
        folder is used as it is. An existing file with the same name is reused only if its bytes match; otherwise
        nothing is overwritten and the call fails.
        """
        with self._lock:
            source = self._rom_file(rom_path)
            roms = self._data_dir_for(project_dir) / "roms"
            try:
                return source.resolve().relative_to(roms.resolve()).as_posix()
            except ValueError:
                pass  # outside the project's ROM folder: copy it in
            target = roms / source.name
            data = source.read_bytes()
            if target.exists() or target.is_symlink():
                if target.is_symlink() or not target.is_file() or target.read_bytes() != data:
                    raise ValueError(f"{target.name} already exists in {roms} with different contents; "
                                     "rename one of them")
                return target.name
            atomic_write(target, data)
            return target.name


    @staticmethod
    def _rom_file(rom_path: str) -> Path:
        if not isinstance(rom_path, str) or not os.path.isabs(rom_path):
            raise ValueError(f"rom_path must be an absolute path to a .gb or .gbc file, got {rom_path!r}")
        path = Path(rom_path)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"rom_path is not a regular file: {rom_path!r}")
        if path.suffix.lower() not in ROM_EXTENSIONS:
            raise ValueError("rom_path must end in .gb or .gbc")
        return path


    def _open_rom(self, path: Path, rom_name: str, data_dir: Path) -> dict[str, Any]:
        with self._lock:
            rom_bytes = path.read_bytes()
            sha1 = hashlib.sha1(rom_bytes).hexdigest()
            key = f"{path.name}-{sha1[:12]}"
            rom_dir = data_dir / self._rom_folder_name(path.name, sha1)
            rom_dir.mkdir(parents=True, exist_ok=True)
            migration.migrate_legacy(data_dir, rom_dir, key, sha1)
            ram_path, rtc_path = self._save_paths(rom_dir)
            ram_file = ram_path.open("rb") if ram_path.is_file() else None
            rtc_file = rtc_path.open("rb") if rtc_path.is_file() else None
            try:
                factory = self._factory
                if factory is None:
                    from pyboy import PyBoy
                    factory = PyBoy
                next_pyboy = factory(
                    str(path), window="null", sound_emulated=False,
                    ram_file=ram_file, rtc_file=rtc_file,
                )
            finally:
                if ram_file is not None:
                    ram_file.close()
                if rtc_file is not None:
                    rtc_file.close()
            try:
                next_pyboy.set_emulation_speed(0)  # unthrottled; SDL2 windows need the main thread, which MCP tools never run on
            except Exception:
                next_pyboy.stop(save=False)
                raise
            if self._pyboy is not None:
                try:
                    self._stop_and_save()
                except Exception:
                    next_pyboy.stop(save=False)
                    raise
            self._pyboy = next_pyboy
            self._hooks.clear()  # hooks belong to the emulator instance they were registered on
            self._rom_name = rom_name
            self._rom_key = key
            self._rom_sha1 = sha1
            self._rom_path = path
            self._data_dir = data_dir
            self._rom_dir = rom_dir
            self._frames = 0
            self._held.clear()
            self._screen_fresh = False
            # Snapshots and experiments from a different ROM can't be compared with this one.
            discarded = 0
            for store in (self._snapshots, self._experiments, self._searches, self._results):
                for ref in [r for r, item in store.items() if item["rom_key"] != key]:
                    del store[ref]
                    discarded += 1
            restored = self._load_history()
            return {**self.status(), "discarded_snapshots_and_experiments": discarded,
                    "restored_from_history": restored}


    def _stop_and_save(self) -> None:
        """Stop the loaded emulator, writing battery saves to saves/ only when the cartridge produced data."""
        ram = io.BytesIO()
        rtc = io.BytesIO()
        self._pyboy.stop(save=True, ram_file=ram, rtc_file=rtc)
        ram_path, rtc_path = self._save_paths(self._rom_dir)
        if ram.getvalue():
            atomic_write(ram_path, ram.getvalue())
        if rtc.getvalue():
            atomic_write(rtc_path, rtc.getvalue())


    def close_game(self) -> dict[str, Any]:
        with self._lock:
            if self._pyboy is not None:
                self._stop_and_save()
            self._pyboy = None
            self._hooks.clear()
            self._rom_name = None
            self._rom_key = None
            self._rom_sha1 = None
            self._rom_path = None
            self._data_dir = None
            self._rom_dir = None
            self._frames = 0
            self._held.clear()
            return self.status()


    def status(self) -> dict[str, Any]:
        with self._lock:
            game = self._pyboy
            return {
                "loaded": game is not None,
                "rom": self._rom_name,
                "data_dir": str(self._data_dir) if self._data_dir is not None else None,
                "rom_folder": str(self._rom_dir) if self._rom_dir is not None else None,
                "screen_fresh": self._screen_fresh if game is not None else None,
                "snapshots": len(self._snapshots),
                "experiments": len(self._experiments),
                "cartridge_title": str(game.cartridge_title) if game is not None else None,
                "timeline_frame": self._frames if game is not None else None,
                "held_buttons": sorted(self._held),
                "paused_between_calls": True,
            }


    @staticmethod
    def _validate_action(frames: int, press: list[str] | None, release: list[str] | None) -> tuple[list[str], list[str]]:
        if isinstance(frames, bool) or not isinstance(frames, int) or not (1 <= frames <= MAX_FRAMES):
            raise ValueError(f"frames must be an integer from 1 to {MAX_FRAMES}")
        p = list(press or [])
        r = list(release or [])
        if any(not isinstance(b, str) or b not in BUTTONS for b in p + r):
            raise ValueError(f"buttons must be one of {sorted(BUTTONS)}")
        if len(set(p)) != len(p) or len(set(r)) != len(r):
            raise ValueError("duplicate buttons in one action are not allowed")
        if set(p).intersection(r):
            raise ValueError("cannot press and release the same button in one action")
        return p, r


    def step(self, frames: int = 1, press: list[str] | None = None, release: list[str] | None = None) -> dict[str, Any]:
        """Advance EXACTLY `frames`. Press/release updates are processed during these frames.

        Buttons remain held until later released. We never sneak in a frame to
        process releases: if you need them applied without movement, tick 1 frame.
        """
        with self._lock:
            game = self._require_game()
            p, r = self._validate_action(frames, press, release)
            # A release is queued in PyBoy and applied on the next tick.
            for button in r:
                if button in self._held:
                    game.button_release(button)
                    self._held.remove(button)
            for button in p:
                if button not in self._held:
                    game.button_press(button)
                    self._held.add(button)
            keep_running = game.tick(frames, render=True, sound=False)
            self._frames += frames
            self._screen_fresh = True
            result = self.status()
            result["advanced_frames"] = frames
            result["emulator_running"] = bool(keep_running)
            return result


    def _validate_sequence(self, actions: list[dict[str, Any]]) -> list[tuple[int, list[str], list[str]]]:
        if not isinstance(actions, list) or not 1 <= len(actions) <= MAX_ACTIONS:
            raise ValueError(f"Provide 1 to {MAX_ACTIONS} actions")
        normalized = []
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError("Each action must be an object")
            frames = action.get("frames", 1)
            p, r = self._validate_action(frames, action.get("press"), action.get("release"))
            normalized.append((frames, p, r))
        total = sum(frames for frames, _, _ in normalized)
        if total > MAX_BATCH_FRAMES:
            raise ValueError(f"A sequence may advance at most {MAX_BATCH_FRAMES} frames")
        return normalized


    def run_sequence(self, actions: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            self._require_game()
            normalized = self._validate_sequence(actions)
            total = sum(frames for frames, _, _ in normalized)
            completed = 0
            for frames, p, r in normalized:
                result = self.step(frames=frames, press=p, release=r)
                completed += 1
                if not result["emulator_running"]:
                    break
            result["requested_frames"] = total
            result["completed_actions"] = completed
            return result


    def step_and_capture(self, frames: int = 1, press: list[str] | None = None,
                         release: list[str] | None = None, scale: int = 2) -> bytes:
        """Atomically step and return the resulting PNG to one MCP caller."""
        with self._lock:
            self.step(frames, press, release)
            return self.screen_png(scale)


    def game_area(self) -> dict[str, Any]:
        with self._lock:
            game = self._require_game()
            area = game.game_area()
            rows = area.tolist() if hasattr(area, "tolist") else [list(row) for row in area]
            if len(rows) > 64 or any(len(row) > 64 for row in rows):
                raise ValueError("game area exceeds 64x64 maximum")
            return {"width": len(rows[0]) if rows else 0, "height": len(rows), "tiles": rows}


    def _set_button(self, button: str, press: bool) -> dict[str, Any]:
        """Press or release one button now. No frame advances; the change applies on the next tick."""
        if button not in BUTTONS:
            raise ValueError(f"button must be one of {sorted(BUTTONS)}")
        with self._lock:
            game = self._require_game()
            if (button in self._held) == press:
                return {**self.status(), "frames_advanced": 0, "changed": False}
            if press:
                game.button_press(button)
                self._held.add(button)
            else:
                game.button_release(button)
                self._held.discard(button)
            return {**self.status(), "frames_advanced": 0, "changed": True,
                    "note": ("held now" if press else "released now") + "; no frame was advanced"}


    def press_button(self, button: str) -> dict[str, Any]:
        """Hold a button. The press reaches the emulator now and is applied on the next frame advance."""
        return self._set_button(button, True)


    def release_button(self, button: str) -> dict[str, Any]:
        return self._set_button(button, False)


    def reset(self) -> dict[str, Any]:
        """Restart the loaded ROM from frame zero. Battery saves are written first, as close_game does."""
        with self._lock:
            self._require_game()
            return {**self._open_rom(self._rom_path, self._rom_name, self._data_dir), "frames_advanced": 0,
                    "note": "restarted from frame 0; hooks cleared; checkpoints on disk are kept"}


    def _loaded_identity(self) -> tuple[str, str, Path]:
        if self._pyboy is None or self._rom_sha1 is None or self._rom_dir is None or self._rom_path is None:
            raise RuntimeError("No game loaded. Call load_game first.")
        return self._rom_sha1, self._rom_name, self._rom_dir

    def _folder_for_sha(self, sha1: str) -> Path:
        """The folder of a ROM by its SHA-1: the loaded ROM, or the one folder whose name ends in that hash prefix."""
        if self._rom_sha1 == sha1 and self._rom_dir is not None:
            return self._rom_dir
        if self._data_dir is None:
            raise RuntimeError("Load a game first so the project folder is known.")
        matches = [p for p in self._data_dir.glob(f"*-{sha1[:8]}") if p.is_dir() and not p.is_symlink()]
        if len(matches) != 1:
            raise ValueError(f"no ROM folder for hash {sha1[:12]}...; load that ROM once first")
        return matches[0]


    def _fingerprint(self) -> dict[str, Any]:
        return replay.fingerprint({r: self._region_bytes(self._pyboy, r) for r in analysis.REGIONS},
                                  self._frames, sorted(self._held))


    def _capture_live_state(self) -> dict[str, Any]:
        """Exact copy of the running emulator (state bytes, frame count, held buttons) for later restore."""
        game = self._require_game()
        buffer = io.BytesIO()
        game.save_state(buffer)
        return {"game": game, "state": buffer.getvalue(), "frames": self._frames,
                "held": set(self._held), "rom_name": self._rom_name, "rom_key": self._rom_key}


    def _restore_live_state(self, original: dict[str, Any]) -> None:
        """Put back the saved state. No frames run: button releases/presses queue for the next explicit tick."""
        game = original["game"]
        game.load_state(io.BytesIO(original["state"]))
        for button in BUTTONS:
            game.button_release(button)
        for button in sorted(original["held"]):
            game.button_press(button)
        self._pyboy = game
        self._rom_name = original["rom_name"]
        self._rom_key = original["rom_key"]
        self._frames = original["frames"]
        self._held = set(original["held"])
        # Restored emulator state is not guaranteed to have a freshly rendered framebuffer.
        self._screen_fresh = False


    @contextmanager
    def _restoring(self):
        """Run the block, then put the live emulator back exactly as it was, even if the block raises."""
        original = self._capture_live_state()
        try:
            yield
        finally:
            self._restore_live_state(original)


    def _region_bytes(self, game: Any, region: str) -> bytes:
        start, end = analysis.REGIONS[region]
        return bytes(int(v) for v in game.memory[start:end])
