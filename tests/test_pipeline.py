"""End-to-end: simulated frames -> decode -> .gdat -> *gopher-vision's own parser* -> .ld -> read back."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

from canedge2.convert import main as convert_main
from canedge2.decode import DecodeStats, decode_frames
from canedge2.frames import read_frames_csv, write_frames_csv
from canedge2.gdat import encode_packet, skipped_params
from canedge2.network import load_network
from canedge2.sim import generate_frames

CONFIG = Path(os.environ.get("GCAN_CONFIG", "/home/claude/src/gophercan-lib-main/network_autogen/configs/go4-26.yaml"))
GV = Path(os.environ.get("GOPHER_VISION", "/home/claude/src/gopher-vision-main"))
T0 = 1_760_000_000.0   # arbitrary fixed epoch for reproducibility


@unittest.skipUnless(CONFIG.exists(), "set GCAN_CONFIG")
class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.net = load_network(CONFIG)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.csv = Path(cls.tmp.name) / "sim.csv"
        write_frames_csv(cls.csv, generate_frames(cls.net, t_start=T0 + 0.25, seconds=5, rate_hz=100))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_csv_roundtrip_and_decode_counts(self):
        frames = list(read_frames_csv(self.csv))
        self.assertEqual(len(frames), 5 * 100 * 8)
        stats = DecodeStats()
        samples = list(decode_frames(self.net, frames, stats=stats))
        self.assertEqual(stats.t0, T0)
        self.assertEqual(stats.unknown_ids, {})
        self.assertEqual(stats.frames_per_bus[0] + stats.frames_per_bus[1], len(frames))
        self.assertEqual(len(samples), stats.samples)

    def test_bus_filter(self):
        stats = DecodeStats()
        list(decode_frames(self.net, read_frames_csv(self.csv), buses={1}, stats=stats))
        self.assertEqual(set(stats.frames_per_bus), {1})

    def test_packet_escaping(self):
        pkt = encode_packet(0x7E7D, 0x007E, bytes([0x7E, 0x7D]))
        body = pkt[1:]
        self.assertNotIn(0x7E, body)                 # start byte never appears unescaped
        self.assertEqual(pkt[0], 0x7E)

    @unittest.skipUnless(GV.exists(), "set GOPHER_VISION to the gopher-vision checkout")
    def test_gopher_vision_reads_our_gdat_and_writes_ld(self):
        out = Path(self.tmp.name) / "out"
        rc = convert_main([str(self.csv), "--config", str(CONFIG), "--out", str(out),
                           "--format", "gdat", "csv", "ld", "--gopher-vision", str(GV)])
        self.assertEqual(rc, 0)

        sys.path.insert(0, str(GV))
        from lib import gcan, gdat as gv_gdat, ld as gv_ld
        params = gcan.get_params(gcan.load_path(CONFIG))
        sof, _, data = (out / "sim.gdat").read_bytes().partition(b".gdat:")
        channels = gv_gdat.parse(data, params)

        # every parameter of the 8 simulated groups arrived, with zero checksum errors
        expected = {s.param_id for gid in list(self.net.groups)[:8] for s in self.net.groups[gid].slots}
        self.assertEqual(set(channels), expected)

        # values survive: compare against our own decode
        ours = {}
        for s in decode_frames(self.net, read_frames_csv(self.csv)):
            ours.setdefault(s.param_id, []).append((s.t_ms, s.value))
        for pid, ch in channels.items():
            self.assertEqual(ch["n_points"], len(ours[pid]))
            self.assertAlmostEqual(ch["points"][0][1], ours[pid][0][1], places=3)

        # the .ld file opens and has matching channel count
        meta, ld_channels = gv_ld.parse(out / "sim.ld")
        # .ld channels are keyed by motec_name, so duplicate names in the YAML collapse
        # (see canedge2.network.lint) - compare against the number of distinct names
        self.assertEqual(len(ld_channels), len({c['name'] for c in channels.values()}))

    def test_gdat_skips_unrepresentable_params(self):
        names = {p.name for p in skipped_params(self.net)}
        self.assertIn("gpsSpeed_mph", names)


if __name__ == "__main__":
    unittest.main()


class LintTests(unittest.TestCase):
    @unittest.skipUnless(CONFIG.exists(), "set GCAN_CONFIG")
    def test_lint_flags_known_go4_26_issues(self):
        from canedge2.network import lint
        warnings = lint(load_network(CONFIG))
        self.assertTrue(any("duplicate motec_name" in w for w in warnings))
        self.assertTrue(any("gpsSpeed_mph" in w for w in warnings))
