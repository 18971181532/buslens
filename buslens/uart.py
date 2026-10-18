"""UART: frame decoding with automatic baud-rate recovery, and encoding.

The decoder works directly on level transitions, so it is independent of any
sample rate.  Auto-baud first estimates the bit time from the narrowest pulse
cluster, then refines it by maximising the number of frames with a legal stop
bit - a much more robust criterion than "the shortest pulse is one bit", which
breaks down for payloads like 0xFF.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from .signal import DigitalSignal

__all__ = ["decode_uart", "encode_uart", "estimate_baud", "UartConfig"]

_PARITY_CHOICES = ("none", "even", "odd")


class UartConfig:
    def __init__(self, baud: int = 115200, data_bits: int = 8,
                 parity: str = "none", stop_bits: float = 1.0,
                 idle: int = 1, lsb_first: bool = True):
        if parity not in _PARITY_CHOICES:
            raise ValueError("parity must be none/even/odd")
        if data_bits < 5 or data_bits > 9:
            raise ValueError("data_bits must be 5..9")
        self.baud = int(baud)
        self.data_bits = int(data_bits)
        self.parity = parity
        self.stop_bits = float(stop_bits)
        self.idle = 1 if idle else 0
        self.lsb_first = lsb_first

    @property
    def bit_ns(self) -> float:
        return 1e9 / self.baud

    @property
    def frame_bits(self) -> float:
        return 1 + self.data_bits + (0 if self.parity == "none" else 1) + self.stop_bits

    def to_dict(self) -> dict:
        return {"baud": self.baud, "dataBits": self.data_bits,
                "parity": self.parity, "stopBits": self.stop_bits,
                "idle": self.idle, "lsbFirst": self.lsb_first}


def _pulse_widths(sig: DigitalSignal, lo: float, hi: float) -> List[float]:
    widths: List[float] = []
    for a, b in sig.intervals(1):
        if lo <= b - a <= hi:
            widths.append(b - a)
    for a, b in sig.intervals(0):
        if lo <= b - a <= hi:
            widths.append(b - a)
    return widths


def estimate_baud(sig: DigitalSignal) -> Tuple[int, float]:
    """Estimate the line rate from pulse statistics.

    Returns ``(baud, confidence)`` where confidence is the fraction of pulses
    that agree with the recovered bit time.
    """
    widths = sorted(w for w in _pulse_widths(sig, 20.0, 1e9) if w > 20.0)
    if not widths:
        return 115200, 0.0
    # the shortest pulses are one bit long; take a low percentile to reject
    # glitches, then cluster around it
    base = widths[max(0, int(0.10 * (len(widths) - 1)))]
    cluster = [w for w in widths if 0.75 * base <= w <= 1.45 * base]
    if not cluster:
        cluster = [base]
    bit_ns = sorted(cluster)[len(cluster) // 2]
    baud = int(round(1e9 / bit_ns))
    agree = sum(1 for w in widths
                if abs(w / bit_ns - round(w / bit_ns)) < 0.18 * max(1.0, round(w / bit_ns)))
    confidence = agree / len(widths)
    return baud, confidence


def _decode_with(sig: DigitalSignal, cfg: UartConfig) -> List[dict]:
    bit = cfg.bit_ns
    total_bits = cfg.frame_bits
    frames: List[dict] = []
    start_level = 1 - cfg.idle

    t = sig.start
    end = sig.end
    guard = 0.35 * bit
    edges = sig.edges()
    idx = 0
    while t < end:
        while idx < len(edges) and edges[idx]["t"] < t:
            idx += 1
        edge = None
        scan = idx
        while scan < len(edges):
            if edges[scan]["level"] == start_level:
                edge = edges[scan]
                break
            scan += 1
        if edge is None:
            break
        t_start = edge["t"]
        # verify the start bit is still active at its centre
        if sig.level_at(t_start + 0.5 * bit) != start_level:
            t = t_start + guard
            continue

        sample = []
        for i in range(cfg.data_bits):
            ts = t_start + (1.5 + i) * bit
            sample.append(1 - sig.level_at(ts) if cfg.idle == 0 else sig.level_at(ts))

        parity_bit = None
        bit_idx = cfg.data_bits
        if cfg.parity != "none":
            parity_bit = sig.level_at(t_start + (1.5 + bit_idx) * bit)
            bit_idx += 1

        stop_ok = sig.level_at(t_start + (1.5 + bit_idx) * bit) == cfg.idle

        value = frame_value(sample, cfg.lsb_first)
        errors = []
        if not stop_ok:
            errors.append("framing")
        if parity_bit is not None:
            ones = sum(sample) + int(parity_bit)
            if cfg.parity == "even" and ones % 2:
                errors.append("parity")
            if cfg.parity == "odd" and ones % 2 == 0:
                errors.append("parity")

        t_end = t_start + total_bits * bit
        frames.append({
            "index": len(frames) + 1,
            "t0": round(t_start, 3),
            "t1": round(t_end, 3),
            "value": value,
            "char": chr(value) if 32 <= value < 127 else ".",
            "bits": sample,
            "parityOk": parity_bit is None or "parity" not in errors,
            "stopOk": stop_ok,
            "errors": errors,
        })
        t = max(t_end - 0.25 * bit, t_start + guard)
    return frames


def frame_value(bits: Sequence[int], lsb_first: bool = True) -> int:
    if lsb_first:
        return sum(b << i for i, b in enumerate(bits))
    v = 0
    for b in bits:
        v = (v << 1) | b
    return v


def decode_uart(sig: DigitalSignal, baud: Optional[int] = None,
                data_bits: int = 8, parity: str = "none",
                stop_bits: float = 1.0, idle: int = 1,
                channel: Optional[str] = None) -> dict:
    """Decode a UART line.  ``baud=None`` enables auto-baud recovery."""
    source = "manual"
    confidence = 1.0
    if baud is None:
        baud, confidence = estimate_baud(sig)
        source = "auto"
        best = None
        # refine around the estimate: the correct bit time maximises valid stops
        for factor in [1.0 + k * 0.004 for k in range(-12, 13)]:
            cfg = UartConfig(int(round(baud * factor)), data_bits, parity, stop_bits, idle)
            frames = _decode_with(sig, cfg)
            good = sum(1 for f in frames if f["stopOk"] and not f["errors"])
            score = (good, -abs(factor - 1.0))
            if best is None or score > best[0]:
                best = (score, cfg)
        cfg = best[1] if best else UartConfig(baud, data_bits, parity, stop_bits, idle)
        baud = cfg.baud
    else:
        cfg = UartConfig(baud, data_bits, parity, stop_bits, idle)

    frames = _decode_with(sig, cfg)
    payload = bytes(f["value"] for f in frames)
    annotations = []
    for f in frames:
        ok = f["stopOk"] and not f["errors"]
        annotations.append({
            "channel": channel or sig.name,
            "t0": f["t0"], "t1": f["t1"],
            "label": "%02X %s" % (f["value"], f["char"]),
            "kind": "byte" if ok else "error",
            "detail": ",".join(f["errors"]) if f["errors"] else "",
        })
    errors = [{"t": f["t0"], "message": "frame %d: %s" % (f["index"], ", ".join(f["errors"]))}
              for f in frames if f["errors"]]
    return {
        "protocol": "uart",
        "channel": channel or sig.name,
        "config": cfg.to_dict(),
        "baudSource": source,
        "baudConfidence": round(confidence, 3),
        "frames": frames,
        "bytes": list(payload),
        "text": payload.decode("ascii", "replace"),
        "annotations": annotations,
        "errors": errors,
        "stats": {
            "frames": len(frames),
            "bytes": len(payload),
            "errors": len(errors),
            "bitTimeNs": round(cfg.bit_ns, 2),
            "throughputBps": round(len(payload) * 10.0 / max(1e-9, (sig.end - sig.start) / 1e9), 1),
        },
    }


def encode_uart(data: bytes, baud: int = 115200, data_bits: int = 8,
                parity: str = "none", stop_bits: float = 1.0,
                name: str = "TX", idle: int = 1,
                gap_bits: float = 0.0) -> DigitalSignal:
    """Build a UART waveform for ``data`` (idle level defaults to high)."""
    cfg = UartConfig(baud, data_bits, parity, stop_bits, idle)
    bit = cfg.bit_ns
    # build with the standard idle-high convention, then invert if requested
    sig = DigitalSignal(name, initial=1)
    t = 0.0
    cur = 1
    for byte in data:
        bits = [(byte >> i) & 1 for i in range(data_bits)]
        frame = [0] + bits
        if parity != "none":
            ones = sum(bits)
            pbit = ones % 2 if parity == "even" else 1 - ones % 2
            frame.append(pbit)
        frame += [1] * max(1, int(round(stop_bits)))
        for b in frame:
            if b != cur:
                sig.add(t, b)
                cur = b
            t += bit
        # trailing idle
        t += gap_bits * bit
    if idle == 0:
        sig = DigitalSignal(name, [(tt, 1 - v) for tt, v in sig.transitions],
                            1 - sig.initial)
    return sig
