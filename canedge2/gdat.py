""".gdat / CSV writers.

.gdat is the format DataLogger_25 writes to its SD card and gopher-vision reads
(see gopher-vision/lib/gdat.py).  Big endian packets:

    7E | TIMESTAMP(4, ms) | PARAM_ID(2) | VALUE(n) | CHECKSUM(1)

CHECKSUM = sum of the unescaped bytes incl. the 7E start byte, mod 256.
Every byte after the start byte is escaped:  7E / 7D  ->  7D, (byte ^ 20).
File starts with "/YYYY-MM-DD-hh-mm-ss.gdat:" (gopher-vision parses t0 from it).

Not representable in .gdat (skipped, and reported):
  * DOUBLE params (gopher-vision has no DOUBLE type)
  * integer-typed params with fractional scale/offset (value would be truncated)
Use the CSV writer for full fidelity.
"""
from __future__ import annotations

import csv
import struct
import time
from pathlib import Path
from typing import Iterable

from .decode import Sample
from .network import Network, Param

START, ESC, ESC_XOR = 0x7E, 0x7D, 0x20

_FMT = {
    "UNSIGNED8": ">B", "UNSIGNED16": ">H", "UNSIGNED32": ">I", "UNSIGNED64": ">Q",
    "SIGNED8": ">b", "SIGNED16": ">h", "SIGNED32": ">i", "SIGNED64": ">q",
    "FLOATING": ">f",
}


def gdat_representable(p: Param) -> bool:
    return p.type in _FMT and not p.fractional_scale_on_int


def skipped_params(net: Network) -> list[Param]:
    return [p for p in net.params.values() if not gdat_representable(p)]


def _escape(b: int, out: bytearray) -> None:
    if b in (START, ESC):
        out.append(ESC)
        out.append(b ^ ESC_XOR)
    else:
        out.append(b)


def encode_packet(t_ms: int, param_id: int, value_bytes: bytes) -> bytes:
    body = struct.pack(">IH", t_ms & 0xFFFFFFFF, param_id) + value_bytes
    checksum = (START + sum(body)) & 0xFF
    out = bytearray([START])
    for b in body:
        _escape(b, out)
    _escape(checksum, out)
    return bytes(out)


def write_gdat(path: str | Path, samples: Iterable[Sample], net: Network, t0_epoch: float) -> dict:
    """Write samples as a .gdat file.  Returns {'packets': n, 'skipped_params': [...]}"""
    stamp = time.strftime("%Y-%m-%d-%H-%M-%S", time.gmtime(t0_epoch))
    n = 0
    skipped: set[str] = set()
    with open(path, "wb") as f:
        f.write(f"/{stamp}.gdat:".encode())
        for s in samples:
            p = net.params[s.param_id]
            if not gdat_representable(p):
                skipped.add(p.name)
                continue
            v = s.value if p.type == "FLOATING" else int(round(s.value))
            f.write(encode_packet(s.t_ms, p.id, struct.pack(_FMT[p.type], v)))
            n += 1
    return {"packets": n, "skipped_params": sorted(skipped)}


def write_csv(path: str | Path, samples: Iterable[Sample], net: Network, t0_epoch: float) -> int:
    """Long-format CSV: t_s (epoch), t_ms (since t0), bus, param, unit, value."""
    n = 0
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_epoch_s", "t_ms", "bus", "param", "unit", "value"])
        for s in samples:
            p = net.params[s.param_id]
            w.writerow([f"{t0_epoch + s.t_ms / 1000:.3f}", s.t_ms, s.bus, p.name, p.unit, repr(s.value)])
            n += 1
    return n


def iter_packets(block: bytes):
    """Yield (t_ms, param_id, value_bytes) for every valid packet in `block`.

    Same parsing rules as gopher-vision (split on 7E, unescape, verify checksum);
    invalid or truncated packets are skipped.
    """
    for chunk in block.split(bytes([START])):
        pkt = bytearray()
        esc = False
        for b in chunk:
            if b == ESC:
                esc = True
            elif esc:
                pkt.append(b ^ ESC_XOR)
                esc = False
            else:
                pkt.append(b)
        if len(pkt) < 7:
            continue
        if (START + sum(pkt[:-1])) & 0xFF != pkt[-1]:
            continue
        t_ms, pid = struct.unpack(">IH", pkt[:6])
        yield t_ms, pid, bytes(pkt[6:-1])
