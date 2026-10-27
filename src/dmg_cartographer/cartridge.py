"""Cartridge header parsing from ROM bytes. Pure: takes bytes, returns facts. Never modifies anything."""
from __future__ import annotations

from typing import Any

MIN_HEADER = 0x150

# Common cartridge type codes (header 0x147). Unknown codes are reported as unknown, not guessed.
CART_TYPES = {
    0x00: "ROM ONLY", 0x01: "MBC1", 0x02: "MBC1+RAM", 0x03: "MBC1+RAM+BATTERY",
    0x05: "MBC2", 0x06: "MBC2+BATTERY", 0x08: "ROM+RAM", 0x09: "ROM+RAM+BATTERY",
    0x0B: "MMM01", 0x0C: "MMM01+RAM", 0x0D: "MMM01+RAM+BATTERY",
    0x0F: "MBC3+TIMER+BATTERY", 0x10: "MBC3+TIMER+RAM+BATTERY", 0x11: "MBC3",
    0x12: "MBC3+RAM", 0x13: "MBC3+RAM+BATTERY",
    0x19: "MBC5", 0x1A: "MBC5+RAM", 0x1B: "MBC5+RAM+BATTERY", 0x1C: "MBC5+RUMBLE",
    0x1D: "MBC5+RUMBLE+RAM", 0x1E: "MBC5+RUMBLE+RAM+BATTERY", 0x20: "MBC6", 0x22: "MBC7+SENSOR+RUMBLE+RAM+BATTERY",
    0xFC: "POCKET CAMERA", 0xFD: "BANDAI TAMA5", 0xFE: "HuC3", 0xFF: "HuC1+RAM+BATTERY",
}
RAM_SIZES = {0x00: 0, 0x01: 2 * 1024, 0x02: 8 * 1024, 0x03: 32 * 1024, 0x04: 128 * 1024, 0x05: 64 * 1024}


def header_checksum(rom: bytes) -> int:
    """Pan Docs header checksum over 0x134-0x14C."""
    x = 0
    for byte in rom[0x134:0x14D]:
        x = (x - byte - 1) & 0xFF
    return x


def parse_header(rom: bytes, file_size: int | None = None) -> dict[str, Any]:
    """Facts from the ROM header plus consistency checks against the file itself."""
    size = len(rom) if file_size is None else file_size
    if len(rom) < MIN_HEADER:
        return {"valid_header": False, "error": f"ROM is {len(rom)} bytes; a header needs {MIN_HEADER}"}
    title = rom[0x134:0x144].split(b"\x00", 1)[0].decode("ascii", errors="replace").rstrip()
    cgb_flag = rom[0x143]
    type_code = rom[0x147]
    rom_code = rom[0x148]
    ram_code = rom[0x149]
    declared_rom_bytes = (32 * 1024) << rom_code if rom_code <= 8 else None
    computed = header_checksum(rom)
    cart_name = CART_TYPES.get(type_code)
    return {
        "valid_header": True,
        "title": title,
        "cgb_flag": f"0x{cgb_flag:02X}",
        "cgb_compatible": cgb_flag in (0x80, 0xC0),
        "cgb_only": cgb_flag == 0xC0,
        "sgb_flag": rom[0x146] == 0x03,
        "cartridge_type": {"code": f"0x{type_code:02X}", "name": cart_name or "unknown code",
                           "mbc": (cart_name or "unknown").split("+")[0] if cart_name else "unknown"},
        "battery": bool(cart_name and "BATTERY" in cart_name),
        "rom_size": {"code": f"0x{rom_code:02X}", "declared_bytes": declared_rom_bytes,
                     "declared_banks": declared_rom_bytes // 0x4000 if declared_rom_bytes else None,
                     "file_bytes": size,
                     "matches_file": declared_rom_bytes == size if declared_rom_bytes else False},
        "ram_size": {"code": f"0x{ram_code:02X}", "bytes": RAM_SIZES.get(ram_code)},
        "header_checksum": {"declared": f"0x{rom[0x14D]:02X}", "computed": f"0x{computed:02X}",
                            "valid": computed == rom[0x14D]},
    }


def title_matches(header_title: str, emulator_title: str | None) -> bool | None:
    """PyBoy's title may be trimmed or padded differently; compare case-insensitively on alphanumerics."""
    if emulator_title is None:
        return None

    def norm(text: str) -> str:
        return "".join(c for c in text.upper() if c.isalnum())

    return norm(header_title) == norm(emulator_title)
