"""Pure address policy for reads and writes: which regions exist, which are writable. No emulator access."""
from __future__ import annotations

# (start, end exclusive, name, writable_by_default, why)
REGION_TABLE = [
    (0x0000, 0x8000, "ROM", False, "cartridge ROM is never writable through this server (writes reach the MBC)"),
    (0x8000, 0xA000, "VRAM", False, "video memory: writes can corrupt the screen; needs allow_sensitive"),
    (0xA000, 0xC000, "CART_RAM", False, "cartridge RAM: may hold saves; needs allow_sensitive"),
    (0xC000, 0xE000, "WRAM", True, "work RAM"),
    (0xE000, 0xFE00, "ECHO", False, "echo RAM mirrors WRAM; write 0xC000-0xDDFF instead"),
    (0xFE00, 0xFEA0, "OAM", False, "sprite attribute memory: needs allow_sensitive"),
    (0xFEA0, 0xFF00, "UNUSABLE", False, "unusable address space"),
    (0xFF00, 0xFF80, "IO", False, "hardware registers: side effects; needs allow_sensitive"),
    (0xFF80, 0xFFFF, "HRAM", True, "high RAM"),
    (0xFFFF, 0x10000, "IE", False, "interrupt enable register: needs allow_sensitive"),
]
MAX_RANGE = 16


def parse_address(value) -> int:
    """Accept an int or a hex/decimal string such as '0xC123' or '49443'. Returns 0..0xFFFF."""
    if isinstance(value, bool):
        raise ValueError("address must be an integer or a string such as '0xC123'")
    if isinstance(value, str):
        text = value.strip()
        try:
            value = int(text, 16) if text.lower().startswith("0x") else int(text, 10)
        except ValueError:
            raise ValueError(f"invalid address {value!r}; use an integer or a string such as '0xC123'") from None
    if not isinstance(value, int) or not 0 <= value <= 0xFFFF:
        raise ValueError("address must be within 0x0000..0xFFFF")
    return value


def region_of(address: int) -> tuple[str, bool, str]:
    """Return (name, writable_by_default, reason) for one address."""
    for start, end, name, writable, why in REGION_TABLE:
        if start <= address < end:
            return name, writable, why
    raise ValueError(f"address 0x{address:04X} is outside the Game Boy address space")


def check_write(address: int, length: int, *, allow_sensitive: bool) -> str:
    """Validate a write target and return its region name. Every byte must sit in one allowed region."""
    if isinstance(length, bool) or not isinstance(length, int) or not 1 <= length <= MAX_RANGE:
        raise ValueError(f"write length must be 1..{MAX_RANGE} bytes")
    if address + length > 0x10000:
        raise ValueError("write runs past the end of the address space")
    names = set()
    for offset in range(length):
        name, writable, why = region_of(address + offset)
        if name == "ROM":
            raise ValueError(f"refusing to write cartridge ROM at 0x{address + offset:04X}: {why}")
        if name in ("ECHO", "UNUSABLE"):
            raise ValueError(f"refusing to write 0x{address + offset:04X}: {why}")
        if not writable and not allow_sensitive:
            raise ValueError(f"refusing to write {name} at 0x{address + offset:04X}: {why}")
        names.add(name)
    if len(names) != 1:
        raise ValueError(f"write range spans several regions {sorted(names)}; split it")
    return names.pop()


def check_values(values: list[int]) -> list[int]:
    if not isinstance(values, list) or not values or len(values) > MAX_RANGE:
        raise ValueError(f"provide 1..{MAX_RANGE} byte values")
    for v in values:
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 255:
            raise ValueError("byte values must be integers 0..255")
    return values
