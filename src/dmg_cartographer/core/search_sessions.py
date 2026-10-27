"""SearchSessionMixin: the search sessions operations of PyBoyController."""
from __future__ import annotations

from typing import Any

from dmg_cartographer.core.shared import MAX_SEARCH_SESSIONS
from dmg_cartographer import analysis
from dmg_cartographer import search


class SearchSessionMixin:
    """Methods for the search sessions part of the emulator controller."""

    @staticmethod
    def _search_bank_info(region: str, game: Any) -> dict[str, Any]:
        if region == "WRAM":
            return {"banked": "cgb_only", "upper_bank_register": "0xFF70",
                    "upper_bank": int(game.memory[0xFF70]),
                    "note": "0xC000-0xC0FF is fixed; 0xD000-0xDFFF follows the bank in 0xFF70 on CGB"}
        return {"banked": False}


    def search_memory(self, session: str | None = None, region: str = "WRAM", dtype: str = "uint8",
                      endianness: str = "little", condition: str = "unknown", exact: int | None = None,
                      low: int | None = None, high: int | None = None, pattern: str | None = None,
                      max_results: int = 200) -> dict[str, Any]:
        with self._lock:
            game = self._require_game()
            if region not in analysis.REGIONS:
                raise ValueError(f"region must be one of {list(analysis.REGIONS)}")
            if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 1000:
                raise ValueError("max_results must be 1..1000")
            parsed = search.validate_request(dtype=dtype, endianness=endianness, condition=condition, exact=exact,
                                             low=low, high=high, pattern=pattern, has_session=session is not None)
            start, end = analysis.REGIONS[region]
            width = search.width_of(dtype)
            data = self._region_bytes(game, region)
            if session is None:
                sess = {"id": None, "rom_key": self._rom_key, "region": region, "dtype": dtype,
                        "endianness": endianness, "candidates": {}, "frame": self._frames}
                addresses = [start + off for off in range(len(data) - width + 1)
                             if parsed is None or search.pattern_matches(data, off, parsed)]
                previous_values: dict[int, int | None] = {a: None for a in addresses}
            else:
                sess = self._searches.get(session)
                if sess is None:
                    raise ValueError(f"unknown search session {session!r} (it may have been discarded after a ROM "
                                     f"change or evicted)")
                if sess["rom_key"] != self._rom_key:
                    raise ValueError(f"search session {session!r} belongs to a different ROM")
                if (sess["region"], sess["dtype"], sess["endianness"]) != (region, dtype, endianness):
                    raise ValueError("region, dtype and endianness must match the search session")
                previous_values = dict(sess["candidates"])
                addresses = list(previous_values)

            before = len(addresses)
            matches = []
            kept: dict[int, int] = {}
            for address in addresses:
                off = address - start
                if off + width > len(data):
                    continue
                if parsed is not None and not search.pattern_matches(data, off, parsed):
                    continue
                current = search.decode(data, off, dtype, endianness)
                previous = previous_values[address]
                if not search.keep(condition, current, previous, exact=exact, low=low, high=high):
                    continue
                kept[address] = current
                matches.append((address, previous, current))

            if session is None:
                session_id = self._new_search_id()
                sess["id"] = session_id
                self._searches[session_id] = sess
                trimmed = self._trim(self._searches, MAX_SEARCH_SESSIONS)
            else:
                session_id = session
                trimmed = []
            sess["candidates"] = kept
            sess["frame"] = self._frames
            value_width = width
            return {
                "session": session_id, "region": region, "dtype": dtype, "endianness": endianness,
                "condition": condition, "frame": self._frames, "bank": self._search_bank_info(region, game),
                "candidates_before": before, "candidates_remaining": len(kept),
                "eliminated": before - len(kept),
                "returned": min(len(matches), max_results), "truncated": len(matches) > max_results,
                "matches": [{"address": f"0x{a:04X}", "previous": self._value(p, dtype, value_width),
                             "current": self._value(c, dtype, value_width)}
                            for a, p, c in matches[:max_results]],
                "evicted_sessions": trimmed,
            }


    def _new_search_id(self) -> str:
        return self._new_id("search")


    @staticmethod
    def _value(value: int | None, dtype: str, width: int) -> dict[str, Any] | None:
        if value is None:
            return None
        mask = (1 << (8 * width)) - 1
        return {"dec": value, "hex": f"0x{value & mask:0{2 * width}X}"}


    def list_search_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"session": s["id"], "region": s["region"], "dtype": s["dtype"], "endianness": s["endianness"],
                     "candidates": len(s["candidates"]), "frame": s["frame"]} for s in self._searches.values()]
