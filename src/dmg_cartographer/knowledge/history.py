"""Persistent experiment and result history, one JSON file per ROM. Pure file handling: no emulator access.

Experiments keep their changes as JSON lists (JSON object keys must be strings), and are decoded back on load.
Snapshots and search sessions are not persisted: snapshots hold full frames and search sessions can hold
thousands of candidates, so both stay in memory for the session.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from dmg_cartographer.core.shared import atomic_write

HISTORY_VERSION = 1


def history_path(rom_dir: Path, rom_sha1: str) -> Path:
    """The experiment history for one ROM: a file in that ROM's folder."""
    if not re.fullmatch(r"[0-9a-f]{40}", rom_sha1):
        raise ValueError("invalid ROM hash")
    return Path(rom_dir) / "history.json"


def encode_experiment(exp: dict[str, Any]) -> dict[str, Any]:
    out = dict(exp)
    changes = exp.get("changes")
    out["changes"] = None if changes is None else [[a, prev, cur] for a, (prev, cur) in sorted(changes.items())]
    return out


def decode_experiment(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw)
    changes = raw.get("changes")
    out["changes"] = None if changes is None else {int(a): (int(p), int(c)) for a, p, c in changes}
    return out


def write_history(path: Path, experiments: list[dict[str, Any]], results: list[dict[str, Any]],
                  next_ids: dict[str, int]) -> None:
    payload = {"version": HISTORY_VERSION, "next_ids": next_ids,
               "experiments": [encode_experiment(e) for e in experiments], "results": results}
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    atomic_write(path, text.encode("utf-8"))


def read_history(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text("utf-8"))
    if data.get("version") != HISTORY_VERSION:
        raise ValueError(f"history file {path.name} has an unsupported version")
    return {"next_ids": data.get("next_ids", {}),
            "experiments": [decode_experiment(e) for e in data.get("experiments", [])],
            "results": data.get("results", [])}
