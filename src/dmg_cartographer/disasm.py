"""Sharp LR35902 (Game Boy CPU) instruction decoder. Pure: bytes in, instructions out.

This is a DECODING INTERPRETATION of bytes, not proof that the bytes execute. Data can look like code.
Undefined opcodes are reported as "DB" with invalid=True.
"""
from __future__ import annotations

from typing import Any

R8 = ["B", "C", "D", "E", "H", "L", "(HL)", "A"]
R16 = ["BC", "DE", "HL", "SP"]
R16_STACK = ["BC", "DE", "HL", "AF"]
CONDS = ["NZ", "Z", "NC", "C"]
# (mnemonic, operand prefix) for the eight ALU operations: ADD A,r / SUB r / ...
ALU = [("ADD", "A,"), ("ADC", "A,"), ("SUB", ""), ("SBC", "A,"), ("AND", ""), ("XOR", ""), ("OR", ""), ("CP", "")]
CB_ROT = ["RLC", "RRC", "RL", "RR", "SLA", "SRA", "SWAP", "SRL"]
UNDEFINED = {0xD3, 0xDB, 0xDD, 0xE3, 0xE4, 0xEB, 0xEC, 0xED, 0xF4, 0xFC, 0xFD}


def _decode_base(op: int, imm: bytes, pc: int) -> tuple[str, str, int, list[int], bool]:
    """Return (mnemonic, operands, length, targets, invalid) for a non-CB opcode.

    `imm` holds the bytes after the opcode; `pc` is the instruction's address (for relative jumps).
    """
    d8 = f"${imm[0]:02X}" if len(imm) >= 1 else "$??"
    a16 = f"${imm[1] << 8 | imm[0]:04X}" if len(imm) >= 2 else "$????"
    rel = (imm[0] - 256 if imm[0] >= 128 else imm[0]) if imm else 0
    target_rel = (pc + 2 + rel) & 0xFFFF
    if op in UNDEFINED:
        return "DB", f"${op:02X}", 1, [], True
    if 0x40 <= op <= 0x7F:
        if op == 0x76:
            return "HALT", "", 1, [], False
        return "LD", f"{R8[(op >> 3) & 7]},{R8[op & 7]}", 1, [], False
    if 0x80 <= op <= 0xBF:
        name, prefix = ALU[(op >> 3) & 7]
        return name, prefix + R8[op & 7], 1, [], False
    x, y, z = op >> 6, (op >> 3) & 7, op & 7
    q, p = (op >> 3) & 1, (op >> 4) & 3
    if op == 0x00:
        return "NOP", "", 1, [], False
    if z == 0 and x == 0:
        if op == 0x08:
            return "LD", f"({a16}),SP", 3, [], False
        if op == 0x10:
            return "STOP", "0", 2, [], False
        if op == 0x18:
            return "JR", f"${target_rel:04X}", 2, [target_rel], False
        cond = CONDS[y - 4]
        return "JR", f"{cond},${target_rel:04X}", 2, [target_rel], False
    if x == 0 and z == 1:
        if q == 0:
            return "LD", f"{R16[p]},{a16}", 3, [], False
        return "ADD", f"HL,{R16[p]}", 1, [], False
    if x == 0 and z == 2:
        mnem, ops = {(0, 0): ("LD", "(BC),A"), (0, 1): ("LD", "A,(BC)"), (1, 0): ("LD", "(DE),A"),
                     (1, 1): ("LD", "A,(DE)"), (2, 0): ("LD", "(HL+),A"), (2, 1): ("LD", "A,(HL+)"),
                     (3, 0): ("LD", "(HL-),A"), (3, 1): ("LD", "A,(HL-)")}[(p, q)]
        return mnem, ops, 1, [], False
    if x == 0 and z == 3:
        return ("DEC" if q else "INC"), R16[p], 1, [], False
    if x == 0 and z == 4:
        return "INC", R8[y], 1, [], False
    if x == 0 and z == 5:
        return "DEC", R8[y], 1, [], False
    if x == 0 and z == 6:
        return "LD", f"{R8[y]},{d8}", 2, [], False
    if x == 0 and z == 7:
        return ["RLCA", "RRCA", "RLA", "RRA", "DAA", "CPL", "SCF", "CCF"][y], "", 1, [], False
    if x == 3 and z == 0:
        if y < 4:
            return "RET", CONDS[y], 1, [], False
        if op == 0xE0:
            return "LDH", f"($FF00+{d8}),A", 2, [], False
        if op == 0xE8:
            signed = f"-${256 - imm[0]:02X}" if imm and imm[0] >= 128 else d8
            return "ADD", f"SP,{signed}", 2, [], False
        if op == 0xF0:
            return "LDH", f"A,($FF00+{d8})", 2, [], False
        if op == 0xF8:
            return "LD", f"HL,SP+{d8}", 2, [], False
    if x == 3 and z == 1:
        if q == 0:
            return "POP", R16_STACK[p], 1, [], False
        return _ret_group(p)
    if x == 3 and z == 2:
        if y < 4:
            return "JP", f"{CONDS[y]},{a16}", 3, [int(a16[1:], 16)] if len(imm) >= 2 else [], False
        if op == 0xE2:
            return "LD", "(C),A", 1, [], False
        if op == 0xEA:
            return "LD", f"({a16}),A", 3, [], False
        if op == 0xF2:
            return "LD", "A,(C)", 1, [], False
        if op == 0xFA:
            return "LD", f"A,({a16})", 3, [], False
    if x == 3 and z == 3:
        if op == 0xC3:
            return "JP", a16, 3, [int(a16[1:], 16)] if len(imm) >= 2 else [], False
        if op == 0xF3:
            return "DI", "", 1, [], False
        if op == 0xFB:
            return "EI", "", 1, [], False
    if x == 3 and z == 4:
        if y < 4:
            return "CALL", f"{CONDS[y]},{a16}", 3, [int(a16[1:], 16)] if len(imm) >= 2 else [], False
    if x == 3 and z == 5:
        if q == 0:
            return "PUSH", R16_STACK[p], 1, [], False
        if op == 0xCD:
            return "CALL", a16, 3, [int(a16[1:], 16)] if len(imm) >= 2 else [], False
    if x == 3 and z == 6:
        name, prefix = ALU[y]
        return name, prefix + d8, 2, [], False
    if x == 3 and z == 7:
        target = y * 8
        return "RST", f"${target:02X}", 1, [target], False
    raise AssertionError(f"unhandled opcode {op:02X}")  # pragma: no cover - table is exhaustive


def _ret_group(p: int) -> tuple[str, str, int, list[int], bool]:
    if p == 0:
        return "RET", "", 1, [], False
    if p == 1:
        return "RETI", "", 1, [], False
    if p == 2:
        return "JP", "(HL)", 1, [], False
    return "LD", "SP,HL", 1, [], False


def _length_of(op: int) -> int:
    return {0x01: 3, 0x08: 3, 0x11: 3, 0x21: 3, 0x31: 3, 0xC2: 3, 0xC3: 3, 0xC4: 3, 0xCA: 3, 0xCC: 3, 0xCD: 3,
            0xD2: 3, 0xD4: 3, 0xDA: 3, 0xDC: 3, 0xEA: 3, 0xFA: 3,
            0x06: 2, 0x0E: 2, 0x16: 2, 0x1E: 2, 0x26: 2, 0x2E: 2, 0x36: 2, 0x3E: 2, 0xC6: 2, 0xCE: 2, 0xD6: 2,
            0xDE: 2, 0xE6: 2, 0xEE: 2, 0xF6: 2, 0xFE: 2, 0xE0: 2, 0xF0: 2, 0xE8: 2, 0xF8: 2, 0x10: 2, 0x18: 2,
            0x20: 2, 0x28: 2, 0x30: 2, 0x38: 2, 0xCB: 2}.get(op, 1)


def decode_one(data: bytes, offset: int, base_address: int) -> dict[str, Any]:
    """Decode the instruction at data[offset]. `base_address` is the address of data[0] (for targets)."""
    op = data[offset]
    pc = base_address + offset
    if op == 0xCB:
        if offset + 1 >= len(data):
            return {"address": pc, "bytes": [op], "mnemonic": "CB", "operands": "??", "length": 1,
                    "targets": [], "invalid": False, "truncated": True}
        sub = data[offset + 1]
        r, y, x = R8[sub & 7], (sub >> 3) & 7, sub >> 6
        if x == 0:
            mnem, ops = CB_ROT[y], r
        else:
            mnem, ops = {1: "BIT", 2: "RES", 3: "SET"}[x], f"{y},{r}"
        return {"address": pc, "bytes": [op, sub], "mnemonic": mnem, "operands": ops, "length": 2,
                "targets": [], "invalid": False, "truncated": False}
    length = _length_of(op)
    imm = data[offset + 1:offset + length]
    truncated = len(imm) < length - 1
    mnem, ops, ln, targets, invalid = _decode_base(op, bytes(imm), pc)
    ln = max(ln, 1)
    raw = data[offset:offset + ln]
    return {"address": pc, "bytes": list(raw), "mnemonic": mnem, "operands": ops, "length": ln,
            "targets": targets, "invalid": invalid, "truncated": truncated or len(raw) < ln}


def disassemble(data: bytes, base_address: int, count: int) -> list[dict[str, Any]]:
    out = []
    offset = 0
    while offset < len(data) and len(out) < count:
        insn = decode_one(data, offset, base_address)
        out.append(insn)
        offset += insn["length"]
    return out
