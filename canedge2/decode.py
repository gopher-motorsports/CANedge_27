"""Raw frames -> timestamped parameter samples."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from .codec import decode_frame
from .frames import RawFrame
from .network import Network


@dataclass(frozen=True)
class Sample:
    t_ms: int        # milliseconds since DecodeStats.t0 (same convention as .gdat)
    param_id: int
    value: float
    bus: int


@dataclass
class DecodeStats:
    t0: float | None = None             # epoch seconds (whole second) that t_ms is relative to
    frames: int = 0
    std_frames: int = 0
    ext_frames: int = 0                 # 29-bit: commands / requests / errors (not decoded here)
    decoded_frames: int = 0
    samples: int = 0
    short_payload: int = 0              # known group but payload shorter than the group layout
    unknown_ids: Counter = field(default_factory=Counter)
    frames_per_bus: Counter = field(default_factory=Counter)

    def summary(self) -> str:
        lines = [
            f"frames: {self.frames}  (11-bit: {self.std_frames}, 29-bit: {self.ext_frames})",
            f"decoded frames: {self.decoded_frames}  ->  {self.samples} parameter samples",
            f"frames per bus: {dict(sorted(self.frames_per_bus.items()))}",
        ]
        if self.short_payload:
            lines.append(f"short payloads (params skipped): {self.short_payload}")
        if self.unknown_ids:
            top = ", ".join(f"{i:#x}x{c}" for i, c in self.unknown_ids.most_common(8))
            lines.append(f"IDs not in the network config: {len(self.unknown_ids)}  (top: {top})")
        return "\n".join(lines)


def decode_frames(net: Network, frames: Iterable[RawFrame], *, buses: set[int] | None = None,
                  stats: DecodeStats | None = None, firmware_quirks: bool = False) -> Iterator[Sample]:
    """Decode frames lazily.  t0 is the first frame's time floored to a second."""
    stats = stats if stats is not None else DecodeStats()
    for fr in frames:
        if buses is not None and fr.bus not in buses:
            continue
        if stats.t0 is None:
            stats.t0 = float(math.floor(fr.t))
        stats.frames += 1
        stats.frames_per_bus[fr.bus] += 1
        if fr.ext:
            stats.ext_frames += 1
            continue
        stats.std_frames += 1
        decoded = decode_frame(net, fr.can_id, fr.data, firmware_quirks=firmware_quirks)
        if decoded is None:
            stats.unknown_ids[fr.can_id] += 1
            continue
        group = net.groups[fr.can_id]
        if len(decoded) < len(group.slots):
            stats.short_payload += 1
        if not decoded:
            continue
        stats.decoded_frames += 1
        t_ms = max(0, int(round((fr.t - stats.t0) * 1000)))
        for p, v in decoded:
            stats.samples += 1
            yield Sample(t_ms, p.id, v, fr.bus)
