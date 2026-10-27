---
name: navigate
description: Drive a Game Boy game to a target state and make the route repeatable - button input, step sequences, checkpoints, recording and replaying a run, and watching what changes in a window of frames. Use when the user wants to get the game to a particular screen, level or situation, or to reproduce or check a run.
---

# Navigate the game

Goal: reach a target state reliably, and keep a route you can repeat, so other experiments start from the same place.

Time moves only through `step` (and `watch`, `experiment` and `branch` runs). Everything else is a read.

## Tools

- `step(frames=N, press=[...], release=[...])`: advance exactly N frames (1-600). Presses stay held until released. A release takes effect on the next step.
- `step(actions=[{"frames": .., "press": [..], "release": [..]}, ...])`: up to 25 steps, 1800 frames in total, in one call. Use it for button sequences.
- `button(button='a', action='press'|'release')`: hold or let go of one button. No frames advance; the change applies on the next step.
- `state(action='save', name=...)`, `state(action='load', name=...)`, `state(action='list')`: checkpoints for the loaded ROM. Loading clears held buttons, so press again explicitly.
- `game(action='reset')`: restart from frame 0. Hooks are cleared; checkpoints stay.
- `replay(name, mode='record', checkpoint=..., actions=[...])`: run from a checkpoint and store the steps with a fingerprint of memory, frame count and held buttons. `replay(name, mode='replay')` restores the checkpoint, reruns, and reports every fingerprint field that differs.
- `watch(frames=N, addresses=[...], actions=[...])`: advance exactly N frames and report monitored changes frame by frame.
- `branch(checkpoint, branches=[{name, actions}, ...])`: run several routes from one checkpoint and compare them. The live emulator is restored afterwards.
- `result(result_id)`: fetch a stored branch or replay result. `screen()` shows the result on screen.

## Workflow

1. `game(action='status')` to confirm the ROM and frame. If nothing is loaded, load it first.
2. Get to a stable starting point: a title screen or menu with no animation running. `step` a few frames and check `screen()`.
3. Save the start: `state(action='save', name='title')`.
4. Work toward the target in short hops. For each hop, `step(actions=[...])` with explicit frame counts, then `screen()` to check. Save a checkpoint at each milestone so you can return to it.
5. When you reach the target, `state(action='save', name='<target>')`. Then `replay(name, mode='record', checkpoint=<start>, actions=[...])` to store the route.
6. To check the route still works, `state(action='load', name=<start>)` then `replay(name, mode='replay')`. Look at `matched`, `mismatches` and `frames_match_recording`.
7. For a single window of frames, use `watch` to see what changed and when.
8. To test several ways to reach the same state, use `branch` with one branch per route.

## Pitfalls

- Frame counts are exact. Menus and text need enough frames to finish; a "press start" that lands during a fade does nothing. Check the screen after each hop.
- A held button stays held until released in a later step. Release what you press.
- Checkpoints are emulator states. They don't survive a ROM change, and the fingerprint doesn't cover VRAM, OAM, I/O or timers, so a replay can match while the picture differs. Check the screen.
- Randomness in the game (seeds from timers or input timing) can make replays diverge. A mismatch in `end.digest` with the same inputs points to that.
- `step(capture=true)` returns the screen after the step. `screen()` alone doesn't advance time.
- Don't store a route as your only record of how to reach a state. Save a checkpoint too, since a route with a wrong frame count is hard to debug from the inputs alone.
