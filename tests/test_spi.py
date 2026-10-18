"""SPI: all four modes, bit order and CS framing."""

import unittest

from buslens.signal import Capture, DigitalSignal
from buslens.spi import decode_spi, encode_spi


class TestSpi(unittest.TestCase):
    def test_all_modes(self):
        for cpol in (0, 1):
            for cpha in (0, 1):
                cap = encode_spi([0x9F, 0x00, 0x12], [0xFF, 0x01, 0x02],
                                 sck_hz=2e6, cpol=cpol, cpha=cpha)
                out = decode_spi(cap.get("SCK"), cap.get("MOSI"),
                                 cap.get("MISO"), cap.get("CS"),
                                 cpol=cpol, cpha=cpha)
                self.assertEqual([w["mosi"] for w in out["words"]],
                                 [0x9F, 0x00, 0x12], (cpol, cpha))
                self.assertEqual([w["miso"] for w in out["words"]],
                                 [0xFF, 0x01, 0x02])
                self.assertEqual(out["errors"], [])
                self.assertEqual(out["stats"]["mode"], (cpol, cpha))
                self.assertAlmostEqual(out["stats"]["clockHz"], 2e6, delta=5000)

    def test_lsb_first(self):
        cap = encode_spi([0x01, 0x80], [0x00, 0x00], sck_hz=1e6, order="lsb")
        out = decode_spi(cap.get("SCK"), cap.get("MOSI"), cap.get("MISO"),
                         cap.get("CS"), cpol=0, cpha=0, order="lsb")
        self.assertEqual([w["mosi"] for w in out["words"]], [0x01, 0x80])

    def test_two_frames_with_cs_gaps(self):
        a = encode_spi([0x9F], [0xEF], sck_hz=2e6)
        b = encode_spi([0x05], [0x00], sck_hz=2e6)
        merged = Capture()
        for name, sig in a.channels.items():
            merged.add(DigitalSignal(name, list(sig.transitions), sig.initial))
        for name, sig in b.channels.items():
            target = merged.channels.get(name)
            for t, v in sig.transitions:
                target.add(t + a.t1 + 2000, v)
        out = decode_spi(merged.get("SCK"), merged.get("MOSI"), merged.get("MISO"),
                         merged.get("CS"), cpol=0, cpha=0)
        self.assertEqual(len(out["frames"]), 2)
        self.assertEqual(out["stats"]["words"], 2)

    def test_annotations_carry_words(self):
        cap = encode_spi([0xAA], [0x55], sck_hz=1e6)
        out = decode_spi(cap.get("SCK"), cap.get("MOSI"), cap.get("MISO"), cap.get("CS"))
        self.assertTrue(out["annotations"])
        self.assertIn("MAA", out["annotations"][0]["label"])
        self.assertIn("S55", out["annotations"][0]["label"])


if __name__ == "__main__":
    unittest.main()
