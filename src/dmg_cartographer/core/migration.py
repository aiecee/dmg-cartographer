"""One-time move of data from the flat layout (earlier versions) into a ROM's own folder.

Earlier versions kept each kind of file in a shared folder, keyed by ROM:

    saves/<file>.ram|.rtc            checkpoints/<file>/            scenes/<file>/       datasets/<file>/
    discoveries/<sha1>.json          replays/<sha1>/               history/<sha1>.json

Each one moves into <ROM folder>/ and never overwrites a file that is already there. Running it again moves
nothing. exports/ is not moved: those folders are named by the user, not by ROM, so there is no ROM to file them under.
"""
from __future__ import annotations

import shutil
from pathlib import Path


def _move(src: Path, dst: Path) -> int:
    """Move a file or folder tree into dst, keeping existing destination files. Returns the number of files moved."""
    if src.is_symlink():
        return 0
    if src.is_file():
        if dst.exists():
            return 0
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return 1
    if not src.is_dir():
        return 0
    moved = 0
    for child in sorted(src.iterdir()):
        moved += _move(child, dst / child.name)
    try:
        src.rmdir()  # only removes the folder if it is now empty
    except OSError:
        pass
    return moved


def migrate_legacy(data_root: Path, rom_dir: Path, rom_key: str, rom_sha1: str) -> int:
    """Move this ROM's files from the flat layout into rom_dir. Returns how many files moved."""
    moves = [
        (data_root / "saves" / f"{rom_key}.ram", rom_dir / "save.ram"),
        (data_root / "saves" / f"{rom_key}.rtc", rom_dir / "save.rtc"),
        (data_root / "checkpoints" / rom_key, rom_dir / "checkpoints"),
        (data_root / "scenes" / rom_key, rom_dir / "scenes"),
        (data_root / "datasets" / rom_key, rom_dir / "datasets"),
        (data_root / "discoveries" / f"{rom_sha1}.json", rom_dir / "discoveries" / f"{rom_sha1}.json"),
        (data_root / "replays" / rom_sha1, rom_dir / "replays" / rom_sha1),
        (data_root / "history" / f"{rom_sha1}.json", rom_dir / "history.json"),
    ]
    return sum(_move(src, dst) for src, dst in moves)
