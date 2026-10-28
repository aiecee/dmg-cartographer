"""Typed output for each raw tool action. Each tool's `data` is one of these, discriminated by `action`.

Every model forbids undeclared fields, so a client can rely on exactly what is listed. Fields that only some
calls return are optional and say so. Hex values are strings like "0xC000".
"""

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class Out(BaseModel):
    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)


# shared pieces -------------------------------------------------------------------------------------

class Byte(Out):
    """A byte value in decimal and hex."""
    dec: int = Field(ge=0)
    hex: str


class SpriteFlip(Out):
    x: bool
    y: bool


class Rect(Out):
    x: int
    y: int
    width: int = Field(ge=0)
    height: int = Field(ge=0)


class Point(Out):
    x: int
    y: int


class GridPos(Out):
    col: int
    row: int


class CgbHeader(Out):
    header_cgb_flag: str
    cgb_compatible: bool
    cgb_only: bool


class EmulatorState(Out):
    """The emulator's state. Game and step actions return it; after close most fields are null."""
    loaded: bool
    rom: str | None
    cartridge_title: str | None
    timeline_frame: int | None
    data_dir: str | None
    rom_folder: str | None = None
    screen_fresh: bool | None
    snapshots: int = Field(ge=0)
    experiments: int = Field(ge=0)
    held_buttons: list[str]
    paused_between_calls: bool


class RestoredHistory(Out):
    experiments: int
    results: int


# game ----------------------------------------------------------------------------------------------

class GameList(Out):
    action: Literal["list"]
    roms: list[str]


class GameLoad(EmulatorState):
    action: Literal["load"]
    discarded_snapshots_and_experiments: int
    restored_from_history: RestoredHistory


class GameStatus(EmulatorState):
    action: Literal["status"]


class GameClose(EmulatorState):
    action: Literal["close"]


class GameReset(EmulatorState):
    action: Literal["reset"]
    discarded_snapshots_and_experiments: int
    restored_from_history: RestoredHistory
    frames_advanced: int
    note: str


GameData = Annotated[Union[GameList, GameLoad, GameStatus, GameClose, GameReset], Field(discriminator="action")]


# step / button -------------------------------------------------------------------------------------

class StepResult(EmulatorState):
    action: Literal["step"]
    advanced_frames: int = Field(ge=0)
    emulator_running: bool


class SequenceResult(EmulatorState):
    action: Literal["sequence"]
    advanced_frames: int = Field(ge=0)
    requested_frames: int
    completed_actions: int
    emulator_running: bool


class ButtonResult(EmulatorState):
    action: Literal["button"]
    changed: bool
    frames_advanced: int = Field(ge=0)
    note: str


StepData = Annotated[Union[StepResult, SequenceResult, ButtonResult], Field(discriminator="action")]


# screen --------------------------------------------------------------------------------------------

class ScreenResult(Out):
    action: Literal["screen"]
    scale: int = Field(ge=1, le=4)


class AnnotatedScreen(Out):
    action: Literal["annotated"]
    frame: int
    overlays: list[Literal["pixels", "tiles", "sprites", "regions"]]
    regions_drawn: int
    sprites_drawn: int
    framebuffer_modified: Literal[False]


ScreenData = Annotated[Union[ScreenResult, AnnotatedScreen], Field(discriminator="action")]


# inspect_video -------------------------------------------------------------------------------------

class VideoArea(Out):
    action: Literal["area"]
    width: int
    height: int
    tiles: list[list[int]]


class VideoTilemap(Out):
    action: Literal["tilemap"]
    layer: Literal["background", "window"]
    x: int
    y: int
    width: int
    height: int
    tiles: list[list[int]]


class Scroll(Out):
    scx: int
    scy: int


class WindowOrigin(Out):
    wx: int
    wy: int


class TileAttributes(Out):
    """CGB attribute byte, decoded. Only present when include_attributes is set on a CGB-compatible cartridge."""
    raw: str
    cgb_palette: int = Field(ge=0, le=7)
    vram_bank: int = Field(ge=0, le=1)
    flip: SpriteFlip
    priority: bool


class TileEntry(Out):
    tile_id: int
    tilemap: GridPos
    pixel: Point
    viewport: Point | None
    visible: bool
    attributes: TileAttributes | str | None = None


class VideoTiles(Out):
    action: Literal["tiles"]
    layer: Literal["background", "window"]
    frame: int
    tiles: list[TileEntry]
    tilemap_rect: Rect
    map_base: str
    lcdc: str
    scroll: Scroll | WindowOrigin
    cgb_header: CgbHeader
    coordinates: str
    hardware_mode: str


class SpriteRaw(Out):
    y: int
    x: int
    tile: int
    attributes: str


class Sprite(Out):
    index: int = Field(ge=0, le=39)
    raw: SpriteRaw
    screen: Rect
    on_screen: bool
    tile_index: int
    tile_indices: list[int]
    priority_behind_background: bool
    flip: SpriteFlip
    palette: dict[str, int]


class VideoSprites(Out):
    action: Literal["sprites"]
    count: int
    returned: int
    truncated: bool
    sprites: list[Sprite]
    frame: int
    lcdc: str
    sprite_height: int
    cgb_header: CgbHeader
    coordinates: str


class DmgPalette(Out):
    address: str
    raw: str
    shade_by_color_index: dict[Literal["0", "1", "2", "3"], int]


class DmgPalettes(Out):
    BGP: DmgPalette
    OBP0: DmgPalette
    OBP1: DmgPalette


class VideoPalette(Out):
    action: Literal["palette"]
    frame: int
    dmg_palettes: DmgPalettes
    cgb_palette_ram: str
    note: str


VideoData = Annotated[Union[VideoArea, VideoTilemap, VideoTiles, VideoSprites, VideoPalette],
                      Field(discriminator="action")]


# memory --------------------------------------------------------------------------------------------

class MemoryRead(Out):
    action: Literal["read"]
    address: int = Field(ge=0, le=0xFFFF)
    hex_address: str
    bytes: list[int]
    hex_bytes: str


class DecodeSource(Out):
    address: str
    length: int
    frame: int


class IntField(Out):
    """uint8, int8, uint16, int16."""
    dec: int
    hex: str


class BitsField(Out):
    dec: int = Field(ge=0, le=1)
    bit: int = Field(ge=0, le=7)
    width: int = Field(ge=1, le=8)
    byte: str


class BcdField(Out):
    valid: bool
    dec: int | None
    bytes: str


class StringField(Out):
    text: str
    bytes: str
    ascii: bool
    note: str


class PatternField(Out):
    matches: bool
    offset: int | None


DecodedField = Union[IntField, BitsField, BcdField, StringField, PatternField, list[IntField]]


class MemoryDecode(Out):
    action: Literal["decode"]
    fields: dict[str, DecodedField]
    source: DecodeSource
    note: str


class WriteObservations(Out):
    """What changed during the experiment frames. Evidence only: it does not prove the write caused it."""
    frames_advanced: int
    memory: dict[Literal["WRAM", "HRAM"], MemoryRegionDiff]
    screen: ScreenDiff


class MemoryWriteBase(Out):
    action: Literal["write"]
    address: str
    length: int
    region: Literal["WRAM", "HRAM", "VRAM", "OAM", "IO", "CART_RAM", "IE"]
    written: list[int]
    previous: list[int]
    side_effects: str


class MemoryWriteDirect(MemoryWriteBase):
    """experiment_frames=0: the write stays in the live emulator."""
    mode: Literal["write"]
    restored: Literal[False]
    frames_advanced: Literal[0]
    warning: str


class MemoryWriteExperiment(MemoryWriteBase):
    """experiment_frames > 0: state is restored afterwards. observations are the diff of what changed; they do not
    prove the write caused it. Their layout matches compare_memory per region; it is not narrowed further here."""
    mode: Literal["experiment"]
    restored: Literal[True]
    frames_advanced: int = Field(ge=1)
    note: str
    observations: WriteObservations


MemoryWrite = Annotated[Union[MemoryWriteDirect, MemoryWriteExperiment], Field(discriminator="mode")]

MemoryData = Annotated[Union[MemoryRead, MemoryDecode, MemoryWrite], Field(discriminator="action")]


# state (checkpoints) --------------------------------------------------------------------------------

class StateSave(Out):
    action: Literal["save"]
    checkpoint: str
    frames: int
    saved: bool


class StateLoad(EmulatorState):
    action: Literal["load"]
    checkpoint: str


class StateList(Out):
    action: Literal["list"]
    checkpoints: list[str]


StateData = Annotated[Union[StateSave, StateLoad, StateList], Field(discriminator="action")]


# snapshot ------------------------------------------------------------------------------------------

class MemoryRegions(Out):
    WRAM: str
    HRAM: str


class SnapshotCapture(Out):
    action: Literal["capture"]
    id: str
    name: str | None
    frame: int
    has_screen: bool
    has_tilemap: bool
    memory_regions: MemoryRegions
    rom: str
    rom_key: str
    timestamp: str
    screen_fresh: bool
    warning: str | None = None


class SnapshotSummary(Out):
    id: str
    name: str | None
    rom: str
    rom_key: str
    frame: int
    timestamp: str
    memory_regions: MemoryRegions
    has_screen: bool
    screen_fresh: bool
    has_tilemap: bool


class SnapshotList(Out):
    action: Literal["list"]
    snapshots: list[SnapshotSummary]


class SnapshotDelete(Out):
    action: Literal["delete"]
    deleted: str
    remaining: int


SnapshotData = Annotated[Union[SnapshotCapture, SnapshotList, SnapshotDelete], Field(discriminator="action")]


# compare -------------------------------------------------------------------------------------------

class MemoryChange(Out):
    address: str
    previous: Byte
    current: Byte
    diff: int
    diff_unsigned: int
    diff_wrapped: int


class MemoryGroup(Out):
    start: str
    end: str
    length: int


class MemoryRegionDiff(Out):
    """Byte changes within one memory region (WRAM or HRAM)."""
    bytes_examined: int
    changed_count: int
    filter: str
    match_count: int
    returned: int
    truncated: bool
    changes: list[MemoryChange]
    group_count: int | None = None
    groups: list[MemoryGroup] | None = None


class CompareMemory(MemoryRegionDiff):
    action: Literal["memory"]
    baseline: str
    target: str
    baseline_frame: int
    target_frame: int
    region: str
    range: str


class ScreenArea(Out):
    x: int
    y: int
    width: int
    height: int
    cells: int


class GridCell(Out):
    col: int
    row: int
    x: int
    y: int
    changed_pixels: int


class ScreenGrid(Out):
    cell_size: int
    columns: int
    rows: int
    changed_cells: int
    cells: list[GridCell]


class ScreenDiff(Out):
    """Changed pixels and cells between two rendered frames."""
    changed_pixels: int
    percent_changed: float
    pixels_examined: int
    mode: Literal["pixel", "grid", "both"]
    threshold: int
    region: Rect | None
    bounding_box: Rect | None = None
    area_count: int | None = None
    areas: list[ScreenArea] | None = None
    grid: ScreenGrid | None = None
    warning: str | None = None


class CompareScreen(ScreenDiff):
    action: Literal["screen"]
    baseline: str
    target: str
    baseline_frame: int
    target_frame: int


CompareData = Annotated[Union[CompareMemory, CompareScreen], Field(discriminator="action")]


# search_memory -------------------------------------------------------------------------------------

class SearchBank(Out):
    banked: str
    upper_bank_register: str | None = None
    upper_bank: int | None = None
    note: str


class SearchMatch(Out):
    address: str
    previous: Byte | None
    current: Byte


class SearchResult(Out):
    action: Literal["search"]
    session: str
    condition: str
    candidates_before: int
    candidates_remaining: int
    eliminated: int
    matches: list[SearchMatch]
    returned: int
    truncated: bool
    region: str
    dtype: str
    endianness: str
    frame: int
    bank: SearchBank
    evicted_sessions: list[str]


class SearchSession(Out):
    session: str
    region: str
    dtype: str
    endianness: str
    candidates: int
    frame: int


class SearchList(Out):
    action: Literal["list"]
    sessions: list[SearchSession]


SearchData = Annotated[Union[SearchResult, SearchList], Field(discriminator="action")]


# inspect_code --------------------------------------------------------------------------------------

class CartridgeType(Out):
    code: str
    name: str
    mbc: str


class RomSize(Out):
    code: str
    declared_bytes: int
    declared_banks: int
    file_bytes: int
    matches_file: bool


class RamSize(Out):
    code: str
    bytes: int


class HeaderChecksum(Out):
    declared: str
    computed: str
    valid: bool


class CartridgeHeader(Out):
    valid_header: bool
    title: str
    cgb_flag: str
    cgb_compatible: bool
    cgb_only: bool
    sgb_flag: bool
    cartridge_type: CartridgeType
    battery: bool
    rom_size: RomSize
    ram_size: RamSize
    header_checksum: HeaderChecksum


class CartridgeChecks(Out):
    header_valid: bool
    header_checksum: bool
    rom_size_matches_file: bool
    title_matches_emulator: bool


class CodeCartridge(Out):
    action: Literal["cartridge"]
    rom_sha1: str
    header: CartridgeHeader
    checks: CartridgeChecks
    all_checks_pass: bool
    file: str
    rom_bytes: int
    emulator_title: str | None
    note: str


class CpuRegisters(Out):
    A: int = Field(ge=0, le=255)
    F: int = Field(ge=0, le=255)
    B: int = Field(ge=0, le=255)
    C: int = Field(ge=0, le=255)
    D: int = Field(ge=0, le=255)
    E: int = Field(ge=0, le=255)
    H: int = Field(ge=0, le=255)
    L: int = Field(ge=0, le=255)
    SP: int = Field(ge=0, le=0xFFFF)
    PC: int = Field(ge=0, le=0xFFFF)


class CpuPairs(Out):
    AF: int
    BC: int
    DE: int
    HL: int


class CpuFlags(Out):
    Z: bool
    N: bool
    H: bool
    C: bool


class CodeCpu(Out):
    action: Literal["cpu"]
    registers: CpuRegisters
    pairs: CpuPairs
    flags: CpuFlags
    hex: dict[str, str]
    frame: int
    held_buttons: list[str]
    not_exposed_by_pyboy: list[str]
    rom_bank_context: str


class CodeRom(Out):
    action: Literal["rom"]
    bank: int
    address: str
    length: int
    hex: str
    bytes: list[int]
    file_offset: str
    matches_emulator: bool | None = None


class Instruction(Out):
    bank: int
    address: str
    bytes: str
    mnemonic: str
    operands: str
    length: int
    targets: list[str]
    invalid: bool
    truncated: bool


class CodeDisasm(Out):
    action: Literal["disasm"]
    bank: int
    start: str
    count: int
    instructions: list[Instruction]
    targets: list[str]
    label: str | None = None


class RomMatch(Out):
    bank: int
    address: str
    file_offset: str


class CodeSearch(Out):
    action: Literal["search"]
    pattern: str
    bank: int | None = None
    count: int
    truncated: bool
    matches: list[RomMatch]


CodeData = Annotated[Union[CodeCartridge, CodeCpu, CodeRom, CodeDisasm, CodeSearch], Field(discriminator="action")]


# hook_execution ------------------------------------------------------------------------------------

class HookObservation(Out):
    """Register snapshot taken when the hook fired. Frame numbers are not available inside callbacks."""
    hit: int
    A: int
    F: int
    BC: int
    DE: int
    HL: int
    SP: int
    PC: int
    frame_note: str


class HookSummary(Out):
    key: str
    bank: int
    address: str
    label: str | None
    count: int
    callback_errors: int
    observations: list[HookObservation]


class HookRegister(Out):
    action: Literal["register"]
    registered: HookSummary


class HookRemove(Out):
    action: Literal["remove"]
    removed: str
    final_count: int


class HookList(Out):
    action: Literal["list"]
    count: int
    hooks: list[HookSummary]
    limitation: str


HookData = Annotated[Union[HookRegister, HookRemove, HookList], Field(discriminator="action")]
