"""Pure, game-agnostic comparison helpers. No emulator access, no MCP: unit-test directly."""
from __future__ import annotations

import io
from collections import defaultdict
from typing import Any, Sequence

from PIL import Image, ImageChops

# name -> (start inclusive, end exclusive). Add VRAM/OAM here later; banked regions
# (CGB WRAM 0xD000-0xDFFF, VRAM) are read through the currently selected bank.
REGIONS = {"WRAM": (0xC000, 0xE000), "HRAM": (0xFF80, 0xFFFF)}
SCREEN_SIZE = (160, 144)
CELL = 8
MEMORY_FILTERS = ("changed", "unchanged", "increased", "decreased", "all")
SCREEN_MODES = ("pixel", "grid", "both")
EXPECTATIONS = ("increase", "decrease", "change", "none")
MAX_MEMORY_RESULTS = 2000


def capped(name: str, rows: list, limit: int) -> dict[str, Any]:
    """`rows` cut to `limit`, with <name>_total (before the cut) and <name>_truncated beside it."""
    return {name: rows[:limit], f"{name}_total": len(rows), f"{name}_truncated": len(rows) > limit}


def _byte(value: int) -> dict[str, Any]:
    return {"dec": value, "hex": f"0x{value:02X}"}


def _change_row(address: int, before: int, after: int) -> dict[str, Any]:
    diff = after - before
    return {
        "address": f"0x{address:04X}",
        "previous": _byte(before),
        "current": _byte(after),
        "diff": diff,                                # plain signed difference, -255..255
        "diff_unsigned": diff & 0xFF,                # modulo-256 difference, 0..255
        "diff_wrapped": ((diff + 128) & 0xFF) - 128,  # as a signed 8-bit step, -128..127
    }


def _groups(addresses: list[int]) -> list[dict[str, Any]]:
    groups: list[list[int]] = []
    for address in addresses:
        if groups and address == groups[-1][1] + 1:
            groups[-1][1] = address
        else:
            groups.append([address, address])
    return [{"start": f"0x{a:04X}", "end": f"0x{b:04X}", "length": b - a + 1} for a, b in groups]


def compare_memory(before: Sequence[int], after: Sequence[int], start: int, *, filter: str = "changed",
                   previous_value: int | None = None, current_value: int | None = None,
                   max_results: int = 200, group: bool = True) -> dict[str, Any]:
    """Byte-by-byte comparison of two equal-length memory reads that begin at `start`."""
    if len(before) != len(after):
        raise ValueError("memory reads have different lengths")
    if filter not in MEMORY_FILTERS:
        raise ValueError(f"filter must be one of {MEMORY_FILTERS}")
    for label, value in (("previous_value", previous_value), ("current_value", current_value)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255):
            raise ValueError(f"{label} must be an integer 0..255")
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= MAX_MEMORY_RESULTS:
        raise ValueError(f"max_results must be 1..{MAX_MEMORY_RESULTS}")

    changed = 0
    matches: list[int] = []
    for offset, (a, b) in enumerate(zip(before, after)):
        if a != b:
            changed += 1
        if ((filter == "changed" and a == b) or (filter == "unchanged" and a != b)
                or (filter == "increased" and b <= a) or (filter == "decreased" and b >= a)
                or (previous_value is not None and a != previous_value)
                or (current_value is not None and b != current_value)):
            continue
        matches.append(offset)

    result: dict[str, Any] = {
        "bytes_examined": len(before),
        "changed_count": changed,
        "filter": filter,
        **capped("changes", matches, max_results),
    }
    result["changes"] = [_change_row(start + i, before[i], after[i]) for i in result["changes"]]
    if group:
        result.update(capped("groups", _groups([start + i for i in matches]), max_results))
    return result


def changed_bytes(before: Sequence[int], after: Sequence[int], start: int) -> dict[int, tuple[int, int]]:
    """Every changed address -> (previous, current). Used to record experiments for candidate search."""
    return {start + i: (a, b) for i, (a, b) in enumerate(zip(before, after)) if a != b}


def normalise_screen(image: Image.Image) -> Image.Image:
    """Return an independent RGB copy at the Game Boy resolution (PyBoy gives RGBA)."""
    if image.size != SCREEN_SIZE:
        raise ValueError(f"expected a {SCREEN_SIZE[0]}x{SCREEN_SIZE[1]} screen, got {image.size[0]}x{image.size[1]}")
    return image.convert("RGB")


def _rect(box: tuple[int, int, int, int]) -> dict[str, int]:
    left, top, right, bottom = box
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def _components(cells: dict[tuple[int, int], tuple[int, int, int, int]]) -> list[dict[str, Any]]:
    """8-connected groups of changed cells -> tight pixel rectangle of the changed pixels in each group."""
    seen: set[tuple[int, int]] = set()
    areas = []
    for start in sorted(cells, key=lambda c: (c[1], c[0])):
        if start in seen:
            continue
        stack, members = [start], []
        seen.add(start)
        while stack:
            cx, cy = stack.pop()
            members.append((cx, cy))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    neighbour = (cx + dx, cy + dy)
                    if neighbour in cells and neighbour not in seen:
                        seen.add(neighbour)
                        stack.append(neighbour)
        boxes = [cells[m] for m in members]
        box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
        areas.append({**_rect(box), "cells": len(members)})
    return areas


def compare_screen(before: Image.Image, after: Image.Image, *, mode: str = "both", threshold: int = 0,
                   region: dict[str, int] | None = None, include_image: bool = False,
                   max_items: int = 360) -> tuple[dict[str, Any], bytes | None]:
    """Compare two rendered frames (sprites included). A pixel changed if any RGB channel differs by > threshold.

    Returns (JSON-ready result, optional PNG highlighting changed pixels in red over a dimmed `after` frame).
    """
    if mode not in SCREEN_MODES:
        raise ValueError(f"mode must be one of {SCREEN_MODES}")
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 254:
        raise ValueError("threshold must be an integer 0..254")
    a, b = normalise_screen(before), normalise_screen(after)
    r, g, bl = ImageChops.difference(a, b).split()
    mask = ImageChops.lighter(ImageChops.lighter(r, g), bl).point(lambda v: 255 if v > threshold else 0)

    box = (0, 0, *SCREEN_SIZE)
    if region is not None:  # the caller validates it (core.screen._check_screen_region)
        box = (region["x"], region["y"], region["x"] + region["width"], region["y"] + region["height"])
        roi = Image.new("L", SCREEN_SIZE, 0)
        roi.paste(255, box)
        mask = ImageChops.darker(mask, roi)

    examined = (box[2] - box[0]) * (box[3] - box[1])
    changed = mask.histogram()[255]
    result: dict[str, Any] = {
        "mode": mode,
        "threshold": threshold,
        "region": _rect(box),
        "pixels_examined": examined,
        "changed_pixels": changed,
        "percent_changed": round(100 * changed / examined, 3),
    }

    cells: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    counts: dict[tuple[int, int], int] = {}
    for cy in range(SCREEN_SIZE[1] // CELL):
        for cx in range(SCREEN_SIZE[0] // CELL):
            cell_box = (cx * CELL, cy * CELL, cx * CELL + CELL, cy * CELL + CELL)
            cell = mask.crop(cell_box)
            inner = cell.getbbox()
            if inner:
                cells[(cx, cy)] = (cell_box[0] + inner[0], cell_box[1] + inner[1],
                                   cell_box[0] + inner[2], cell_box[1] + inner[3])
                counts[(cx, cy)] = cell.histogram()[255]

    if mode in ("pixel", "both"):
        overall = mask.getbbox()
        areas = _components(cells)
        result["bounding_box"] = _rect(overall) if overall else None
        result.update(capped("areas", areas, max_items))
    if mode in ("grid", "both"):
        rows = [{"col": cx, "row": cy, "x": cx * CELL, "y": cy * CELL, "changed_pixels": counts[(cx, cy)]}
                for cx, cy in sorted(cells, key=lambda c: (c[1], c[0]))]
        result["grid"] = {"cell_size": CELL, "columns": SCREEN_SIZE[0] // CELL, "rows": SCREEN_SIZE[1] // CELL,
                          **capped("cells", rows, max_items)}

    png = None
    if include_image:
        dimmed = Image.eval(b.convert("L"), lambda v: v // 3).convert("RGB")
        highlight = Image.composite(Image.new("RGB", SCREEN_SIZE, (255, 0, 0)), dimmed, mask)
        data = io.BytesIO()
        highlight.resize((SCREEN_SIZE[0] * 2, SCREEN_SIZE[1] * 2), Image.Resampling.NEAREST).save(data, format="PNG")
        png = data.getvalue()
    return result, png


def compare_tilemaps(before: dict[str, list[list[int]]], after: dict[str, list[list[int]]],
                     max_results: int = 200) -> dict[str, Any]:
    """Changed tile IDs per layer of two 32x32 tilemap captures."""
    out = {}
    for layer in before.keys() & after.keys():
        changes = [{"x": x, "y": y, "previous": prev, "current": cur}
                   for y, (row_a, row_b) in enumerate(zip(before[layer], after[layer]))
                   for x, (prev, cur) in enumerate(zip(row_a, row_b)) if prev != cur]
        out[layer] = capped("changes", changes, max_results)
    return out


def meets(rule: str | int, diff: int) -> bool:
    if rule == "increase":
        return diff > 0
    if rule == "decrease":
        return diff < 0
    if rule == "change":
        return diff != 0
    if rule == "none":
        return diff == 0
    return diff == rule


def _confidence(support: int, counter: int, consistent: bool) -> str:
    if counter == 0 and support >= 2 and consistent:
        return "high"
    if counter == 0:
        return "medium"
    return "low"


def find_memory_candidates(experiments: list[dict[str, Any]], expected_change: dict[str, str | int] | None = None,
                           max_results: int = 50) -> dict[str, Any]:
    """Rank addresses whose changes line up with experiment labels. Correlation only: verify every result.

    Each experiment: {"id", "name", "changes": {address: (previous, current)}}. Experiments sharing a
    name are repeats of the same action. Unchanged addresses have diff 0.

    - With `expected_change` ({name: "increase"|"decrease"|"change"|"none"|exact int diff}), an address
      is scored against those rules in every experiment carrying one of those names.
    - Without it, each name is contrasted against all other names: addresses that change in that
      action's experiments but not in the others rank highest.
    """
    if len(experiments) < 2:
        raise ValueError("need at least 2 experiments to compare")
    names = {e["name"] for e in experiments}
    if expected_change is not None:
        if not expected_change:
            raise ValueError("expected_change must not be empty")
        for name, rule in expected_change.items():
            if name not in names:
                raise ValueError(f"no experiment named {name!r}; names: {sorted(names)}")
            if not (rule in EXPECTATIONS or (isinstance(rule, int) and not isinstance(rule, bool))):
                raise ValueError(f"expected_change values must be one of {EXPECTATIONS} or an integer diff")

    addresses = sorted({a for e in experiments for a in e["changes"]})

    def observation(e: dict[str, Any], address: int) -> dict[str, Any]:
        prev, cur = e["changes"].get(address, (None, None))
        return {"experiment": e["id"], "name": e["name"], "previous": prev, "current": cur,
                "diff": 0 if prev is None else cur - prev}

    candidates = []
    if expected_change is not None:
        judged = [e for e in experiments if e["name"] in expected_change]
        for address in addresses:
            support, counter = [], []
            for e in judged:
                obs = observation(e, address)
                (support if meets(expected_change[e["name"]], obs["diff"]) else counter).append(obs)
            if not support:
                continue
            diffs_by_name = defaultdict(set)
            for obs in support:
                diffs_by_name[obs["name"]].add(obs["diff"])
            consistent = all(len(d) == 1 for d in diffs_by_name.values())
            candidates.append({"address": f"0x{address:04X}", "action": None,
                               "score": round(len(support) / len(judged), 3),
                               "confidence": _confidence(len(support), len(counter), consistent),
                               "consistent_diff": consistent, "evidence": support, "counterexamples": counter})
    else:
        by_name = defaultdict(list)
        for e in experiments:
            by_name[e["name"]].append(e)
        for name, own in by_name.items():
            others = [e for e in experiments if e["name"] != name]
            for address in addresses:
                hits = [observation(e, address) for e in own if address in e["changes"]]
                if not hits:
                    continue
                misses = [observation(e, address) for e in own if address not in e["changes"]]
                elsewhere = [observation(e, address) for e in others if address in e["changes"]]
                share_others = len(elsewhere) / len(others) if others else 0.0
                consistent = len({h["diff"] for h in hits}) == 1
                candidates.append({"address": f"0x{address:04X}", "action": name,
                                   "score": round(len(hits) / len(own) - share_others, 3),
                                   "confidence": _confidence(len(hits), len(misses) + len(elsewhere), consistent),
                                   "consistent_diff": consistent, "evidence": hits,
                                   "counterexamples": misses + elsewhere})

    rank = {"high": 0, "medium": 1, "low": 2}
    candidates.sort(key=lambda c: (rank[c["confidence"]], -c["score"], len(c["counterexamples"]), c["address"]))
    return {
        "experiments": [e["id"] for e in experiments],
        "addresses_considered": len(addresses),
        **capped("candidates", candidates, max_results),
        "note": "Correlation, not proof. Confirm candidates with new experiments, e.g. from a restored checkpoint.",
    }


def compare_branches(baseline: dict[str, bytes], branches: dict[str, dict[str, bytes]],
                     max_results: int = 50) -> dict[str, Any]:
    """Compare each branch's memory regions against the shared baseline and against each other.

    `baseline` and each branch map region name -> bytes. A byte is "unique" to a branch when only that branch
    changed it relative to the baseline. Frame-sampled evidence only: the same as any other snapshot diff.
    """
    per_branch: dict[str, dict[str, dict[int, tuple[int, int]]]] = {}
    for name, regions in branches.items():
        per_branch[name] = {}
        for region, (start, _) in REGIONS.items():
            if region in baseline and region in regions:
                per_branch[name][region] = changed_bytes(baseline[region], regions[region], start)

    # (region, address) -> branches that changed it
    changers: dict[tuple[str, int], list[str]] = defaultdict(list)
    for name, regions in per_branch.items():
        for region, changes in regions.items():
            for address in changes:
                changers[(region, address)].append(name)

    out: dict[str, Any] = {"branches": {}, "shared_change_count": 0}
    for key, names in changers.items():
        if len(names) > 1:
            out["shared_change_count"] += 1
    for name, regions in per_branch.items():
        unique = sorted((region, address) for (region, address), names in changers.items()
                        if names == [name])
        rows = {region: [_change_row(address, prev, cur) for address, (prev, cur) in sorted(changes.items())]
                for region, changes in regions.items()}
        branch = capped("unique_changes", unique, max_results)
        branch["unique_changes"] = [{"region": region, **_change_row(address, *per_branch[name][region][address])}
                                    for region, address in branch["unique_changes"]]
        # changes is one list per region; the total and the flag cover all regions together.
        branch["changes"] = {region: rows_[:max_results] for region, rows_ in rows.items()}
        branch["changes_total"] = sum(len(rows_) for rows_ in rows.values())
        branch["changes_truncated"] = any(len(rows_) > max_results for rows_ in rows.values())
        out["branches"][name] = branch
    return out


def correlate_with_screen(records: list[dict[str, Any]], max_results: int = 20) -> dict[str, Any]:
    """Cross-reference memory changes with a screen-changed flag across experiments.

    Each record: {"id", "name", "changes": {address: (previous, current)}, "screen_changed": bool}.
    For each address that changed at least once, counts over experiments:
      a = memory changed and screen changed      b = memory changed, screen static
      c = screen changed, memory unchanged       d = neither
    `agreement` = (a + d) / n. This is correlation between two observations, not evidence of cause.
    """
    if len(records) < 2:
        raise ValueError("need at least 2 experiments to correlate")
    addresses = sorted({a for r in records for a in r["changes"]})
    n = len(records)
    candidates = []
    for address in addresses:
        a = b = c = d = 0
        both, counter, signs = [], [], {"+": 0, "-": 0, "0": 0}
        for r in records:
            mem = address in r["changes"]
            scr = bool(r["screen_changed"])
            if mem and scr:
                a += 1
                both.append(r["id"])
                prev, cur = r["changes"][address]
                signs["+" if cur > prev else "-" if cur < prev else "0"] += 1
            elif mem:
                b += 1
                counter.append({"experiment": r["id"], "kind": "memory changed, screen static"})
            elif scr:
                c += 1
                counter.append({"experiment": r["id"], "kind": "screen changed, memory unchanged"})
            else:
                d += 1
        if a + b == 0:
            continue
        agreement = round((a + d) / n, 3)
        if a > 0 and b == 0 and c == 0:
            pattern = "changes exactly when the screen changes"
        elif a == 0 and b > 0:
            pattern = "changes without a visible change"
        else:
            pattern = "mixed"
        # Never "high": correlation between recorded observations cannot establish a relationship.
        confidence = "medium" if (n >= 10 and agreement == 1.0 and a > 0 and d > 0) else "low"
        candidates.append({
            "address": f"0x{address:04X}", "pattern": pattern, "confidence": confidence,
            "agreement": agreement, "samples": n,
            "counts": {"memory_and_screen": a, "memory_only": b, "screen_only": c, "neither": d},
            "direction_when_both": {k: v for k, v in signs.items() if v},
            "evidence": both, **capped("counterexamples", counter, 20),
        })
    candidates.sort(key=lambda c: (-c["agreement"], -c["counts"]["memory_and_screen"], c["address"]))
    return {"samples": n, "addresses_considered": len(addresses), **capped("candidates", candidates, max_results),
            "note": ("Correlation between recorded observations only. The screen flag says that something "
                     "visible changed, not which object moved. Confirm with experiment(action='validate').")}


def sprite_changes(before: list[dict[str, Any]], after: list[dict[str, Any]], max_items: int = 50) -> dict[str, Any]:
    """Compare two sprite lists from graphics.sprite_rows() for the same OAM slots.

    Direct observations: a slot's screen position or tile changed. Slots are matched by OAM index.
    Inferred: a moved slot whose tile ID is unchanged is LIKELY the same object, but games reuse slots, so this
    match is labelled as inference and never reported as an identity.
    """
    by_before = {r["index"]: r for r in before}
    by_after = {r["index"]: r for r in after}
    moved, appeared, disappeared, tile_swapped = [], [], [], []
    unchanged = 0
    for index in sorted(set(by_before) | set(by_after)):
        b, a = by_before.get(index), by_after.get(index)
        if b is None or a is None:
            continue
        b_on, a_on = b["on_screen"], a["on_screen"]
        if not b_on and a_on:
            appeared.append({"slot": index, "at": {"x": a["screen"]["x"], "y": a["screen"]["y"]}})
        elif b_on and not a_on:
            disappeared.append({"slot": index, "was_at": {"x": b["screen"]["x"], "y": b["screen"]["y"]}})
        elif b_on and a_on:
            pos_b = (b["screen"]["x"], b["screen"]["y"])
            pos_a = (a["screen"]["x"], a["screen"]["y"])
            same_tile = b["tile_index"] == a["tile_index"]
            if pos_b != pos_a:
                moved.append({"slot": index, "from": {"x": pos_b[0], "y": pos_b[1]},
                              "to": {"x": pos_a[0], "y": pos_a[1]},
                              "delta": {"dx": pos_a[0] - pos_b[0], "dy": pos_a[1] - pos_b[1]},
                              "tile_index_before": b["tile_index"], "tile_index_after": a["tile_index"],
                              "inferred_same_object": same_tile})
            elif not same_tile:
                tile_swapped.append({"slot": index, "tile_index_before": b["tile_index"],
                                     "tile_index_after": a["tile_index"]})
            else:
                unchanged += 1
    return {
        **capped("moved", moved, max_items),
        **capped("appeared", appeared, max_items),
        **capped("disappeared", disappeared, max_items),
        **capped("tile_changed_in_place", tile_swapped, max_items),
        "unchanged_on_screen": unchanged,
        "identity_basis": ("OAM slot index is a direct observation. 'inferred_same_object' compares tile IDs only, "
                           "so it is an inference that a game may break."),
    }


def changed_pixel_count(a, b) -> int:
    """Pixels that differ between two same-size RGB images (any channel)."""
    if a.size != b.size:
        raise ValueError("images must be the same size")
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).convert("L")
    return diff.point(lambda v: 255 if v else 0).histogram()[255]
