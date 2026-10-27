# dmg-cartographer

A Game Boy and Game Boy Color reverse-engineering toolkit for AI agents, built on [PyBoy](https://github.com/Baekalfen/PyBoy) and the MCP Python SDK. It ships as a plugin for Claude Code and Codex: an MCP server with about 21 tools, plus four skills that describe how to investigate a game.

It also produces training data. Labelled scenes export to a plain-file dataset, and a state map lets a training loop read the game's state straight from PyBoy, with no agent in the loop.

ROMs are not included. Use only ROMs you are entitled to use.

## Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/). It fetches Python 3.11+, PyBoy and the other dependencies on first use.
- Claude Code or Codex.

## Install

Clone the repo, then point your client at it.

```bash
git clone <this repo> dmg-cartographer
cd dmg-cartographer
uv sync --extra dev   # optional: prepares the environment so the first launch is quick
uv run pytest         # optional: runs the test suite
```

### Claude Code

The folder is a plugin: `.claude-plugin/plugin.json` and `.mcp.json` describe the server, and `skills/` holds the four skills. Install it from the folder, or add it to a marketplace.

The server is launched by `.mcp.json`. It currently runs `uvx --from git+https://github.com/aiecee/dmg-cartographer dmg-cartographer`. Until that repo is public and pushed, change `args` to run from your clone:

```json
{ "mcpServers": { "dmg-cartographer": { "type": "stdio", "command": "uvx", "args": ["--from", "/absolute/path/to/dmg-cartographer", "dmg-cartographer"] } } }
```

### Codex

`.codex-plugin/plugin.json` points at the same `skills/` folder and `.mcp.json`. Codex doesn't expand Claude's `${CLAUDE_PLUGIN_ROOT}`, so the launch path must be absolute, as above. Check that your Codex version reads the `mcpServers` key from plugin files.

## First calls

```text
game(action="list", rom_dir="/absolute/path/to/roms")
game(action="load", rom_path="/absolute/path/to/roms/game.gb")
step(frames=60)
state(action="save", name="start")
screen()
```

`step` is the only tool that advances time. Everything else reads, saves or compares.

## Tools

The server registers about 21 tools:

| Tool | Purpose |
| --- | --- |
| `game` | list ROMs, load one by absolute path, close, reset, status |
| `step`, `button` | advance exactly N frames; hold or release buttons |
| `screen` | the current frame as PNG, with optional overlays |
| `inspect_video` | tilemap, tiles, OAM sprites, palettes, the game-area matrix |
| `memory` | read, decode with typed fields, write (flag required) |
| `state`, `snapshot` | checkpoints on disk; in-memory snapshots |
| `compare`, `screen_changes` | what changed in memory or on screen |
| `search_memory` | narrow candidate addresses over several observations |
| `inspect_code`, `hook_execution` | ROM header, CPU registers, disassembly; execution hooks (flag required) |
| `experiment`, `branch`, `watch`, `replay`, `result` | repeatable experiments and comparisons |
| `discoveries`, `export` | saved hypotheses and report folders |
| `scene` | save and export labelled states as a dataset |
| `state_map` | validate and read a map.json |

Every result has `frame`, `rom_sha1`, `held_buttons` and a `data` object whose `action` says which result it is. The server's instructions and each tool's docstring describe the arguments.

## Skills

- **find-variable** finds and confirms what a memory address means.
- **graphics** works out what is on screen.
- **code** finds the code behind a behaviour.
- **navigate** reaches a target state and repeats the route.

## Where data goes

Each ROM gets one folder. Its name is a slug of the file name followed by the first 8 characters of its SHA-1:

```text
<data root>/Dr-Mario-World-Rev-1-d31d67d0/
  save.ram, save.rtc        battery saves
  checkpoints/              <name>.state and <name>.json
  scenes/<name>/            scene.json, capture.png, state.state
  datasets/<name>/          manifest.json, scenes.jsonl, images/, states/ (optional)
  maps/map.json             the ROM's state map, used when state_map gets no path
  replays/, discoveries/, exports/, history.json
```

The data root is:

1. `DMG_CARTOGRAPHER_DIR`, if set (must be absolute);
2. otherwise `<project>/.pyboy-mcp/` for a ROM kept in `<project>/.pyboy-mcp/roms/`;
3. otherwise `.dmg-cartographer/` next to the ROM.

Data from the earlier flat layout (`saves/`, `checkpoints/`, `scenes/`, `datasets/`, `discoveries/`, `replays/`, `history/`) moves into the ROM folder the first time that ROM is loaded. Files already in the ROM folder are never overwritten, and a second load moves nothing. `exports/` is not moved, because its folders are named by you, not by ROM.

## Environment variables

| Variable | Effect |
| --- | --- |
| `DMG_CARTOGRAPHER_DIR` | absolute data root for all ROMs |
| `DMG_CARTOGRAPHER_ENABLE_WRITES=1` | allows `memory(action="write")` |
| `DMG_CARTOGRAPHER_ENABLE_DEBUG=1` | allows `hook_execution` |
| `DMG_CARTOGRAPHER_GROUPS` | comma-separated tool groups, `raw,investigate` by default |
| `DMG_CARTOGRAPHER_MAX_SNAPSHOTS` | in-memory snapshot and experiment limit, 64 by default |

## Scene dataset format (version 1)

`scene(action="export", name=...)` writes:

- `manifest.json`: `format` (`dmg-cartographer-scenes`), `format_version` (1), ROM identity, `scene_count`, `fields`;
- `scenes.jsonl`: one object per line with `name`, `frame`, `rom_sha1`, `held_buttons`, `screen_fresh`, `note`, `values`, `image`, `state`;
- `images/<name>.png`, and `states/<name>.state` when exported with `include_states`.

Read it without MCP:

```python
from dmg_cartographer.dataset import iter_scenes

for scene in iter_scenes("…/datasets/train-v1"):
    scene.values, scene.image, scene.held_buttons
```

`dmg_cartographer.dataset` uses only the standard library. A reader refuses any other `format_version`.

## State map format (version 1)

A `map.json` says where a game keeps its state. Its JSON Schema is in `src/dmg_cartographer/map.schema.json`, and an example is in `examples/`.

```json
{
  "format": "dmg-cartographer-map", "format_version": 1,
  "rom": "game.gb", "rom_sha1": "<40 hex>",
  "fields": [
    {"name": "board", "address": "0xC800", "type": "uint8", "shape": [16, 8], "status": "hypothesis"},
    {"name": "level", "address": "0xC0A0", "type": "uint16", "endian": "little"}
  ]
}
```

Types are `uint8`, `int8`, `uint16`, `int16` and `bits`. `shape` makes a nested array. `lookup` maps raw values to labels. Addresses must be in WRAM (0xC000-0xDFFF) or HRAM (0xFF80-0xFFFE). `status` is `hypothesis` or `verified`.

Read it from PyBoy, with no MCP:

```python
from dmg_cartographer.state_map import load_map, read_state

state_map = load_map("maps/map.json")
values = read_state(pyboy, state_map, rom_sha1=sha1, labelled=True)
```

The example map's addresses come from earlier calibration and are marked `hypothesis`. Check them on your ROM before relying on them.

## Tests

```bash
uv run pytest
```

Most tests use a synthetic emulator and need no ROM. The real-ROM tests read a Dr. Mario ROM from `DMG_TEST_ROM`, or from `~/projects/dr-mario-re/.pyboy-mcp/roms/`. They are skipped when no ROM is found.

## Limits

- Discovery and map values are hypotheses until confirmed by repeated experiments. The tools say so, and the skills tell agents not to mark anything verified on a correlation alone.
- Frame-sampled watches can miss values that change and change back within one sample window.
- Sprite disappearance and in-place tile changes are described from the analysis code; they haven't been seen on a real ROM yet.
