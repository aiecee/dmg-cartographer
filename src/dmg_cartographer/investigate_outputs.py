"""Typed output for the investigate tools. Each action's `data` is one of these models.

Shared pieces (memory diffs, screen diffs, grids, byte values) come from outputs.py. Every model forbids
undeclared fields. Where a payload is still open-ended (for example the contents of a rejected expectation),
the field is typed as a list of dictionaries and says so in its docstring.
"""

from typing import Annotated, Any, Literal, Union

from pydantic import Field

from dmg_cartographer.outputs import (
    Byte, GridPos, MemoryChange, MemoryGroup, MemoryRegionDiff, Out, Point, Rect, ScreenArea, ScreenDiff, ScreenGrid,
)

ButtonName = Literal["a", "b", "start", "select", "up", "down", "left", "right"]
Region = Literal["WRAM", "HRAM"]
Confidence = Literal["low", "medium", "high"]
DiscoveryStatus = Literal["hypothesis", "verified", "disproven"]


# shared ---------------------------------------------------------------------------------------------

class InputAction(Out):
    """A requested input step: hold these buttons for this many frames, then release those."""
    frames: int = Field(ge=1, le=600)
    press: list[ButtonName]
    release: list[ButtonName]


class InputEvent(Out):
    """An input that was applied, at the frame it took effect."""
    frame: int
    press: list[ButtonName]
    release: list[ButtonName]


class ScreenSummary(Out):
    changed_pixels: int
    percent_changed: float
    bounding_box: Rect | None


class Counterexample(Out):
    experiment: str
    kind: str


# experiment ------------------------------------------------------------------------------------------

class ExperimentRecord(Out):
    """One stored experiment: the before and after snapshots and what the inputs did."""
    id: str
    name: str
    rom: str
    before: str
    after: str
    start_frame: int
    end_frame: int
    advanced_frames: int
    requested_frames: int
    completed_actions: int
    timestamp: str
    changed_addresses: int
    actions: list[InputAction]
    screen_summary: ScreenSummary | None = None  # absent when compare_screen=false


class ExperimentRun(ExperimentRecord):
    held_buttons: list[ButtonName]
    emulator_running: bool
    memory: dict[Region, MemoryRegionDiff] | None = None
    screen: ScreenDiff | None = None


class ExperimentRunData(ExperimentRun):
    action: Literal["run"]


class ExperimentListData(Out):
    action: Literal["list"]
    experiments: list[ExperimentRecord]


class RankEvidence(Out):
    experiment: str
    name: str
    previous: int | None
    current: int | None
    diff: int


class RankCandidate(Out):
    address: str
    action: str | None
    score: float
    confidence: Confidence
    consistent_diff: bool
    evidence: list[RankEvidence]
    counterexamples: list[Counterexample]


class SuggestedValidation(Out):
    address: str
    expected: dict[str, str | int]
    experiments: dict[str, list[InputAction]]
    repeats: int = Field(ge=1)
    next: str


class ExperimentRankData(Out):
    action: Literal["rank"]
    experiments: list[str]
    addresses_considered: int
    candidates: list[RankCandidate]
    candidates_total: int
    candidates_truncated: bool
    note: str
    suggested_validation: list[SuggestedValidation]


class CorrelationCounts(Out):
    memory_and_screen: int
    memory_only: int
    screen_only: int
    neither: int


class Correlation(Out):
    address: str
    pattern: str
    confidence: Confidence
    agreement: float = Field(ge=0, le=1)
    samples: int
    counts: CorrelationCounts
    direction_when_both: dict[str, int]
    evidence: list[str]
    counterexamples: list[Counterexample]
    counterexamples_total: int
    counterexamples_truncated: bool


class Excluded(Out):
    """An experiment left out of correlation, with the reason."""
    id: str
    reason: str


class ExperimentCorrelateData(Out):
    action: Literal["correlate"]
    samples: int
    addresses_considered: int
    candidates: list[Correlation]
    candidates_total: int
    candidates_truncated: bool
    note: str
    experiments: list[str]
    excluded: list[Excluded]
    frame_counts: dict[str, int]
    input_events: dict[str, list[InputAction]]  # the requested steps of each experiment, keyed by its id
    screen_change_rule: str


class ActionTally(Out):
    runs: int
    confirmed: int
    contradicted: int


class ValidateRun(Out):
    experiment: str
    name: str
    advanced_frames: int
    diff: int
    previous: int | None
    current: int | None
    screen_changed: bool


class Contradiction(Out):
    """An experiment whose observed change did not match the expectation."""
    experiment: str
    check: str
    diff: int
    expected: Literal["increase", "decrease", "change", "none"] | int


class PerturbationResult(Out):
    """A value written after the checkpoint, the frames advanced, and what the byte read back as.
    The emulator is restored afterwards; result_id stores the record."""
    address: str
    checkpoint: str
    value_written: int = Field(ge=0, le=255)
    frames_advanced: int = Field(ge=0)
    value_after: int = Field(ge=0, le=255)
    reverted: bool
    screen_changed: bool
    note: str
    result_id: str


class ExperimentValidateData(Out):
    action: Literal["validate"]
    address: str
    checkpoint: str
    verdict: Literal["supported_by_experiments", "contradicted", "inconclusive"]
    verdict_meaning: str
    per_action: dict[str, ActionTally]
    confirmations: int
    contradictions: list[Contradiction]
    contradictions_total: int
    contradictions_truncated: bool
    runs: list[ValidateRun]
    frames_advanced_total: int
    perturbation: PerturbationResult | None
    limitations: list[str]


ExperimentData = Annotated[
    Union[ExperimentRunData, ExperimentListData, ExperimentRankData, ExperimentCorrelateData, ExperimentValidateData],
    Field(discriminator="action"),
]


# branch ----------------------------------------------------------------------------------------------

class UniqueChange(MemoryChange):
    region: Region


class BranchRun(Out):
    name: str
    start_frame: int
    end_frame: int
    requested_frames: int
    advanced_frames: int
    completed_actions: int
    held_buttons: list[ButtonName]
    emulator_running: bool
    unique_changes: list[UniqueChange]
    unique_changes_total: int
    unique_changes_truncated: bool
    changes: dict[Region, list[MemoryChange]]
    changes_total: int
    changes_truncated: bool
    screen: ScreenDiff | None = None


class BranchResult(Out):
    checkpoint: str
    baseline_frame: int
    compare: list[Literal["memory", "screen", "sprites"]]
    branches: list[BranchRun]
    shared_change_count: int
    restored: bool
    result_id: str


# watch -------------------------------------------------------------------------------------------------

class TileRectOut(Out):
    layer: Literal["background", "window"]
    x: int
    y: int
    width: int
    height: int


class WatchMonitors(Out):
    addresses: list[str]
    sprites: list[int]
    tiles: TileRectOut | None
    screen_region: Rect | None
    cpu: bool


class AddressRef(Out):
    address: str
    region: Region


class MemoryEvent(Out):
    kind: Literal["memory"]
    frame: int
    address: str
    previous: int
    current: int


class SpriteState(Out):
    x: int
    y: int
    tile: int
    attributes: str


class SpriteEvent(Out):
    kind: Literal["sprite"]
    frame: int
    slot: int = Field(ge=0, le=39)
    previous: SpriteState
    current: SpriteState


class TileEvent(Out):
    kind: Literal["tile"]
    frame: int
    layer: Literal["background", "window"]
    tilemap: GridPos
    previous: int
    current: int


class ScreenRegionEvent(Out):
    kind: Literal["screen_region"]
    frame: int
    region: Rect
    changed_pixels: int


class CpuEvent(Out):
    kind: Literal["cpu"]
    frame: int
    register_name: str = Field(alias="register")
    previous: int
    current: int


WatchEvent = Annotated[
    Union[MemoryEvent, SpriteEvent, TileEvent, ScreenRegionEvent, CpuEvent], Field(discriminator="kind")
]


class WatchResult(Out):
    start_frame: int
    end_frame: int
    advanced_frames: int
    requested_frames: int
    sample_every: int
    samples: int
    change_count: int
    record_changes_only: bool
    monitors: WatchMonitors
    addresses: list[AddressRef]
    events: list[WatchEvent]
    events_total: int
    events_truncated: bool
    input_events: list[InputEvent]
    input_events_total: int
    input_events_truncated: bool
    limitation: str


# replay -----------------------------------------------------------------------------------------------

class ReplayEnd(Out):
    frame: int
    held: list[ButtonName]
    regions: dict[str, str]
    digest: str


class ReplayRecord(Out):
    mode: Literal["record"]
    name: str
    stored: bool
    checkpoint: str
    requested_frames: int
    end: ReplayEnd
    evidence_ref: str


class ReplayMismatch(Out):
    """One fingerprint field that differed between the recording and the replay. Field names look like
    'start.frame', 'end.digest' or 'end.regions.WRAM'. Values are frame numbers, digests or region hashes."""
    field: str
    recorded: int | str | None
    observed: int | str | None


class ReplayReplayFields(Out):
    mode: Literal["replay"]
    name: str
    completed: bool
    matched: bool
    mismatches: list[ReplayMismatch]
    mismatches_total: int
    mismatches_truncated: bool
    requested_frames: int
    advanced_frames: int
    recorded_frames: int
    frames_match_recording: bool
    possible_sources: list[str]
    fingerprint_scope: str
    restored: bool
    evidence_ref: str


class ReplayReplay(ReplayReplayFields):
    result_id: str


ReplayData = Annotated[Union[ReplayRecord, ReplayReplay], Field(discriminator="mode")]


# result (stored branch or replay result) ---------------------------------------------------------------

class StoredBranch(BranchResult):
    kind: Literal["branch"]
    rom_key: str


class StoredReplay(ReplayReplayFields):
    kind: Literal["replay"]
    rom_key: str
    result_id: str


ResultData = Annotated[Union[StoredBranch, StoredReplay], Field(discriminator="kind")]


# discoveries ---------------------------------------------------------------------------------------------

class DiscoveryRecord(Out):
    name: str
    address: str | None
    region: Region | None
    data_type: str | None
    bank: int | None
    confidence: Confidence
    status: DiscoveryStatus
    labels: list[str]
    description: str
    updated: str
    evidence_count: int
    counterexample_count: int


class DiscoverySave(DiscoveryRecord):
    action: Literal["save"]
    evidence: list[str]
    counterexamples: list[str]


class DiscoveryList(Out):
    action: Literal["list"]
    rom_sha1: str
    title: str | None
    discoveries: list[DiscoveryRecord]
    discoveries_total: int
    discoveries_truncated: bool


class DiscoveryExport(Out):
    action: Literal["export"]
    name: str
    file: str
    count: int
    bytes: int


DiscoveriesData = Annotated[Union[DiscoverySave, DiscoveryList, DiscoveryExport], Field(discriminator="action")]


# export --------------------------------------------------------------------------------------------------

class ExportFiles(Out):
    json_path: str = Field(alias="json")
    markdown: str | None = None


class ExportCounts(Out):
    discoveries: int
    experiments: int
    replays: int
    snapshots: int


class ExportReport(Out):
    name: str
    folder: str
    files: ExportFiles
    bytes: int
    counts: ExportCounts


# screen_changes --------------------------------------------------------------------------------------------

class ChangedPixels(Out):
    changed: int
    percent_changed: float
    examined: int
    threshold: int
    region: Rect


class SpriteAppeared(Out):
    slot: int
    at: Point


class SpriteDelta(Out):
    dx: int
    dy: int


class SpriteMove(Out):
    """A sprite in the same OAM slot that is on screen in both frames at a different position.
    inferred_same_object is only a guess from matching tile IDs."""
    slot: int = Field(ge=0, le=39)
    from_pos: Point = Field(alias="from")
    to: Point
    delta: SpriteDelta
    tile_index_before: int
    tile_index_after: int
    inferred_same_object: bool


class SpriteDisappeared(Out):
    """Visible in the first frame, off screen in the second. Shape from analysis.py; not yet seen on the ROM."""
    slot: int = Field(ge=0, le=39)
    was_at: Point


class SpriteTileSwap(Out):
    """Same slot, same position, different tile. Shape from analysis.py; not yet seen on the ROM."""
    slot: int = Field(ge=0, le=39)
    tile_index_before: int
    tile_index_after: int


class SpriteChanges(Out):
    """Sprite identity is by OAM slot. identity_basis states what the slot match does and does not prove."""
    moved: list[SpriteMove]
    moved_total: int
    moved_truncated: bool
    appeared: list[SpriteAppeared]
    appeared_total: int
    appeared_truncated: bool
    disappeared: list[SpriteDisappeared]
    disappeared_total: int
    disappeared_truncated: bool
    tile_changed_in_place: list[SpriteTileSwap]
    tile_changed_in_place_total: int
    tile_changed_in_place_truncated: bool
    unchanged_on_screen: int
    identity_basis: str


class TileChange(Out):
    x: int
    y: int
    previous: int
    current: int


class TileLayerChanges(Out):
    changes: list[TileChange]
    changes_total: int
    changes_truncated: bool


class BackgroundTileChanges(Out):
    background: TileLayerChanges
    window: TileLayerChanges


class TilesUnavailable(Out):
    """Returned instead when the snapshots were not taken with include_tilemap=true."""
    unavailable: str


class ScreenChangesData(Out):
    baseline: str
    target: str
    baseline_frame: int
    target_frame: int
    basis: str
    pixels: ChangedPixels
    bounding_box: Rect | None
    rectangles: list[ScreenArea]
    rectangles_total: int
    rectangles_truncated: bool
    cells: ScreenGrid | None = None
    sprites: SpriteChanges | None = None
    background_tiles: BackgroundTileChanges | TilesUnavailable | None = None
    summary: str
    warning: str | None = None
