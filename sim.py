"""Synthetic GopherCAN traffic, written as the CSV frame format.

Lets you exercise the whole pipeline before the CANedge2 exists:
    python -m canedge2.sim --config go4-26.yaml --out sim.csv --seconds 20
"""
from __future__ import annotations

import argparse
import math
import time

from .codec import encode_group
from .frames import RawFrame, write_frames_csv
from .network import Network, load_network


def _wave(p, t: float, phase: float) -> float:
    """A sine that stays comfortably inside the param's raw range."""
    bits = 8 * p.length
    raw_max = min((1 << (bits - 1)) - 1, 20000)   # in range for signed+unsigned, and plausible-looking
    raw = raw_max * (0.5 + 0.4 * math.sin(2 * math.pi * 0.5 * t + phase))
    return raw * p.scale + p.offset


def generate_frames(net: Network, *, t_start: float, seconds: float, rate_hz: int = 100,
                    group_ids: list[int] | None = None, n_buses: int = 2):
    """Yield frames for `group_ids` (default: first 8 groups) at `rate_hz`, alternating buses."""
    gids = group_ids or list(net.groups)[:8]
    # skip groups holding params the idealised encoder can't represent (none expected)
    steps = int(seconds * rate_hz)
    for k in range(steps):
        t = k / rate_hz
        for gi, gid in enumerate(gids):
            vals = {s.param_id: _wave(net.params[s.param_id], t, 0.7 * s.param_id)
                    for s in net.groups[gid].slots}
            yield RawFrame(t=t_start + t + gi * 0.0003, bus=gi % n_buses, can_id=gid,
                           ext=False, data=encode_group(net, gid, vals))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True, help="GopherCAN network YAML")
    ap.add_argument("--out", required=True, help="output .csv")
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--rate", type=int, default=100)
    ap.add_argument("--groups", type=lambda s: [int(x, 0) for x in s.split(",")], default=None)
    a = ap.parse_args(argv)
    net = load_network(a.config)
    n = write_frames_csv(a.out, generate_frames(net, t_start=time.time(), seconds=a.seconds,
                                                rate_hz=a.rate, group_ids=a.groups))
    print(f"wrote {n} frames to {a.out}")


if __name__ == "__main__":
    main()
