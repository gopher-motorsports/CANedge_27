"""Multi-file sessions, the watcher's idle/re-convert logic, and the UDP live push."""
import os
import socket
import struct
import tempfile
import unittest
from pathlib import Path

from canedge2.convert import convert_session
from canedge2.decode import DecodeStats, decode_frames
from canedge2.frames import read_frames_csv, write_frames_csv
from canedge2.gdat import iter_packets
from canedge2.network import load_network
from canedge2.sessions import scan_local, session_is_idle
from canedge2.sim import generate_frames
from canedge2.watch import latest_values_packets, process_once, send_udp

CONFIG = Path(os.environ.get("GCAN_CONFIG", "/home/claude/src/gophercan-lib-main/network_autogen/configs/go4-26.yaml"))
T0 = 1_760_000_000.0


def write_session(root: Path, net, device="3B912722", session=4, seconds=30, split_s=10,
                  names=("00000001", "00000002-1760000010", "00000010-1760000020")):
    """Simulate a drive the logger split into several files (non-zero-padded-sort trap: 2 vs 10)."""
    frames = list(generate_frames(net, t_start=T0 + 0.2, seconds=seconds, rate_hz=50))
    d = root / device / f"{session:08d}"
    d.mkdir(parents=True)
    paths = []
    for i, name in enumerate(names):
        chunk = [f for f in frames if int((f.t - (T0 + 0.2)) // split_s) == i]
        p = d / f"{name}.csv"
        write_frames_csv(p, chunk)
        paths.append(p)
    return frames, paths


@unittest.skipUnless(CONFIG.exists(), "set GCAN_CONFIG")
class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.net = load_network(CONFIG)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "LOGS"
        self.out = Path(self.tmp.name) / "out"
        self.frames, self.paths = write_session(self.root, self.net)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_groups_session_and_orders_splits_numerically(self):
        found = scan_local(self.root)
        self.assertEqual(len(found), 1)
        (key, files), = found.items()
        self.assertEqual((key.device, key.session), ("3B912722", 4))
        self.assertEqual([f.split for f in files], [1, 2, 10])

    def test_split_files_decode_as_one_stream(self):
        res = convert_session([self.paths[0].parent], self.net, CONFIG, self.out, "drive", ["gdat"], quiet=True)
        ref = DecodeStats()
        n_ref = sum(1 for _ in decode_frames(self.net, self.frames, stats=ref))
        self.assertEqual(res["files"], 3)
        self.assertEqual(res["samples"], n_ref)                  # nothing lost at file boundaries
        self.assertEqual(res["stats"].t0, ref.t0)                # one shared time base
        t_ms = [t for t, _, _ in iter_packets((self.out / "drive.gdat").read_bytes().partition(b".gdat:")[2])]
        self.assertGreater(max(t_ms), 29_000)                    # 30 s drive, not three 10 s ones restarting at 0

    def test_watcher_waits_for_idle_then_converts_once_then_reconverts_on_new_split(self):
        mtime = max(f.mtime for fs in scan_local(self.root).values() for f in fs)
        kw = dict(formats=["gdat"], quiet=True)
        # still uploading: not converted
        self.assertEqual(process_once(self.root, self.out, self.net, CONFIG, now=mtime + 5, idle_s=120, **kw), [])
        # idle long enough: converted
        done = process_once(self.root, self.out, self.net, CONFIG, now=mtime + 200, idle_s=120, **kw)
        self.assertEqual(len(done), 1)
        self.assertTrue((self.out / "3B912722" / "3B912722_00000004.gdat").exists())
        # nothing new: no-op
        self.assertEqual(process_once(self.root, self.out, self.net, CONFIG, now=mtime + 300, idle_s=120, **kw), [])
        # a late split arrives: converted again
        write_frames_csv(self.paths[0].parent / "00000011-1760000030.csv", self.frames[:10])
        again = process_once(self.root, self.out, self.net, CONFIG, now=mtime + 600, idle_s=120, **kw)
        self.assertEqual(len(again), 1)

    def test_session_is_idle(self):
        files = scan_local(self.root)[next(iter(scan_local(self.root)))]
        m = max(f.mtime for f in files)
        self.assertFalse(session_is_idle(files, m + 10, 120))
        self.assertTrue(session_is_idle(files, m + 121, 120))

    def test_udp_live_push_sends_latest_value_per_param_in_small_datagrams(self):
        pkts = latest_values_packets(self.net, self.paths[-1])
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 0))
        rx.settimeout(2)
        port = rx.getsockname()[1]
        n = send_udp(pkts, "127.0.0.1", port)
        got = []
        for _ in range(n):
            data, _ = rx.recvfrom(4096)
            self.assertLessEqual(len(data), 990)                  # fits gopher-vision's 1000-byte read
            got.extend(iter_packets(data))
        rx.close()
        self.assertEqual(len(got), len(pkts))                     # no packet split across datagrams
        expected_ids = {s.param_id for s in decode_frames(self.net, read_frames_csv(self.paths[-1]))}
        self.assertEqual({pid for _, pid, _ in got}, {p for p in expected_ids
                         if self.net.params[p].type != "DOUBLE" and not self.net.params[p].fractional_scale_on_int})


if __name__ == "__main__":
    unittest.main()
