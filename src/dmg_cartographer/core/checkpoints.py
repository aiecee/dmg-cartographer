"""CheckpointMixin: the checkpoints operations of PyBoyController."""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from dmg_cartographer.core.shared import BUTTONS, NAME_PATTERN, atomic_write


class CheckpointMixin:
    """Methods for the checkpoints part of the emulator controller."""

    def _checkpoint_paths(self, name: str) -> tuple[Path, Path]:
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            raise ValueError("checkpoint name: 1-48 alphanumeric, hyphen or underscore characters")
        self._require_game()
        # Each game gets its own checkpoint directory, and there is no path input.
        folder = self._rom_dir / "checkpoints"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / (name + ".state"), folder / (name + ".json")


    def save_checkpoint(self, name: str) -> dict[str, Any]:
        with self._lock:
            game = self._require_game()
            state_path, meta_path = self._checkpoint_paths(name)
            buffer = io.BytesIO()
            game.save_state(buffer)
            atomic_write(state_path, buffer.getvalue())
            atomic_write(meta_path, json.dumps({"frames": self._frames}).encode("utf-8"))
            return {"checkpoint": name, "saved": True, "frames": self._frames}


    def load_checkpoint(self, name: str) -> dict[str, Any]:
        with self._lock:
            game = self._require_game()
            state_path, meta_path = self._checkpoint_paths(name)
            if not state_path.is_file() or state_path.is_symlink():
                raise ValueError(f"checkpoint {name!r} does not exist for this ROM")
            with state_path.open("rb") as f:
                game.load_state(f)
            # Queue release of every possible button. PyBoy processes these on
            # the NEXT requested tick, so restoration adds no hidden frames.
            # Callers should press desired buttons explicitly on their next step.
            for button in BUTTONS:
                game.button_release(button)
            self._held.clear()
            self._screen_fresh = False
            self._frames = json.loads(meta_path.read_text("utf-8"))["frames"] if meta_path.is_file() else 0
            return {"checkpoint": name, "loaded": True, **self.status()}


    def list_checkpoints(self) -> list[str]:
        with self._lock:
            self._require_game()
            folder = self._rom_dir / "checkpoints"
            if not folder.is_dir():
                return []
            return sorted(p.stem for p in folder.glob("*.state") if p.is_file() and not p.is_symlink() and NAME_PATTERN.fullmatch(p.stem))
