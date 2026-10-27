"""State map format and reader, using a fake memory array. No ROM needed."""
import copy
import json
from pathlib import Path

import pytest

from dmg_cartographer import state_map
from dmg_cartographer.state_map import MapError, load_map, read_state, validate_map

SHA = "ab" * 20
BASE = {"format": "dmg-cartographer-map", "format_version": 1, "rom": "game.gb", "rom_sha1": SHA, "fields": []}


class FakeMemory:
    def __init__(self, cells):
        self.cells = bytearray(0x10000)
        for address, value in cells.items():
            self.cells[address] = value

    def __getitem__(self, key):
        return list(self.cells[key]) if isinstance(key, slice) else self.cells[key]


class FakePyBoy:
    def __init__(self, cells):
        self.memory = FakeMemory(cells)


def mapped(*fields):
    return validate_map({**BASE, "fields": list(fields)})


def test_reads_scalars_signed_and_16_bit_and_bits():
    m = mapped({"name": "a", "address": "0xC000", "type": "uint8"},
               {"name": "b", "address": 0xC001, "type": "int8"},
               {"name": "c", "address": "0xC002", "type": "uint16"},
               {"name": "d", "address": "0xC004", "type": "uint16", "endian": "big"},
               {"name": "e", "address": "0xC006", "type": "int16"},
               {"name": "f", "address": "0xC008", "type": "bits", "bit": 3, "width": 2})
    pyboy = FakePyBoy({0xC000: 7, 0xC001: 0xFF, 0xC002: 0x34, 0xC003: 0x12, 0xC004: 0x12, 0xC005: 0x34,
                       0xC006: 0xFE, 0xC007: 0xFF, 0xC008: 0b0101_1000})
    values = read_state(pyboy, m)
    assert values == {"a": 7, "b": -1, "c": 0x1234, "d": 0x1234, "e": -2, "f": 0b11}


def test_arrays_are_nested_row_major():
    m = mapped({"name": "board", "address": "0xC800", "type": "uint8", "shape": [2, 3]})
    pyboy = FakePyBoy({0xC800 + i: i + 1 for i in range(6)})
    assert read_state(pyboy, m)["board"] == [[1, 2, 3], [4, 5, 6]]


def test_lookup_labels_scalars_and_array_elements():
    m = mapped({"name": "phase", "address": "0xC000", "type": "uint8", "lookup": {"0": "title", "1": "active"}},
               {"name": "cells", "address": "0xC010", "type": "uint8", "shape": [3],
                "lookup": {"0": "empty"}})
    pyboy = FakePyBoy({0xC000: 1, 0xC010: 0, 0xC011: 0, 0xC012: 9})
    values = read_state(pyboy, m, labelled=True)
    assert values["phase"] == "active"
    assert values["cells"] == ["empty", "empty", "unlabelled:0x0009"]
    assert read_state(pyboy, m)["phase"] == 1


def test_rom_sha_must_match_the_loaded_rom():
    m = mapped({"name": "a", "address": "0xC000", "type": "uint8"})
    with pytest.raises(MapError, match="map is for ROM"):
        read_state(FakePyBoy({}), m, rom_sha1="cd" * 20)


@pytest.mark.parametrize("bad, message", [
    ({**BASE, "format": "other"}, "not a dmg-cartographer-map"),
    ({**BASE, "format_version": 2}, "unsupported format_version 2"),
    ({**BASE, "rom_sha1": "XYZ"}, "rom_sha1 must be 40"),
    ({**BASE, "fields": [{"name": "a", "address": "0x0100", "type": "uint8"}]}, "outside WRAM"),
    ({**BASE, "fields": [{"name": "a", "address": "0xDFFF", "type": "uint16"}]}, "crosses out of"),
    ({**BASE, "fields": [{"name": "a", "address": "0xC000", "type": "float"}]}, "type must be one of"),
    ({**BASE, "fields": [{"name": "A", "address": "0xC000", "type": "uint8"}]}, "name must be"),
    ({**BASE, "fields": [{"name": "a", "address": "0xC000", "type": "uint8"},
                         {"name": "a", "address": "0xC001", "type": "uint8"}]}, "duplicate field name"),
    ({**BASE, "fields": [{"name": "a", "address": "0xC000", "type": "uint8", "endian": "big"}]},
     "endian applies only"),
    ({**BASE, "fields": [{"name": "a", "address": "0xC000", "type": "bits", "bit": 6, "width": 3}]},
     "width must be 1-8"),
    ({**BASE, "fields": [{"name": "a", "address": "0xC000", "type": "uint8", "bit": 2}]},
     "bit and width apply only"),
    ({**BASE, "fields": [{"name": "a", "address": "0xC000", "type": "uint8", "shape": [17, 16]}]},
     "limit is 256"),
    ({**BASE, "fields": [{"name": "a", "address": "0xC000", "type": "uint8", "shape": [1024]}]},
     "limit is 256"),
    ({**BASE, "fields": [{"name": "a", "address": "0xDF00", "type": "uint16", "shape": [256]}]}, "crosses out of"),
    ({**BASE, "fields": [{"name": "a", "address": "0xC000", "type": "uint8", "lookup": {"x": "bad"}}]},
     "must be an integer written as a string"),
    ({**BASE, "fields": [{"name": "a", "address": "0xC000", "type": "uint8", "status": "maybe"}]},
     "status must be"),
])
def test_rejects_invalid_maps(bad, message):
    with pytest.raises(MapError, match=message):
        validate_map(copy.deepcopy(bad))


def test_load_map_reads_a_file_and_reports_bad_json(tmp_path):
    path = tmp_path / "map.json"
    path.write_text(json.dumps({**BASE, "fields": [{"name": "a", "address": "0xC000", "type": "uint8"}]}))
    assert load_map(path).names() == ["a"]
    path.write_text("{not json")
    with pytest.raises(MapError, match="cannot read"):
        load_map(path)


def test_schema_file_is_valid_json_and_matches_the_format_constants():
    schema = json.loads((Path(state_map.__file__).parent / "map.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["format"]["const"] == state_map.FORMAT
    assert schema["properties"]["format_version"]["const"] == state_map.FORMAT_VERSION
    assert set(schema["$defs"]["field"]["properties"]["type"]["enum"]) == set(state_map.SCALAR_SIZE)
