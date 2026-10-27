import io

import pytest
from PIL import Image

from dmg_cartographer import analysis


def blank(color=(0, 0, 0)):
    return Image.new("RGB", (160, 144), color)


def test_identical_memory_has_no_differences():
    data = bytes(range(256)) * 4
    result = analysis.compare_memory(data, data, 0xC000)
    assert result["bytes_examined"] == 1024
    assert result["changed_count"] == result["match_count"] == 0
    assert result["changes"] == [] and result["groups"] == []


def test_single_and_multiple_memory_changes_with_groups_and_diffs():
    before = bytearray(16)
    after = bytearray(16)
    after[2] = 5                       # 0 -> 5
    after[3], after[4] = 1, 2          # contiguous with 2
    before[9], after[9] = 255, 0       # wraps
    result = analysis.compare_memory(bytes(before), bytes(after), 0xC000)
    assert result["changed_count"] == 4
    assert [c["address"] for c in result["changes"]] == ["0xC002", "0xC003", "0xC004", "0xC009"]
    first = result["changes"][0]
    assert first["previous"] == {"dec": 0, "hex": "0x00"} and first["current"] == {"dec": 5, "hex": "0x05"}
    wrap = result["changes"][3]
    assert (wrap["diff"], wrap["diff_unsigned"], wrap["diff_wrapped"]) == (-255, 1, 1)
    assert result["groups"] == [{"start": "0xC002", "end": "0xC004", "length": 3},
                                {"start": "0xC009", "end": "0xC009", "length": 1}]


def test_memory_filters_and_truncation():
    before = bytes([10, 10, 10, 10, 10])
    after = bytes([10, 11, 9, 20, 10])

    def addresses(**kw):
        return [c["address"] for c in analysis.compare_memory(before, after, 0xFF80, **kw)["changes"]]

    assert addresses(filter="increased") == ["0xFF81", "0xFF83"]
    assert addresses(filter="decreased") == ["0xFF82"]
    assert addresses(filter="unchanged") == ["0xFF80", "0xFF84"]
    assert len(addresses(filter="all")) == 5
    assert addresses(filter="all", current_value=20) == ["0xFF83"]
    assert addresses(filter="all", previous_value=10, current_value=9) == ["0xFF82"]
    limited = analysis.compare_memory(before, after, 0xFF80, max_results=1)
    assert (limited["match_count"], limited["returned"], limited["truncated"]) == (3, 1, True)
    with pytest.raises(ValueError):
        analysis.compare_memory(before, after, 0, filter="bigger")
    with pytest.raises(ValueError):
        analysis.compare_memory(before, after, 0, current_value=256)


def test_identical_screens_and_format_normalisation():
    rgba = Image.new("RGBA", (160, 144), (40, 80, 120, 255))
    result, png = analysis.compare_screen(rgba, rgba.convert("RGB"))
    assert result["changed_pixels"] == 0
    assert result["bounding_box"] is None and result["areas"] == []
    assert result["grid"]["changed_cells"] == 0
    assert png is None
    with pytest.raises(ValueError):
        analysis.compare_screen(Image.new("RGB", (10, 10)), blank())


def test_one_pixel_change():
    after = blank()
    after.putpixel((17, 9), (255, 255, 255))
    result, _ = analysis.compare_screen(blank(), after)
    assert result["changed_pixels"] == 1
    assert result["percent_changed"] == round(100 / (160 * 144), 3)
    assert result["bounding_box"] == {"x": 17, "y": 9, "width": 1, "height": 1}
    assert result["grid"]["cells"] == [{"col": 2, "row": 1, "x": 16, "y": 8, "changed_pixels": 1}]


def test_multi_tile_change_and_separate_areas():
    after = blank()
    after.paste((200, 0, 0), (20, 20, 36, 36))      # 16x16 block spanning 3x3 cells
    after.paste((0, 200, 0), (120, 100, 128, 108))  # one aligned cell, far away
    result, png = analysis.compare_screen(blank(), after, include_image=True)
    assert result["changed_pixels"] == 16 * 16 + 64
    assert result["bounding_box"] == {"x": 20, "y": 20, "width": 108, "height": 88}
    # The 8x8 block at y=100..107 straddles two cell rows, so it is two cells.
    assert result["areas"] == [{"x": 20, "y": 20, "width": 16, "height": 16, "cells": 9},
                               {"x": 120, "y": 100, "width": 8, "height": 8, "cells": 2}]
    assert result["grid"]["changed_cells"] == 11
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (320, 288)


def test_threshold_region_and_modes():
    after = blank()
    after.putpixel((5, 5), (3, 0, 0))            # tiny colour change
    after.paste((255, 255, 255), (100, 100, 108, 108))
    result, _ = analysis.compare_screen(blank(), after, threshold=3)
    assert result["changed_pixels"] == 64
    roi, _ = analysis.compare_screen(blank(), after, region={"x": 0, "y": 0, "width": 50, "height": 50})
    assert roi["changed_pixels"] == 1 and roi["pixels_examined"] == 2500
    pixel_only, _ = analysis.compare_screen(blank(), after, mode="pixel")
    assert "grid" not in pixel_only and "areas" in pixel_only
    grid_only, _ = analysis.compare_screen(blank(), after, mode="grid")
    # threshold 0 also counts the faint pixel at (5,5): 1 cell there + 4 cells for the white block.
    assert "areas" not in grid_only and grid_only["grid"]["changed_cells"] == 5


def test_candidates_require_known_names_and_valid_rules():
    exps = [{"id": "exp-0001", "name": "left", "changes": {0xC000: (5, 4)}},
            {"id": "exp-0002", "name": "idle", "changes": {}}]
    with pytest.raises(ValueError):
        analysis.find_memory_candidates(exps[:1])
    with pytest.raises(ValueError):
        analysis.find_memory_candidates(exps, {"jump": "change"})
    with pytest.raises(ValueError):
        analysis.find_memory_candidates(exps, {"left": "sideways"})
    result = analysis.find_memory_candidates(exps, {"left": -1, "idle": "none"})
    assert result["candidates"][0]["address"] == "0xC000"
    assert result["candidates"][0]["counterexamples"] == []
