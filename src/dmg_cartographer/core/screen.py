"""ScreenMixin: the screen operations of PyBoyController."""
from __future__ import annotations

import io
from typing import Any

from dmg_cartographer.core.shared import _png, read_oam, tile_rect_in_map
from dmg_cartographer import analysis
from dmg_cartographer import graphics


def _tile_image(tile):
    """PyBoy 2.x exposes Tile.image() as a method that returns a PIL image; test doubles expose a plain image."""
    image = tile.image
    return image() if callable(image) else image


class ScreenMixin:
    """Methods for the screen part of the emulator controller."""

    def screen_png(self, scale: int = 2) -> bytes:
        with self._lock:
            game = self._require_game()
            if isinstance(scale, bool) or not isinstance(scale, int) or not 1 <= scale <= 4:
                raise ValueError("scale must be an integer from 1 to 4")
            frame = game.screen.image.copy()
            if scale != 1:
                from PIL import Image as PILImage
                frame = frame.resize((frame.width * scale, frame.height * scale), PILImage.Resampling.NEAREST)
            data = io.BytesIO()
            frame.save(data, format="PNG")
            return data.getvalue()


    def tilemap(self, layer: str = "background", x: int = 0, y: int = 0, width: int = 20, height: int = 18) -> dict[str, Any]:
        with self._lock:
            game = self._require_game()
            if layer not in ("background", "window"):
                raise ValueError("layer must be 'background' or 'window'")
            if any(type(n) is not int for n in (x, y, width, height)) or not tile_rect_in_map(x, y, width, height):
                raise ValueError("tilemap rectangle must be inside the 32x32 grid")
            tilemap = game.tilemap_background if layer == "background" else game.tilemap_window
            return {"layer": layer, "x": x, "y": y, "width": width, "height": height,
                    "tiles": [[int(tilemap[col, row]) for col in range(x, x + width)]
                              for row in range(y, y + height)]}


    def _header_cgb(self, game: Any) -> dict[str, Any]:
        try:
            return graphics.cartridge_is_cgb(int(game.memory[0, graphics.CGB_FLAG_ADDR]))
        except Exception as exc:  # unreadable header: report it rather than guessing
            return {"header_cgb_flag": None, "cgb_compatible": None, "cgb_only": None,
                    "error": f"{type(exc).__name__}: {exc}"}


    @staticmethod
    def _check_screen_region(region: Any) -> dict[str, int]:
        if not isinstance(region, dict):
            raise ValueError("region must be an object with x, y, width, height")
        try:
            x, y, w, h = (int(region[k]) for k in ("x", "y", "width", "height"))
        except (KeyError, TypeError, ValueError):
            raise ValueError("region needs integer x, y, width, height") from None
        if w < 1 or h < 1 or x < 0 or y < 0 or x + w > graphics.SCREEN_W or y + h > graphics.SCREEN_H:
            raise ValueError("region must lie inside the 160x144 screen")
        return {"x": x, "y": y, "width": w, "height": h}


    def inspect_sprites(self, indices: list[int] | None = None, region: dict[str, int] | None = None,
                        include_images: bool = False, max_results: int = 40) -> tuple[dict[str, Any], list[bytes]]:
        with self._lock:
            game = self._require_game()
            if indices is not None:
                if not isinstance(indices, list) or not 1 <= len(indices) <= graphics.SPRITE_COUNT or any(
                        isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < graphics.SPRITE_COUNT
                        for i in indices):
                    raise ValueError("indices must be a list of sprite slots 0..39")
            if region is not None:
                region = self._check_screen_region(region)
            if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 40:
                raise ValueError("max_results must be 1..40")
            oam, lcdc = read_oam(game)
            header = self._header_cgb(game)
            rows = graphics.sprite_rows(oam, lcdc, cgb=bool(header.get("cgb_compatible")), indices=indices,
                                        region=region)
            pngs: list[bytes] = []
            if include_images:
                if len(rows) > 8:
                    raise ValueError("include_images returns at most 8 sprites; filter with indices or region")
                for row in rows:
                    pngs.append(_png(_tile_image(game.get_sprite(row["index"]).tiles[0])))
            result = {
                "frame": self._frames,
                "lcdc": f"0x{lcdc:02X}", "sprite_height": 16 if lcdc & 0x04 else 8,
                "cgb_header": header,
                "count": len(rows), "returned": min(len(rows), max_results),
                "truncated": len(rows) > max_results,
                "sprites": rows[:max_results],
                "coordinates": "raw = OAM bytes; screen = raw minus (8, 16); screen is the 160x144 viewport",
            }
            return result, pngs


    def inspect_tiles(self, layer: str = "background", x: int = 0, y: int = 0, width: int = 20, height: int = 18,
                      include_images: bool = False, include_grid: bool = False,
                      include_attributes: bool = False) -> tuple[dict[str, Any], list[bytes], bytes | None]:
        if layer not in ("background", "window"):
            raise ValueError("layer must be 'background' or 'window'")
        for label, value in (("x", x), ("y", y), ("width", width), ("height", height)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{label} must be an integer")
        if not tile_rect_in_map(x, y, width, height):
            raise ValueError("tilemap rectangle must be inside the 32x32 grid")
        if include_images and width * height > 16:
            raise ValueError("include_images returns at most 16 tiles; request a smaller rectangle")
        with self._lock:
            game = self._require_game()
            lcdc = int(game.memory[graphics.LCDC])
            scx, scy = int(game.memory[graphics.SCX]), int(game.memory[graphics.SCY])
            wx, wy = int(game.memory[graphics.WX]), int(game.memory[graphics.WY])
            header = self._header_cgb(game)
            tilemap = game.tilemap_background if layer == "background" else game.tilemap_window
            base = graphics.map_base(lcdc, layer)
            tiles, images = [], []
            for row in range(y, y + height):
                for col in range(x, x + width):
                    entry = {"tile_id": int(tilemap[col, row]),
                             **graphics.tile_placement(layer, col, row, scx=scx, scy=scy, wx=wx, wy=wy)}
                    if include_attributes:
                        if header.get("cgb_compatible"):
                            value = int(game.memory[1, base + row * 32 + col])
                            entry["attributes"] = graphics.decode_tile_attribute(value)
                        else:
                            entry["attributes"] = "unavailable: cartridge header is not CGB-compatible"
                    if include_images:
                        images.append(_png(_tile_image(tilemap.tile(col, row))))
                    tiles.append(entry)
            grid = None
            if include_grid:
                grid = _png(graphics.annotate(analysis.normalise_screen(game.screen.image), tiles=True))
            scroll = ({"scx": scx, "scy": scy} if layer == "background" else {"wx": wx, "wy": wy})
            result = {
                "frame": self._frames, "layer": layer,
                "tilemap_rect": {"x": x, "y": y, "width": width, "height": height},
                "map_base": f"0x{base:04X}", "lcdc": f"0x{lcdc:02X}", "scroll": scroll,
                "cgb_header": header,
                "tiles": tiles,
                "coordinates": ("tilemap = col/row in the 32x32 map; pixel = x/y in the 256x256 map; "
                                "viewport = screen x/y (may be negative for partly visible tiles)"),
                "hardware_mode": "the running hardware mode (DMG or CGB) is not exposed by PyBoy's public API",
            }
            return result, images, grid


    def annotate_screen(self, overlays: list[str] | None = None, regions: list[dict[str, Any]] | None = None,
                        scale: int = 2) -> tuple[bytes, dict[str, Any]]:
        overlays = ["pixels", "tiles"] if overlays is None else overlays
        valid = {"pixels", "tiles", "sprites", "regions"}
        if not isinstance(overlays, list) or not overlays or any(o not in valid for o in overlays):
            raise ValueError(f"overlays must be a non-empty list from {sorted(valid)}")
        if isinstance(scale, bool) or not isinstance(scale, int) or not 1 <= scale <= 4:
            raise ValueError("scale must be 1-4")
        clean_regions = []
        for r in regions or []:
            clean = self._check_screen_region(r)
            label = r.get("label")
            if label is not None and (not isinstance(label, str) or len(label) > 32):
                raise ValueError("region label must be a string of up to 32 characters")
            clean_regions.append({**clean, "label": label})
        if len(clean_regions) > 16:
            raise ValueError("at most 16 regions")
        with self._lock:
            game = self._require_game()
            image = analysis.normalise_screen(game.screen.image)
            sprites = []
            if "sprites" in overlays:
                oam, lcdc = read_oam(game)
                sprites = graphics.sprite_rows(oam, lcdc, cgb=bool(self._header_cgb(game).get("cgb_compatible")))
            png = _png(graphics.annotate(image, pixels="pixels" in overlays, tiles="tiles" in overlays,
                                         sprites=sprites, regions=clean_regions if "regions" in overlays else [],
                                         scale=scale))
            return png, {"overlays": overlays, "sprites_drawn": len(sprites), "regions_drawn": len(clean_regions),
                         "framebuffer_modified": False, "frame": self._frames}


    def get_palette(self) -> dict[str, Any]:
        """DMG palette registers. A shade is the 2-bit value stored in the register, not a displayed colour."""
        with self._lock:
            game = self._require_game()
            registers = {"BGP": 0xFF47, "OBP0": 0xFF48, "OBP1": 0xFF49}
            palettes = {}
            for name, address in registers.items():
                value = int(game.memory[address])
                palettes[name] = {"address": f"0x{address:04X}", "raw": f"0x{value:02X}",
                                  "shade_by_color_index": {str(i): (value >> (2 * i)) & 3 for i in range(4)}}
            return {"frame": self._frames, "dmg_palettes": palettes,
                    "cgb_palette_ram": "not exposed by PyBoy's public API; CGB colour palettes cannot be read here",
                    "note": "the on-screen grey levels depend on the emulator's colour mapping, which is not read"}
