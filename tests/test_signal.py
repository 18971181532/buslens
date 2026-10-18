"""Waveform storage, VCD/CSV round-trips and sampling."""

import unittest

from buslens.signal import Capture, DigitalSignal, parse_csv, parse_vcd, write_vcd


class TestDigitalSignal(unittest.TestCase):
    def test_redundant_transitions_are_merged(self):
        sig = DigitalSignal("A", [(10, 0), (20, 1), (25, 1), (30, 0)], initial=0)
        self.assertEqual(sig.transitions, [(20, 1), (30, 0)])
        self.assertEqual(sig.level_at(0), 0)
        self.assertEqual(sig.level_at(20), 1)
        self.assertEqual(sig.level_at(29.9), 1)
        self.assertEqual(sig.level_at(30), 0)

    def test_edges_and_intervals(self):
        sig = DigitalSignal("A", [(10, 1), (30, 0), (50, 1)], initial=0)
        edges = sig.edges()
        self.assertEqual([e["level"] for e in edges], [1, 0, 1])
        self.assertEqual(sig.intervals(1), [(10.0, 30.0)])
        self.assertEqual(sig.pulse_widths(1), [20.0])

    def test_sample_and_from_samples(self):
        sig = DigitalSignal("A", [(0, 1), (30, 0)], initial=0)
        bits = sig.sample(1e8, 0, 39)  # 10 ns period
        self.assertEqual(bits, [1, 1, 1, 0])
        edge = DigitalSignal.from_samples("B", [0, 1, 1, 0], 1e6)
        self.assertEqual(edge.transitions, [(1000.0, 1), (3000.0, 0)])

    def test_slice(self):
        sig = DigitalSignal("A", [(10, 1), (20, 0), (30, 1)], initial=0)
        part = sig.slice(15, 35)
        self.assertEqual(part.level_at(16), 1)
        self.assertEqual(part.level_at(25), 0)
        self.assertEqual(part.transitions, [(20, 0), (30, 1)])


class TestVcd(unittest.TestCase):
    def test_roundtrip(self):
        cap = Capture({
            "SCL": DigitalSignal("SCL", [(100, 1), (200, 0)], initial=1),
            "SDA": DigitalSignal("SDA", [(50, 1), (400, 0)], initial=1),
        })
        text = write_vcd(cap)
        back = parse_vcd(text)
        self.assertEqual(set(back.channels), {"SCL", "SDA"})
        self.assertEqual(back.channels["SCL"].level_at(150), 1)
        self.assertEqual(back.channels["SCL"].level_at(250), 0)
        self.assertEqual(back.channels["SDA"].level_at(450), 0)

    def test_timescale(self):
        vcd = """$timescale 1 us $end
$var wire 1 ! A $end
$enddefinitions $end
#0
0!
#2
1!
"""
        cap = parse_vcd(vcd)
        self.assertEqual(cap.channels["A"].transitions, [(2000.0, 1)])


class TestCsv(unittest.TestCase):
    def test_header_table(self):
        cap = parse_csv("t,CLK,DATA\n0,0,1\n10,1,1\n20,0,0\n")
        self.assertEqual(cap.channels["CLK"].transitions, [(10.0, 1), (20.0, 0)])
        self.assertEqual(cap.channels["DATA"].level_at(25), 0)

    def test_single_column_samples(self):
        cap = parse_csv("0\n1\n1\n0\n", sample_rate_hz=1e6)
        self.assertEqual(cap.channels["CH0"].transitions, [(1000.0, 1), (3000.0, 0)])


if __name__ == "__main__":
    unittest.main()
