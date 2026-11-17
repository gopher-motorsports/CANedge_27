"""Watch a folder of uploaded CANedge logs; convert each finished drive; optionally feed live view.

Typical pit-lane setup: MinIO (S3) runs on the laptop, the CANedge2 uploads to it over WiFi,
and this watcher turns each session into .gdat/.ld and (optionally) pushes the latest values
to gopher-vision's live view over UDP.

    python -m canedge2.watch LOGS/ --config go4-26.yaml --out data/ --format gdat ld \
        --gopher-vision ../gopher-vision --idle 120 --udp 127.0.0.1:5000

LOGS/ must look like the SD card: DEVICE/SESSION/SPLIT.MF4 (S3 mirror: SPLIT-EPOCH.MF4).
Use --s3-* options to mirror a MinIO bucket into LOGS/ first (untested, see sessions.sync_s3).

A session is converted once no file in it has changed for --idle seconds, and converted
again if more files for it arrive later.  Progress is kept in OUT/.canedge2_state.json.
Live push (--udp) sends, for each newly arrived file, the LAST value of every parameter
(gopher-vision's live view only shows latest values), in datagrams under 1000 bytes.
"""
from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path

from .convert import convert_session
from .decode import decode_frames
from .frames import read_frames
from .gdat import _FMT, encode_packet, gdat_representable
import struct

from .network import Network, load_network
from .sessions import SessionFile, SessionKey, scan_local, session_is_idle, sync_s3

DATAGRAM_MAX = 990      # gopher-vision reads UDP in 1000-byte blocks; stay under it


def latest_values_packets(net: Network, path: Path) -> list[bytes]:
    """One .gdat packet per parameter: its last value in this file."""
    last = {}
    for s in decode_frames(net, read_frames(path)):
        last[s.param_id] = s
    pkts = []
    for pid, s in last.items():
        p = net.params[pid]
        if not gdat_representable(p):
            continue
        v = s.value if p.type == "FLOATING" else int(round(s.value))
        pkts.append(encode_packet(s.t_ms, pid, struct.pack(_FMT[p.type], v)))
    return pkts


def send_udp(packets: list[bytes], host: str, port: int) -> int:
    """Send packets in datagrams that never split a packet. Returns datagram count."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    n, buf = 0, b""
    try:
        for pkt in packets:
            if buf and len(buf) + len(pkt) > DATAGRAM_MAX:
                sock.sendto(buf, (host, port)); n += 1; buf = b""
            buf += pkt
        if buf:
            sock.sendto(buf, (host, port)); n += 1
    finally:
        sock.close()
    return n


def _load_state(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def process_once(root: Path, out_dir: Path, net: Network, config_path: Path, formats: list[str], *,
                 idle_s: float = 120, now: float | None = None, gopher_vision: Path | None = None,
                 udp: tuple[str, int] | None = None, state_path: Path | None = None,
                 seen_live: set[Path] | None = None, quiet: bool = False) -> list[SessionKey]:
    """One polling pass.  Returns the sessions converted in this pass."""
    now = time.time() if now is None else now
    state_path = state_path or out_dir / ".canedge2_state.json"
    state = _load_state(state_path)
    seen_live = seen_live if seen_live is not None else set()
    converted: list[SessionKey] = []

    for key, files in sorted(scan_local(root).items(), key=lambda kv: str(kv[0])):
        if udp:
            for f in files:
                if f.path not in seen_live and now - f.mtime < idle_s * 4:   # only fresh files go live
                    seen_live.add(f.path)
                    try:
                        send_udp(latest_values_packets(net, f.path), *udp)
                    except Exception as e:                                    # a bad file must not stop the loop
                        if not quiet:
                            print(f"live push failed for {f.path}: {e}")
        done = state.get(str(key))
        if done and done["files"] == len(files):
            continue
        if not session_is_idle(files, now, idle_s):
            continue
        stem = f"{key.device}_{key.session:08d}"
        res = convert_session([f.path for f in files], net, config_path, out_dir / key.device, stem,
                              formats, gopher_vision=gopher_vision, quiet=quiet)
        if res["samples"]:
            state[str(key)] = {"files": len(files), "converted_at": now}
            converted.append(key)
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))
    return converted


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("root", type=Path, help="folder laid out like the SD card (DEVICE/SESSION/SPLIT.MF4)")
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--format", nargs="+", choices=["gdat", "csv", "ld"], default=["gdat", "csv"])
    ap.add_argument("--gopher-vision", type=Path)
    ap.add_argument("--idle", type=float, default=120, help="seconds without new files before a session counts as finished")
    ap.add_argument("--poll", type=float, default=10, help="seconds between scans")
    ap.add_argument("--udp", help="host:port of gopher-vision's live view (socket mode)")
    ap.add_argument("--once", action="store_true", help="scan once and exit")
    ap.add_argument("--s3-bucket"); ap.add_argument("--s3-endpoint"); ap.add_argument("--s3-key"); ap.add_argument("--s3-secret")
    a = ap.parse_args(argv)
    if "ld" in a.format and not a.gopher_vision:
        ap.error("--format ld needs --gopher-vision PATH")

    net = load_network(a.config)
    udp = None
    if a.udp:
        host, port = a.udp.rsplit(":", 1)
        udp = (host, int(port))
    seen_live: set[Path] = set()
    print(f"watching {a.root}  (idle {a.idle:.0f}s, poll {a.poll:.0f}s)")
    while True:
        if a.s3_bucket:
            for p in sync_s3(a.s3_bucket, a.s3_endpoint, a.s3_key, a.s3_secret, a.root):
                print("fetched", p)
        for key in process_once(a.root, a.out, net, a.config, a.format, idle_s=a.idle,
                                gopher_vision=a.gopher_vision, udp=udp, seen_live=seen_live):
            print(f"converted session {key}")
        if a.once:
            return 0
        time.sleep(a.poll)


if __name__ == "__main__":
    raise SystemExit(main())
