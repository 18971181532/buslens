"""PWM measurement, servo detection and WS2812 decoding."""

import unittest

from buslens.pwm import decode_pwm, decode_ws2812, encode_pwm, encode_ws2812
from buslens.signal import DigitalSignal


class TestPwm(unittest.TestCase):
    def test_frequency_and_duty(self):
        sig = encode_pwm(2000, 0.25, pulses=20)
        out = decode_pwm(sig)
        self.assertAlmostEqual(out["stats"]["frequencyHz"], 2000, delta=20)
        self.assertAlmostEqual(out["stats"]["dutyPct"], 25.0, delta=0.2)
        self.assertEqual(out["stats"]["pulses"], 20)
        self.assertEqual(out["errors"], [])

    def test_servo_classification(self):
        out = decode_pwm(encode_pwm(50, 0.075, pulses=10))
        self.assertEqual(out["kind"], "servo")
        self.assertAlmostEqual(out["stats"]["highUs"], 1500, delta=2)

    def test_missing_pulse_is_detected(self):
        sig = DigitalSignal("PWM", initial=0)
        period = 1_000_000.0
        t = 0.0
        for i in range(10):
            if i == 4:
                t += period      # skip one pulse
                continue
            sig.add(t, 1)
            sig.add(t + 0.3 * period, 0)
            t += period
        out = decode_pwm(sig)
        self.assertGreaterEqual(out["stats"]["missingPulses"], 1)


class TestWs2812(unittest.TestCase):
    def test_colours(self):
        pixels = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (16, 32, 64)]
        out = decode_ws2812(encode_ws2812(pixels))
        self.assertEqual([p["hex"] for p in out["pixels"]],
                         ["#FF0000", "#00FF00", "#0000FF", "#102040"])
        self.assertEqual(out["stats"]["frames"], 1)
        self.assertAlmostEqual(out["stats"]["bitRateHz"], 800_000, delta=1000)
        self.assertEqual(out["errors"], [])

    def test_reset_splits_frames(self):
        a = encode_ws2812([(1, 2, 3), (4, 5, 6)])
        b = encode_ws2812([(7, 8, 9)])
        merged = DigitalSignal("DIN", list(a.transitions)
                               + [(t + a.end + 90_000, v) for t, v in b.transitions])
        out = decode_ws2812(merged)
        self.assertEqual(out["stats"]["frames"], 2)
        self.assertEqual(len(out["pixels"]), 3)

    def test_pixel_annotation(self):
        out = decode_ws2812(encode_ws2812([(10, 20, 30)]))
        self.assertTrue(out["annotations"])
        self.assertEqual(out["annotations"][0]["label"], "#0A141E")


if __name__ == "__main__":
    unittest.main()
