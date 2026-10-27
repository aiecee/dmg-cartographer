"""Constants and helpers shared by the core mixins and the knowledge store."""
from __future__ import annotations

import io
import os
import re
import tempfile
from pathlib import Path

from dmg_cartographer import graphics

BUTTONS = frozenset({"a", "b", "start", "select", "up", "down", "left", "right"})

ROM_EXTENSIONS = frozenset({".gb", ".gbc"})

NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$")

MAX_FRAMES = 600

MAX_ACTIONS = 25

MAX_BATCH_FRAMES = 1800

MAX_MEMORY_READ = 256
MAX_RESULTS = 64

ID_PATTERN = re.compile(r"^(snap|exp)-\d+$")

MAX_LABEL = 64

DEFAULT_RETENTION = 64

MAX_SEARCH_SESSIONS = 16

MAX_BRANCHES = 8

BRANCH_COMPARE = ("memory", "screen")

def _png(image) -> bytes:
    data = io.BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


def atomic_write(path: Path, data: bytes) -> None:
    """Write bytes via a temp file in the same folder, then rename over `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def tile_rect_in_map(x: int, y: int, width: int, height: int) -> bool:
    """True when a tile rectangle lies inside the 32x32 tile map."""
    return x >= 0 and y >= 0 and width >= 1 and height >= 1 and x + width <= 32 and y + height <= 32


def read_oam(game) -> tuple[bytes, int]:
    """Sprite table bytes and the LCDC control byte, read through PyBoy's memory API."""
    oam = bytes(int(v) for v in game.memory[graphics.OAM_START:graphics.OAM_END])
    return oam, int(game.memory[graphics.LCDC])


def read_registers(game) -> dict[str, int]:
    rf = game.register_file
    return {"A": rf.A, "F": rf.F, "B": rf.B, "C": rf.C, "D": rf.D, "E": rf.E, "HL": rf.HL, "SP": rf.SP, "PC": rf.PC}
