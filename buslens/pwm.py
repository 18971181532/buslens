"""PWM / servo pulse measurement and WS2812 (NeoPixel) bit-stream decoding."""

from __future__ import annotations

import statistics
from typing import Dict, List, Optional, Sequence

from .signal import Capture, DigitalSignal

__all__ = ["decode_pwm", "decode_ws2812", "encode_ws2812", "encode_pwm"]


def _stats(values: Sequence[float]) -> Dict[str, float]:
    if not values:
        return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0}
    return {
        "mean": statistics.fmean(values),
        "std": statistics.pstdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
    }


def decode_pwm(sig: DigitalSignal, channel: Optional[str] = None) -> dict:
    """Measure period, duty cycle, jitter and missing pulses."""
    rises = [e["t"] for e in sig.edges() if e["level"] == 1]
    highs = [b - a for a, b in sig.intervals(1)]
    periods = [b - a for a, b in zip(rises, rises[1:])]
    if len(periods) < 1 or not highs:
        raise ValueError("not enough PWM pulses in the capture")

    freq_stats = _stats([1e9 / p for p in periods])
    period_stats = _stats(periods)
    high_stats = _stats(highs)
    duty = [h / p for h, p in zip(highs, periods)] if len(periods) >= len(highs) else \
        [h / period_stats["mean"] for h in highs]

    median_period = period_stats["mean"]
    gaps = []
    for a, b in zip(rises, rises[1:]):
        ratio = (b - a) / median_period if median_period else 0
        if ratio > 1.6:
            gaps.append({"t0": round(a, 1), "t1": round(b, 1),
                         "missingPulses": max(0, int(round(ratio)) - 1)})

    jitter_pct = 100.0 * period_stats["std"] / median_period if median_period else 0.0
    annotations = []
    for i, (a, b) in enumerate(sig.intervals(1)[:64]):
        annotations.append({
            "channel": channel or sig.name, "t0": round(a, 1), "t1": round(b, 1),
            "label": "%.1f us / %.1f%%" % ((b - a) / 1000.0,
                                           100.0 * (b - a) / median_period),
            "kind": "pulse",
        })

    kind = "pwm"
    if 40 <= freq_stats["mean"] <= 60 and 400e3 <= high_stats["mean"] <= 2600e3:
        kind = "servo"

    return {
        "protocol": "pwm",
        "channel": channel or sig.name,
        "kind": kind,
        "annotations": annotations,
        "errors": [{"t": g["t0"], "message": "gap of %.1f us (%.0f missing pulses)"
                                             % ((g["t1"] - g["t0"]) / 1000.0,
                                                g["missingPulses"])}
                   for g in gaps],
        "stats": {
            "pulses": len(highs),
            "frequencyHz": round(freq_stats["mean"], 2),
            "frequencyJitterPct": round(jitter_pct, 3),
            "periodUs": round(period_stats["mean"] / 1000.0, 3),
            "highUs": round(high_stats["mean"] / 1000.0, 3),
            "dutyPct": round(100.0 * statistics.fmean(duty), 2),
            "dutyStdPct": round(100.0 * (statistics.pstdev(duty) if len(duty) > 1 else 0.0), 3),
            "missingPulses": sum(g["missingPulses"] for g in gaps),
            "gaps": gaps,
        },
    }


def decode_ws2812(sig: DigitalSignal, threshold_ns: float = 550.0,
                  reset_ns: float = 50_000.0, channel: Optional[str] = None) -> dict:
    """Decode a WS2812/SK6812 one-wire stream into GRB pixels."""
    highs = sig.intervals(1)
    if not highs:
        raise ValueError("no pulses found")

    bits: List[dict] = []
    frames: List[dict] = []
    current: List[int] = []
    ambiguous = 0
    prev_end: Optional[float] = None

    for a, b in highs:
        width = b - a
        if prev_end is not None and (a - prev_end) > reset_ns and current:
            frames.append(_finish_frame(frames, current, highs, channel or sig.name))
            current = []
        if abs(width - threshold_ns) < 120:
            ambiguous += 1
        bit = 1 if width > threshold_ns else 0
        bits.append({"t": a, "widthNs": width, "bit": bit})
        current.append(bit)
        prev_end = b
    if current:
        frames.append(_finish_frame(frames, current, highs, channel or sig.name))

    pixels = [p for f in frames for p in f["pixels"]]
    annotations = []
    for f in frames:
        for p in f["pixels"]:
            annotations.append({
                "channel": channel or sig.name,
                "t0": p["t0"], "t1": p["t1"],
                "label": p["hex"], "kind": "pixel",
            })
    errors = []
    for f in frames:
        if f["remainder"]:
            errors.append({"t": f["t0"],
                           "message": "frame has %d leftover bits" % f["remainder"]})
    starts = [a for a, _b in highs]
    wire_periods = [b - a for a, b in zip(starts, starts[1:]) if 0 < b - a < 20_000]
    stats = {
        "frames": len(frames),
        "pixels": len(pixels),
        "bits": len(bits),
        "ambiguousBits": ambiguous,
        "bitRateHz": round(1e9 / statistics.median(wire_periods), 1)
        if wire_periods else None,
        "zeroHighNs": round(statistics.fmean(
            [b["widthNs"] for b in bits if b["bit"] == 0]), 1) if any(
            b["bit"] == 0 for b in bits) else None,
        "oneHighNs": round(statistics.fmean(
            [b["widthNs"] for b in bits if b["bit"] == 1]), 1) if any(
            b["bit"] == 1 for b in bits) else None,
    }
    return {
        "protocol": "ws2812",
        "channel": channel or sig.name,
        "frames": frames,
        "pixels": pixels,
        "annotations": annotations,
        "errors": errors,
        "stats": stats,
    }


def _finish_frame(frames: List[dict], bits: List[int], highs, channel: str) -> dict:
    pixels = []
    usable = len(bits) - len(bits) % 24
    for i in range(0, usable, 24):
        g = _byte(bits[i:i + 8])
        r = _byte(bits[i + 8:i + 16])
        b = _byte(bits[i + 16:i + 24])
        t0 = highs[i][0]
        t1 = highs[i + 23][1]
        pixels.append({"r": r, "g": g, "b": b, "hex": "#%02X%02X%02X" % (r, g, b),
                       "t0": round(t0, 1), "t1": round(t1, 1)})
    return {"index": len(frames) + 1, "pixels": pixels,
            "bits": len(bits), "remainder": len(bits) % 24,
            "t0": round(highs[0][0], 1) if highs else 0.0}


def _byte(bits: Sequence[int]) -> int:
    v = 0
    for b in bits:
        v = (v << 1) | b
    return v


def encode_ws2812(pixels: Sequence[Sequence[int]], t0h: float = 350.0,
                  t1h: float = 700.0, period: float = 1250.0,
                  reset: float = 80_000.0, name: str = "DIN") -> DigitalSignal:
    """Encode RGB pixels as a WS2812 stream (GRB order on the wire)."""
    sig = DigitalSignal(name, initial=0)
    t = 0.0
    for r, g, b in pixels:
        for byte in (g, r, b):
            for i in range(7, -1, -1):
                bit = (byte >> i) & 1
                high = t1h if bit else t0h
                sig.add(t, 1)
                sig.add(t + high, 0)
                t += period
    t += reset
    return sig


def encode_pwm(freq_hz: float, duty: float, pulses: int = 10,
               jitter_ns: float = 0.0, name: str = "PWM") -> DigitalSignal:
    period = 1e9 / float(freq_hz)
    sig = DigitalSignal(name, initial=0)
    t = 0.0
    for i in range(pulses):
        high = period * duty
        if jitter_ns and i % 3 == 0:
            high += jitter_ns
        sig.add(t, 1)
        sig.add(t + high, 0)
        t += period
    return sig
