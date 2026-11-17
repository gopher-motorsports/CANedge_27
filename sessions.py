"""Find and group CANedge log files into sessions.

CANedge file layout (same on the SD card and, minus the LOG/ prefix, on S3):
    [LOG/]DEVICE_ID/SESSION/SPLIT.MF4          SD card
    DEVICE_ID/SESSION/SPLIT-EPOCH.MF4          S3 object names (epoch suffix added on upload)
A SESSION is one power cycle of the logger, so for this team one session ~ one drive.
Splits within a session are numbered in order and must be decoded as ONE stream so
they share a single t0.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

LOG_EXTS = (".mf4", ".csv")     # .csv = simulated frames (tests / bench); .mfc/.mfe/.mfm need converting first
_NAME = re.compile(r"^(\d+)(?:-\d+)?$")


@dataclass(frozen=True)
class SessionKey:
    device: str
    session: int

    def __str__(self):
        return f"{self.device}/{self.session:08d}"


@dataclass(frozen=True)
class SessionFile:
    key: SessionKey
    split: int
    path: Path
    mtime: float


def scan_local(root: str | Path, exts: tuple[str, ...] = LOG_EXTS) -> dict[SessionKey, list[SessionFile]]:
    """Scan `root` for DEVICE/SESSION/SPLIT[-EPOCH].ext files (any depth above that)."""
    root = Path(root)
    found: dict[SessionKey, list[SessionFile]] = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in exts:
            continue
        m = _NAME.match(path.stem)
        session_dir, device_dir = path.parent.name, path.parent.parent.name
        if not m or not session_dir.isdigit() or not device_dir:
            continue
        key = SessionKey(device_dir, int(session_dir))
        found.setdefault(key, []).append(SessionFile(key, int(m.group(1)), path, path.stat().st_mtime))
    for files in found.values():
        files.sort(key=lambda f: f.split)
    return found


def session_is_idle(files: list[SessionFile], now: float, idle_s: float) -> bool:
    """Heuristic 'drive is over': no file in the session has changed for idle_s seconds.
    (Verify on real hardware how/when the last split of a power cycle is uploaded.)"""
    return now - max(f.mtime for f in files) >= idle_s


def sync_s3(bucket: str, endpoint: str, key: str, secret: str, mirror: str | Path) -> list[Path]:
    """Download new CANedge objects from an S3/MinIO bucket into `mirror`, keeping the layout.

    UNTESTED (needs `pip install s3fs` and a running server).  Returns the files fetched.
    The watcher then scans `mirror` exactly as it would an SD card copy.
    """
    import s3fs
    fs = s3fs.S3FileSystem(key=key, secret=secret, client_kwargs={"endpoint_url": endpoint})
    pattern = re.compile(r"^[^/]+/(\d+)/(\d+)(?:-\d+)?\.mf4$", re.I)
    mirror = Path(mirror)
    fetched = []
    for obj in fs.find(bucket):
        rel = obj[len(bucket) + 1:] if obj.startswith(bucket + "/") else obj
        if not pattern.match(rel):
            continue
        dest = mirror / rel
        if dest.exists() and dest.stat().st_size == fs.info(obj)["size"]:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        fs.get(obj, str(dest))
        fetched.append(dest)
    return fetched
