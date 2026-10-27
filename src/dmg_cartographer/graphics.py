"""Pure graphics interpretation: OAM sprite decoding, tilemap placement, grid overlays. No emulator access.

Coordinate systems used throughout (never mixed in one field):
- tilemap coordinates: column/row in a 32x32 tile map (0-31).
- pixel coordinates: x/y in the 256x256 background (or window) map, 0-255.
- viewport coordinates: screen pixels after scrolling, 0-159 by 0-143; may be negative when a tile is
  only partly on screen.
"""
from __future__ import annotations

from typing import Any, Sequence

OAM_START = 0xFE00
OAM_END = 0xFEA0
SPRITE_COUNT = 40
SCREEN_W, SCREEN_H = 160, 144
LCDC = 0xFF40
SCY, SCX = 0xFF42, 0xFF43
WY, WX = 0xFF4A, 0xFF4B
CGB_FLAG_ADDR = 0x0143
MAP_SIZE = 32


def sprite_rows(oam: Sequence[int], lcdc: int, *, cgb: bool, indices: list[int] | None = None,
                region: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Decode OAM entries. Raw hardware values and interpreted screen values are kept in separate fields.

    `indices` filters by OAM slot. `region` keeps sprites whose screen rectangle overlaps it.
    Off-screen or hidden sprites are still listed, with on_screen False.
    """
    if len(oam) != OAM_END - OAM_START:
        raise ValueError(f"OAM must be {OAM_END - OAM_START} bytes")
    tall = bool(lcdc & 0x04)
    height = 16 if tall else 8
    wanted = range(SPRITE_COUNT) if indices is None else indices
    rows = []
    for index in wanted:
        raw_y, raw_x, tile, attr = (int(v) for v in oam[index * 4:index * 4 + 4])
        x, y = raw_x - 8, raw_y - 16
        on_screen = -height < y < SCREEN_H and -8 < x < SCREEN_W
        if region is not None and not _overlaps(x, y, 8, height, region):
            continue
        row = {
            "index": index,
            "raw": {"y": raw_y, "x": raw_x, "tile": tile, "attributes": f"0x{attr:02X}"},
            "screen": {"x": x, "y": y, "width": 8, "height": height},
            "on_screen": on_screen,
            "tile_index": tile & 0xFE if tall else tile,
            "tile_indices": [tile & 0xFE, (tile & 0xFE) + 1] if tall else [tile],
            "priority_behind_background": bool(attr & 0x80),
            "flip": {"x": bool(attr & 0x20), "y": bool(attr & 0x40)},
            "palette": {"dmg": (attr >> 4) & 1},
        }
        if cgb:
            row["palette"]["cgb"] = attr & 0x07
            row["palette"]["cgb_vram_bank"] = (attr >> 3) & 1
        rows.append(row)
    return rows


def _overlaps(x: int, y: int, w: int, h: int, region: dict[str, int]) -> bool:
    return (x < region["x"] + region["width"] and x + w > region["x"]
            and y < region["y"] + region["height"] and y + h > region["y"])


def map_base(lcdc: int, layer: str) -> int:
    """Tile map base address selected by LCDC. Background uses bit 3, window uses bit 6."""
    bit = 0x08 if layer == "background" else 0x40
    return 0x9C00 if lcdc & bit else 0x9800


def tile_placement(layer: str, col: int, row: int, *, scx: int, scy: int, wx: int, wy: int) -> dict[str, Any]:
    """Where one map tile sits: its pixel origin in the map, and its viewport position if visible.

    Background tiles scroll with SCX/SCY. The window is anchored at (WX-7, WY) and does not scroll.
    """
    pixel = {"x": col * 8, "y": row * 8}
    if layer == "window":
        vx, vy = col * 8 + wx - 7, row * 8 + wy
        visible = vx < SCREEN_W and vx + 8 > 0 and vy < SCREEN_H and vy + 8 > 0 and wx < 167 and wy < 144
    else:
        dx, dy = (col * 8 - scx) % 256, (row * 8 - scy) % 256
        vx = dx if dx < SCREEN_W else dx - 256
        vy = dy if dy < SCREEN_H else dy - 256
        visible = (dx < SCREEN_W or dx > 248) and (dy < SCREEN_H or dy > 248)
    return {"tilemap": {"col": col, "row": row}, "pixel": pixel,
            "viewport": {"x": vx, "y": vy} if visible else None, "visible": visible}


def decode_tile_attribute(value: int) -> dict[str, Any]:
    """CGB background map attribute byte (VRAM bank 1 at the same address as the tile ID)."""
    return {"raw": f"0x{value:02X}", "cgb_palette": value & 0x07, "vram_bank": (value >> 3) & 1,
            "flip": {"x": bool(value & 0x20), "y": bool(value & 0x40)},
            "priority": bool(value & 0x80)}


def cartridge_is_cgb(header_byte: int) -> dict[str, Any]:
    """Header byte 0x0143: 0x80 = works on DMG and CGB, 0xC0 = CGB only, otherwise DMG only."""
    return {"header_cgb_flag": f"0x{header_byte:02X}", "cgb_compatible": header_byte in (0x80, 0xC0),
            "cgb_only": header_byte == 0xC0}


def annotate(image, *, pixels: bool = False, tiles: bool = False, sprites: list[dict[str, Any]] | None = None,
             regions: list[dict[str, Any]] | None = None, scale: int = 2):
    """Overlay labels on a COPY of the screen. Overlays: pixel ticks, 8-px tile grid, sprite boxes, regions.

    `sprites` are rows from sprite_rows(); `regions` are {"x","y","width","height","label"} in screen pixels.
    The input image is never modified, so the emulator's framebuffer is unaffected.
    """
    from PIL import ImageDraw

    canvas = image.resize((image.width * scale, image.height * scale)).convert("RGB")
    draw = ImageDraw.Draw(canvas)
    if tiles:
        for x in range(0, image.width + 1, 8):
            draw.line([(x * scale, 0), (x * scale, canvas.height - 1)], fill=(255, 255, 0))
        for y in range(0, image.height + 1, 8):
            draw.line([(0, y * scale), (canvas.width - 1, y * scale)], fill=(255, 255, 0))
    if pixels:
        for x in range(0, image.width, 16):
            draw.text((x * scale + 2, 2), str(x), fill=(0, 255, 255))
        for y in range(16, image.height, 16):
            draw.text((2, y * scale + 2), str(y), fill=(0, 255, 255))
    for row in sprites or []:
        s = row["screen"]
        box = [s["x"] * scale, s["y"] * scale, (s["x"] + s["width"]) * scale - 1, (s["y"] + s["height"]) * scale - 1]
        draw.rectangle(box, outline=(0, 255, 0))
        draw.text((box[0] + 1, box[1] + 1), f"S{row['index']}", fill=(0, 255, 0))
    for region in regions or []:
        box = [region["x"] * scale, region["y"] * scale, (region["x"] + region["width"]) * scale - 1,
               (region["y"] + region["height"]) * scale - 1]
        draw.rectangle(box, outline=(255, 0, 255))
        if region.get("label"):
            draw.text((box[0] + 2, box[3] - 12), str(region["label"]), fill=(255, 0, 255))
    return canvas
