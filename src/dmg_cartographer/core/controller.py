"""PyBoyController: one emulator instance, shared by every tool group (raw and investigate)."""
from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from dmg_cartographer.core.checkpoints import CheckpointMixin
from dmg_cartographer.core.debugging import DebugMixin
from dmg_cartographer.core.emulator import EmulatorMixin
from dmg_cartographer.core.experiments import ExperimentMixin
from dmg_cartographer.core.knowledge import KnowledgeMixin
from dmg_cartographer.core.memory import MemoryMixin
from dmg_cartographer.core.screen import ScreenMixin
from dmg_cartographer.core.search_sessions import SearchSessionMixin
from dmg_cartographer.core.shared import DEFAULT_RETENTION
from dmg_cartographer.core.snapshots import SnapshotMixin
from dmg_cartographer.core.scenes import SceneMixin
from dmg_cartographer.core.state_map_mixin import StateMapMixin


class PyBoyController(
    EmulatorMixin,
    CheckpointMixin,
    ScreenMixin,
    SnapshotMixin,
    SceneMixin,
    StateMapMixin,
    SearchSessionMixin,
    MemoryMixin,
    ExperimentMixin,
    KnowledgeMixin,
    DebugMixin,
):
    """Owns the single PyBoy instance and its lock. Behaviour is split across the mixins."""

    STALE_SCREEN = ("screen buffer may be stale: no frame has been rendered since load_game/load_checkpoint; "
                    "advance at least 1 frame for a freshly rendered screen")


    def __init__(self, pyboy_factory: Callable[..., Any] | None = None,
                 max_snapshots: int = DEFAULT_RETENTION, max_experiments: int = DEFAULT_RETENTION) -> None:
        if not 1 <= max_snapshots <= 512 or not 1 <= max_experiments <= 512:
            raise ValueError("retention limits must be 1..512")
        self._factory = pyboy_factory
        # In-memory only. Each snapshot is ~8 KB of RAM plus a 160x144 RGB frame (~70 KB).
        self._snapshots: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._experiments: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._searches: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max_snapshots = max_snapshots
        self._max_experiments = max_experiments
        self._next_id = {"snap": 1, "exp": 1, "search": 1, "result": 1}
        # Stored branch and replay results, by result id (in memory, this session only).
        self._results: OrderedDict[str, dict[str, Any]] = OrderedDict()
        # False until a rendered tick follows load_game/load_checkpoint: the screen buffer may be stale.
        self._screen_fresh = False
        self._lock = threading.RLock()
        self._pyboy: Any | None = None
        self._rom_name: str | None = None
        self._rom_key: str | None = None
        self._rom_sha1: str | None = None
        # Execution hooks registered on the current emulator instance: "bank:address" -> record.
        self._hooks: dict[str, dict[str, Any]] = {}
        self._rom_path: Path | None = None
        # The loaded game's <project>/.pyboy-mcp folder; saves and checkpoints follow it.
        self._data_dir: Path | None = None
        # The loaded game's folder inside .pyboy-mcp: <slug>-<sha1[:8]>. Checkpoints, scenes, maps, saves and
        # knowledge files all live under it.
        self._rom_dir: Path | None = None
        self._frames = 0
        self._held: set[str] = set()

