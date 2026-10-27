"""DebugMixin: the debugging operations of PyBoyController."""
from __future__ import annotations

from typing import Any

from dmg_cartographer import decode
from dmg_cartographer import disasm
from dmg_cartographer import memwrite
from dmg_cartographer.core.shared import read_registers


class DebugMixin:
    """Methods for the debugging part of the emulator controller."""

    def inspect_cpu(self) -> dict[str, Any]:
        with self._lock:
            game = self._require_game()
            state = decode.cpu_state(read_registers(game))
            state.update({"frame": self._frames, "held_buttons": sorted(self._held),
                          "rom_bank_context": ("not exposed by PyBoy's public API; read ROM with inspect_rom and an "
                                               "explicit bank, or use disassemble_rom")})
            return state


    def inspect_rom(self, bank: int, address: Any, length: int = 16, ascii: bool = False,
                    cross_check: bool = True) -> dict[str, Any]:
        address = memwrite.parse_address(address)
        with self._lock:
            rom_path = self._rom_path
            if rom_path is None:
                raise RuntimeError("No game loaded. Call load_game first.")
            rom = rom_path.read_bytes()
            data = decode.read_rom(rom, bank, address, length)
            result = {"bank": bank, "address": f"0x{address:04X}", "length": length,
                      "file_offset": f"0x{decode.rom_offset(bank, address):06X}",
                      "hex": data.hex(" ").upper(), "bytes": list(data)}
            if ascii:
                result["ascii"] = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
            result["matches_emulator"] = None
            if cross_check and self._pyboy is not None:
                try:
                    seen = bytes(int(v) for v in self._pyboy.memory[bank, address:address + length])
                    result["matches_emulator"] = seen == data
                except Exception as exc:  # the emulator view may not expose this bank
                    result["emulator_check_error"] = f"{type(exc).__name__}: {exc}"
            return result


    def decode_memory(self, fields: list[dict[str, Any]], address: Any = None, length: int = 16,
                      hex_bytes: str | None = None) -> dict[str, Any]:
        if (address is None) == (hex_bytes is None):
            raise ValueError("give either address (+ length) or hex_bytes")
        if hex_bytes is not None:
            try:
                data = bytes.fromhex(hex_bytes.replace(" ", ""))
            except ValueError:
                raise ValueError("hex_bytes must be hex digits, e.g. 'C0 01 FF'") from None
            if not 1 <= len(data) <= decode.MAX_READ:
                raise ValueError(f"hex_bytes must be 1..{decode.MAX_READ} bytes")
            source = {"hex": data.hex(" ").upper()}
        else:
            start = memwrite.parse_address(address)
            if isinstance(length, bool) or not isinstance(length, int) or not 1 <= length <= decode.MAX_READ:
                raise ValueError(f"length must be 1..{decode.MAX_READ}")
            if start + length > 0x10000:
                raise ValueError("read runs past the end of the address space")
            with self._lock:
                game = self._require_game()
                data = bytes(int(v) for v in game.memory[start:start + length])
            source = {"address": f"0x{start:04X}", "length": length, "frame": self._frames}
        return {"source": source, "fields": decode.decode_fields(data, fields),
                "note": "interpretations of bytes already read; emulator state is not changed"}


    def disassemble_rom(self, bank: int, address: Any, count: int = 32) -> dict[str, Any]:
        address = memwrite.parse_address(address)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 64:
            raise ValueError("count must be 1..64 instructions")
        with self._lock:
            if self._rom_path is None:
                raise RuntimeError("No game loaded. Call load_game first.")
            rom = self._rom_path.read_bytes()
            bank_end = 0x4000 if bank == 0 else 0x8000
            span = min(count * 3, bank_end - address) if isinstance(address, int) and 0 <= address < bank_end else 0
            if span < 1:
                raise ValueError("address must be inside the bank window (0x0000-0x3FFF, or 0x4000-0x7FFF for bank 1+)")
            data = decode.read_rom(rom, bank, address, span)
            instructions = disasm.disassemble(data, address, count)
            rows = []
            for insn in instructions:
                if insn["address"] + insn["length"] > bank_end:
                    insn["truncated"] = True  # runs past the end of this bank's window
                rows.append({"bank": bank, "address": f"0x{insn['address']:04X}",
                             "bytes": " ".join(f"{b:02X}" for b in insn["bytes"]),
                             "mnemonic": insn["mnemonic"], "operands": insn["operands"],
                             "length": insn["length"], "targets": [f"0x{t:04X}" for t in insn["targets"]],
                             "invalid": insn["invalid"], "truncated": insn["truncated"]})
            targets = sorted({t for r in rows for t in r["targets"]})
            return {"bank": bank, "start": f"0x{address:04X}", "count": len(rows), "instructions": rows,
                    "targets": targets,
                    "label": ("decoding interpretation of ROM bytes, not proof of executable control flow; "
                              "data can decode as valid instructions")}


    def hook_execution(self, action: str, bank: int | None = None, address: Any = None,
                       label: str | None = None, max_observations: int = 20) -> dict[str, Any]:
        """Execution hooks fire when the CPU reaches bank:address. They count hits and keep register snapshots.

        This is NOT a memory watchpoint: it observes instruction fetch at one address only.
        """
        with self._lock:
            game = self._require_game()
            if action not in ("register", "remove", "list"):
                raise ValueError("action must be 'register', 'remove' or 'list'")
            if action == "list":
                return {"count": len(self._hooks), "hooks": [self._hook_summary(h) for h in self._hooks.values()],
                        "limitation": "execution hooks count instruction fetches at one address, not data accesses"}
            if bank is None or address is None:
                raise ValueError("bank and address are required")
            address = memwrite.parse_address(address)
            if isinstance(bank, bool) or not isinstance(bank, int) or not 0 <= bank <= 255:
                raise ValueError("bank must be an integer 0-255")
            if isinstance(address, bool) or not isinstance(address, int):
                raise ValueError("address must be an integer")
            rom_size = self._rom_path.stat().st_size
            decode.check_rom_read(rom_size, bank, address, 1)
            key = f"{bank}:{address:04X}"
            if action == "remove":
                if key not in self._hooks:
                    raise ValueError(f"no hook at bank {bank} address 0x{address:04X}")
                game.hook_deregister(bank, address)
                removed = self._hooks.pop(key)
                return {"removed": key, "final_count": removed["count"]}
            if key in self._hooks:
                raise ValueError(f"a hook already exists at {key}; remove it first")
            if len(self._hooks) >= 16:
                raise ValueError("at most 16 execution hooks at once")
            if isinstance(max_observations, bool) or not isinstance(max_observations, int) \
                    or not 0 <= max_observations <= 100:
                raise ValueError("max_observations must be 0..100")
            if label is not None and (not isinstance(label, str) or len(label) > 32):
                raise ValueError("label must be a string of up to 32 characters")
            record = {"key": key, "bank": bank, "address": address, "label": label, "count": 0,
                      "callback_errors": 0, "max_observations": max_observations, "observations": []}
            game.hook_register(bank, address, self._hook_fire, record)
            self._hooks[key] = record
            return {"registered": self._hook_summary(record)}


    def _hook_fire(self, record: dict[str, Any]) -> None:
        """Runs inside PyBoy's tick. Keep it small: count, and snapshot registers up to a bound."""
        try:
            record["count"] += 1
            if len(record["observations"]) < record["max_observations"] and self._pyboy is not None:
                rf = self._pyboy.register_file
                record["observations"].append({
                    "hit": record["count"], "A": rf.A, "F": rf.F, "BC": (rf.B << 8) | rf.C,
                    "DE": (rf.D << 8) | rf.E, "HL": rf.HL, "SP": rf.SP, "PC": rf.PC,
                    "frame_note": "frame numbers are not available inside hook callbacks"})
        except Exception:
            record["callback_errors"] += 1


    @staticmethod
    def _hook_summary(record: dict[str, Any]) -> dict[str, Any]:
        return {"key": record["key"], "bank": record["bank"], "address": f"0x{record['address']:04X}",
                "label": record["label"], "count": record["count"], "callback_errors": record["callback_errors"],
                "observations": record["observations"]}


