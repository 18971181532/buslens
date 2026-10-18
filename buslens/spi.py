"""SPI: mode-aware decoding (CPOL/CPHA), CS framing and a waveform encoder.

SPI has no protocol-level framing beyond the chip-select line, so the decoder
is built around two rules:

* CPOL decides which level is idle; the *leading* edge is the first transition
  away from idle after CS asserts.
* CPHA decides whether data is sampled on the leading (0) or trailing (1) edge.

Both master and slave shift on the opposite edge, so MOSI and MISO are sampled
together - which is exactly what a logic analyser captures.
"""

from __future__ import annotations

import statistics
from typing import List, Optional, Sequence, Tuple

from .signal import Capture, DigitalSignal

__all__ = ["decode_spi", "encode_spi"]


def _word_value(bits: Sequence[int], order: str) -> int:
    if order == "lsb":
        return sum(b << i for i, b in enumerate(bits))
    v = 0
    for b in bits:
        v = (v << 1) | b
    return v


def decode_spi(sck: DigitalSignal, mosi: Optional[DigitalSignal] = None,
               miso: Optional[DigitalSignal] = None,
               cs: Optional[DigitalSignal] = None, cpol: int = 0, cpha: int = 0,
               bits: int = 8, order: str = "msb", cs_active: int = 0,
               cs_name: Optional[str] = None) -> dict:
    if cpol not in (0, 1) or cpha not in (0, 1):
        raise ValueError("cpol/cpha must be 0 or 1")
    if order not in ("msb", "lsb"):
        raise ValueError("order must be msb/lsb")
    sck_name = sck.name
    cs_name = cs_name or (cs.name if cs else None)

    frames: List[dict] = []
    annotations: List[dict] = []
    errors: List[dict] = []
    all_words: List[dict] = []
    sck_periods: List[float] = []

    # frame windows from CS (or the whole capture)
    windows: List[Tuple[float, float]] = []
    if cs is not None:
        active = cs.intervals(cs_active)
        if not active:
            windows = [(sck.start, sck.end)]
        else:
            windows = active
    else:
        windows = [(sck.start, sck.end)]

    leading_level = 1 - cpol
    sample_level = leading_level if cpha == 0 else cpol

    edge_list = sck.edges()
    for w0, w1 in windows:
        frame_edges = [e for e in edge_list if w0 <= e["t"] <= w1]
        if not frame_edges:
            continue
        # clock rate: distance between same-direction edges
        rises = [e["t"] for e in frame_edges if e["level"] == 1]
        for a, b in zip(rises, rises[1:]):
            sck_periods.append(b - a)

        samples: List[dict] = []
        for e in frame_edges:
            if e["level"] != sample_level:
                continue
            t = e["t"]
            samples.append({
                "t": t,
                "mosi": mosi.level_at(t) if mosi is not None else None,
                "miso": miso.level_at(t) if miso is not None else None,
            })
        if not samples:
            continue

        words: List[dict] = []
        for start in range(0, len(samples), bits):
            chunk = samples[start:start + bits]
            mosi_bits = [s["mosi"] for s in chunk if s["mosi"] is not None]
            miso_bits = [s["miso"] for s in chunk if s["miso"] is not None]
            word = {
                "index": len(all_words) + 1,
                "t0": round(chunk[0]["t"], 3),
                "t1": round(chunk[-1]["t"], 3),
                "bits": len(chunk),
                "mosi": _word_value(mosi_bits, order) if mosi_bits else None,
                "miso": _word_value(miso_bits, order) if miso_bits else None,
            }
            if len(chunk) != bits:
                errors.append({"t": word["t0"],
                               "message": "incomplete word (%d of %d bits)"
                                          % (len(chunk), bits)})
                word["partial"] = True
            words.append(word)
            all_words.append(word)

            label = ""
            if word["mosi"] is not None:
                label += "M%02X" % word["mosi"]
            if word["miso"] is not None:
                label += " S%02X" % word["miso"]
            annotations.append({
                "channel": (mosi.name if mosi is not None else
                            (miso.name if miso is not None else sck_name)),
                "t0": word["t0"], "t1": word["t1"],
                "label": label.strip(), "kind": "word",
            })

        frames.append({
            "index": len(frames) + 1,
            "t0": round(w0, 3),
            "t1": round(w1, 3),
            "words": words,
            "cs": cs_name,
        })

    clock_hz = round(1e9 / statistics.median(sck_periods), 1) if sck_periods else None
    span = max(1e-9, (windows[-1][1] - windows[0][0]) if windows else 0.0)
    total_bits = sum(w["bits"] for w in all_words)
    stats = {
        "frames": len(frames),
        "words": len(all_words),
        "bytes": total_bits // 8,
        "clockHz": clock_hz,
        "mode": (cpol, cpha),
        "utilization": round(100.0 * total_bits * (1e9 / clock_hz) / span, 2) if clock_hz else None,
        "durationUs": round(span / 1000.0, 2),
    }
    return {
        "protocol": "spi",
        "channels": {"sck": sck_name, "cs": cs_name,
                     "mosi": mosi.name if mosi else None,
                     "miso": miso.name if miso else None},
        "config": {"cpol": cpol, "cpha": cpha, "bits": bits, "order": order,
                   "csActive": cs_active},
        "frames": frames,
        "words": all_words,
        "annotations": annotations,
        "errors": errors,
        "stats": stats,
    }


def encode_spi(mosi: Sequence[int], miso: Optional[Sequence[int]] = None,
               sck_hz: float = 1_000_000, cpol: int = 0, cpha: int = 0,
               bits: int = 8, order: str = "msb", cs_gap_ns: float = 400.0,
               names: Tuple[str, str, str, str] = ("CS", "SCK", "MOSI", "MISO")) -> Capture:
    """Build a 4-wire SPI capture.  ``miso`` defaults to all zero bytes."""
    cs_name, sck_name, mosi_name, miso_name = names
    period = 1e9 / float(sck_hz)
    cs = DigitalSignal(cs_name, initial=1)
    sck = DigitalSignal(sck_name, initial=cpol)
    mo = DigitalSignal(mosi_name, initial=0)
    mi = DigitalSignal(miso_name, initial=0)

    if miso is None:
        miso = [0] * len(mosi)
    if len(miso) < len(mosi):
        miso = list(miso) + [0] * (len(mosi) - len(miso))

    t = 0.0
    cs.add(t, 0)
    t += 0.4 * period

    for idx, byte in enumerate(mosi):
        mbyte = miso[idx]
        bits_m = [(byte >> (7 - i)) & 1 for i in range(8)] if order == "msb" \
            else [(byte >> i) & 1 for i in range(8)]
        bits_s = [(mbyte >> (7 - i)) & 1 for i in range(8)] if order == "msb" \
            else [(mbyte >> i) & 1 for i in range(8)]
        for bm, bs in zip(bits_m, bits_s):
            if cpha == 0:
                mo.add(t + 0.10 * period, bm)
                mi.add(t + 0.10 * period, bs)
                sck.add(t + 0.50 * period, 1 - cpol)      # sample edge
                sck.add(t + 0.90 * period, cpol)          # shift edge
            else:
                sck.add(t + 0.20 * period, 1 - cpol)      # shift edge
                mo.add(t + 0.40 * period, bm)
                mi.add(t + 0.40 * period, bs)
                sck.add(t + 0.80 * period, cpol)          # sample edge
            t += period

    t += 0.4 * period
    cs.add(t, 1)
    cap = Capture({cs_name: cs, sck_name: sck, mosi_name: mo, miso_name: mi})
    cap.t0 = 0.0
    cap.t1 = t + cs_gap_ns
    return cap
