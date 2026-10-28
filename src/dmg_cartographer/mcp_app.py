"""Shared MCP instance, tool-group gating, models and the single PyBoy controller."""
from typing import Any, Generic, Literal, TypeVar
import functools
import os

from dmg_cartographer.core.controller import PyBoyController
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
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
    "dmg-cartographer",
    instructions=(
        "Control one local Game Boy emulator, and record what its memory and screen mean. "
        "Start with game(action='load', project_dir=<absolute project folder>, "
        "rom_path=<absolute path to a .gb or .gbc file>). The game is PAUSED between calls; only step advances "
        "frames, and a pressed button stays held until released. state(action='save'|'load', name) are "
        "checkpoints. Every result has frame, rom_sha1, held_buttons and a data object. "
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

def _expose(group: str, read_only: bool = False):
    """Register a tool only when its group is enabled and, for sensitive tools, its flag is set.

    read_only marks tools that never advance frames or write files, so clients can skip approval prompts."""
    def decorator(fn):
        if group not in GROUPS:
            return fn
        flag = SENSITIVE_TOOLS.get(fn.__name__)
        if flag is not None and not SENSITIVE_ENABLED[flag]:
            return fn
        mcp.tool(annotations=ToolAnnotations(readOnlyHint=True) if read_only else None)(_report_errors(fn))
        return fn
    return decorator


def _report_errors(fn):
    """Re-raise ValueError and RuntimeError as ToolError so the client sees the message.

    The SDK sends only "Error executing tool <name>" for any other exception, which hides every input check."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, RuntimeError) as exc:
            raise ToolError(str(exc)) from exc
    return wrapper

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
