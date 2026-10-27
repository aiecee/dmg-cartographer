"""Memory interpretation helpers, CPU state decoding, and banked ROM slicing. Pure: no emulator access.

Interpretations are applied to bytes the caller already read, so they never change emulator state.
"""
from __future__ import annotations

from typing import Any

TYPE_SIZES = {"uint8": 1, "int8": 1, "uint16": 2, "int16": 2}
ROM_BANK = 0x4000
MAX_READ = 256
MAX_FIELDS = 32


def _int(data: bytes, offset: int, size: int, signed: bool, endianness: str) -> int:
    return int.from_bytes(data[offset:offset + size], endianness if size > 1 else "little", signed=signed)


def _need(data: bytes, offset: int, length: int, name: str) -> None:
    if offset < 0 or length < 1 or offset + length > len(data):
        raise ValueError(f"field {name!r} reads bytes {offset}..{offset + length - 1}, outside the {len(data)} "
                         f"bytes provided")


def decode_field(data: bytes, field: dict[str, Any], name: str = "field") -> Any:
    """Interpret one field. Supported types: uint8/int8/uint16/int16, bits, bcd, string, array, pattern."""
    ftype = field.get("type")
    offset = field.get("offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise ValueError(f"{name}: offset must be an integer")
    endianness = field.get("endianness", "little")
    if endianness not in ("little", "big"):
        raise ValueError(f"{name}: endianness must be 'little' or 'big'")
    if ftype in TYPE_SIZES:
        size = TYPE_SIZES[ftype]
        _need(data, offset, size, name)
        value = _int(data, offset, size, ftype.startswith("int"), endianness)
        return {"dec": value, "hex": f"0x{value & ((1 << 8 * size) - 1):0{2 * size}X}"}
    if ftype == "bits":
        _need(data, offset, 1, name)
        bit, width = field.get("bit", 0), field.get("width", 1)
        if not (0 <= bit <= 7 and 1 <= width <= 8 and bit + width <= 8):
            raise ValueError(f"{name}: bits need bit 0-7 and width 1-8 with bit+width <= 8")
        value = (data[offset] >> bit) & ((1 << width) - 1)
        return {"dec": value, "bit": bit, "width": width, "byte": f"0x{data[offset]:02X}"}
    if ftype == "bcd":
        length = field.get("length", 1)
        _need(data, offset, length, name)
        raw = data[offset:offset + length]
        ordered = raw if endianness == "big" else raw[::-1]
        digits = []
        for byte in ordered:
            hi, lo = byte >> 4, byte & 0xF
            if hi > 9 or lo > 9:
                return {"valid": False, "bytes": raw.hex(), "error": "a nibble is not a decimal digit (A-F)"}
            digits.append(f"{hi}{lo}")
        return {"valid": True, "dec": int("".join(digits)), "bytes": raw.hex()}
    if ftype == "string":
        length = field.get("length", 16)
        _need(data, offset, length, name)
        raw = data[offset:offset + length]
        text = raw.split(b"\x00", 1)[0]
        printable = all(32 <= b < 127 for b in text)
        return {"text": text.decode("ascii", errors="replace") if printable else None,
                "bytes": text.hex(), "ascii": printable,
                "note": "raw ASCII only; game text tables differ and are not assumed"}
    if ftype == "array":
        count, stride = field.get("count", 1), field.get("stride")
        item = field.get("item")
        if not isinstance(item, dict):
            raise ValueError(f"{name}: array needs an 'item' field description")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_FIELDS:
            raise ValueError(f"{name}: array count must be 1..{MAX_FIELDS}")
        step = stride if stride is not None else TYPE_SIZES.get(item.get("type"), 1)
        return [decode_field(data, {**item, "offset": offset + i * step}, f"{name}[{i}]") for i in range(count)]
    if ftype == "pattern":
        from dmg_cartographer.search import parse_pattern, pattern_matches
        pat = parse_pattern(field.get("pattern", ""))
        return {"matches": pattern_matches(data, offset, pat), "offset": offset}
    raise ValueError(f"{name}: unknown type {ftype!r}; use uint8, int8, uint16, int16, bits, bcd, string, array, "
                     f"pattern")


def decode_fields(data: bytes, fields: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(fields, list) or not 1 <= len(fields) <= MAX_FIELDS:
        raise ValueError(f"provide 1..{MAX_FIELDS} fields")
    out = {}
    for i, field in enumerate(fields):
        if not isinstance(field, dict) or not isinstance(field.get("name"), str):
            raise ValueError("each field needs a string name")
        name = field["name"]
        if name in out:
            raise ValueError(f"duplicate field name {name!r}")
        out[name] = decode_field(data, field, name)
    return out


def cpu_state(regs: dict[str, int]) -> dict[str, Any]:
    """Expand register values into bytes, pairs and flags. Flag bits: Z 0x80, N 0x40, H 0x20, C 0x10."""
    f = regs["F"] & 0xF0
    hl = regs["HL"] & 0xFFFF
    return {
        "registers": {"A": regs["A"], "F": f, "B": regs["B"], "C": regs["C"], "D": regs["D"], "E": regs["E"],
                      "H": hl >> 8, "L": hl & 0xFF, "SP": regs["SP"] & 0xFFFF, "PC": regs["PC"] & 0xFFFF},
        "pairs": {"AF": (regs["A"] << 8) | f, "BC": (regs["B"] << 8) | regs["C"],
                  "DE": (regs["D"] << 8) | regs["E"], "HL": hl},
        "flags": {"Z": bool(f & 0x80), "N": bool(f & 0x40), "H": bool(f & 0x20), "C": bool(f & 0x10)},
        "hex": {k: f"0x{v:04X}" if k in ("SP", "PC") else f"0x{v:02X}" for k, v in
                {"A": regs["A"], "F": f, "B": regs["B"], "C": regs["C"], "D": regs["D"], "E": regs["E"],
                 "H": hl >> 8, "L": hl & 0xFF, "SP": regs["SP"] & 0xFFFF, "PC": regs["PC"] & 0xFFFF}.items()},
        "not_exposed_by_pyboy": ["IME (interrupt master enable)", "halted/stopped state", "interrupt flag registers",
                                 "cycle counter (private API)"],
    }


def rom_offset(bank: int, address: int) -> int:
    """File offset of a ROM address in a bank. Bank 0 is fixed at 0x0000-0x3FFF; banks 1+ map to 0x4000-0x7FFF."""
    if bank == 0:
        return address
    return bank * ROM_BANK + (address - ROM_BANK)


def check_rom_read(rom_size: int, bank: int, address: int, length: int) -> None:
    if isinstance(bank, bool) or not isinstance(bank, int) or not isinstance(address, int) or isinstance(address, bool):
        raise ValueError("bank and address must be integers")
    if isinstance(length, bool) or not isinstance(length, int) or not 1 <= length <= MAX_READ:
        raise ValueError(f"length must be 1..{MAX_READ}")
    banks = rom_size // ROM_BANK
    if not 0 <= bank < max(banks, 1):
        raise ValueError(f"bank must be 0..{max(banks, 1) - 1} for this ROM ({rom_size} bytes)")
    if bank == 0:
        if not 0 <= address or address + length > ROM_BANK:
            raise ValueError("bank 0 is fixed at 0x0000-0x3FFF; keep address + length inside it")
    elif not ROM_BANK <= address or address + length > 0x8000:
        raise ValueError("banks 1+ are visible at 0x4000-0x7FFF; keep address + length inside it")
    if rom_offset(bank, address) + length > rom_size:
        raise ValueError("read runs past the end of the ROM file")


def read_rom(rom: bytes, bank: int, address: int, length: int) -> bytes:
    check_rom_read(len(rom), bank, address, length)
    start = rom_offset(bank, address)
    return rom[start:start + length]
