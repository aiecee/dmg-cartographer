"""SceneMixin: labelled game states for training data.

A scene is one folder holding everything needed to recreate and read one moment of the game:

    scenes/<rom_key>/<name>/
      scene.json   schema_version, frame, rom_sha1, held buttons, note, decoded values
      capture.png  the rendered frame at 1x
      state.state  the emulator save state, so the scene can be loaded again

Values are decoded with the same field rules as memory(action="decode"), from one block of memory at `address`.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from dmg_cartographer import dataset
from dmg_cartographer.core.shared import NAME_PATTERN, atomic_write

SCENE_SCHEMA_VERSION = 1
SCENE_FILES = {"scene_json": "scene.json", "capture_png": "capture.png", "state": "state.state"}


class SceneMixin:
    """Methods for saving, listing and reading scenes."""

    def _scene_folder(self, name: str) -> Path:
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            raise ValueError("scene name: 1-48 alphanumeric, hyphen or underscore characters")
        self._require_game()
        return self._rom_dir / "scenes" / name

    def save_scene(self, name: str, address: int, length: int, fields: list[dict[str, Any]],
                   note: str = "") -> dict[str, Any]:
        """Write a scene folder from the current frame. Advances no frames; overwrites a scene of the same name."""
        if len(note) > 500:
            raise ValueError("note must be at most 500 characters")
        with self._lock:
            game = self._require_game()
            folder = self._scene_folder(name)
            values = self.decode_memory(fields, address=address, length=length, hex_bytes=None)["fields"]
            buffer = io.BytesIO()
            game.save_state(buffer)
            atomic_write(folder / SCENE_FILES["state"], buffer.getvalue())
            atomic_write(folder / SCENE_FILES["capture_png"], self.screen_png(1))
            record = {
                "schema_version": SCENE_SCHEMA_VERSION,
                "name": name,
                "rom": self._rom_name,
                "rom_sha1": self._rom_sha1,
                "frame": self._frames,
                "held_buttons": sorted(self._held),
                "screen_fresh": self._screen_fresh,
                "note": note,
                "values": values,
            }
            atomic_write(folder / SCENE_FILES["scene_json"],
                         (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8"))
            return {**record, "folder": str(folder), "files": SCENE_FILES}

    def list_scenes(self) -> list[str]:
        with self._lock:
            self._require_game()
            folder = self._rom_dir / "scenes"
            if not folder.is_dir():
                return []
            return sorted(p.name for p in folder.iterdir()
                          if p.is_dir() and not p.is_symlink() and (p / SCENE_FILES["scene_json"]).is_file())

    def read_scene(self, name: str) -> dict[str, Any]:
        with self._lock:
            path = self._scene_folder(name) / SCENE_FILES["scene_json"]
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"scene {name!r} does not exist for this ROM")
            return json.loads(path.read_text("utf-8"))

    def export_scenes(self, name: str, include_states: bool = False) -> dict[str, Any]:
        """Write every saved scene for this ROM as a versioned dataset folder under datasets/<rom>/<name>/."""
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            raise ValueError("dataset name: 1-48 alphanumeric, hyphen or underscore characters")
        with self._lock:
            self._require_game()
            names = self.list_scenes()
            if not names:
                raise ValueError("no scenes saved for this ROM; save one with scene(action='save') first")
            records = [self.read_scene(scene_name) for scene_name in names]
            for record in records:
                if record["schema_version"] != SCENE_SCHEMA_VERSION:
                    raise ValueError(f"scene {record['name']!r} has schema_version {record['schema_version']}; "
                                     f"this build exports version {SCENE_SCHEMA_VERSION}")
            base = self._rom_dir / "scenes"
            images = {scene_name: base / scene_name / SCENE_FILES["capture_png"] for scene_name in names}
            states = ({scene_name: base / scene_name / SCENE_FILES["state"] for scene_name in names}
                      if include_states else None)
            folder = self._rom_dir / "datasets" / name
            manifest = dataset.write_dataset(folder, rom=self._rom_name, rom_sha1=self._rom_sha1, records=records,
                                             image_sources=images, state_sources=states)
            return {"name": name, "folder": str(folder), "format": manifest["format"],
                    "format_version": manifest["format_version"], "scene_count": manifest["scene_count"],
                    "include_states": include_states, "manifest_file": dataset.MANIFEST,
                    "index_file": dataset.INDEX, "scenes": names}
