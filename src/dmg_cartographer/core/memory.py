"""MemoryMixin: the memory operations of PyBoyController."""
from __future__ import annotations

from typing import Any

from dmg_cartographer.core.shared import MAX_FRAMES, MAX_MEMORY_READ
from dmg_cartographer import analysis
from dmg_cartographer import memwrite


class MemoryMixin:
    """Methods for the memory part of the emulator controller."""

    def read_memory(self, address: int, length: int = 1) -> dict[str, Any]:
        with self._lock:
            game = self._require_game()
            if (type(address) is not int or type(length) is not int
                or address < 0 or length < 1 or length > MAX_MEMORY_READ
                or address + length > 0x10000):
                raise ValueError(f"address must be 0..65535; length 1..{MAX_MEMORY_READ}, within address space")
            values = [int(game.memory[address + offset]) for offset in range(length)]
            return {"address": address, "hex_address": f"0x{address:04X}", "bytes": values,
                    "hex_bytes": " ".join(f"{value:02X}" for value in values)}


    def write_memory(self, address: Any, value: Any, expected: Any = None, allow_sensitive: bool = False,
                     experiment_frames: int = 0, max_results: int = 50) -> dict[str, Any]:
        addr = memwrite.parse_address(address)
        values = memwrite.check_values(value if isinstance(value, list) else [value])
        region = memwrite.check_write(addr, len(values), allow_sensitive=allow_sensitive)
        if expected is not None:
            expected = expected if isinstance(expected, list) else [expected]
            if len(expected) != len(values):
                raise ValueError("expected must match the number of bytes written")
            memwrite.check_values(expected)
        if isinstance(experiment_frames, bool) or not isinstance(experiment_frames, int) \
                or not 0 <= experiment_frames <= MAX_FRAMES:
            raise ValueError(f"experiment_frames must be an integer 0..{MAX_FRAMES}")
        with self._lock:
            game = self._require_game()
            previous = [int(game.memory[addr + i]) for i in range(len(values))]
            if expected is not None and previous != expected:
                raise ValueError(f"expected {[f'0x{v:02X}' for v in expected]} at 0x{addr:04X} but found "
                                 f"{[f'0x{v:02X}' for v in previous]}; nothing was written")
            base = {"address": f"0x{addr:04X}", "length": len(values), "region": region,
                    "previous": previous, "written": values,
                    "side_effects": ("the game may react to this value on later frames; a changed value is not "
                                     "proof of what the byte means")}
            if experiment_frames == 0:
                for i, v in enumerate(values):
                    game.memory[addr + i] = v
                return {**base, "mode": "write", "frames_advanced": 0, "restored": False,
                        "warning": "the write persists in the live emulator; use memory(action='write') with "
                                   "experiment_frames to test it and restore"}

            with self._restoring():
                before = self._read_state(True, True, False)
                for i, v in enumerate(values):
                    game.memory[addr + i] = v
                self.step(experiment_frames)
                after = self._read_state(True, True, False)
                memory = {}
                for name, (start, _) in analysis.REGIONS.items():
                    memory[name] = analysis.compare_memory(
                        before["memory"][name], after["memory"][name], start, max_results=max_results)
                screen, _ = analysis.compare_screen(before["screen"], after["screen"], mode="both",
                                                    max_items=max_results)
                observations = {"frames_advanced": self._frames - before["frame"], "memory": memory,
                                "screen": screen}
            return {**base, "mode": "experiment", "frames_advanced": experiment_frames, "restored": True,
                    "observations": observations,
                    "note": "observations show what changed after the write; they do not prove the write caused it"}
