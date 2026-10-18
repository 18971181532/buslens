"""End-to-end analysis of the built-in captures."""

import unittest

from buslens.analyze import analyze, quality_score
from buslens.demos import build_demo, demo_list


class TestDemos(unittest.TestCase):
    def test_every_demo_decodes(self):
        for item in demo_list():
            built = build_demo(item["id"])
            result = analyze(built["capture"], built["protocol"], built["config"],
                             source="demo:" + item["id"])
            self.assertTrue(result["ok"])
            self.assertEqual(result["protocol"], item["protocol"])
            self.assertIn(result["quality"]["grade"], ("A", "B", "C", "D"))
            self.assertTrue(result["summary"]["primary"])
            self.assertGreater(len(result["capture"]["channels"]), 0)
            for ann in result["annotations"]:
                self.assertIn("channel", ann)
                self.assertIn("label", ann)

    def test_i2c_eeprom_registers_and_nack(self):
        built = build_demo("i2c_eeprom")
        result = analyze(built["capture"], "i2c", built["config"])
        self.assertEqual(result["stats"]["transactions"], 5)
        self.assertTrue(any("NACK" in e["message"] for e in result["errors"]))
        self.assertEqual(result["registerMap"]["0x50"]["0x10"]["value"], "0x11")
        self.assertLess(result["quality"]["score"], 100)

    def test_sensor_demo_has_stretches(self):
        built = build_demo("i2c_sensor")
        result = analyze(built["capture"], "i2c", built["config"])
        self.assertGreater(result["stats"]["stretches"], 0)
        self.assertTrue(result["quality"]["reasons"])

    def test_uart_demo_text(self):
        built = build_demo("uart_at")
        result = analyze(built["capture"], "uart", built["config"])
        self.assertEqual(result["decoded"]["text"]["TX"], "AT+VER?\r\n")
        self.assertIn("BusLens", result["decoded"]["text"]["RX"])

    def test_ws2812_pixels(self):
        built = build_demo("ws2812")
        result = analyze(built["capture"], "ws2812", built["config"])
        self.assertEqual(len(result["decoded"]["pixels"]), 8)
        self.assertEqual(result["summary"]["primary"], "8 像素")

    def test_spi_words(self):
        built = build_demo("spi_flash")
        result = analyze(built["capture"], "spi", built["config"])
        self.assertEqual(result["decoded"]["words"][0]["mosi"], 0x9F)
        self.assertEqual(result["decoded"]["words"][0]["miso"], 0xEF)


class TestQuality(unittest.TestCase):
    def test_errors_lower_the_score(self):
        built = build_demo("i2c_eeprom")
        result = analyze(built["capture"], "i2c", built["config"])
        clean = dict(result["decoded"])
        clean["errors"] = []
        better = quality_score(clean, built["capture"])
        self.assertGreater(better["score"], result["quality"]["score"])

    def test_grade_boundaries(self):
        fake = {"protocol": "i2c", "errors": [], "stats": {}}
        from buslens.signal import Capture
        cap = Capture()
        self.assertEqual(quality_score(fake, cap)["grade"], "A")
        fake["errors"] = [{"message": "x", "level": "error"}] * 8
        self.assertEqual(quality_score(fake, cap)["grade"], "D")


if __name__ == "__main__":
    unittest.main()
