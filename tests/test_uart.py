"""UART: encode/decode round-trip, auto-baud, parity and framing errors."""

import unittest

from buslens.signal import DigitalSignal
from buslens.uart import decode_uart, encode_uart, estimate_baud


class TestUartRoundTrip(unittest.TestCase):
    def test_payloads_and_rates(self):
        for baud in (9600, 57600, 115200, 921600):
            for payload in (b"A", b"Hello, MCU!\r\n", bytes(range(0, 32))):
                sig = encode_uart(payload, baud)
                out = decode_uart(sig, baud=baud)
                self.assertEqual(bytes(out["bytes"]), payload, (baud, payload))
                self.assertEqual(out["errors"], [])

    def test_auto_baud(self):
        sig = encode_uart(b"AT+VER?\r\n", 115200)
        out = decode_uart(sig)   # no baud supplied
        self.assertEqual(out["baudSource"], "auto")
        self.assertEqual(out["config"]["baud"], 115200)
        self.assertEqual(bytes(out["bytes"]), b"AT+VER?\r\n")
        self.assertGreater(out["baudConfidence"], 0.9)

    def test_auto_baud_at_low_rate(self):
        sig = encode_uart(b"\x55\xAA\x0F\xF0", 9600)
        out = decode_uart(sig)
        self.assertEqual(out["config"]["baud"], 9600)
        self.assertEqual(bytes(out["bytes"]), b"\x55\xAA\x0F\xF0")

    def test_parity_even_and_two_stop_bits(self):
        sig = encode_uart(b"0123456789", 19200, parity="even", stop_bits=2)
        out = decode_uart(sig, parity="even", stop_bits=2)
        self.assertEqual(bytes(out["bytes"]), b"0123456789")
        self.assertEqual(out["errors"], [])

    def test_wrong_parity_is_reported(self):
        sig = encode_uart(b"ABC", 115200, parity="even")
        out = decode_uart(sig, baud=115200, parity="odd")
        self.assertTrue(any("parity" in e["message"] for e in out["errors"]))

    def test_inverted_idle(self):
        sig = encode_uart(b"OK", 115200, idle=0)
        out = decode_uart(sig, baud=115200, idle=0)
        self.assertEqual(bytes(out["bytes"]), b"OK")


class TestUartErrors(unittest.TestCase):
    def test_framing_error(self):
        bit = 1e9 / 115200
        sig = DigitalSignal("TX", initial=1)
        t = 0.0
        cur = 1
        for b in [0, 1, 0, 1, 0, 1, 0, 1, 0, 0]:   # last bit = bad stop
            if b != cur:
                sig.add(t, b)
                cur = b
            t += bit
        sig.add(t, 1)
        out = decode_uart(sig, baud=115200)
        self.assertTrue(out["errors"])
        self.assertIn("framing", out["errors"][0]["message"])

    def test_estimate_baud_from_statistics(self):
        sig = encode_uart(b"\x00\xFF\x00\xFF", 4800)
        baud, confidence = estimate_baud(sig)
        self.assertAlmostEqual(baud, 4800, delta=80)
        self.assertGreater(confidence, 0.5)


if __name__ == "__main__":
    unittest.main()
