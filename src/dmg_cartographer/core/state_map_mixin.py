"""StateMapMixin: read a map.json against the loaded game."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from dmg_cartographer.state_map import load_map, read_state


class StateMapMixin:
    """Methods that apply a state map to the loaded game. Advances no frames."""

    def validate_state_map(self, path: str) -> dict[str, Any]:
        state_map = load_map(Path(path))
        return {"path": path, "format": "dmg-cartographer-map", "format_version": 1, "rom": state_map.rom,
                "rom_sha1": state_map.rom_sha1, "field_count": len(state_map.fields),
                "fields": [{"name": f.name, "address": f"0x{f.address:04X}", "type": f.type,
                            "shape": list(f.shape) if f.shape else None, "status": f.status}
                           for f in state_map.fields]}

    def read_state_map(self, path: str, labelled: bool = False) -> dict[str, Any]:
        with self._lock:
            game = self._require_game()
            state_map = load_map(Path(path))
            values = read_state(game, state_map, rom_sha1=self._rom_sha1, labelled=labelled)
            return {"path": path, "labelled": labelled, "values": values}

    def default_map_path(self) -> Path:
        """Where the loaded ROM's map lives unless a path is given: <rom folder>/maps/map.json."""
        with self._lock:
            self._require_game()
            return self._rom_dir / "maps" / "map.json"
