"""Digital waveform primitives: transition lists, sampling, VCD and CSV I/O.

A signal is stored as a list of ``(t_ns, level)`` transitions instead of a
fixed-rate sample array.  That keeps the time resolution equal to the capture
resolution (a logic analyser stores timestamps, not samples) and makes
edge-driven protocol decoders exact: a 100 ns glitch is visible even if the
"sample rate" would have been 1 MS/s.

All times are nanoseconds (float).
"""

from __future__ import annotations

import bisect
import csv
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "DigitalSignal",
    "Capture",
    "parse_vcd",
    "write_vcd",
    "parse_csv",
]

Level = int  # 0 or 1
Transition = Tuple[float, Level]


class DigitalSignal:
    """A single digital line, stored as normalised transitions."""

    __slots__ = ("name", "transitions", "initial", "times")

    def __init__(self, name: str, transitions: Optional[Sequence[Transition]] = None,
                 initial: Level = 0):
        self.name = name
        self.initial = 1 if initial else 0
        self.transitions: List[Transition] = []
        self.times: List[float] = []
        self.extend(transitions or [])

    # ------------------------------------------------------------------ build
    def extend(self, transitions: Iterable[Transition]) -> "DigitalSignal":
        for t, level in transitions:
            self.add(float(t), int(level))
        return self

    def add(self, t: float, level: Level) -> "DigitalSignal":
        """Append a transition, merging redundant ones."""
        level = 1 if level else 0
        if self.transitions and t < self.transitions[-1][0]:
            raise ValueError("transitions must be appended in time order")
        current = self.transitions[-1][1] if self.transitions else self.initial
        if level == current:
            return self
        if self.transitions and t == self.transitions[-1][0]:
            self.transitions[-1] = (t, level)
            return self
        self.transitions.append((t, level))
        self.times.append(t)
        return self

    def invert(self) -> "DigitalSignal":
        out = DigitalSignal(self.name + "_n", [(t, 1 - v) for t, v in self.transitions],
                            1 - self.initial)
        return out

    def copy(self) -> "DigitalSignal":
        return DigitalSignal(self.name, list(self.transitions), self.initial)

    def replace(self, transitions: Sequence[Transition]) -> "DigitalSignal":
        self.transitions = []
        self.times = []
        return self.extend(transitions)

    # ------------------------------------------------------------------ query
    def __len__(self) -> int:
        return len(self.transitions)

    def __repr__(self) -> str:
        return "DigitalSignal(%r, %d transitions, initial=%d)" % (
            self.name, len(self.transitions), self.initial)

    @property
    def duration(self) -> float:
        return self.transitions[-1][0] if self.transitions else 0.0

    @property
    def start(self) -> float:
        return self.transitions[0][0] if self.transitions else 0.0

    @property
    def end(self) -> float:
        return self.transitions[-1][0] if self.transitions else 0.0

    def level_at(self, t: float) -> Level:
        """Level just after time ``t`` (binary search over transitions)."""
        idx = bisect.bisect_right(self.times, t)
        if idx == 0:
            return self.initial
        return self.transitions[idx - 1][1]

    def edges(self, t0: Optional[float] = None, t1: Optional[float] = None,
              rising_only: bool = False) -> List[dict]:
        """Transitions with the previous level attached."""
        out: List[dict] = []
        prev = self.initial
        for t, level in self.transitions:
            if (t0 is None or t >= t0) and (t1 is None or t <= t1):
                if not rising_only or (prev == 0 and level == 1):
                    out.append({"t": t, "level": level, "prev": prev})
            prev = level
        return out

    def intervals(self, level: Level = 1,
                  t0: Optional[float] = None,
                  t1: Optional[float] = None) -> List[Tuple[float, float]]:
        """Time intervals during which the signal sits at ``level``."""
        t0 = self.start if t0 is None else t0
        t1 = self.end if t1 is None else t1
        out: List[Tuple[float, float]] = []
        cur = self.level_at(t0)
        start = t0
        for t, lvl in self.transitions:
            if t <= t0:
                continue
            if t > t1:
                break
            if lvl != cur:
                if cur == level and t > start:
                    out.append((start, t))
                cur = lvl
                start = t
        if cur == level and t1 > start:
            out.append((start, t1))
        return out

    def pulse_widths(self, level: Level = 1) -> List[float]:
        return [b - a for a, b in self.intervals(level)]

    def sample(self, fs_hz: float, t0: float = 0.0,
               t1: Optional[float] = None) -> List[int]:
        t1 = self.end if t1 is None else t1
        dt = 1e9 / fs_hz
        n = int(math.floor((t1 - t0) / dt)) + 1
        return [self.level_at(t0 + i * dt) for i in range(n)]

    @classmethod
    def from_samples(cls, name: str, bits: Sequence[int], fs_hz: float,
                     t0: float = 0.0, threshold: float = 0.5) -> "DigitalSignal":
        dt = 1e9 / fs_hz
        sig = cls(name, initial=1 if bits and bits[0] >= threshold else 0)
        for i, b in enumerate(bits):
            sig.add(t0 + i * dt, 1 if b >= threshold else 0)
        return sig

    def slice(self, t0: float, t1: float) -> "DigitalSignal":
        out = DigitalSignal(self.name, initial=self.level_at(t0))
        for t, lvl in self.transitions:
            if t0 < t < t1:
                out.add(t, lvl)
        return out

    def to_json(self) -> dict:
        return {"name": self.name, "initial": self.initial,
                "transitions": [[round(t, 3), v] for t, v in self.transitions]}

    @classmethod
    def from_json(cls, payload: dict) -> "DigitalSignal":
        return cls(payload["name"], [(t, v) for t, v in payload["transitions"]],
                   payload.get("initial", 0))


@dataclass
class Capture:
    """A multi-channel capture with a shared time base."""

    channels: Dict[str, DigitalSignal] = field(default_factory=dict)
    t0: float = 0.0
    t1: float = 0.0

    def __post_init__(self) -> None:
        if self.channels and not self.t1:
            self.t1 = max(sig.end for sig in self.channels.values())
        if self.channels and not self.t0:
            starts = [sig.start for sig in self.channels.values() if sig.transitions]
            self.t0 = min(starts) if starts else 0.0

    def add(self, signal: DigitalSignal) -> "Capture":
        self.channels[signal.name] = signal
        self.t1 = max(self.t1, signal.end)
        return self

    def get(self, name: str) -> DigitalSignal:
        if name not in self.channels:
            raise KeyError("channel %r not found (have: %s)"
                           % (name, ", ".join(self.channels)))
        return self.channels[name]

    @property
    def duration(self) -> float:
        return max(0.0, self.t1 - self.t0)

    def to_json(self) -> dict:
        return {"t0": self.t0, "t1": self.t1,
                "channels": [sig.to_json() for sig in self.channels.values()]}

    def to_vcd(self, path: Optional[str] = None) -> str:
        return write_vcd(self, path)

    @classmethod
    def from_vcd_text(cls, text: str) -> "Capture":
        return parse_vcd(text)


# ---------------------------------------------------------------------------
# VCD
# ---------------------------------------------------------------------------

def _parse_timescale(tokens: Sequence[str]) -> float:
    """Return nanoseconds per timescale unit."""
    if not tokens:
        return 1.0
    unit = tokens[-1].lower()
    try:
        magnitude = float(tokens[0])
    except ValueError:
        magnitude = 1.0
    factors = {"s": 1e9, "ms": 1e6, "us": 1e3, "ns": 1.0, "ps": 1e-3, "fs": 1e-6}
    return magnitude * factors.get(unit, 1.0)


def parse_vcd(text: str) -> Capture:
    """Minimal but tolerant VCD reader (scalar wires, one scope level)."""
    import re

    ids: Dict[str, str] = {}
    signals: Dict[str, DigitalSignal] = {}
    order: List[str] = []
    scale = 1.0
    t = 0.0
    in_header = True
    tokens = text.split()

    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok == "$timescale":
            j = i + 1
            parts = []
            while j < n and tokens[j] != "$end":
                parts.append(tokens[j])
                j += 1
            scale = _parse_timescale(parts)
            i = j + 1
            continue
        if tok == "$var":
            j = i + 1
            parts = []
            while j < n and tokens[j] != "$end":
                parts.append(tokens[j])
                j += 1
            if len(parts) >= 4:
                ident = parts[2]
                name = parts[3]
                ids[ident] = name
                if name not in signals:
                    signals[name] = DigitalSignal(name)
                    order.append(name)
            i = j + 1
            continue
        if tok in ("$scope", "$upscope", "$comment", "$date", "$version"):
            j = i + 1
            while j < n and tokens[j] != "$end":
                j += 1
            i = j + 1
            continue
        if tok == "$enddefinitions":
            in_header = False
            i += 1
            if i < n and tokens[i] == "$end":
                i += 1
            continue
        if tok == "$end":
            i += 1
            continue
        if tok.startswith("$"):
            # unknown directive: skip to $end
            j = i + 1
            while j < n and tokens[j] != "$end":
                j += 1
            i = j + 1
            continue
        if tok.startswith("#"):
            try:
                t = float(tok[1:]) * scale
            except ValueError:
                pass
            i += 1
            in_header = False
            continue
        if not in_header and tok and tok[0] in "01xzXZ":
            level = 1 if tok[0] == "1" else 0
            ident = tok[1:]
            if ident in ids:
                signals[ids[ident]].add(t, level)
            i += 1
            continue
        if not in_header and tok.startswith("b"):
            # vector change: apply to the least significant bit of each scalar id
            ident = tok[1:].lstrip("01xzXZ")
            if ident in ids:
                bits = tok[1:].split(ident)[0]
                signals[ids[ident]].add(t, 1 if bits and bits[-1] == "1" else 0)
            i += 1
            continue
        i += 1

    cap = Capture(signals)
    cap.t0 = min((s.start for s in signals.values() if s.transitions), default=0.0)
    cap.t1 = max((s.end for s in signals.values()), default=0.0)
    return cap


def write_vcd(cap: Capture, path: Optional[str] = None) -> str:
    ids = {name: chr(33 + i) for i, name in enumerate(cap.channels)}
    lines = ["$timescale 1 ns $end", "$scope module buslens $end"]
    for name, ident in ids.items():
        lines.append("$var wire 1 %s %s $end" % (ident, name))
    lines.append("$upscope $end")
    lines.append("$enddefinitions $end")
    lines.append("#0")
    for name, sig in cap.channels.items():
        lines.append("%d%s" % (sig.initial, ids[name]))

    merged: List[Tuple[float, int, str]] = []
    for name, sig in cap.channels.items():
        for t, lvl in sig.transitions:
            merged.append((t, lvl, name))
    merged.sort(key=lambda x: (x[0], x[2]))
    last_t = None
    for t, lvl, name in merged:
        if last_t != t:
            lines.append("#%d" % round(t))
            last_t = t
        lines.append("%d%s" % (lvl, ids[name]))
    text = "\n".join(lines) + "\n"
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return text


# ---------------------------------------------------------------------------
# CSV: time,CH0,CH1,...   (also accepts a single column of 0/1 samples)
# ---------------------------------------------------------------------------

def parse_csv(text: str, sample_rate_hz: Optional[float] = None) -> Capture:
    rows = [r for r in csv.reader(text.splitlines()) if r and not r[0].startswith("#")]
    if not rows:
        raise ValueError("empty CSV")
    header = [h.strip() for h in rows[0]]
    has_header = not _is_number(header[0])
    data = rows[1:] if has_header else rows

    if len(rows[0]) == 1 and not has_header:
        # single column of 0/1 samples
        bits = [1 if float(v[0]) >= 0.5 else 0 for v in data if v]
        fs = sample_rate_hz or 1e6
        return Capture({"CH0": DigitalSignal.from_samples("CH0", bits, fs)})

    names = header[1:] if has_header else ["CH%d" % i for i in range(len(rows[0]) - 1)]

    sigs = {name: DigitalSignal(name) for name in names}
    for row in data:
        if len(row) < len(names) + 1:
            continue
        try:
            t = float(row[0])
        except ValueError:
            continue
        for i, name in enumerate(names):
            try:
                lvl = 1 if float(row[i + 1]) >= 0.5 else 0
            except ValueError:
                continue
            sigs[name].add(t, lvl)
    return Capture(sigs)


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False
