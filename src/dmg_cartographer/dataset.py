"""Scene dataset format: write and read. Standard library only, so a training loop can import it without MCP.

A dataset is a folder:

    manifest.json        format name, format_version, ROM identity, scene count, field names
    scenes.jsonl         one JSON object per line, sorted by name
    images/<name>.png    the 160x144 frame for each scene
    states/<name>.state  optional emulator save states (only when exported with include_states)

Each line of scenes.jsonl has: name, frame, rom_sha1, held_buttons, screen_fresh, note, values, image, state.
`values` is the decoded field dictionary exactly as the scene tool saved it. `image` and `state` are paths relative
to the dataset folder; `state` is null when states were not exported.

Format version 1. Readers reject any other format_version rather than guessing.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

FORMAT = "dmg-cartographer-scenes"
FORMAT_VERSION = 1
MANIFEST = "manifest.json"
INDEX = "scenes.jsonl"
IMAGES = "images"
STATES = "states"


class DatasetError(ValueError):
    """The folder is not a dataset this reader understands, or one of its files is missing."""


@dataclass(frozen=True)
class Scene:
    name: str
    frame: int
    rom_sha1: str
    held_buttons: list[str]
    screen_fresh: bool
    note: str
    values: dict[str, Any]
    image: Path
    state: Path | None


def write_dataset(folder: Path, *, rom: str, rom_sha1: str, records: list[dict[str, Any]],
                  image_sources: dict[str, Path], state_sources: dict[str, Path] | None) -> dict[str, Any]:
    """Write a dataset into `folder`, replacing any earlier export there. Returns the manifest.

    records are scene.json contents. image_sources and state_sources map scene name to the file to copy.
    Only this dataset's own images/ and states/ folders are cleared; nothing else in `folder` is touched.
    """
    folder.mkdir(parents=True, exist_ok=True)
    for sub in (IMAGES, STATES):
        shutil.rmtree(folder / sub, ignore_errors=True)
    (folder / IMAGES).mkdir()
    if state_sources is not None:
        (folder / STATES).mkdir()
    rows = []
    fields: set[str] = set()
    for record in sorted(records, key=lambda r: r["name"]):
        name = record["name"]
        image = f"{IMAGES}/{name}.png"
        shutil.copyfile(image_sources[name], folder / image)
        state = None
        if state_sources is not None:
            state = f"{STATES}/{name}.state"
            shutil.copyfile(state_sources[name], folder / state)
        fields.update(record["values"])
        rows.append({"name": name, "frame": record["frame"], "rom_sha1": record["rom_sha1"],
                     "held_buttons": record["held_buttons"], "screen_fresh": record["screen_fresh"],
                     "note": record["note"], "values": record["values"], "image": image, "state": state})
    (folder / INDEX).write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    manifest = {"format": FORMAT, "format_version": FORMAT_VERSION, "rom": rom, "rom_sha1": rom_sha1,
                "scene_count": len(rows), "scene_schema_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "index": INDEX, "images": IMAGES, "states": STATES if state_sources is not None else None,
                "fields": sorted(fields)}
    (folder / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def read_manifest(folder: str | Path) -> dict[str, Any]:
    path = Path(folder) / MANIFEST
    if not path.is_file():
        raise DatasetError(f"no {MANIFEST} in {folder}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT:
        raise DatasetError(f"not a {FORMAT} dataset: format is {manifest.get('format')!r}")
    if manifest.get("format_version") != FORMAT_VERSION:
        raise DatasetError(f"unsupported format_version {manifest.get('format_version')!r}; this reader reads "
                           f"version {FORMAT_VERSION}")
    return manifest


def iter_scenes(folder: str | Path) -> Iterator[Scene]:
    """Yield every scene in the dataset, in name order. Raises DatasetError if a listed file is missing."""
    root = Path(folder)
    manifest = read_manifest(root)
    with (root / manifest["index"]).open(encoding="utf-8") as index:
        for line in index:
            if not line.strip():
                continue
            row = json.loads(line)
            image = root / row["image"]
            state = root / row["state"] if row["state"] is not None else None
            for path in (image, state):
                if path is not None and not path.is_file():
                    raise DatasetError(f"scene {row['name']!r} lists a missing file: {path.name}")
            yield Scene(name=row["name"], frame=row["frame"], rom_sha1=row["rom_sha1"],
                        held_buttons=row["held_buttons"], screen_fresh=row["screen_fresh"], note=row["note"],
                        values=row["values"], image=image, state=state)
