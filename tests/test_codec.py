import os
import unittest
from pathlib import Path

from canedge2.codec import decode_frame, encode_group
from canedge2.network import load_network

CONFIG = Path(os.environ.get("GCAN_CONFIG", "/home/claude/src/gophercan-lib-main/network_autogen/configs/go4-26.yaml"))


@unittest.skipUnless(CONFIG.exists(), "set GCAN_CONFIG to a GopherCAN network YAML")
class CodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.net = load_network(CONFIG)

    def test_hand_built_inverter_frame(self):
        # group 1034 (0x40A): erpm S32 MSB @0, duty FLOATING scale .1 @4 (2B), voltage S16 @6
        # -1500 erpm, raw 253 -> 25.3 %, 400 V  (bytes worked out by hand from the YAML)
        data = bytes.fromhex("FFFFFA24" "00FD" "0190")
        got = {p.name: v for p, v in decode_frame(self.net, 1034, data)}
        self.assertEqual(got["electricalRPM_FL_erpm"], -1500)
        self.assertAlmostEqual(got["dutyCycle_FL_percent"], 25.3, places=4)
        self.assertEqual(got["inputInverterVoltage_FL_V"], 400)

    def test_unknown_id_returns_none(self):
        self.assertIsNone(decode_frame(self.net, 0x7FF, bytes(8)))

    def test_short_payload_drops_only_missing_params(self):
        got = decode_frame(self.net, 1034, bytes.fromhex("FFFFFA24"))   # only first param fits
        self.assertEqual([p.name for p, _ in got], ["electricalRPM_FL_erpm"])

    def test_roundtrip_every_group(self):
        """encode -> decode returns the value to within one quantisation step."""
        checked = 0
        for gid, g in self.net.groups.items():
            vals = {}
            for s in g.slots:
                p = self.net.params[s.param_id]
                bits = 8 * p.length
                raw = ((1 << (bits - 1)) - 1) // 3          # in range for signed + unsigned
                vals[p.id] = raw * p.scale + p.offset
            payload = encode_group(self.net, gid, vals)
            for p, v in decode_frame(self.net, gid, payload):
                if p.type == "DOUBLE":
                    continue
                self.assertAlmostEqual(v, vals[p.id], delta=abs(p.scale) * 1.001 + abs(vals[p.id]) * 2e-7 + 1e-3,   # + float32 spacing
                                       msg=f"{p.name} in group {gid:#x}")
                checked += 1
        self.assertGreater(checked, 700)

    def test_firmware_quirk_zeroes_fractional_integer_scale(self):
        p = self.net.param_by_name("gpsSpeed_mph")          # UNSIGNED16, scale 0.01
        self.assertTrue(p.fractional_scale_on_int)
        payload = encode_group(self.net, p.group_id, {p.id: 12.34})
        intended = dict((q.name, v) for q, v in decode_frame(self.net, p.group_id, payload))
        onboard = dict((q.name, v) for q, v in decode_frame(self.net, p.group_id, payload, firmware_quirks=True))
        self.assertAlmostEqual(intended["gpsSpeed_mph"], 12.34, places=2)
        self.assertEqual(onboard["gpsSpeed_mph"], 0)


if __name__ == "__main__":
    unittest.main()
