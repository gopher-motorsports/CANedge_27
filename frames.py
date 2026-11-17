"""Raw CAN frame container + a plain-CSV frame format.

The CSV format exists so everything downstream of the logger can be developed
and tested with no hardware and no MF4 library:  t,bus,id,ext,data
  t     epoch seconds (UTC), microsecond precision
  bus   0-based channel index as seen by the logger (CANedge2: 0 or 1)
  id    hex CAN ID (e.g. 0x40A)
  ext   0 = 11-bit (GopherCAN data), 1 = 29-bit (commands/requests/errors)
  data  hex payload, no spaces
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


@dataclass(frozen=True)
class RawFrame:
    t: float          # epoch seconds, UTC
    bus: int          # 0-based logger channel
    can_id: int
    ext: bool
    data: bytes


def write_frames_csv(path: str | Path, frames: Iterable[RawFrame]) -> int:
    n = 0
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "bus", "id", "ext", "data"])
        for fr in frames:
            w.writerow([f"{fr.t:.6f}", fr.bus, f"0x{fr.can_id:X}", int(fr.ext), fr.data.hex()])
            n += 1
    return n


def read_frames_csv(path: str | Path) -> Iterator[RawFrame]:
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            yield RawFrame(float(row["t"]), int(row["bus"]), int(row["id"], 0),
                           bool(int(row["ext"])), bytes.fromhex(row["data"]))


def read_frames(path: str | Path) -> Iterator[RawFrame]:
    """Pick a frame source from the file extension."""
    suffix = Path(path).suffix.lower()
    if suffix == ".csv":
        return read_frames_csv(path)
    if suffix in (".mf4", ".mfc", ".mfe", ".mfm"):
        from .mf4_source import read_mf4_frames   # lazy: needs asammdf
        return read_mf4_frames(path)
    raise ValueError(f"unsupported input type: {suffix}")
