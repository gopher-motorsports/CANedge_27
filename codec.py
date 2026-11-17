"""Encode/decode GopherCAN group payloads (mirror of GopherCAN.c).

Source of truth: gophercan-lib/GopherCAN.c  decode_parameter()/encode_parameter().

Wire format of a data frame: 11-bit CAN ID == group ID, up to 8 data bytes.
Each parameter occupies `length` bytes at byte offset `start`, MSB- or LSB-first.
    physical = raw * scale + offset
FLOATING/DOUBLE parameters put a *signed* integer on the wire.

Firmware quirk
--------------
For integer-typed parameters the firmware computes
    data = raw * (T)SCALE + (T)OFFSET          # (T) = the param's integer type
so a fractional scale truncates to 0 (go4-26: gpsSpeed_mph and gpsCourse_deg,
both UNSIGNED16 with scale 0.01, always read 0 on the car).  By default this
module decodes the *intended* physical value; pass firmware_quirks=True to
reproduce what the old logger recorded.
"""
from __future__ import annotations

import math

import numpy as np

from .network import Network, Param

F32 = np.float32


def _signed(raw: int, bits: int) -> int:
    raw &= (1 << bits) - 1
    return raw - (1 << bits) if raw >> (bits - 1) else raw


def _llround(x: float) -> int:
    """C llround(): round half away from zero."""
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def _raw_from_bytes(data: bytes, start: int, length: int, encoding: str) -> int:
    chunk = data[start:start + length]
    return int.from_bytes(chunk, "big" if encoding == "MSB" else "little")


def decode_slot(p: Param, start: int, data: bytes, *, firmware_quirks: bool = False):
    """Decode one parameter occupying bytes [start, start+p.length) of `data`.

    Returns None if the payload is too short or the wire length is one the
    firmware also rejects (FLOATING with length 8).
    """
    length = p.length
    if start + length > len(data):
        return None
    raw = _raw_from_bytes(data, start, length, p.encoding)

    if p.type == "FLOATING":
        if length not in (1, 2, 4):
            return None
        v = _signed(raw, 8 * length)
        return float(F32(v) * F32(p.scale) + F32(p.offset))   # float32 maths, as on the MCU
    if p.type == "DOUBLE":
        if length not in (1, 2, 4, 8):
            return None
        return _signed(raw, 8 * length) * p.scale + p.offset

    bits = 8 * p.size
    if firmware_quirks:
        v = raw * int(p.scale) + int(p.offset)
        return _signed(v, bits) if p.is_signed else v & ((1 << bits) - 1)
    base = _signed(raw, bits) if p.is_signed else raw & ((1 << bits) - 1)
    v = base * p.scale + p.offset
    return int(v) if float(v).is_integer() else v


def decode_frame(net: Network, can_id: int, data: bytes, *, firmware_quirks: bool = False):
    """Decode a standard-ID frame.

    Returns [(Param, value), ...], or None if `can_id` is not a known group.
    Parameters that do not fit inside `data` (short DLC) are omitted.
    """
    group = net.groups.get(can_id)
    if group is None:
        return None
    out = []
    for slot in group.slots:
        p = net.params[slot.param_id]
        v = decode_slot(p, slot.start, data, firmware_quirks=firmware_quirks)
        if v is not None:
            out.append((p, v))
    return out


def encode_group(net: Network, group_id: int, values: dict[int, float]) -> bytes:
    """Build the 8-byte payload for a group from {param_id: physical value}.

    Idealised physical encoding for the simulator and tests; it does not
    reproduce the integer-cast quirk in the firmware's own encoder.
    Parameters missing from `values` are left as zero bytes.
    """
    group = net.groups[group_id]
    buf = bytearray(8)
    for slot in group.slots:
        p = net.params[slot.param_id]
        if p.id not in values:
            continue
        raw = _llround((values[p.id] - p.offset) / (p.scale or 1.0))
        bits = 8 * p.length
        lo, hi = ((-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if p.is_signed
                  else (0, (1 << bits) - 1))
        if not lo <= raw <= hi:
            raise OverflowError(f"{p.name}: raw {raw} does not fit in {p.length} byte(s)")
        buf[slot.start:slot.start + p.length] = (raw & ((1 << bits) - 1)).to_bytes(
            p.length, "big" if p.encoding == "MSB" else "little")
    return bytes(buf)
