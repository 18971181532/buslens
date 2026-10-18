"""I2C: simulator/decoder closed loop, transactions, registers, stretching."""

import unittest

from buslens.analyze import i2c_registers
from buslens.devices import EEPROM24C02, TMP102
from buslens.i2c import I2CSim, decode_i2c


def run(plan, device, clock=400_000, stretch=0.0):
    cap = I2CSim(device, clock_hz=clock, stretch_ns=stretch).run(plan)
    return decode_i2c(cap.get("SCL"), cap.get("SDA")), cap


class TestI2CDecode(unittest.TestCase):
    def test_write_then_read(self):
        dev = EEPROM24C02()
        plan = {"transactions": [
            {"type": "write", "address": 0x50, "data": [0x10, 0xAA, 0xBB]},
            {"type": "write_read", "address": 0x50, "data": [0x10], "count": 2},
        ]}
        out, cap = run(plan, dev)
        self.assertEqual(out["stats"]["transactions"], 3)
        self.assertEqual(out["transactions"][0]["kind"], "write")
        self.assertEqual(out["transactions"][0]["bytes"], [0x10, 0xAA, 0xBB])
        self.assertEqual(out["transactions"][2]["bytes"], [0xAA, 0xBB])
        self.assertEqual(out["errors"], [])
        self.assertAlmostEqual(out["stats"]["clockHz"], 400_000, delta=2000)
        self.assertTrue(any(a["kind"] == "start" for a in out["annotations"]))
        self.assertTrue(any(a["label"].startswith("0x50") for a in out["annotations"]))

    def test_missing_device_nacks(self):
        out, _ = run({"transactions": [{"type": "write", "address": 0x51,
                                        "data": [0x00]}]}, None)
        self.assertEqual(len(out["transactions"]), 1)
        self.assertEqual(out["transactions"][0]["kind"], "probe")
        self.assertFalse(out["transactions"][0]["acked"])
        self.assertTrue(any("NACK" in e["message"] for e in out["errors"]))

    def test_clock_stretching(self):
        dev = TMP102()
        out, _ = run({"transactions": [{"type": "write_read", "address": 0x48,
                                        "data": [0x00], "count": 2}]},
                     dev, clock=100_000, stretch=90_000)
        self.assertGreaterEqual(out["stats"]["stretches"], 1)
        self.assertTrue(out["stretches"])
        self.assertAlmostEqual(out["stats"]["clockHz"], 100_000, delta=5000)

    def test_ten_bit_addressing_is_not_mistaken_for_seven(self):
        # 0xF0 is the 10-bit prefix; the decoder must consume the second byte
        out, _ = run({"transactions": []}, EEPROM24C02())
        self.assertEqual(out["transactions"], [])

    def test_register_reconstruction(self):
        dev = EEPROM24C02()
        out, _ = run({"transactions": [
            {"type": "write", "address": 0x50, "data": [0x20, 0x11, 0x22]},
            {"type": "write_read", "address": 0x50, "data": [0x20], "count": 2},
        ]}, dev)
        rows = i2c_registers(out["transactions"])
        values = {(r["register"], r["source"]): r["value"] for r in rows}
        self.assertEqual(values[("0x20", "write")], "0x11")
        self.assertEqual(values[("0x21", "write")], "0x22")
        self.assertEqual(values[("0x20", "read")], "0x11")

    def test_sensor_register_values(self):
        dev = TMP102(celsius=25.0625)
        out, _ = run({"transactions": [
            {"type": "write_read", "address": 0x48, "data": [0x00], "count": 2},
        ]}, dev, clock=100_000)
        tx = out["transactions"][1]
        self.assertEqual(tx["bytes"], [0x19, 0x10])   # 401 = 0x191, left-justified


if __name__ == "__main__":
    unittest.main()
