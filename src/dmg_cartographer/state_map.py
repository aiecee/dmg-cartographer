"""State map: a JSON description of where a game keeps its state, and a reader that decodes it from PyBoy.

The reader needs no MCP and no agent. A training loop can do:

    from dmg_cartographer.state_map import load_map, read_state
    state_map = load_map("map.json")
    values = read_state(pyboy, state_map, rom_sha1=sha1, labelled=True)

map.json (format_version 1):

    {
      "format": "dmg-cartographer-map",
      "format_version": 1,
      "rom": "Dr. Mario (World) (Rev 1).gb",
      "rom_sha1": "<40 hex characters>",
      "fields": [
        {"name": "board", "address": "0xC800", "type": "uint8", "shape": [16, 8], "status": "hypothesis",
         "lookup": {"0": "empty"}, "note": "row-major, row 0 at the top"},
        {"name": "level", "address": "0xC0A0", "type": "uint16", "endian": "little"},
        {"name": "pill_flag", "address": "0xC0B0", "type": "bits", "bit": 3, "width": 1}
      ]
    }

Types: uint8, int8, uint16, int16 (16-bit types take "endian": little or big), and bits (one byte, "bit" 0-7 and
"width" 1-8). "shape" turns a field into a nested array of that type, row-major. Addresses must lie in WRAM
(0xC000-0xDFFF) or HRAM (0xFF80-0xFFFE), and a field may not cross from one region into the other.
"status" is hypothesis or verified, as in the discovery records. "lookup" maps a raw value, written as a string,
to a label; it applies to each element of an array.

Standard library, plus PyBoy only for read_state. Nothing here imports MCP.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FORMAT = "dmg-cartographer-map"
FORMAT_VERSION = 1
MAX_ELEMENTS = 256
MAX_BYTES = 1024
REGIONS = ((0xC000, 0xDFFF, "WRAM"), (0xFF80, 0xFFFE, "HRAM"))
SCALAR_SIZE = {"uint8": 1, "int8": 1, "uint16": 2, "int16": 2, "bits": 1}
ROM_SHA1 = re.compile(r"^[0-9a-f]{40}$")
HEX_ADDRESS = re.compile(r"^0x[0-9A-Fa-f]{1,4}$")
FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class MapError(ValueError):
    """map.json is not valid for this format, or does not match the ROM being read."""


@dataclass(frozen=True)
class FieldSpec:
    name: str
    address: int
    type: str
    size: int
    shape: tuple[int, ...] | None
    endian: str
    bit: int
    width: int
    status: str
    lookup: dict[int, str]
    note: str


@dataclass(frozen=True)
class StateMap:
    rom: str
    rom_sha1: str
    fields: tuple[FieldSpec, ...] = field(default_factory=tuple)

    def names(self) -> list[str]:
        return [f.name for f in self.fields]


def _where(path: str, message: str) -> MapError:
    return MapError(f"{path}: {message}")


def _address(value: Any, path: str) -> int:
    if isinstance(value, bool):
        raise _where(path, "address must be an integer or a hex string like \"0xC800\"")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and HEX_ADDRESS.fullmatch(value):
        number = int(value, 16)
    else:
        raise _where(path, "address must be an integer or a hex string like \"0xC800\"")
    if not any(low <= number <= high for low, high, _ in REGIONS):
        raise _where(path, f"address 0x{number:04X} is outside WRAM (0xC000-0xDFFF) and HRAM (0xFF80-0xFFFE)")
    return number


def _region_of(address: int, last: int) -> str | None:
    for low, high, name in REGIONS:
        if low <= address <= high and low <= last <= high:
            return name
    return None


def _field(raw: Any, index: int, seen: set[str]) -> FieldSpec:
    path = f"fields[{index}]"
    if not isinstance(raw, dict):
        raise _where(path, "must be an object")
    name = raw.get("name")
    if not isinstance(name, str) or not FIELD_NAME.fullmatch(name):
        raise _where(path, "name must be lowercase letters, digits and underscores, starting with a letter")
    path = f"fields[{index}] ({name})"
    if name in seen:
        raise _where(path, "duplicate field name")
    seen.add(name)
    kind = raw.get("type")
    if kind not in SCALAR_SIZE:
        raise _where(path, f"type must be one of {sorted(SCALAR_SIZE)}")
    address = _address(raw.get("address"), path)
    endian = raw.get("endian", "little")
    if endian not in ("little", "big"):
        raise _where(path, "endian must be little or big")
    if kind in ("uint8", "int8", "bits") and "endian" in raw:
        raise _where(path, "endian applies only to 16-bit types")
    bit, width = raw.get("bit", 0), raw.get("width", 1)
    if kind == "bits":
        if not isinstance(bit, int) or not 0 <= bit <= 7:
            raise _where(path, "bit must be 0-7")
        if not isinstance(width, int) or not 1 <= width <= 8 - bit:
            raise _where(path, "width must be 1-8 and fit within the byte after bit")
    elif "bit" in raw or "width" in raw:
        raise _where(path, "bit and width apply only to the bits type")
    shape = raw.get("shape")
    if shape is not None:
        if not isinstance(shape, list) or not shape or not all(isinstance(d, int) and d >= 1 for d in shape):
            raise _where(path, "shape must be a non-empty list of positive integers")
        elements = 1
        for d in shape:
            elements *= d
        if elements > MAX_ELEMENTS:
            raise _where(path, f"shape has {elements} elements; the limit is {MAX_ELEMENTS}")
        shape = tuple(shape)
        count = elements
    else:
        count = 1
    size = SCALAR_SIZE[kind] * count
    if size > MAX_BYTES:
        raise _where(path, f"field covers {size} bytes; the limit is {MAX_BYTES}")
    if _region_of(address, address + size - 1) is None:
        raise _where(path, "field crosses out of its WRAM or HRAM region")
    status = raw.get("status", "hypothesis")
    if status not in ("hypothesis", "verified"):
        raise _where(path, "status must be hypothesis or verified")
    lookup: dict[int, str] = {}
    for key, label in (raw.get("lookup") or {}).items():
        if not isinstance(key, str) or not re.fullmatch(r"-?\d+", key):
            raise _where(path, f"lookup key {key!r} must be an integer written as a string")
        if not isinstance(label, str) or not label:
            raise _where(path, f"lookup label for {key} must be a non-empty string")
        lookup[int(key)] = label
    note = raw.get("note", "")
    if not isinstance(note, str) or len(note) > 500:
        raise _where(path, "note must be a string of at most 500 characters")
    return FieldSpec(name=name, address=address, type=kind, size=size, shape=shape, endian=endian,
                     bit=bit, width=width, status=status, lookup=lookup, note=note)


def validate_map(raw: Any) -> StateMap:
    """Check a parsed map.json and return it as a StateMap. Raises MapError with the path of the first problem."""
    if not isinstance(raw, dict):
        raise MapError("map must be a JSON object")
    if raw.get("format") != FORMAT:
        raise MapError(f"not a {FORMAT} map: format is {raw.get('format')!r}")
    if raw.get("format_version") != FORMAT_VERSION:
        raise MapError(f"unsupported format_version {raw.get('format_version')!r}; this reader reads "
                       f"version {FORMAT_VERSION}")
    sha1 = raw.get("rom_sha1")
    if not isinstance(sha1, str) or not ROM_SHA1.fullmatch(sha1):
        raise MapError("rom_sha1 must be 40 lowercase hex characters")
    rom = raw.get("rom")
    if not isinstance(rom, str) or not rom:
        raise MapError("rom must be the ROM file name")
    fields_raw = raw.get("fields")
    if not isinstance(fields_raw, list):
        raise MapError("fields must be a list")
    seen: set[str] = set()
    specs = tuple(_field(item, i, seen) for i, item in enumerate(fields_raw))
    return StateMap(rom=rom, rom_sha1=sha1, fields=specs)


def load_map(path: str | Path) -> StateMap:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MapError(f"cannot read {path}: {error}") from error
    return validate_map(raw)


def _signed(value: int, bits: int) -> int:
    return value - (1 << bits) if value >= 1 << (bits - 1) else value


def _scalar(data: list[int], spec_type: str, endian: str, bit: int, width: int) -> int:
    if spec_type == "uint8":
        return data[0]
    if spec_type == "int8":
        return _signed(data[0], 8)
    if spec_type == "bits":
        return (data[0] >> bit) & ((1 << width) - 1)
    raw = data[0] | (data[1] << 8) if endian == "little" else (data[0] << 8) | data[1]
    return _signed(raw, 16) if spec_type == "int16" else raw


def _nest(flat: list[Any], shape: tuple[int, ...]) -> list[Any]:
    if len(shape) == 1:
        return flat
    step = len(flat) // shape[0]
    return [_nest(flat[i * step:(i + 1) * step], shape[1:]) for i in range(shape[0])]


def _labelled(value: int, lookup: dict[int, str]) -> str:
    return lookup.get(value, f"unlabelled:0x{value & 0xFFFF:04X}")


def _decode(spec: FieldSpec, memory: Any, labelled: bool) -> Any:
    raw = list(memory[spec.address:spec.address + spec.size])
    if spec.shape is None:
        value = _scalar(raw, spec.type, spec.endian, spec.bit, spec.width)
        return _labelled(value, spec.lookup) if labelled and spec.lookup else value
    item = SCALAR_SIZE[spec.type]
    values = [_scalar(raw[i:i + item], spec.type, spec.endian, spec.bit, spec.width)
              for i in range(0, len(raw), item)]
    if labelled and spec.lookup:
        values = [_labelled(v, spec.lookup) for v in values]
    return _nest(values, spec.shape)


def read_state(pyboy: Any, state_map: StateMap, *, rom_sha1: str | None = None, labelled: bool = False) -> dict[str, Any]:
    """Decode every field of the map from the emulator's current memory. Advances no frames.

    rom_sha1: if given, must match the map. labelled: return lookup labels where the map has them.
    """
    if rom_sha1 is not None and rom_sha1 != state_map.rom_sha1:
        raise MapError(f"map is for ROM {state_map.rom_sha1[:12]}..., but the loaded ROM is {rom_sha1[:12]}...")
    memory = pyboy.memory
    return {spec.name: _decode(spec, memory, labelled) for spec in state_map.fields}
