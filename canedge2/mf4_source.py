"""CANedge2 MF4 log files -> RawFrame.

!!! UNVERIFIED AGAINST A REAL FILE !!!
Written from the ASAM MDF4 bus-logging layout (CAN_DataFrame.* channels) that
CANedge logs follow.  Nothing here could be run before the device arrived, so
treat it as the first thing to check:

    python -m canedge2.mf4_source  path/to/00000001.MF4     # prints what's in the file

Needs `pip install asammdf`.  Notes for when real files exist:
  * CANedge can write compressed (.MFC) or encrypted (.MFE/.MFM) logs; convert
    those to plain .MF4 with CSS Electronics' MF4 converter tools first.
  * A log cut short by power loss may need "finalizing" before it opens
    (CSS ships an `mdf2finalized` tool).
  * CSS now recommends python-can's MF4 reader (or their MF4 decoders) over
    their legacy mdf_iter package; swapping this module for python-can is a
    drop-in change because everything downstream only sees RawFrame.
  * BusChannel numbering (0- or 1-based) is assumed 1-based -> BUS_BASE below.
"""
from __future__ import annotations

import sys
from typing import Iterator

import numpy as np

from .frames import RawFrame

BUS_BASE = 1   # CANedge channel labels are assumed to be 1 and 2


def _need_asammdf():
    try:
        from asammdf import MDF
        return MDF
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("reading MF4 needs asammdf:  pip install asammdf") from e


def describe(path: str) -> None:
    MDF = _need_asammdf()
    with MDF(path) as mdf:
        print(f"file: {path}\nstart: {mdf.header.start_time}\ngroups: {len(mdf.groups)}")
        can = sorted(n for n in mdf.channels_db if n.startswith("CAN"))
        print("CAN-related channels:", can[:40] or "NONE FOUND")


def read_mf4_frames(path: str) -> Iterator[RawFrame]:
    MDF = _need_asammdf()
    with MDF(path) as mdf:
        t_file = mdf.header.start_time.timestamp()        # absolute start of the log
        places = mdf.whereis("CAN_DataFrame.ID")
        if not places:
            have = sorted(n for n in mdf.channels_db if n.startswith("CAN"))
            raise ValueError(f"{path}: no CAN_DataFrame.ID channel. CAN channels present: {have[:20]}")

        for group, _index in places:
            def sig(name):
                return mdf.get(name, group=group)

            ids = sig("CAN_DataFrame.ID")
            ts = np.asarray(ids.timestamps, dtype=float)
            can_ids = np.asarray(ids.samples).astype(np.uint32)
            ide = np.asarray(sig("CAN_DataFrame.IDE").samples).astype(bool)
            bus = np.asarray(sig("CAN_DataFrame.BusChannel").samples).astype(int) - BUS_BASE
            data = np.asarray(sig("CAN_DataFrame.DataBytes").samples)
            dlen = np.asarray(sig("CAN_DataFrame.DataLength").samples).astype(int)

            for i in range(len(ts)):
                yield RawFrame(
                    t=t_file + float(ts[i]),
                    bus=int(bus[i]),
                    can_id=int(can_ids[i]) & (0x1FFFFFFF if ide[i] else 0x7FF),
                    ext=bool(ide[i]),
                    data=bytes(data[i][: dlen[i]].astype(np.uint8)),
                )


if __name__ == "__main__":
    describe(sys.argv[1])
