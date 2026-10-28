"""Raw emulator and reverse-engineering tools. Each tool groups related actions behind one name.

Every result is an Envelope (frame, rom_sha1, held_buttons, data). Image results are returned after the envelope.
Each action's data is one of the models in outputs.py, selected by its `action` field.
"""

from typing import Any, Literal

from mcp.server.mcpserver import Image

from dmg_cartographer.mcp_app import (
    Action, Button, CONTROLLER, DataType, Endianness, Envelope, LabelledRegion, MemoryFilter, MemoryRegion,
    ScreenRegion, SearchCondition, SENSITIVE_ENABLED, _expose, envelope,
)
from dmg_cartographer.outputs import (
    CodeData, CompareData, GameData, HookData, MemoryData, ScreenData, SearchData, SnapshotData, StateData,
    StepData, VideoData,
)


def _as(action: str, core: dict[str, Any]) -> dict[str, Any]:
    """Tag a core result with the action it answers, so the output union can pick the right model."""
    return {**core, "action": action}


def _with_images(data: dict[str, Any], shape: Any, *images: bytes | None) -> list:
    """Envelope first, then each non-empty PNG as an Image."""
    return [envelope(data, shape), *[Image(data=p, format="png") for p in images if p]]


@_expose("raw")
def game(action: Literal["list", "load", "close", "reset", "status"], project_dir: str | None = None,
         rom_name: str | None = None, rom_path: str | None = None) -> Envelope[GameData]:
    """Manage the loaded ROM. Everything lives in <project_dir>/.dmg-cartographer/, which is created if missing.

    Use first: load a ROM (needs project_dir) before any other tool; also status, reset and close.

    list   -> roms: ROM paths under <project_dir>/.dmg-cartographer/roms/. Needs project_dir (absolute).
    load   -> the emulator state after loading, paused at frame 0. Needs project_dir and one of:
              rom_name, a path from list; or rom_path, an absolute path to a .gb or .gbc file anywhere. A rom_path
              is copied into .dmg-cartographer/roms/ first and the game runs from the copy, so moving or deleting
              the original doesn't matter. An existing ROM of the same name with different contents is not
              overwritten; the load fails instead.
    close  -> the state after closing the ROM.
    reset  -> restarts the ROM at frame 0; clears hooks; checkpoints on disk are kept.
    status -> loaded, rom, cartridge title and timeline_frame, without changing anything.
    """
    if action == "list":
        if project_dir is None:
            raise ValueError("game(action='list') needs project_dir")
        return envelope({"action": "list", "roms": CONTROLLER.list_games(project_dir)}, GameData)
    if action == "load":
        if project_dir is None or (rom_name is None) == (rom_path is None):
            raise ValueError("game(action='load') needs project_dir and either rom_name or rom_path")
        if rom_path is not None:
            rom_name = CONTROLLER.add_rom(project_dir, rom_path)
        return envelope(_as("load", CONTROLLER.load_game(project_dir, rom_name)), GameData)
    if action == "close":
        return envelope(_as("close", CONTROLLER.close_game()), GameData)
    if action == "reset":
        return envelope(_as("reset", CONTROLLER.reset()), GameData)
    return envelope(_as("status", CONTROLLER.status()), GameData)


@_expose("raw")
def step(frames: int = 1, press: list[Button] | None = None, release: list[Button] | None = None,
         actions: list[Action] | None = None, capture: bool = False, scale: int = 2) -> list:
    """Advance the emulator. The only tool that moves time.

    Use to play the game: press buttons and let frames run to reach a menu, level or situation.

    Give either frames (1-600) with press/release, or actions (up to 25 steps, 1800 frames in total).
    Presses persist across calls until released. capture=true also returns the resulting screen as a PNG.
    """
    if actions is not None:
        data = _as("sequence", CONTROLLER.run_sequence([a.model_dump() for a in actions]))
        png = CONTROLLER.screen_png(scale) if capture else None
    elif capture:
        png = CONTROLLER.step_and_capture(frames, press, release, scale)
        data = {"action": "step", "advanced_frames": frames, "emulator_running": True, **CONTROLLER.status()}
    else:
        data = _as("step", CONTROLLER.step(frames, press, release))
        png = None
    return _with_images(data, StepData, png)


@_expose("raw")
def button(button: Button, action: Literal["press", "release"]) -> Envelope[StepData]:
    """Hold or release one button. No frame advances: the change applies on the next step.

    Use to hold a button across several steps; step(press=..., release=...) is usually simpler.
    """
    if action == "press":
        return envelope(_as("button", CONTROLLER.press_button(button)), StepData)
    return envelope(_as("button", CONTROLLER.release_button(button)), StepData)


@_expose("raw", read_only=True)
def screen(scale: int = 2, overlays: list[Literal["pixels", "tiles", "sprites", "regions"]] | None = None,
           regions: list[LabelledRegion] | None = None) -> list:
    """The current frame as a PNG (scale 1-4, nearest-neighbour).

    Use to look at the game as a picture. To read the board, pieces or HUD as data, use inspect_video.

    With overlays or regions the PNG is annotated: pixel ticks, 8px tile grid, sprite boxes, labelled regions.
    Works on a copy; the framebuffer is not changed. Never advances frames.
    """
    if overlays or regions:
        region_dicts = [r.model_dump() for r in regions] if regions else None
        png, meta = CONTROLLER.annotate_screen(overlays, region_dicts, scale)
        return _with_images(_as("annotated", meta), ScreenData, png)
    return _with_images(_as("screen", {"scale": scale}), ScreenData, CONTROLLER.screen_png(scale))


@_expose("raw", read_only=True)
def inspect_video(kind: Literal["area", "tilemap", "tiles", "sprites", "palette"],
                  layer: Literal["background", "window"] = "background", x: int = 0, y: int = 0,
                  width: int = 20, height: int = 18, indices: list[int] | None = None,
                  region: ScreenRegion | None = None, include_images: bool = False, include_grid: bool = False,
                  include_attributes: bool = False, max_results: int = 40) -> list:
    """Read the video side of the emulator. Read-only: never advances frames.

    Use to read what is on screen as data, without pixels: sprites (moving pieces, the player) and background or window tiles (boards, HUD digits, text).

    kind:
      area     -> PyBoy's generic game-area tile matrix.
      tilemap  -> tile IDs in a rectangle of the 32x32 background or window map (x, y, width, height).
      tiles    -> tile IDs with viewport placement; include_images adds tile PNGs (up to 16);
                  include_grid adds a 2x grid image; include_attributes reads CGB palette/flip bytes.
      sprites  -> decoded OAM sprites (slots 0-39, filter by indices or region); include_images adds the
                  first tile of up to 8 sprites. Background and window objects are not sprites.
      palette  -> DMG palette registers BGP, OBP0, OBP1 as raw values and shades. CGB palette RAM is not readable.
    """
    if kind == "area":
        return _with_images(_as("area", CONTROLLER.game_area()), VideoData)
    if kind == "tilemap":
        return _with_images(_as("tilemap", CONTROLLER.tilemap(layer, x, y, width, height)), VideoData)
    if kind == "tiles":
        result, images, grid = CONTROLLER.inspect_tiles(layer, x, y, width, height, include_images, include_grid,
                                                        include_attributes)
        return _with_images(_as("tiles", result), VideoData, *images, grid)
    if kind == "sprites":
        result, pngs = CONTROLLER.inspect_sprites(indices, region.model_dump() if region else None,
                                                  include_images, max_results)
        return _with_images(_as("sprites", result), VideoData, *pngs)
    return _with_images(_as("palette", CONTROLLER.get_palette()), VideoData)


@_expose("raw")
def memory(action: Literal["read", "decode", "write"], address: int | str | None = None, length: int = 1,
           fields: list[dict] | None = None, hex_bytes: str | None = None, value: int | list[int] | None = None,
           expected: int | list[int] | None = None, allow_sensitive: bool = False, experiment_frames: int = 0,
           max_results: int = 50) -> Envelope[MemoryData]:
    """Read, interpret or write emulated memory.

    Use to read a known address (score, level, piece) or decode bytes into typed fields.

    read   -> address (int) and length (up to 256 bytes) of the 64KiB address space.
    decode -> interprets bytes without changing state. Give address + length, or hex_bytes. Field types: uint8,
              int8, uint16, int16 (endianness little/big), bits (offset, bit, width), bcd (length), string (length),
              array (count, stride, item), pattern (?? wildcards). Each field has a name and optional offset.
    write  -> 1-16 bytes of WRAM or HRAM. expected refuses the write unless the current bytes match.
              experiment_frames > 0 saves state, writes, advances that many frames with no input, reports the
              change, then restores. VRAM, OAM, I/O, cartridge RAM or IE need allow_sensitive=true.
              Only enabled when DMG_CARTOGRAPHER_ENABLE_WRITES=1. A changed value is evidence, not proof.
    """
    if action == "read":
        return envelope(_as("read", CONTROLLER.read_memory(address, length)), MemoryData)
    if action == "decode":
        return envelope(_as("decode", CONTROLLER.decode_memory(fields or [], address, length, hex_bytes)),
                        MemoryData)
    if not SENSITIVE_ENABLED["write"]:
        raise ValueError("memory(action='write') is disabled; set DMG_CARTOGRAPHER_ENABLE_WRITES=1 to enable it")
    return envelope(_as("write", CONTROLLER.write_memory(address, value, expected, allow_sensitive,
                                                         experiment_frames, max_results)), MemoryData)


@_expose("raw")
def state(action: Literal["save", "load", "list"], name: str | None = None) -> Envelope[StateData]:
    """Checkpoints: named emulator save states for the loaded ROM.

    Use to save a point in the game and return to it, so trial and error starts from the same place.

    save -> writes a checkpoint (name: alphanumeric, '-' or '_').
    load -> restores it; held-button tracking resets.
    list -> checkpoint names for the loaded ROM.
    """
    if action == "save":
        return envelope(_as("save", CONTROLLER.save_checkpoint(name)), StateData)
    if action == "load":
        return envelope(_as("load", CONTROLLER.load_checkpoint(name)), StateData)
    return envelope({"action": "list", "checkpoints": CONTROLLER.list_checkpoints()}, StateData)


@_expose("raw")
def snapshot(action: Literal["capture", "list", "delete"], name: str | None = None, snapshot_id: str | None = None,
             include_memory: bool = True, include_screen: bool = True,
             include_tilemap: bool = False) -> Envelope[SnapshotData]:
    """In-memory snapshots of the current state, for comparing later. Never advances frames.

    Use to mark a 'before' moment, then compare or screen_changes against it after an input.

    capture -> stores WRAM, HRAM, screen and optional tilemaps; returns an id such as "snap-0003".
               Oldest are evicted past the retention limit; they are dropped when a ROM is loaded.
    list    -> metadata for stored snapshots.
    delete  -> removes one by id or unique name (snapshot_id).
    """
    if action == "capture":
        return envelope(_as("capture", CONTROLLER.capture_snapshot(name, include_memory, include_screen,
                                                                   include_tilemap)), SnapshotData)
    if action == "list":
        return envelope({"action": "list", "snapshots": CONTROLLER.list_snapshots()}, SnapshotData)
    return envelope(_as("delete", CONTROLLER.delete_snapshot(snapshot_id)), SnapshotData)


@_expose("raw", read_only=True)
def compare(kind: Literal["memory", "screen"], baseline: str, current: bool = True, target: str | None = None,
            region: MemoryRegion | ScreenRegion | None = None, filter: MemoryFilter = "changed",
            previous_value: int | None = None, current_value: int | None = None, max_results: int = 200,
            group: bool = True, mode: Literal["pixel", "grid", "both"] = "both", threshold: int = 0,
            include_image: bool = False) -> list:
    """Compare a snapshot against the live state (current=True) or another snapshot (target). Never advances frames.

    Use to see which RAM bytes or pixels an input changed since a snapshot.

    memory -> byte changes in region (WRAM 0xC000-0xDFFF or HRAM 0xFF80-0xFFFE). filter selects changed,
              increased, etc.; previous_value/current_value keep exact byte values only.
    screen -> changed pixels, bounding boxes and changed 8x8 cells (mode). threshold is per-channel (0-254).
              region is a rectangle in screen pixels. include_image adds a PNG with changes in red.
    A changed byte is evidence, not a variable: confirm with repeated experiments.
    """
    if kind == "memory":
        result = CONTROLLER.compare_memory(baseline, region or "WRAM", current, target, filter, previous_value,
                                           current_value, max_results, group)
        return _with_images(_as("memory", result), CompareData)
    result, png = CONTROLLER.compare_screen(baseline, current, target, mode, threshold,
                                            region.model_dump() if region else None, include_image)
    return _with_images(_as("screen", result), CompareData, png)


@_expose("raw", read_only=True)
def search_memory(action: Literal["search", "list"], session: str | None = None,
                  region: MemoryRegion = "WRAM", dtype: DataType = "uint8", endianness: Endianness = "little",
                  condition: SearchCondition = "unknown", exact: int | None = None, low: int | None = None,
                  high: int | None = None, pattern: str | None = None,
                  max_results: int = 200) -> Envelope[SearchData]:
    """Narrow down memory addresses across observations.

    Use to find where the game stores a value (score, lives, level, piece, timer) by narrowing candidates as it changes.

    search -> start with session=None and condition "unknown" (keeps every address), or "exact", "range" or
              "pattern" (hex bytes like "00 FF ?? 12"). Pass the returned session with "increased", "decreased",
              "changed" or "unchanged" to keep only addresses that moved that way since the last search.
              Values come from the currently selected bank; WRAM's upper bank is reported per result.
    list   -> active search sessions (metadata only).
    """
    if action == "list":
        return envelope({"action": "list", "sessions": CONTROLLER.list_search_sessions()}, SearchData)
    return envelope(_as("search", CONTROLLER.search_memory(session, region, dtype, endianness, condition, exact,
                                                           low, high, pattern, max_results)), SearchData)


@_expose("raw", read_only=True)
def inspect_code(kind: Literal["cartridge", "cpu", "rom", "disasm", "search"], bank: int | None = None,
                 address: str | None = None, length: int = 16, count: int = 32, ascii: bool = False,
                 cross_check: bool = True, pattern: str | None = None,
                 max_results: int = 50) -> Envelope[CodeData]:
    """Inspect the running CPU or the ROM file. Read-only; never modifies the ROM or the emulator.

    Use to read the cartridge header, CPU registers, ROM bytes or disassembly when finding the code behind a behaviour,
    and to search the ROM for the code that references an address.

    cartridge -> header facts: title, type, ROM/RAM size, CGB flag, SHA-1, and integrity checks.
    cpu       -> registers (A, F, B, C, D, E, H, L, SP, PC), pairs and flags. Interrupt state is not exposed.
    rom       -> bytes from the cartridge file at bank and address (at most 256); cross_check compares them with
                 the emulator's view of the same bank. Bank 0 is 0x0000-0x3FFF; banks 1+ are 0x4000-0x7FFF.
    disasm    -> LR35902 instructions from the ROM file (up to 64), including CB-prefixed. This is a
                 decoding interpretation, not proof the bytes execute. Undefined opcodes are marked invalid.
    search    -> every place pattern (hex bytes with ?? wildcards, e.g. "A0 C0" or "CD ?? 01") occurs in the ROM
                 file, or only in bank. Addresses are little-endian, so 0xC0A0 is "A0 C0". Each match has bank,
                 address and file_offset; bank and address go straight into kind='disasm'. At most max_results
                 (1-500); matches_total counts every match.
    """
    if kind == "cartridge":
        return envelope(_as("cartridge", CONTROLLER.inspect_cartridge()), CodeData)
    if kind == "cpu":
        return envelope(_as("cpu", CONTROLLER.inspect_cpu()), CodeData)
    if kind == "rom":
        return envelope(_as("rom", CONTROLLER.inspect_rom(bank, address, length, ascii, cross_check)), CodeData)
    if kind == "search":
        if pattern is None:
            raise ValueError("inspect_code(kind='search') needs pattern, e.g. 'A0 C0'")
        return envelope(_as("search", CONTROLLER.search_rom(pattern, bank, max_results)), CodeData)
    return envelope(_as("disasm", CONTROLLER.disassemble_rom(bank, address, count)), CodeData)


@_expose("raw")
def hook_execution(action: Literal["register", "remove", "list"], bank: int | None = None,
                   address: str | None = None, label: str | None = None,
                   max_observations: int = 20) -> Envelope[HookData]:
    """Register, remove or list execution hooks at a bank and address (up to 16).

    Use to check whether a routine runs, and how often, while you step.

    A hook counts instruction fetches at one address and keeps up to max_observations register snapshots. It is
    not a memory watchpoint. Frame numbers are not available inside hook callbacks. Hooks are cleared on game reset
    and load. Only enabled when DMG_CARTOGRAPHER_ENABLE_DEBUG=1.
    """
    return envelope(_as(action, CONTROLLER.hook_execution(action, bank, address, label, max_observations)),
                    HookData)
