"""Load a GopherCAN network YAML (gophercan-lib/network_autogen/configs/*.yaml).

This deliberately mirrors network_autogen/autogen.py so that the Python side
sees the same network the firmware was built from:

  * A parameter's `length` (bytes on the wire) and `group_id` come from the
    LAST group that lists it (autogen.py overwrites them on every occurrence).
  * Group payloads are addressed by byte `start` (0..7); a parameter occupies
    bytes [start, start + length).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

TYPE_BYTES = {
    "UNSIGNED8": 1, "UNSIGNED16": 2, "UNSIGNED32": 4, "UNSIGNED64": 8,
    "SIGNED8": 1, "SIGNED16": 2, "SIGNED32": 4, "SIGNED64": 8,
    "FLOATING": 4, "DOUBLE": 8,
}


@dataclass(frozen=True)
class Param:
    id: int
    name: str
    motec_name: str
    unit: str
    type: str
    encoding: str      # "MSB" (big endian) or "LSB" (little endian)
    scale: float
    offset: float
    length: int        # bytes occupied in the CAN payload
    group_id: int      # 11-bit CAN ID of the (last) group carrying it

    @property
    def size(self) -> int:
        """Size of the in-memory type (not necessarily the wire length)."""
        return TYPE_BYTES[self.type]

    @property
    def is_float(self) -> bool:
        return self.type in ("FLOATING", "DOUBLE")

    @property
    def is_signed(self) -> bool:
        return self.type.startswith("SIGNED") or self.is_float

    @property
    def fractional_scale_on_int(self) -> bool:
        """Integer-typed param with non-integer scale/offset.

        Firmware casts scale/offset to the integer type before use, so such a
        parameter decodes wrongly on-car (e.g. 0.01 -> 0).  See codec.py.
        """
        return (not self.is_float) and (
            float(self.scale) != int(self.scale) or float(self.offset) != int(self.offset)
        )


@dataclass(frozen=True)
class Slot:
    start: int
    param_id: int


@dataclass(frozen=True)
class Group:
    id: int                 # 11-bit CAN ID
    slots: tuple[Slot, ...]


@dataclass
class Network:
    name: str
    buses: list[str]
    modules: dict[str, dict]
    params: dict[int, Param]
    groups: dict[int, Group]
    commands: dict[int, str]
    errors: dict[int, str]

    def param_by_name(self, name: str) -> Param:
        for p in self.params.values():
            if p.name == name:
                return p
        raise KeyError(name)


def load_network(path: str | Path) -> Network:
    path = Path(path)
    with open(path) as f:
        cfg = yaml.safe_load(f)

    for key in ("buses", "parameters", "groups"):
        if key not in cfg:
            raise ValueError(f"{path}: missing top-level key '{key}'")

    raw_params: dict[str, dict] = {}
    seen_ids: dict[int, str] = {}
    for name, p in cfg["parameters"].items():
        if p["id"] in seen_ids:
            raise ValueError(f"duplicate parameter id {p['id']}: {seen_ids[p['id']]} / {name}")
        if p["type"] not in TYPE_BYTES:
            raise ValueError(f"{name}: unknown type {p['type']}")
        seen_ids[p["id"]] = name
        raw_params[name] = p

    last_len: dict[str, int] = {}
    last_group: dict[str, int] = {}
    groups: dict[int, Group] = {}
    for g in cfg["groups"]:
        slots = []
        for gp in g["parameters"]:
            name = gp["name"]
            if name not in raw_params:
                raise ValueError(f"group {g['id']:#x}: unknown parameter '{name}'")
            if gp["start"] + gp["length"] > 8:
                raise ValueError(f"group {g['id']:#x}: '{name}' overruns the 8-byte payload")
            last_len[name] = gp["length"]
            last_group[name] = g["id"]
            slots.append(Slot(gp["start"], raw_params[name]["id"]))
        if g["id"] in groups:
            raise ValueError(f"duplicate group id {g['id']:#x}")
        groups[g["id"]] = Group(g["id"], tuple(slots))

    params: dict[int, Param] = {}
    for name, p in raw_params.items():
        if name not in last_len:
            continue  # defined but never carried by a CAN group
        params[p["id"]] = Param(
            id=p["id"], name=name, motec_name=p.get("motec_name", name),
            unit=p.get("unit", ""), type=p["type"], encoding=p.get("encoding", "MSB"),
            scale=float(p.get("scale", 1)), offset=float(p.get("offset", 0)),
            length=last_len[name], group_id=last_group[name],
        )

    return Network(
        name=path.stem, buses=list(cfg["buses"]), modules=cfg.get("modules", {}),
        params=params, groups=groups,
        commands=cfg.get("commands", {}) or {}, errors=cfg.get("errors", {}) or {},
    )


def lint(net: Network) -> list[str]:
    """Problems in the network config that silently cost data downstream."""
    from collections import defaultdict
    msgs = []
    by_name = defaultdict(list)
    for p in net.params.values():
        by_name[p.motec_name].append(p.name)
    for motec, names in sorted(by_name.items()):
        if len(names) > 1:
            msgs.append(f"duplicate motec_name '{motec}' used by {', '.join(names)} "
                        "-> gopher-vision's .ld reader keeps only one channel")
    for p in net.params.values():
        if p.fractional_scale_on_int:
            msgs.append(f"{p.name}: {p.type} with scale {p.scale} -> the on-car firmware decodes this as 0 "
                        "(canedge2 decodes the intended value; not representable in .gdat)")
    return msgs
