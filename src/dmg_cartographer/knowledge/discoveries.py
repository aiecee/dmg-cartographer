"""Persistent, human-readable discovery store (one JSON file per ROM hash). No emulator access.

A discovery is a hypothesis about a game. Saving one never verifies it: "verified" needs at least one
existing experiment or replay that supports it, and "disproven" needs one that contradicts it.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from dmg_cartographer.core.shared import atomic_write
from dmg_cartographer import search

STATUSES = ("hypothesis", "verified", "disproven")
CONFIDENCE = ("low", "medium", "high")
REGIONS = ("WRAM", "HRAM")
NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
ID_PATTERN = re.compile(r"^(snap|exp|search)-\d+$")
MAX_EVIDENCE = 50
MAX_LABELS = 8
MAX_DESCRIPTION = 500


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _refs(values: Any, label: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > MAX_EVIDENCE:
        raise ValueError(f"{label} must be a list of up to {MAX_EVIDENCE} references")
    out = []
    for v in values:
        if not isinstance(v, str) or not 1 <= len(v) <= 128:
            raise ValueError(f"each {label} entry must be a string of 1-128 characters")
        out.append(v)
    return out


def validate_discovery(fields: dict[str, Any], *, existing: dict[str, Any] | None,
                       checkable_refs: Iterable[str]) -> dict[str, Any]:
    """Return a normalised discovery. `checkable_refs` lists the experiment/replay refs that really exist."""
    known = set(checkable_refs)
    name = fields["name"]
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise ValueError("name must start with a letter and use letters, digits, '_' or '-' (max 64)")
    if ID_PATTERN.match(name):
        raise ValueError(f"name {name!r} looks like a snapshot/experiment/search id; choose another name")
    address = fields.get("address")
    if address is not None and (isinstance(address, bool) or not isinstance(address, int)
                                or not 0 <= address <= 0xFFFF):
        raise ValueError("address must be an integer 0x0000-0xFFFF or null")
    region = fields.get("region")
    if region is not None and region not in REGIONS:
        raise ValueError(f"region must be one of {list(REGIONS)} or null")
    if address is not None and region is None:
        raise ValueError("a discovery with an address needs a region (WRAM or HRAM)")
    data_type = fields.get("data_type")
    if data_type is not None and data_type not in search.DTYPES:
        raise ValueError(f"data_type must be one of {list(search.DTYPES)} or null")
    bank = fields.get("bank")
    if bank is not None and (isinstance(bank, bool) or not isinstance(bank, int) or not 0 <= bank <= 7):
        raise ValueError("bank must be an integer 0-7 or null (null = the bank selected when it was observed)")
    confidence = fields.get("confidence", "low")
    if confidence not in CONFIDENCE:
        raise ValueError(f"confidence must be one of {list(CONFIDENCE)}")
    status = fields.get("status", "hypothesis")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {list(STATUSES)}")
    description = fields.get("description", "") or ""
    if not isinstance(description, str) or len(description) > MAX_DESCRIPTION:
        raise ValueError(f"description must be a string of at most {MAX_DESCRIPTION} characters")
    labels = fields.get("labels") or []
    if not isinstance(labels, list) or len(labels) > MAX_LABELS or not all(
            isinstance(x, str) and 1 <= len(x) <= 32 for x in labels):
        raise ValueError(f"labels must be up to {MAX_LABELS} strings of 1-32 characters")

    evidence = _refs(fields.get("evidence"), "evidence")
    counter = _refs(fields.get("counterexamples"), "counterexamples")
    if existing is not None:
        evidence = list(dict.fromkeys(existing.get("evidence", []) + evidence))
        counter = list(dict.fromkeys(existing.get("counterexamples", []) + counter))

    checkable_support = [r for r in evidence if r in known]
    checkable_counter = [r for r in counter if r in known]
    if status == "verified":
        if not checkable_support:
            raise ValueError("verified needs at least one existing experiment or replay in evidence "
                             "(e.g. 'exp-0003' or 'replay:left_check'); saving a claim alone does not verify it")
        if set(checkable_support) & set(checkable_counter):
            raise ValueError("a reference cannot both support and contradict the discovery")
    if status == "disproven" and not checkable_counter:
        raise ValueError("disproven needs at least one existing experiment or replay in counterexamples")

    stamp = now()
    record = {
        "name": name, "address": None if address is None else f"0x{address:04X}", "region": region,
        "data_type": data_type, "bank": bank, "confidence": confidence, "status": status,
        "description": description, "labels": labels,
        "evidence": evidence, "counterexamples": counter,
        "checkable_support": checkable_support, "checkable_counterexamples": checkable_counter,
        "created": existing["created"] if existing else stamp, "updated": stamp,
    }
    if existing is not None and existing.get("status") != status:
        record["status_changed"] = stamp
    return record


class DiscoveryStore:
    """One JSON file per ROM SHA-1 under the project's .pyboy-mcp/discoveries/ folder."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def _path(self, rom_sha1: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{40}", rom_sha1):
            raise ValueError("invalid ROM hash")
        return self.directory / f"{rom_sha1}.json"

    def read(self, rom_sha1: str, rom_file: str | None = None, title: str | None = None) -> dict[str, Any]:
        path = self._path(rom_sha1)
        if not path.is_file():
            return {"rom_sha1": rom_sha1, "rom_file": rom_file, "title": title, "discoveries": {}}
        return json.loads(path.read_text("utf-8"))

    def write(self, rom_sha1: str, data: dict[str, Any]) -> None:
        payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
        atomic_write(self._path(rom_sha1), payload.encode("utf-8"))

    def all_roms(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted(p.stem for p in self.directory.glob("*.json") if re.fullmatch(r"[0-9a-f]{40}", p.stem))


def matches(record: dict[str, Any], *, address: int | None, region: str | None, label: str | None,
            confidence: str | None, status: str | None) -> bool:
    if address is not None and record.get("address") != f"0x{address:04X}":
        return False
    if region is not None and record.get("region") != region:
        return False
    if label is not None and label not in record.get("labels", []):
        return False
    if confidence is not None and record.get("confidence") != confidence:
        return False
    if status is not None and record.get("status") != status:
        return False
    return True


def summary(record: dict[str, Any], include_evidence: bool) -> dict[str, Any]:
    keys = ("name", "address", "region", "data_type", "bank", "confidence", "status", "labels", "description",
            "updated")
    out = {k: record.get(k) for k in keys}
    out["evidence_count"] = len(record.get("evidence", []))
    out["counterexample_count"] = len(record.get("counterexamples", []))
    if include_evidence:
        out["evidence"] = record.get("evidence", [])
        out["counterexamples"] = record.get("counterexamples", [])
    return out
