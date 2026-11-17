"""CANedge2 logs (one file, several split files, or a session folder) -> .gdat / .csv / .ld

    python -m canedge2.convert LOG/3B912722/00000004 --config go4-26.yaml --out out/ \
        --format gdat csv ld --gopher-vision ../gopher-vision

Inputs may be .MF4 files, .csv frame files, or a folder (all logs inside, in split order).
Everything is decoded as ONE stream, so a drive that the logger split into many files
comes out as one .gdat / .ld with a single time base.
`ld` reuses gopher-vision's own .gdat parser and MoTeC .ld writer.
"""
from __future__ import annotations

import argparse
import itertools
import sys
import time
from pathlib import Path

from .decode import DecodeStats, decode_frames
from .frames import read_frames
from .gdat import write_csv, write_gdat
from .network import Network, lint, load_network
from .sessions import LOG_EXTS


def expand_inputs(inputs: list[Path]) -> list[Path]:
    """Folders become their log files in numeric split order; files pass through."""
    out: list[Path] = []
    for p in inputs:
        if p.is_dir():
            files = [f for f in p.iterdir() if f.is_file() and f.suffix.lower() in LOG_EXTS]
            files.sort(key=lambda f: (int(f.stem.split("-")[0]) if f.stem.split("-")[0].isdigit() else 0, f.name))
            out.extend(files)
        else:
            out.append(p)
    if not out:
        raise FileNotFoundError("no log files found in the given inputs")
    return out


def gdat_to_ld(gdat_path: Path, config_path: Path, gopher_vision: Path, ld_path: Path) -> None:
    sys.path.insert(0, str(gopher_vision))
    from lib import gcan, gdat as gv_gdat, ld          # gopher-vision modules

    params = gcan.get_params(gcan.load_path(config_path))
    sof, _, data = gdat_path.read_bytes().partition(b".gdat:")
    channels = gv_gdat.parse(data, params)
    if ld_path.exists():
        ld_path.unlink()
    ld.write(ld_path, channels, gv_gdat.get_t0(sof))


def convert_session(inputs: list[Path], net: Network, config_path: Path, out_dir: Path, stem: str,
                    formats: list[str], *, buses: set[int] | None = None,
                    gopher_vision: Path | None = None, firmware_quirks: bool = False,
                    quiet: bool = False) -> dict:
    """Decode `inputs` as one stream and write the requested formats into out_dir."""
    log = (lambda *a: None) if quiet else print
    out_dir.mkdir(parents=True, exist_ok=True)
    files = expand_inputs(inputs)
    frames = itertools.chain.from_iterable(read_frames(f) for f in files)
    stats = DecodeStats()
    samples = list(decode_frames(net, frames, buses=buses, stats=stats, firmware_quirks=firmware_quirks))
    log(f"{len(files)} file(s)\n" + stats.summary())
    result = {"files": len(files), "samples": len(samples), "stats": stats, "written": []}
    if not samples:
        log("no samples decoded - wrong --config for this log?")
        return result

    if "gdat" in formats or "ld" in formats:
        gdat_path = out_dir / f"{stem}.gdat"
        info = write_gdat(gdat_path, samples, net, stats.t0)
        result["written"].append(gdat_path)
        log(f"wrote {gdat_path}  ({info['packets']} packets)")
        if info["skipped_params"]:
            log("  not representable in .gdat, use --format csv: " + ", ".join(info["skipped_params"]))
        if "ld" in formats:
            ld_path = out_dir / f"{stem}.ld"
            gdat_to_ld(gdat_path, config_path, gopher_vision, ld_path)
            result["written"].append(ld_path)
            log(f"wrote {ld_path}")
    if "csv" in formats:
        p = out_dir / f"{stem}.decoded.csv"
        n = write_csv(p, samples, net, stats.t0)
        result["written"].append(p)
        log(f"wrote {p}  ({n} rows)")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path, help=".MF4 / .csv files, or a session folder")
    ap.add_argument("--config", required=True, type=Path, help="GopherCAN network YAML (same one the firmware was built from)")
    ap.add_argument("--out", type=Path, default=Path("."), help="output directory")
    ap.add_argument("--name", help="output file stem (default: first input's name / folder name)")
    ap.add_argument("--format", nargs="+", choices=["gdat", "csv", "ld"], default=["gdat"])
    ap.add_argument("--bus", nargs="+", type=int, help="only decode these logger channels (0-based)")
    ap.add_argument("--gopher-vision", type=Path, help="path to the gopher-vision checkout (needed for ld)")
    ap.add_argument("--firmware-quirks", action="store_true",
                    help="reproduce the on-car integer-cast bug instead of decoding the intended values")
    a = ap.parse_args(argv)

    if "ld" in a.format and not a.gopher_vision:
        ap.error("--format ld needs --gopher-vision PATH")
    net = load_network(a.config)
    for w in lint(net):
        print("config warning:", w)
    stem = a.name or (a.inputs[0].name if a.inputs[0].is_dir() else a.inputs[0].stem)
    start = time.time()
    res = convert_session(a.inputs, net, a.config, a.out, stem, a.format,
                          buses=set(a.bus) if a.bus else None, gopher_vision=a.gopher_vision,
                          firmware_quirks=a.firmware_quirks)
    print(f"done in {time.time() - start:.1f}s")
    return 0 if res["samples"] else 1


if __name__ == "__main__":
    sys.exit(main())
