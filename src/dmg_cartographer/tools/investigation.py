"""Investigation tools: several emulator operations per call. Every frame is counted, and results are enveloped.

Eight tools. Each one groups the actions it used to be separate tools for.
"""

from typing import Any, Literal

from mcp.server.mcpserver import Image

from dmg_cartographer.investigate_outputs import (
    BranchResult, DiscoveriesData, ExperimentData, ExportReport, ReplayData, ResultData, ScreenChangesData, WatchResult,
)
from dmg_cartographer.mcp_app import (
    Action, BranchSpec, CONTROLLER, Confidence, DataType, Envelope, Perturb, ScreenRegion, Status, TileRect, _expose,
    envelope,
)

Expectation = dict[str, Literal["increase", "decrease", "change", "none"] | int]


def _as(action: str, core: dict[str, Any]) -> dict[str, Any]:
    return {**core, "action": action}


@_expose("investigate")
def experiment(action: Literal["run", "list", "rank", "correlate", "validate"],
               name: str | None = None, actions: list[Action] | None = None,
               compare_memory: bool = True, compare_screen: bool = True,
               names: list[str] | None = None, expected_change: Expectation | None = None,
               experiments: dict[str, list[Action]] | None = None, address: str | None = None,
               checkpoint: str | None = None, expected: Expectation | None = None, repeats: int = 2,
               screen_expected: dict[str, Literal["changes", "static"]] | None = None,
               perturb: Perturb | None = None, allow_writes: bool = False, max_results: int = 50) -> Envelope[ExperimentData]:
    """Record, list and test experiments: named input runs compared against memory and screen.

    run      -> snapshot, run actions exactly (no hidden frames), snapshot again, compare. Needs name and actions.
                Reuse the same name for repeats of one action so rank can group them. Stored as "exp-NNNN".
    list     -> stored experiments (metadata only).
    rank     -> memory addresses whose changes line up with experiment names. Needs names (ids or names);
                expected_change optionally maps a name to increase, decrease, change, none or an exact diff.
                Results are correlations: confirm them with validate.
    correlate-> ranks addresses by how well changes match visible screen changes. Needs names; both memory and
                screen must have been compared. Correlation only, never proof of cause.
    validate -> tests one address from a checkpoint with named input sets (experiments), repeated `repeats` times.
                Needs address, checkpoint, experiments and expected. screen_expected adds visible checks.
                perturb (needs allow_writes=true) writes a value, advances frames and restores. The verdict is
                supported_by_experiments, contradicted or inconclusive. It never marks a hypothesis verified.
    """
    if action == "run":
        if name is None or actions is None:
            raise ValueError("experiment(action='run') needs name and actions")
        core = CONTROLLER.run_experiment(name, [a.model_dump() for a in actions], compare_memory, compare_screen,
                                         max_results)
        return envelope(_as("run", core), ExperimentData)
    if action == "list":
        return envelope({"action": "list", "experiments": CONTROLLER.list_experiments()}, ExperimentData)
    if action == "rank":
        if not names:
            raise ValueError("experiment(action='rank') needs names")
        return envelope(_as("rank", CONTROLLER.find_memory_candidates(names, expected_change, max_results)),
                        ExperimentData)
    if action == "correlate":
        if not names:
            raise ValueError("experiment(action='correlate') needs names")
        return envelope(_as("correlate", CONTROLLER.investigate_correlations(names, max_results)), ExperimentData)
    if address is None or checkpoint is None or experiments is None or expected is None:
        raise ValueError("experiment(action='validate') needs address, checkpoint, experiments and expected")
    core = CONTROLLER.investigate_validate(
        address, checkpoint, {n: [a.model_dump() for a in acts] for n, acts in experiments.items()}, expected,
        repeats, screen_expected, perturb.model_dump() if perturb is not None else None, allow_writes)
    return envelope(_as("validate", core), ExperimentData)


@_expose("investigate")
def branch(checkpoint: str, branches: list[BranchSpec],
           compare: list[Literal["memory", "screen", "sprites"]] | None = None,
           max_results: int = 50) -> Envelope[BranchResult]:
    """Run several action sequences from the SAME checkpoint and compare each against it and each other.

    Each branch loads the checkpoint and runs its actions with exact frame counts. unique_changes shows bytes only
    that branch touched; shared_change_count counts bytes touched by all. The live emulator (frames, held buttons,
    memory) is restored afterwards, even if a branch fails. "sprites" in compare is not implemented yet.
    Results are frame-sampled: a byte that changes and changes back within a branch is not reported.
    """
    core = CONTROLLER.branch_experiment(
        checkpoint, [{"name": b.name, "actions": [a.model_dump() for a in b.actions]} for b in branches],
        compare, max_results)
    return envelope(core, BranchResult)


@_expose("investigate")
def watch(addresses: list[str] | None = None, sprites: list[int] | None = None,
          tiles: TileRect | None = None, screen_region: ScreenRegion | None = None,
          cpu: bool = False, frames: int = 1, sample_every: int = 1, record_changes_only: bool = True,
          actions: list[Action] | None = None, max_events: int = 200) -> Envelope[WatchResult]:
    """Advance exactly `frames` frames and report changes in the chosen monitors. Choose at least one.

    addresses: up to 16 WRAM, HRAM or ROM addresses. sprites: up to 8 OAM slots. tiles: a map rectangle (up to 64
    tiles). screen_region: a rectangle of the screen. cpu: register values. actions, if given, must total `frames`
    and are recorded as input_events. Sampled every `sample_every` frames; the end state is always sampled.
    Writes that revert within one sampling window are not seen: this is frame sampling, not instruction tracing.
    """
    monitors: dict[str, Any] = {}
    if addresses is not None:
        monitors["addresses"] = addresses
    if sprites is not None:
        monitors["sprites"] = sprites
    if tiles is not None:
        monitors["tiles"] = tiles.model_dump()
    if screen_region is not None:
        monitors["screen_region"] = screen_region.model_dump()
    if cpu:
        monitors["cpu"] = True
    core = CONTROLLER.investigate_watch(monitors, frames, sample_every, record_changes_only,
                                        [a.model_dump() for a in actions] if actions is not None else None,
                                        max_events)
    return envelope(core, WatchResult)


@_expose("investigate")
def replay(name: str, mode: Literal["record", "replay"] = "record", checkpoint: str | None = None,
           actions: list[Action] | None = None, expect: dict[str, int] | None = None) -> Envelope[ReplayData]:
    """Record a run from a checkpoint, or replay it and report mismatches.

    record: runs actions exactly from the checkpoint and stores them with a fingerprint of WRAM, HRAM, frame count
            and held buttons before and after. expect {address: value} must hold, or nothing is stored.
    replay: restores the same checkpoint, reruns the stored steps and reports every fingerprinted field that differs.
    The live emulator is restored afterwards. The fingerprint does not cover VRAM, OAM, I/O registers or timers.
    """
    core = CONTROLLER.record_replay(name, mode, checkpoint,
                                    [a.model_dump() for a in actions] if actions is not None else None, expect)
    return envelope(core, ReplayData)


@_expose("investigate")
def result(result_id: str) -> Envelope[ResultData]:
    """Retrieve a stored branch or replay result by its result_id (kept for this session)."""
    return envelope(CONTROLLER.get_result(result_id), ResultData)


@_expose("investigate")
def discoveries(action: Literal["save", "list", "export"], name: str | None = None,
                address: str | None = None, region: Literal["WRAM", "HRAM"] | None = None,
                data_type: DataType | None = None, bank: int | None = None,
                confidence: Confidence | None = None, description: str | None = None,
                evidence: list[str] | None = None, counterexamples: list[str] | None = None,
                status: Status | None = None, labels: list[str] | None = None, update: bool = False,
                label: str | None = None, include_evidence: bool = False,
                rom_sha1: str | None = None) -> Envelope[DiscoveriesData]:
    """This game's saved hypotheses (JSON per ROM hash).

    save   -> record a discovery. status "verified" needs an experiment id ('exp-0003') or replay ('replay:name') in
              evidence; "disproven" needs one in counterexamples. Saving never verifies. update=true merges evidence.
    list   -> filter by address, region, label, confidence, status. rom_sha1 selects another ROM.
    export -> write the discoveries to exports/<name>/discoveries.json.
    """
    if action == "save":
        if name is None:
            raise ValueError("discoveries(action='save') needs name")
        core = CONTROLLER.save_discovery(name, address, region, data_type, bank, confidence or "low",
                                         description or "", evidence, counterexamples, status or "hypothesis",
                                         labels, update)
        return envelope(_as("save", core), DiscoveriesData)
    if action == "list":
        core = CONTROLLER.get_discoveries(address, region, label, confidence, status, include_evidence, rom_sha1)
        return envelope(_as("list", core), DiscoveriesData)
    if name is None:
        raise ValueError("discoveries(action='export') needs name")
    return envelope(_as("export", CONTROLLER.export_discoveries(name)), DiscoveriesData)


@_expose("investigate")
def export(name: str, candidates: list[str] | None = None, include_screenshots: bool = False,
           markdown: bool = True, max_experiments: int = 200) -> Envelope[ExportReport]:
    """Write an investigation report to .pyboy-mcp/exports/<name>/ as analysis.json (+ analysis.md, screenshot PNG).

    Includes ROM metadata, discoveries, experiments with screen summaries, snapshots, search sessions, replays, and
    candidate addresses for the experiment names or ids in candidates. Returns file paths and counts.
    """
    core = CONTROLLER.export_analysis(name, candidates, include_screenshots, markdown, max_experiments)
    return envelope(core, ExportReport)


@_expose("investigate")
def screen_changes(baseline: str, current: bool = True, target: str | None = None,
                   mode: Literal["pixel", "grid", "both"] = "both", threshold: int = 0,
                   region: ScreenRegion | None = None, include_sprites: bool = True,
                   include_tiles: bool = True, include_image: bool = False,
                   max_items: int = 50) -> list[Envelope[ScreenChangesData] | Image]:
    """Describe what visibly changed between two frames: changed pixels, 8x8 cells, rectangles, sprites, tiles.

    baseline / target: snapshot ids or names (current=true compares with the live frame). Sprites are matched by OAM
    slot. Background tiles need include_tiles, and snapshots taken with include_tilemap=true. include_image adds a
    2x PNG with changed pixels in red. Never advances frames.
    """
    core, png = CONTROLLER.investigate_screen_changes(baseline, current, target, mode, threshold,
                                                      region.model_dump() if region else None, include_sprites,
                                                      include_tiles, include_image, max_items)
    out: list[Any] = [envelope(core, ScreenChangesData)]
    if png:
        out.append(Image(data=png, format="png"))
    return out
