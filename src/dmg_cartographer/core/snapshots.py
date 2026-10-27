"""SnapshotMixin: the snapshots operations of PyBoyController."""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from dmg_cartographer.core.shared import ID_PATTERN, MAX_LABEL, read_oam
from dmg_cartographer import analysis


class SnapshotMixin:
    """Methods for the snapshots part of the emulator controller."""

    def _new_id(self, kind: str) -> str:
        number = self._next_id[kind]
        self._next_id[kind] += 1
        return f"{kind}-{number:04d}"


    @staticmethod
    def _check_label(name: Any, what: str) -> str:
        if not isinstance(name, str) or not 1 <= len(name) <= MAX_LABEL:
            raise ValueError(f"{what} name must be a string of 1-{MAX_LABEL} characters")
        if ID_PATTERN.fullmatch(name):
            raise ValueError(f"{what} name {name!r} looks like an id (snap-N / exp-N); choose another name")
        return name


    @staticmethod
    def _trim(store: OrderedDict[str, dict[str, Any]], limit: int) -> list[str]:
        evicted = []
        while len(store) > limit:
            evicted.append(store.popitem(last=False)[0])
        return evicted


    def _read_state(self, memory: bool, screen: bool, tilemap: bool) -> dict[str, Any]:
        """Read the current emulator state. Never ticks."""
        game = self._require_game()
        state: dict[str, Any] = {
            "rom": self._rom_name, "rom_key": self._rom_key, "frame": self._frames,
            "timestamp": datetime.now(timezone.utc).isoformat(), "screen_fresh": self._screen_fresh,
            "memory": {}, "memory_errors": {}, "screen": None, "tilemaps": None, "oam": None, "lcdc": None,
        }
        if memory:
            for region, (start, end) in analysis.REGIONS.items():
                try:
                    values = bytes(int(v) for v in game.memory[start:end])
                    if len(values) != end - start:
                        raise ValueError(f"read {len(values)} bytes, expected {end - start}")
                    state["memory"][region] = values
                except Exception as exc:  # report the region as unreadable instead of failing the capture
                    state["memory_errors"][region] = f"{type(exc).__name__}: {exc}"
        if screen:
            state["screen"] = analysis.normalise_screen(game.screen.image)
            # Sprite table and LCD control are stored with the frame so sprite movement can be compared later.
            state["oam"], state["lcdc"] = read_oam(game)
        if tilemap:
            state["tilemaps"] = {layer: self.tilemap(layer, 0, 0, 32, 32)["tiles"] for layer in ("background", "window")}
        return state


    @staticmethod
    def _snapshot_meta(snap: dict[str, Any]) -> dict[str, Any]:
        meta = {
            "id": snap["id"], "name": snap["name"], "rom": snap["rom"], "rom_key": snap["rom_key"],
            "frame": snap["frame"], "timestamp": snap["timestamp"],
            "memory_regions": {region: f"0x{analysis.REGIONS[region][0]:04X}-0x{analysis.REGIONS[region][1] - 1:04X}"
                               for region in snap["memory"]},
            "has_screen": snap["screen"] is not None, "screen_fresh": snap["screen_fresh"],
            "has_tilemap": snap["tilemaps"] is not None,
        }
        if snap["memory_errors"]:
            meta["memory_errors"] = snap["memory_errors"]
        return meta


    def _store_snapshot(self, name: str | None, state: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        snap = {"id": self._new_id("snap"), "name": name, **state}
        self._snapshots[snap["id"]] = snap
        return snap, self._trim(self._snapshots, self._max_snapshots)


    def capture_snapshot(self, name: str | None = None, include_memory: bool = True, include_screen: bool = True,
                         include_tilemap: bool = False) -> dict[str, Any]:
        with self._lock:
            if name is not None:
                self._check_label(name, "snapshot")
            if not (include_memory or include_screen or include_tilemap):
                raise ValueError("enable at least one of include_memory, include_screen, include_tilemap")
            snap, evicted = self._store_snapshot(name, self._read_state(include_memory, include_screen, include_tilemap))
            result = self._snapshot_meta(snap)
            if evicted:
                result["evicted"] = evicted
            if include_screen and not snap["screen_fresh"]:
                result["warning"] = self.STALE_SCREEN
            return result


    def list_snapshots(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._snapshot_meta(s) for s in self._snapshots.values()]


    def _resolve_snapshot(self, ref: Any) -> dict[str, Any]:
        if not isinstance(ref, str) or not ref:
            raise ValueError("snapshot reference must be a snapshot id or unique name")
        if ref in self._snapshots:
            return self._snapshots[ref]
        named = [s for s in self._snapshots.values() if s["name"] == ref]
        if len(named) == 1:
            return named[0]
        if named:
            raise ValueError(f"snapshot name {ref!r} is ambiguous ({[s['id'] for s in named]}); use an id")
        raise ValueError(f"unknown snapshot {ref!r} (it may have been evicted or discarded after a ROM change); "
                         f"call list_snapshots")


    def delete_snapshot(self, snapshot: str) -> dict[str, Any]:
        with self._lock:
            snap = self._resolve_snapshot(snapshot)
            del self._snapshots[snap["id"]]
            return {"deleted": snap["id"], "remaining": len(self._snapshots)}


    def _pair(self, baseline: str, current: bool, target: str | None, *, memory: bool = False,
              screen: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        base = self._resolve_snapshot(baseline)
        if current:
            if target is not None:
                raise ValueError("pass target only with current=False")
            other = {"id": "current", "name": None,
                     **self._read_state(memory, screen, screen and base["tilemaps"] is not None)}
        else:
            if target is None:
                raise ValueError("current=False needs a target snapshot")
            other = self._resolve_snapshot(target)
        if base["rom_key"] != other["rom_key"]:
            raise ValueError(f"{base['id']} ({base['rom']}) and {other['id']} ({other['rom']}) come from "
                             f"different ROMs and cannot be compared")
        return base, other


    def compare_memory(self, baseline: str, region: str = "WRAM", current: bool = True, target: str | None = None,
                       filter: str = "changed", previous_value: int | None = None, current_value: int | None = None,
                       max_results: int = 200, group: bool = True) -> dict[str, Any]:
        with self._lock:
            if region not in analysis.REGIONS:
                raise ValueError(f"region must be one of {list(analysis.REGIONS)}")
            base, other = self._pair(baseline, current, target, memory=True)
            for snap in (base, other):
                if region not in snap["memory"]:
                    reason = snap["memory_errors"].get(region, "captured with include_memory=False")
                    raise ValueError(f"{snap['id']} has no {region} data: {reason}")
            start, end = analysis.REGIONS[region]
            result = analysis.compare_memory(
                base["memory"][region], other["memory"][region], start, filter=filter,
                previous_value=previous_value, current_value=current_value, max_results=max_results, group=group)
            return {"baseline": base["id"], "target": other["id"], "region": region,
                    "range": f"0x{start:04X}-0x{end - 1:04X}",
                    "baseline_frame": base["frame"], "target_frame": other["frame"], **result}


    def compare_screen(self, baseline: str, current: bool = True, target: str | None = None, mode: str = "both",
                       threshold: int = 0, region: dict[str, int] | None = None,
                       include_image: bool = False) -> tuple[dict[str, Any], bytes | None]:
        with self._lock:
            if region is not None:
                region = self._check_screen_region(region)
            base, other = self._pair(baseline, current, target, screen=True)
            for snap in (base, other):
                if snap["screen"] is None:
                    raise ValueError(f"{snap['id']} was captured without a screen")
            result, png = analysis.compare_screen(base["screen"], other["screen"], mode=mode, threshold=threshold,
                                                  region=region, include_image=include_image)
            result = {"baseline": base["id"], "target": other["id"],
                      "baseline_frame": base["frame"], "target_frame": other["frame"], **result}
            if base["tilemaps"] is not None and other["tilemaps"] is not None:
                result["tilemaps"] = analysis.compare_tilemaps(base["tilemaps"], other["tilemaps"])
            if not (base["screen_fresh"] and other["screen_fresh"]):
                result["warning"] = self.STALE_SCREEN
            return result, png
