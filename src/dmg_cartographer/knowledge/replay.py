"""Deterministic replay records: fingerprints, comparison, storage. No emulator access.

A fingerprint covers WRAM, HRAM, frame count and held buttons, read through PyBoy's public memory API. It does
NOT cover everything inside the emulator (VRAM, OAM, timers, audio), so a match is strong evidence, not proof.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dmg_cartographer.core.shared import atomic_write

MAX_STEPS = 25
MAX_EXPECT = 16
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def fingerprint(regions: dict[str, bytes], frame: int, held: list[str]) -> dict[str, Any]:
    parts = {name: hashlib.sha256(data).hexdigest()[:16] for name, data in sorted(regions.items())}
    combined = hashlib.sha256(json.dumps([parts, frame, sorted(held)], sort_keys=True).encode()).hexdigest()
    return {"frame": frame, "held": sorted(held), "regions": parts, "digest": combined[:32]}


def compare(recorded: dict[str, Any], observed: dict[str, Any]) -> list[dict[str, Any]]:
    """Every field that differs. An empty list means the replay matched."""
    mismatches = []
    for key in ("frame", "held", "digest"):
        if recorded.get(key) != observed.get(key):
            mismatches.append({"field": key, "recorded": recorded.get(key), "observed": observed.get(key)})
    for region in sorted(set(recorded.get("regions", {})) | set(observed.get("regions", {}))):
        if recorded.get("regions", {}).get(region) != observed.get("regions", {}).get(region):
            mismatches.append({"field": f"regions.{region}", "recorded": recorded.get("regions", {}).get(region),
                               "observed": observed.get("regions", {}).get(region)})
    return mismatches


def validate_name(name: Any) -> str:
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise ValueError("replay name must be 1-64 letters, digits, '_' or '-'")
    return name


class ReplayStore:
    """One JSON file per replay under <data>/replays/<rom_sha1>/<name>.json."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def _path(self, rom_sha1: str, name: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{40}", rom_sha1):
            raise ValueError("invalid ROM hash")
        return self.directory / rom_sha1 / f"{validate_name(name)}.json"

    def read(self, rom_sha1: str, name: str) -> dict[str, Any] | None:
        path = self._path(rom_sha1, name)
        return json.loads(path.read_text("utf-8")) if path.is_file() else None

    def write(self, rom_sha1: str, name: str, record: dict[str, Any]) -> None:
        payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
        atomic_write(self._path(rom_sha1, name), payload.encode("utf-8"))

    def list(self, rom_sha1: str) -> list[str]:
        folder = self.directory / rom_sha1
        return sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
