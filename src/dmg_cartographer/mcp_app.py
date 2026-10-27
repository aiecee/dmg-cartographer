"""Shared MCP instance, tool-group gating, models and the single PyBoy controller."""
from typing import Any, Generic, Literal, TypeVar
import os

from dmg_cartographer.core.controller import PyBoyController
from mcp.server import MCPServer
from pydantic import BaseModel, Field

T = TypeVar("T")

# (no self import) Action, Button, CONTROLLER, Confidence, DataType, Endianness, GROUPS, MemoryFilter, MemoryRegion, SENSITIVE_ENABLED, SENSITIVE_TOOLS, ScreenRegion, SearchCondition, Status, _RETENTION, mcp


_RETENTION = int(os.environ.get("DMG_CARTOGRAPHER_MAX_SNAPSHOTS", "64"))

CONTROLLER = PyBoyController(max_snapshots=_RETENTION, max_experiments=_RETENTION)

Button = Literal["a", "b", "start", "select", "up", "down", "left", "right"]

class Action(BaseModel):
    frames: int = Field(default=1, ge=1, le=600)
    press: list[Button] = Field(default_factory=list)
    release: list[Button] = Field(default_factory=list)

mcp = MCPServer(
    "PyBoy Controller",
    instructions=(
        "Control one local Game Boy emulator, and record what its memory and screen mean. "
        "Start with game(action='load', rom_path=<absolute path to a .gb or .gbc file>). game(action='list', "
        "rom_dir=<absolute folder>) finds ROM files. Each ROM gets one folder holding its checkpoints, scenes, datasets, "
        "maps, saves and knowledge files, next to the ROM or in DMG_CARTOGRAPHER_DIR. "
        "Every result has frame, rom_sha1 and held_buttons, plus a data object whose action field says which "
        "result it is. "
        "The game is PAUSED between calls. Time only moves in step: step(frames=1..600, press, release) or "
        "step(actions=[...]) for up to 25 steps (1800 frames). Nothing else advances frames. A press held with "
        "button() or step() stays held until released. A release takes effect on the next step, with no extra frame. "
        "step(capture=true) returns the screen as a PNG too. screen() gives the current frame, with optional "
        "overlays (pixels, tiles, sprites, regions); it never advances frames. "
        "Checkpoints: state(action='save', name) and state(action='load', name). Use them for trial and error; "
        "loading a checkpoint clears held buttons. Snapshots are in-memory copies for comparing later: "
        "snapshot(action='capture', name), then compare(kind='memory' or 'screen', baseline=<id>). Snapshot and "
        "compare calls never advance frames. "
        "Reading memory: memory(action='read', address, length) returns bytes; memory(action='decode', ...) "
        "interprets bytes with typed fields; search_memory narrows candidate addresses over several observations "
        "(condition unknown, then changed, increased, exact, and so on). "
        "Video: inspect_video(kind='tilemap' | 'tiles' | 'sprites' | 'palette' | 'area') reads the background, "
        "window, OAM sprites and palette registers. screen_changes(baseline=<id>) describes what visibly changed. "
        "Code: inspect_code(kind='cartridge' | 'cpu' | 'rom' | 'disasm') reads the header, registers, ROM bytes and "
        "instructions. hook_execution counts fetches at one address (debug flag only). "
        "Experiments: experiment(action='run', name, actions) snapshots, runs the actions and compares, with a "
        "stored id like exp-0001. experiment(action='rank', names=[...]) ranks addresses that line up with the "
        "named experiments. experiment(action='correlate', names=[...]) ranks them against visible screen changes. "
        "experiment(action='validate', address, checkpoint, experiments, expected) tests one address from a "
        "checkpoint. branch runs several action sequences from one checkpoint. watch advances exactly N frames and "
        "reports changes in the monitors you choose. replay(mode='record') stores a run from a checkpoint, and "
        "replay(mode='replay') checks it still reproduces. result(result_id) fetches a stored branch or replay. "
        "All of these are correlations or repeatability checks, not proof of cause. Confirm them with fresh "
        "experiments from new checkpoints. "
        "Knowledge: discoveries(action='save', ...) records a hypothesis. It never verifies by itself; status "
        "'verified' needs an experiment or replay id as evidence. discoveries(action='list') and export write "
        "the saved set. export(name) writes a report folder. "
        "Training data: scene(action='save', name, address, length, fields) labels the current frame with decoded "
        "values, a PNG and a save state. scene(action='export', name) writes a versioned dataset folder that "
        "dmg_cartographer.dataset.iter_scenes reads without MCP. "
        "State maps: state_map(action='validate' | 'read', path) checks and reads a map.json, which describes "
        "where the game keeps its state. Left out, path is the ROM's own maps/map.json. "
        "Writes need DMG_CARTOGRAPHER_ENABLE_WRITES=1 (memory action write). Hooks need DMG_CARTOGRAPHER_ENABLE_DEBUG=1. "
        "Never assume RAM addresses carry over between games."
    ),
)

# Tool groups: DMG_CARTOGRAPHER_GROUPS=raw,investigate (default both). Sensitive tools are off unless their flag is set:
# DMG_CARTOGRAPHER_ENABLE_WRITES=1 for memory(action="write"), DMG_CARTOGRAPHER_ENABLE_DEBUG=1 for hook_execution.
GROUPS = {g.strip() for g in os.environ.get("DMG_CARTOGRAPHER_GROUPS", "raw,investigate").split(",") if g.strip()}

SENSITIVE_ENABLED = {
    "write": os.environ.get("DMG_CARTOGRAPHER_ENABLE_WRITES") == "1",
    "debug": os.environ.get("DMG_CARTOGRAPHER_ENABLE_DEBUG") == "1",
}

SENSITIVE_TOOLS = {"hook_execution": "debug"}  # memory(action="write") is gated inside the tool

def _expose(group: str):
    """Register a tool only when its group is enabled and, for sensitive tools, its flag is set."""
    def decorator(fn):
        if group not in GROUPS:
            return fn
        flag = SENSITIVE_TOOLS.get(fn.__name__)
        if flag is not None and not SENSITIVE_ENABLED[flag]:
            return fn
        return mcp.tool()(fn)
    return decorator

MemoryRegion = Literal["WRAM", "HRAM"]

MemoryFilter = Literal["changed", "unchanged", "increased", "decreased", "all"]

class ScreenRegion(BaseModel):
    x: int = Field(ge=0, le=159)
    y: int = Field(ge=0, le=143)
    width: int = Field(ge=1, le=160)
    height: int = Field(ge=1, le=144)

class BranchSpec(BaseModel):
    name: str
    actions: list[Action]

DataType = Literal["uint8", "int8", "uint16", "int16"]

Endianness = Literal["little", "big"]

SearchCondition = Literal["unknown", "exact", "range", "increased", "decreased", "changed", "unchanged", "pattern"]

class TileRect(BaseModel):
    layer: Literal["background", "window"]
    x: int = Field(ge=0, le=31)
    y: int = Field(ge=0, le=31)
    width: int = Field(ge=1, le=32)
    height: int = Field(ge=1, le=32)

class LabelledRegion(ScreenRegion):
    label: str | None = Field(default=None, max_length=32)

Confidence = Literal["low", "medium", "high"]

Status = Literal["hypothesis", "verified", "disproven"]

class Perturb(BaseModel):
    value: int = Field(ge=0, le=255)
    frames: int = Field(ge=0, le=600)


class Envelope(BaseModel, Generic[T]):
    """Every tool's result: where the emulator is, which ROM is loaded, and the action's own data."""
    frame: int | None = Field(description="Emulator timeline frame after this call; null when no game is loaded.")
    rom_sha1: str | None = Field(description="SHA-1 of the loaded ROM file; null when no game is loaded.")
    held_buttons: list[str] = Field(description="Buttons held down after this call, sorted.")
    data: T = Field(description="The action's result, validated against the tool's output union.")


def envelope(data: dict[str, Any], shape: Any) -> Envelope:
    """Wrap an action's result with the emulator state at the time it returned. shape is the tool's output union."""
    status = CONTROLLER.status()
    return Envelope[shape](frame=status["timeline_frame"], rom_sha1=CONTROLLER._rom_sha1,
                           held_buttons=status["held_buttons"], data=data)
