---
name: graphics
description: Work out what is on a Game Boy screen - tiles, sprites (OAM), background and window maps, palettes, and which pixels changed between frames. Use when the user asks what is drawn, where a sprite or tile is, what a visual change means, or how to locate objects on screen.
---

# Read the screen

Goal: say what is drawn and where, using structured data rather than guessing from the picture.

Start by looking, then measure. `screen()` returns the frame as a PNG; overlays draw helpers on a copy and never change the frame.

## Tools

- `screen(scale=2)`: the current frame. Add `overlays=['pixels','tiles','sprites','regions']` to draw a tile grid and sprite boxes.
- `inspect_video(kind='sprites', indices=[...], region=..., include_images=true)`: decoded OAM entries (slots 0-39). Gives screen position, tile index, flip, priority, palette. Only hardware sprites appear here.
- `inspect_video(kind='tilemap', layer='background'|'window', x, y, width, height)`: tile IDs in a rectangle of the 32x32 map.
- `inspect_video(kind='tiles', ..., include_images=true, include_grid=true)`: tile IDs with where each one sits on screen (viewport), with optional tile images. `include_attributes=true` adds CGB palette and flip bytes on CGB cartridges.
- `inspect_video(kind='palette')`: the DMG palette registers BGP, OBP0 and OBP1, with the shade each colour index maps to.
- `inspect_video(kind='area')`: PyBoy's generic game-area tile matrix.
- `compare(kind='screen', baseline=<snapshot id>, ...)`: changed pixels, bounding box and changed 8x8 cells.
- `screen_changes(baseline=<snapshot id>)`: a fuller description of a change, including sprites that moved, appeared or disappeared, and background tiles that changed. Background tiles need snapshots taken with `include_tilemap=true`.
- `snapshot(action='capture', name=..., include_tilemap=true)`: store a frame for comparing later.

## Workflow

1. `screen()` and look. Name the things you see, such as the player, enemies, pickups, the HUD.
2. `inspect_video(kind='sprites')` to find which OAM slots draw those objects. Note each slot's `tile_index`.
3. Background or window objects are not sprites. Use `inspect_video(kind='tilemap')` for them, and `inspect_video(kind='tiles')` for where they are on screen.
4. To find which slot is which object, change the object with one input and compare: `snapshot`, `step`, then `screen_changes` with the baseline id. A sprite that moved keeps its OAM slot in the report (`identity_basis` says what the slot match does and does not prove).
5. Confirm the mapping with a second input before recording it. Store it with `discoveries(action='save', ...)`.

## Pitfalls

- A sprite's screen coordinates include an 8 and 16 pixel offset from OAM. `inspect_video(kind='sprites')` already converts them; check `on_screen` before trusting a position.
- `screen()` shows the current frame. A freshly loaded checkpoint may not have rendered yet. Advance one frame (`step(frames=1)`) if `screen_fresh` is false.
- Palettes change colours but not tile IDs. A colour change alone is not a new object.
- Sprite slot identity is an observation, not an object identity. The game can reuse a slot for a different object.
- `include_images` and grids add data to the response. Use them when the answer depends on the picture, not by default.
