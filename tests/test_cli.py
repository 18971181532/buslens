"""CLI smoke tests: listing, decoding, JSON output and export."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from buslens.cli import main


class TestCli(unittest.TestCase):
    def run_cli(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    def test_demos(self):
        code, out = self.run_cli(["demos"])
        self.assertEqual(code, 0)
        self.assertIn("i2c_eeprom", out)
        self.assertIn("ws2812", out)

    def test_decode_human(self):
        code, out = self.run_cli(["decode", "--demo", "spi_flash"])
        self.assertEqual(code, 0)
        self.assertIn("0x9F", out)
        self.assertIn("0xEF", out)

    def test_decode_json(self):
        code, out = self.run_cli(["decode", "--demo", "uart_at", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["protocol"], "uart")
        self.assertIn("decoded", payload)

    def test_export_vcd(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cap.vcd")
            code, out = self.run_cli(["export", "--demo", "i2c_sensor", "--out", path])
            self.assertEqual(code, 0)
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("$enddefinitions", text)
            self.assertIn("SCL", text)

    def test_save_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "result.json")
            code, _ = self.run_cli(["decode", "--demo", "pwm_servo", "--save", path])
            self.assertEqual(code, 0)
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
            self.assertEqual(payload["protocol"], "pwm")


if __name__ == "__main__":
    unittest.main()
