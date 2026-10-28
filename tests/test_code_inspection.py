"""CPU state, ROM reads, memory interpretation, disassembly, execution hooks, tracing.

Synthetic emulator and synthetic ROM bytes. The hook tests simulate execution with a fake hook table: they prove
the controller's bookkeeping, not PyBoy's hook dispatch. Real-PyBoy checks need a ROM and are not claimed here.
"""
from types import SimpleNamespace

import pytest

from dmg_cartographer import decode
from dmg_cartographer import disasm
from dmg_cartographer.core.controller import PyBoyController
from test_inspection import BankedSim
from test_knowledge import make_rom


class HookSim(BankedSim):
    """Adds the register file and the hook API that PyBoy exposes; hooks fire only when a test calls fire()."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.register_file = SimpleNamespace(A=0x12, F=0xB0, B=0x01, C=0x02, D=0x03, E=0x04, HL=0xC123,
                                             SP=0xDFF0, PC=0x0150)
        self.hooks = {}

    def hook_register(self, bank, addr, callback, context):
        self.hooks[(bank, addr)] = (callback, context)

    def hook_deregister(self, bank, addr):
        self.hooks.pop((bank, addr), None)

    def fire(self, bank, addr, times=1):
        callback, context = self.hooks[(bank, addr)]
        for _ in range(times):
            callback(context)


CODE = bytes([0x3E, 0x12,        # LD A,$12
              0xCD, 0x34, 0x12,  # CALL $1234
              0xC9,              # RET
              0x00,              # NOP
              0xCB, 0x37,        # SWAP A
              0x76,              # HALT
              0xDB,              # undefined opcode
              0x20, 0xFE])       # JR NZ,-2 (targets itself)


@pytest.fixture
def project(tmp_path):
    roms = tmp_path / ".dmg-cartographer" / "roms"
    roms.mkdir(parents=True)
    rom = bytearray(make_rom())
    rom[0x100:0x100 + len(CODE)] = CODE
    rom[0x4000:0x4004] = b"\x11\x22\x33\x44"        # bank 1, first bytes
    (roms / "puzzle.gb").write_bytes(bytes(rom))
    return str(tmp_path)


@pytest.fixture
def emu(project):
    c = PyBoyController(HookSim)
    c.load_game(project, "puzzle.gb")
    return c


# ---------- disassembler ----------

KNOWN = [
    (b"\x00", "NOP"), (b"\x3e\x12", "LD A,$12"), (b"\xc3\x50\x01", "JP $0150"), (b"\xcd\x34\x12", "CALL $1234"),
    (b"\x18\xfe", "JR $0100"), (b"\x20\x05", "JR NZ,$0107"), (b"\xcb\x37", "SWAP A"), (b"\xcb\x7e", "BIT 7,(HL)"),
    (b"\x76", "HALT"), (b"\xe0\x44", "LDH ($FF00+$44),A"), (b"\xea\x00\xc0", "LD ($C000),A"),
    (b"\x22", "LD (HL+),A"), (b"\xc9", "RET"), (b"\xd9", "RETI"), (b"\xf8\xfe", "LD HL,SP+$FE"),
    (b"\xe8\xfe", "ADD SP,-$02"), (b"\x10\x00", "STOP 0"), (b"\xd3", "DB $D3"), (b"\x86", "ADD A,(HL)"),
    (b"\xff", "RST $38"), (b"\x08\x00\xc0", "LD ($C000),SP"), (b"\x77", "LD (HL),A"), (b"\xa8", "XOR B"),
]


@pytest.mark.parametrize("raw,text", KNOWN)
def test_known_encodings(raw, text):
    insn = disasm.decode_one(raw, 0, 0x0100)
    assert insn["mnemonic"] + (" " + insn["operands"] if insn["operands"] else "") == text
    assert insn["length"] == len(raw)


def test_every_opcode_length_matches_its_table_and_cb_is_two_bytes():
    for op in range(256):
        if op == 0xCB:
            continue
        assert disasm.decode_one(bytes([op]) + bytes(3), 0, 0)["length"] == disasm._length_of(op)
    assert all(disasm.decode_one(bytes([0xCB, o]), 0, 0)["length"] == 2 for o in range(256))


def test_undefined_opcodes_are_invalid_and_relative_targets_are_absolute():
    assert disasm.decode_one(b"\xdb", 0, 0)["invalid"] is True
    jr = disasm.decode_one(b"\x18\x05", 0, 0x0200)
    assert jr["targets"] == [0x0207]


# ---------- memory interpretation ----------

def test_integer_fields_endianness_and_signedness():
    data = bytes([0x34, 0x12, 0xFF])
    assert decode.decode_field(data, {"type": "uint16", "offset": 0})["dec"] == 0x1234
    assert decode.decode_field(data, {"type": "uint16", "offset": 0, "endianness": "big"})["dec"] == 0x3412
    assert decode.decode_field(data, {"type": "int8", "offset": 2})["dec"] == -1
    assert decode.decode_field(data, {"type": "uint8", "offset": 2})["hex"] == "0xFF"


def test_bits_bcd_string_array_and_pattern():
    data = bytes([0b1011_0100, 0x12, 0x99, 0x41, 0x42, 0x00, 0xAA, 0xBB, 0xCC])
    assert decode.decode_field(data, {"type": "bits", "offset": 0, "bit": 4, "width": 3})["dec"] == 0b011
    assert decode.decode_field(data, {"type": "bcd", "offset": 1, "length": 1})["dec"] == 12
    assert decode.decode_field(data, {"type": "bcd", "offset": 2, "length": 1})["dec"] == 99
    assert decode.decode_field(data, {"type": "bcd", "offset": 0, "length": 1})["valid"] is False  # 0xB4 nibble
    assert decode.decode_field(data, {"type": "string", "offset": 3, "length": 6})["text"] == "AB"
    arr = decode.decode_field(data, {"type": "array", "offset": 6, "count": 2, "stride": 1,
                                     "item": {"type": "uint8"}})
    assert [x["dec"] for x in arr] == [0xAA, 0xBB]
    assert decode.decode_field(data, {"type": "pattern", "offset": 6, "pattern": "AA ?? CC"})["matches"] is True
    assert decode.decode_field(data, {"type": "pattern", "offset": 6, "pattern": "AA ?? BB"})["matches"] is False


def test_interpretation_rejects_bad_input_and_duplicates():
    data = bytes(4)
    with pytest.raises(ValueError, match="outside"):
        decode.decode_field(data, {"type": "uint16", "offset": 3}, "far")
    with pytest.raises(ValueError, match="unknown type"):
        decode.decode_field(data, {"type": "float"})
    with pytest.raises(ValueError, match="duplicate"):
        decode.decode_fields(data, [{"name": "a", "type": "uint8"}, {"name": "a", "type": "uint8"}])
    with pytest.raises(ValueError, match="bits need"):
        decode.decode_field(data, {"type": "bits", "bit": 6, "width": 4})


def test_decode_memory_tool_from_hex_and_from_emulator(emu):
    out = emu.decode_memory([{"name": "x", "type": "uint16", "offset": 0, "endianness": "big"}],
                            hex_bytes="C1 23")
    assert out["fields"]["x"]["dec"] == 0xC123 and out["source"]["hex"] == "C1 23"
    emu._pyboy.memory.base[0xC100:0xC102] = [0x34, 0x12]
    live = emu.decode_memory([{"name": "x", "type": "uint16"}], address="0xC100", length=2)
    assert live["fields"]["x"]["dec"] == 0x1234
    with pytest.raises(ValueError):
        emu.decode_memory([{"name": "x", "type": "uint8"}], address="0xC100", hex_bytes="00")


# ---------- CPU state ----------

def test_cpu_state_splits_registers_and_flags(emu):
    state = emu.inspect_cpu()
    assert state["registers"]["H"] == 0xC1 and state["registers"]["L"] == 0x23
    assert state["pairs"]["AF"] == 0x12B0
    assert state["flags"] == {"Z": True, "N": False, "H": True, "C": True}
    assert state["registers"]["PC"] == 0x0150 and state["hex"]["SP"] == "0xDFF0"
    assert any("IME" in item for item in state["not_exposed_by_pyboy"])


def test_inspect_cpu_and_rom_never_advance(emu):
    ticks = emu._pyboy.ticks
    emu.inspect_cpu()
    emu.inspect_rom(0, "0x0100", 8)
    emu.disassemble_rom(0, "0x0100", 4)
    emu.search_rom("CD ?? 12")
    assert emu._pyboy.ticks == ticks


# ---------- ROM reads ----------

def test_rom_reads_respect_banks_and_bounds(emu):
    row = emu.inspect_rom(0, "0x0100", 4, ascii=True, cross_check=False)
    assert row["bytes"] == list(CODE[:4]) and row["hex"] == "3E 12 CD 34"
    bank1 = emu.inspect_rom(1, "0x4000", 4, cross_check=False)
    assert bank1["bytes"] == [0x11, 0x22, 0x33, 0x44] and bank1["file_offset"] == "0x004000"
    for bad in [dict(bank=0, address=0x3FFE, length=4), dict(bank=1, address=0x3FFF, length=2),
                dict(bank=1, address=0x7FFF, length=4), dict(bank=2, address=0x4000, length=1),
                dict(bank=0, address=0x0100, length=300)]:
        with pytest.raises(ValueError):
            emu.inspect_rom(cross_check=False, **bad)


def test_rom_cross_check_reports_disagreement(emu):
    result = emu.inspect_rom(0, "0x0100", 4)
    assert result["matches_emulator"] is False  # the synthetic emulator does not hold this ROM's bytes


# ---------- disassembly ----------

def test_disassembly_of_known_code(emu):
    out = emu.disassemble_rom(0, "0x0100", 8)
    rows = out["instructions"]
    assert [r["mnemonic"] for r in rows[:6]] == ["LD", "CALL", "RET", "NOP", "SWAP", "HALT"]
    assert rows[1]["targets"] == ["0x1234"] and rows[0]["bytes"] == "3E 12"
    assert rows[6]["invalid"] is True and rows[6]["mnemonic"] == "DB"
    assert rows[7]["mnemonic"] == "JR" and rows[7]["targets"] == ["0x010B"]  # JR NZ,-2 at 0x010B targets itself
    assert out["targets"] == ["0x010B", "0x1234"]
    assert "not proof" in out["label"]


def test_disassembly_marks_instructions_cut_by_the_bank_end(emu):
    # A CALL (3 bytes) starting two bytes before the end of bank 1's window cannot fit.
    data = bytearray(emu._rom_path.read_bytes())
    data[0x7FFE] = 0xCD
    emu._rom_path.write_bytes(bytes(data))
    out = emu.disassemble_rom(1, "0x7FFE", 4)
    assert out["instructions"][0]["mnemonic"] == "CALL"
    assert out["instructions"][0]["truncated"] is True
    with pytest.raises(ValueError):
        emu.disassemble_rom(0, "0x4000", 2)  # bank 0 window is 0x0000-0x3FFF
    with pytest.raises(ValueError):
        emu.disassemble_rom(0, "0x0100", 65)


# ---------- ROM search ----------

def test_rom_search_finds_bank_0_and_switchable_bank_matches(emu):
    data = bytearray(emu._rom_path.read_bytes())
    data[0x0200:0x0203] = b"\x21\xA0\xC0"        # LD HL,$C0A0 in bank 0
    data[0x4F00:0x4F03] = b"\xFA\xA0\xC0"        # LD A,($C0A0) in bank 1
    emu._rom_path.write_bytes(bytes(data))
    out = emu.search_rom("a0 c0")
    assert out["pattern"] == "A0 C0" and out["truncated"] is False
    assert out["matches"] == [{"bank": 0, "address": "0x0201", "file_offset": "0x000201"},
                              {"bank": 1, "address": "0x4F01", "file_offset": "0x004F01"}]
    assert emu.search_rom("A0 C0", bank=1)["matches"] == [out["matches"][1]]
    # a match feeds straight into the disassembler, one byte back for the opcode
    insn = emu.disassemble_rom(1, "0x4F00", 1)["instructions"][0]
    assert insn["mnemonic"] == "LD" and insn["bytes"] == "FA A0 C0"


def test_rom_search_wildcards_and_cap(emu):
    out = emu.search_rom("CD ?? 12")                 # CALL $1234 at 0x0102
    assert [m["address"] for m in out["matches"]] == ["0x0102"]
    capped = emu.search_rom("00 00", max_results=3)  # the synthetic ROM is mostly zeros
    assert capped["count"] == 3 and capped["truncated"] is True
    assert [m["address"] for m in capped["matches"]] == ["0x0000", "0x0001", "0x0002"]  # overlapping


def test_rom_search_rejects_bad_input(emu):
    for bad in [dict(pattern="A0 C"), dict(pattern="ZZ"), dict(pattern=""), dict(pattern="A0", bank=2),
                dict(pattern="A0", max_results=0)]:
        with pytest.raises(ValueError):
            emu.search_rom(**bad)


# ---------- hooks ----------

def test_hook_register_count_observe_and_remove(emu):
    registered = emu.hook_execution("register", 0, "0x0150", label="entry", max_observations=2)
    assert registered["registered"]["count"] == 0
    emu._pyboy.fire(0, 0x0150, times=3)
    listed = emu.hook_execution("list")
    hook = listed["hooks"][0]
    assert hook["count"] == 3 and len(hook["observations"]) == 2          # bounded
    first = hook["observations"][0]
    assert first["PC"] == 0x0150 and first["A"] == 0x12 and first["HL"] == 0xC123 and first["BC"] == 0x0102
    assert "frame numbers" in first["frame_note"]
    removed = emu.hook_execution("remove", 0, "0x0150")
    assert removed["final_count"] == 3 and (0, 0x0150) not in emu._pyboy.hooks
    with pytest.raises(ValueError):
        emu.hook_execution("remove", 0, "0x0150")


def test_hook_validation_and_limits(emu):
    emu.hook_execution("register", 0, "0x0100")
    with pytest.raises(ValueError, match="already exists"):
        emu.hook_execution("register", 0, "0x0100")
    with pytest.raises(ValueError):
        emu.hook_execution("register", 0, "0x4000")        # bank 0 is 0x0000-0x3FFF
    with pytest.raises(ValueError):
        emu.hook_execution("register", 5, "0x4000")        # bank out of range for this ROM
    with pytest.raises(ValueError):
        emu.hook_execution("register", None, "0x0100")
    with pytest.raises(ValueError):
        emu.hook_execution("spy", 0, "0x0100")
    for i in range(15):
        emu.hook_execution("register", 1, hex(0x4000 + i))
    with pytest.raises(ValueError, match="at most 16"):
        emu.hook_execution("register", 1, "0x4100")


def test_hooks_are_cleared_when_a_different_rom_loads(emu, project):
    emu.hook_execution("register", 0, "0x0150")
    emu.load_game(project, "puzzle.gb")
    assert emu.hook_execution("list")["count"] == 0


def test_hook_callback_errors_are_counted_not_raised(emu):
    emu.hook_execution("register", 0, "0x0150")
    emu._pyboy.register_file = None  # makes the snapshot inside the callback fail
    emu._pyboy.fire(0, 0x0150, times=2)  # must not raise into the emulator
    hook = emu.hook_execution("list")["hooks"][0]
    assert hook["count"] == 2 and hook["callback_errors"] == 2
