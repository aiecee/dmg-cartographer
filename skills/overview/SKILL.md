---
name: overview
description: Investigate or automate a Game Boy game (.gb/.gbc ROM) with the dmg-cartographer emulator tools - play it, read its screen and memory, find where it keeps its state (board, score, pieces, lives), and build state maps and datasets for RL or ML. Start here for any Game Boy ROM task, then use the navigate, find-variable, graphics or code skill for the step at hand.
---

# dmg-cartographer overview

Goal: understand how a Game Boy game keeps its state, with evidence, and hand that knowledge to other code (a state map, a dataset, a report).

The tools are MCP tools from the `dmg-cartographer` server (in Codex: `mcp__dmg_cartographer__*`). They keep checkpoints, experiments and discoveries for the ROM, so other sessions can reuse them.

## How the emulator behaves

- Start with `game(action='load', project_dir=<absolute project folder>, rom_path=<absolute .gb/.gbc path>)`. The ROM is copied into `<project_dir>/.dmg-cartographer/roms/`; later loads can use `rom_name` from `game(action='list')`. Each ROM has its own folder there for checkpoints, scenes, maps and knowledge.
- The game is paused between calls. Only `step` moves time (and `watch`, `experiment`, `branch` and `replay`, which run steps). A pressed button stays held until released.
- `state(action='save'|'load', name)` are checkpoints on disk. Loading one clears held buttons. Save one before any trial and error.
- `snapshot(action='capture')` keeps an in-memory copy; `compare` and `screen_changes` diff against it. None of these advance frames.
- Every result has `frame`, `rom_sha1`, `held_buttons` and a `data` object whose `action` says which result it is.
- Writing memory needs `DMG_CARTOGRAPHER_ENABLE_WRITES=1`. `hook_execution` needs `DMG_CARTOGRAPHER_ENABLE_DEBUG=1`.

## Which skill for which job

| Job | Skill | Main tools |
|---|---|---|
| Reach a screen, level or situation, and make the route repeatable | `navigate` | `step`, `state`, `replay`, `branch`, `watch` |
| Read what is on screen as data: sprites, board tiles, HUD digits | `graphics` | `inspect_video`, `screen`, `screen_changes` |
| Find which RAM byte holds a value (score, level, piece, timer) | `find-variable` | `search_memory`, `experiment`, `memory` |
| Find the code behind a behaviour | `code` | `inspect_code`, `hook_execution` |

## Usual order

1. Load the ROM and get to a stable point (`navigate`). Save a checkpoint.
2. Read what the screen shows as data (`graphics`). Moving objects are usually sprites; boards and HUDs are usually background or window tiles.
3. Find the RAM behind each value (`find-variable`), and check it against what the screen shows.
4. Record each finding with `discoveries(action='save', ...)`. Status `verified` needs an experiment or replay id as evidence.
5. Hand it on:
   - `state_map`: the ROM's `maps/map.json` lists where the game keeps its state. An RL environment or other code reads the game through it.
   - `scene(action='save')` labels the current frame with decoded values, a PNG and a save state; `scene(action='export')` writes a dataset that `dmg_cartographer.dataset.iter_scenes` reads without MCP.
   - `export(name)` writes a report of discoveries and experiments.

## Pitfalls

- Rank, correlate, branch and watch results are correlations, not proof of cause. Confirm with fresh experiments from new checkpoints.
- Never assume RAM addresses carry over between games, or between versions of one game.
- Act on the user's goal with the tools; checkpoints make trial and error cheap. A plan with no tool calls rarely helps.
