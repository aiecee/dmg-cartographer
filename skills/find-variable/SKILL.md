---
name: find-variable
description: Find and confirm what a Game Boy memory address means (score, health, position, timer, state flags) using snapshots, experiments, search and validation. Use when the user wants to know which RAM byte holds some game value, or wants to confirm or disprove a candidate address.
---

# Find a game variable

Goal: a memory address, with its type and meaning, backed by repeatable experiments and recorded as a discovery.

The emulator is paused between calls. Only `step` and `watch`, and experiment and branch runs, move time.

## Workflow

1. **Load and go to a repeatable point.** `game(action='load', project_dir=<absolute project folder>, rom_path=<absolute ROM path>)`. Advance to a stable state with `step`, then `state(action='save', name='start')`. Every later comparison starts from that checkpoint.
2. **Pick the value to find and how it changes.** Write down the on-screen meaning and the input that should change it, such as "pressing right moves the player one tile".
3. **Gather candidates.** Two routes:
   - Experiments: `experiment(action='run', name='right', actions=[...])` from the checkpoint, then run `left` and `idle` the same way, each with a fresh `state(action='load', name='start')` first. Reuse one name per action so repeats group together.
   - Search: `search_memory(action='search', condition='unknown')` keeps every address. Then step, and narrow with `condition='changed'`, `'increased'`, `'decreased'` or `'exact'` (with `exact=<value>`), passing the returned `session` each time.
4. **Rank them.** `experiment(action='rank', names=['right','left','idle'], expected_change={'right': 'increase', 'left': 'decrease', 'idle': 'none'})`. Rank output is a correlation, not proof.
5. **Validate from a fresh checkpoint.** `experiment(action='validate', address='0xC123', checkpoint='start', experiments={'right': [...], 'left': [...], 'idle': [...]}, expected={...}, repeats=2)`. The verdict is `supported_by_experiments`, `contradicted` or `inconclusive`. A supported result still doesn't prove the cause. Try `perturb` with `allow_writes=true` only when you need to show the byte drives the behaviour, and remember writes need `DMG_CARTOGRAPHER_ENABLE_WRITES=1`.
6. **Check the meaning across states.** Repeat from other checkpoints, such as a different level or score. Read the byte with `memory(action='decode', address=..., length=..., fields=[...])` to see its type (uint8, uint16 little/big endian, bcd, bits).
7. **Record it.** `discoveries(action='save', name='player_x', address='0xC123', region='WRAM', data_type='uint8', confidence='medium', description='...', evidence=['exp-0003'])`. Set `status='verified'` only with experiment ids as evidence; never mark it verified because the rank looked good.

## Common mistakes

- Comparing experiments that start from different states. Always `state(action='load', ...)` before each run.
- Trusting one run. A byte that moved once is a candidate. Two repeats and a control (`idle`) are the minimum.
- Confusing bytes that change every frame (timers, RNG, animation counters) with the variable. Use `idle` to rule them out.
- Using `snapshot` or `compare` output as proof. They show what changed, not why.
- Assuming an address carries over to another game. It doesn't.

## Storing results for training

Once a variable is confirmed, add it to the ROM's map (`state_map`, path left out, so `maps/map.json`) with `status: "verified"` only if the discovery is verified. `scene(action='save')` labels states with the decoded values.
