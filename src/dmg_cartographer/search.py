"""Pure memory-search helpers: value decoding, candidate filters, byte patterns. No emulator access."""
from __future__ import annotations

from typing import Any, Sequence

DTYPES = {"uint8": (1, False), "int8": (1, True), "uint16": (2, False), "int16": (2, True)}
ENDIANNESS = ("little", "big")
CONDITIONS = ("unknown", "exact", "range", "increased", "decreased", "changed", "unchanged", "pattern")
NEEDS_PREVIOUS = frozenset({"increased", "decreased", "changed", "unchanged"})
MAX_PATTERN_BYTES = 32


def width_of(dtype: str) -> int:
    return DTYPES[dtype][0]


def value_limits(dtype: str) -> tuple[int, int]:
    width, signed = DTYPES[dtype]
    if signed:
        return -(1 << (8 * width - 1)), (1 << (8 * width - 1)) - 1
    return 0, (1 << (8 * width)) - 1


def decode(data: Sequence[int], offset: int, dtype: str, endianness: str) -> int:
    """Decode the value at `offset`. Single bytes ignore endianness."""
    width, signed = DTYPES[dtype]
    raw = bytes(data[offset:offset + width])
    return int.from_bytes(raw, endianness if width > 1 else "little", signed=signed)


def parse_pattern(text: str) -> list[int | None]:
    """'00 FF ?? 12' -> [0x00, 0xFF, None, 0x12]. '??' matches any byte."""
    if not isinstance(text, str):
        raise ValueError("pattern must be a string of hex bytes such as '00 FF ?? 12'")
    tokens = text.split()
    if not 1 <= len(tokens) <= MAX_PATTERN_BYTES:
        raise ValueError(f"pattern must have 1..{MAX_PATTERN_BYTES} bytes")
    out: list[int | None] = []
    for token in tokens:
        if token == "??":
            out.append(None)
        elif len(token) == 2 and all(c in "0123456789abcdefABCDEF" for c in token):
            out.append(int(token, 16))
        else:
            raise ValueError(f"invalid pattern byte {token!r}; use two hex digits or ??")
    return out


def pattern_matches(data: Sequence[int], offset: int, pattern: list[int | None]) -> bool:
    if offset + len(pattern) > len(data):
        return False
    return all(want is None or data[offset + i] == want for i, want in enumerate(pattern))


def validate_request(*, dtype: str, endianness: str, condition: str, exact: int | None, low: int | None,
                     high: int | None, pattern: str | None, has_session: bool) -> list[int | None] | None:
    """Check arguments and return the parsed pattern (or None). Raises ValueError with a clear message."""
    if dtype not in DTYPES:
        raise ValueError(f"dtype must be one of {list(DTYPES)}")
    if endianness not in ENDIANNESS:
        raise ValueError(f"endianness must be one of {list(ENDIANNESS)}")
    if condition not in CONDITIONS:
        raise ValueError(f"condition must be one of {list(CONDITIONS)}")
    if condition in NEEDS_PREVIOUS and not has_session:
        raise ValueError(f"condition {condition!r} compares with earlier values; start from a session "
                         f"(a first search can use unknown, exact, range or pattern)")
    if condition == "unknown" and has_session:
        raise ValueError("condition 'unknown' only starts a session; refine it with a later condition")
    lo, hi = value_limits(dtype)

    def check_int(value: Any, label: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise ValueError(f"{label} must be an integer in {lo}..{hi} for {dtype}")

    if condition == "exact":
        check_int(exact, "exact")
    elif exact is not None:
        raise ValueError("exact is only used with condition 'exact'")
    if condition == "range":
        check_int(low, "low")
        check_int(high, "high")
        if low > high:
            raise ValueError("low must be <= high")
    elif low is not None or high is not None:
        raise ValueError("low and high are only used with condition 'range'")
    if condition == "pattern":
        if dtype != "uint8":
            raise ValueError("pattern searches compare raw bytes; use dtype uint8")
        return parse_pattern(pattern)
    if pattern is not None:
        raise ValueError("pattern is only used with condition 'pattern'")
    return None


def keep(condition: str, current: int, previous: int | None, *, exact: int | None, low: int | None,
         high: int | None) -> bool:
    """Value-based conditions. `previous` is required for the NEEDS_PREVIOUS conditions."""
    if condition in ("unknown", "pattern"):
        return True
    if condition == "exact":
        return current == exact
    if condition == "range":
        return low <= current <= high
    if previous is None:
        raise ValueError(f"condition {condition!r} needs a previous value")
    if condition == "increased":
        return current > previous
    if condition == "decreased":
        return current < previous
    if condition == "changed":
        return current != previous
    if condition == "unchanged":
        return current == previous
    raise ValueError(f"unknown condition {condition!r}")
