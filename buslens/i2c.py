"""I2C: edge-accurate decoding, transaction grouping and bus simulation.

Decoder
-------
I2C is defined by *conditions*, not by a bit rate: a START is an SDA falling
edge while SCL is high, everything else is sampled on SCL rising edges.  The
decoder therefore consumes the merged transition list of both lines, which
keeps sub-bit timing information (clock stretching, setup violations) intact.

Simulator
---------
:class:`I2CSim` drives the same waveform model from a device model, including
open-drain wired-AND behaviour (the line is low if either the master or the
slave pulls it low).  Encoder and decoder form a closed loop, which is what
the test suite uses to verify both.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .signal import Capture, DigitalSignal

__all__ = ["decode_i2c", "I2CSim", "I2CDevice", "I2C_PRESETS"]

I2C_PRESETS = {
    "standard": 100_000,
    "fast": 400_000,
    "fast+": 1_000_000,
}


class I2CDevice:
    """Minimal slave interface used by the simulator."""

    address: int = 0x50

    def start(self, read: bool) -> bool:
        """Return True if the address matched (drives ACK)."""
        return True

    def write(self, value: int) -> bool:
        """Consume a byte written by the master; return True to ACK."""
        return True

    def read(self) -> int:
        """Produce the next byte for a master read."""
        return 0xFF

    def stop(self) -> None:
        pass


@dataclass
class _Byte:
    kind: str          # "address" | "data"
    value: int
    ack: bool
    t0: float
    t1: float
    read: bool = False


def decode_i2c(scl: DigitalSignal, sda: DigitalSignal,
               stretch_ns: float = 50_000.0,
               scl_name: Optional[str] = None,
               sda_name: Optional[str] = None,
               glitch_ns: float = 0.0) -> dict:
    """Decode an I2C capture into events, transactions and annotations."""
    scl_name = scl_name or scl.name
    sda_name = sda_name or sda.name

    merged: List[Tuple[float, int, str, int]] = []
    for t, lvl in scl.transitions:
        merged.append((t, 1, "scl", lvl))
    for t, lvl in sda.transitions:
        merged.append((t, 0, "sda", lvl))
    merged.sort(key=lambda e: (e[0], e[1]))

    events: List[dict] = []
    errors: List[dict] = []
    annotations: List[dict] = []
    transactions: List[dict] = []
    stretches: List[dict] = []
    busy_time = 0.0

    in_txn = False
    txn_start = 0.0
    txn_address: Optional[int] = None
    txn_read = False
    txn_bytes: List[int] = []
    txn_events: List[dict] = []
    txn_acked = True
    ten_prefix = False

    bit_in_byte = 0
    shift = 0
    byte_t0 = 0.0
    expecting_address = False
    address_byte_count = 0

    scl_low_since: Optional[float] = None
    scl_periods: List[float] = []
    last_scl_rise: Optional[float] = None
    violated_setup = 0

    # initial state sanity
    if scl.initial != 1:
        errors.append({"t": scl.start, "message": "SCL is not idle-high at capture start",
                       "level": "warning"})

    for t, _prio, ch, lvl in merged:
        if ch == "sda":
            if scl.level_at(t) == 1:
                if lvl == 0:
                    was_in_txn = in_txn
                    if in_txn:
                        # repeated START: close the current transaction
                        transactions.append(_finish_txn(
                            len(transactions) + 1, txn_address, txn_read, txn_bytes,
                            txn_acked, txn_start, t, txn_events))
                        busy_time += t - txn_start
                    in_txn = True
                    txn_start = t
                    txn_address = None
                    txn_read = False
                    txn_bytes = []
                    txn_events = []
                    txn_acked = True
                    ten_prefix = False
                    expecting_address = True
                    address_byte_count = 0
                    bit_in_byte = 0
                    shift = 0
                    ev = {"kind": "start", "t": round(t, 3), "repeated": was_in_txn}
                    events.append(ev)
                    txn_events.append(ev)
                    annotations.append({"channel": sda_name, "t0": round(t, 3),
                                        "t1": round(t, 3), "label": "S", "kind": "start"})
                else:
                    if in_txn:
                        ev = {"kind": "stop", "t": round(t, 3)}
                        events.append(ev)
                        txn_events.append(ev)
                        annotations.append({"channel": sda_name, "t0": round(t, 3),
                                            "t1": round(t, 3), "label": "P", "kind": "stop"})
                        transactions.append(_finish_txn(
                            len(transactions) + 1, txn_address, txn_read, txn_bytes,
                            txn_acked, txn_start, t, txn_events))
                        busy_time += t - txn_start
                        in_txn = False
                        txn_address = None
                        txn_bytes = []
                        expecting_address = False
                    else:
                        errors.append({"t": round(t, 3),
                                       "message": "STOP condition outside a transaction",
                                       "level": "warning"})
            continue

        # SCL edge
        if lvl == 1:
            if scl_low_since is not None:
                low = t - scl_low_since
                if low >= stretch_ns:
                    stretches.append({"t0": round(scl_low_since, 3), "t1": round(t, 3),
                                      "ns": round(low, 1)})
                    annotations.append({"channel": scl_name, "t0": round(scl_low_since, 3),
                                        "t1": round(t, 3), "label": "stretch",
                                        "kind": "stretch"})
                scl_low_since = None
            if last_scl_rise is not None:
                scl_periods.append(t - last_scl_rise)
            last_scl_rise = t

            if not in_txn:
                continue
            if bit_in_byte == 0:
                byte_t0 = t
            sda_level = sda.level_at(t)
            if bit_in_byte < 8:
                shift = (shift << 1) | sda_level
                bit_in_byte += 1
                continue

            # 9th clock: ACK / NACK
            ack = (sda_level == 0)
            # During a master read the last byte is NACKed on purpose: that is
            # how the master terminates the transfer, not a fault.
            expected_nack = txn_read and not expecting_address and len(txn_bytes) > 0
            if not ack and not expected_nack:
                txn_acked = False
                errors.append({"t": round(t, 3),
                               "message": "NACK at byte %d" % (len(txn_bytes) + 1),
                               "level": "error"})
            if expecting_address:
                raw = shift
                is_ten = (raw & 0xF8) == 0xF0 and address_byte_count == 0
                if is_ten:
                    ten_prefix = True
                    txn_address = (raw & 0x06) << 7
                    address_byte_count = 1
                    ev = {"kind": "address", "t": round(byte_t0, 3), "value": raw >> 1,
                          "read": False, "ack": ack, "ten": True, "partial": True}
                    events.append(ev)
                    txn_events.append(ev)
                    annotations.append({"channel": sda_name, "t0": round(byte_t0, 3),
                                        "t1": round(t, 3), "label": "A10",
                                        "kind": "ack" if ack else "nack"})
                    shift = 0
                    bit_in_byte = 0
                    continue
                if ten_prefix and address_byte_count == 1:
                    txn_address = (txn_address or 0) | shift
                    address_byte_count = 2
                    ev = {"kind": "address", "t": round(byte_t0, 3),
                          "value": txn_address, "read": False, "ack": ack, "ten": True}
                    events.append(ev)
                    txn_events.append(ev)
                    annotations.append({"channel": sda_name, "t0": round(byte_t0, 3),
                                        "t1": round(t, 3),
                                        "label": "0x%03X" % txn_address,
                                        "kind": "ack" if ack else "nack"})
                    shift = 0
                    bit_in_byte = 0
                    expecting_address = False
                    continue

                txn_address = raw >> 1
                txn_read = bool(raw & 1)
                ev = {"kind": "address", "t": round(byte_t0, 3),
                      "value": txn_address, "read": txn_read, "ack": ack}
                events.append(ev)
                txn_events.append(ev)
                annotations.append({
                    "channel": sda_name, "t0": round(byte_t0, 3), "t1": round(t, 3),
                    "label": "0x%02X %s" % (txn_address, "R" if txn_read else "W"),
                    "kind": "ack" if ack else "nack",
                })
                expecting_address = False
                if not ack:
                    # no device: the master should issue a STOP/repeated START
                    pass
            else:
                txn_bytes.append(shift)
                ev = {"kind": "data", "t": round(byte_t0, 3), "value": shift,
                      "ack": ack, "read": txn_read}
                events.append(ev)
                txn_events.append(ev)
                annotations.append({
                    "channel": sda_name, "t0": round(byte_t0, 3), "t1": round(t, 3),
                    "label": "%02X" % shift, "kind": "ack" if ack else "nack",
                })
            shift = 0
            bit_in_byte = 0
        else:
            scl_low_since = t

    # unterminated transaction at the end of the capture
    if in_txn:
        end_t = min(sda.end, scl.end)
        transactions.append(_finish_txn(len(transactions) + 1, txn_address, txn_read,
                                        txn_bytes, txn_acked, txn_start, end_t,
                                        txn_events, open_ended=True))
        busy_time += end_t - txn_start
        errors.append({"t": round(txn_start, 3),
                       "message": "capture ended inside a transaction", "level": "warning"})

    clock_hz = None
    if scl_periods:
        clock_hz = round(1e9 / statistics.median(scl_periods), 1)
    span = max(1e-9, scl.end - scl.start)
    bit_time_ns = (1e9 / clock_hz) if clock_hz else None
    avg_setup = None
    if bit_time_ns and bit_time_ns > 0:
        avg_setup = bisect_setup_margin(merged, bit_time_ns)

    stats = {
        "transactions": len(transactions),
        "bytes": sum(len(tx["bytes"]) for tx in transactions),
        "nacks": sum(1 for e in events if e.get("ack") is False),
        "stretches": len(stretches),
        "clockHz": clock_hz,
        "utilization": round(100.0 * busy_time / span, 2),
        "durationUs": round(span / 1000.0, 2),
        "setupMargin": avg_setup,
    }
    return {
        "protocol": "i2c",
        "channels": {"scl": scl_name, "sda": sda_name},
        "config": {"clockHz": clock_hz, "stretchThresholdNs": stretch_ns},
        "events": events,
        "transactions": transactions,
        "annotations": annotations,
        "errors": errors,
        "stretches": stretches,
        "stats": stats,
    }


def bisect_setup_margin(merged: Sequence[Tuple[float, int, str, int]],
                        bit_ns: float) -> Optional[float]:
    """Worst-case SDA setup time before an SCL rising edge, as a fraction of a bit."""
    last_sda = None
    worst = None
    for t, _prio, ch, _lvl in merged:
        if ch == "sda":
            last_sda = t
        elif last_sda is not None:
            margin = (t - last_sda) / bit_ns
            if worst is None or margin < worst:
                worst = margin
    return None if worst is None else round(max(0.0, worst), 3)


def _finish_txn(index: int, address: Optional[int], read: bool, data: List[int],
                acked: bool, t0: float, t1: float, events: List[dict],
                open_ended: bool = False) -> dict:
    kind = "read" if read else "write"
    if address is None:
        kind = "unknown"
    elif not acked and not data:
        kind = "probe"
    return {
        "index": index,
        "address": address,
        "read": read,
        "kind": kind,
        "bytes": list(data),
        "acked": acked,
        "t0": round(t0, 3),
        "t1": round(t1, 3),
        "durationUs": round((t1 - t0) / 1000.0, 2),
        "events": events,
        "openEnded": open_ended,
    }


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

class I2CSim:
    """Build an I2C waveform from a transaction plan and a device model."""

    def __init__(self, device: Optional[I2CDevice] = None, clock_hz: int = 100_000,
                 rise_ns: float = 80.0, idle_ns: float = 3000.0,
                 stretch_ns: float = 0.0, jitter_ns: float = 0.0):
        self.device = device
        self.period = 1e9 / float(clock_hz)
        self.rise = rise_ns
        self.idle = idle_ns
        self.stretch = stretch_ns
        self.jitter = jitter_ns

        self.scl = DigitalSignal("SCL", initial=1)
        self.sda = DigitalSignal("SDA", initial=1)
        self.t = 0.0
        self._rng = 1103515245

    # ------------------------------------------------------------------ utils
    def _rand(self) -> float:
        # deterministic LCG so captures are reproducible
        self._rng = (1103515245 * self._rng + 12345) & 0x7FFFFFFF
        return (self._rng / 0x7FFFFFFF) - 0.5

    def _jitter(self) -> float:
        return self._rng * 0 if not self.jitter else self._rand() * self.jitter

    def _sda(self, t: float, level: int) -> None:
        self.sda.add(t + self._jitter(), level)

    def _scl(self, t: float, level: int) -> None:
        # a rise is slower than a fall; both are modelled by the edge time
        self.scl.add(t, level)

    def _low_phase(self, sda_level: int, extra_low: float = 0.0) -> None:
        """One bit: SDA changes early in the low phase, SCL goes high, then low.

        ``extra_low`` stretches the *low* phase, which is what a slave does when
        it needs more time after an ACK (clock stretching).
        """
        p = self.period
        self._sda(self.t + 0.15 * p, sda_level)
        self._scl(self.t + 0.5 * p + extra_low, 1)
        self._scl(self.t + p + extra_low, 0)
        self.t += p + extra_low

    def _start(self, repeated: bool = False) -> None:
        p = self.period
        if repeated:
            # bring SDA high while SCL is low, then a START condition
            self._sda(self.t + 0.15 * p, 1)
            self._scl(self.t + 0.4 * p, 1)
            self._sda(self.t + 0.65 * p, 0)
            self._scl(self.t + p, 0)
            self.t += p
        else:
            self._sda(self.t + 0.2 * p, 0)
            self._scl(self.t + 0.6 * p, 0)
            self.t += 0.6 * p

    def _stop(self) -> None:
        p = self.period
        self._sda(self.t + 0.15 * p, 0)
        self._scl(self.t + 0.4 * p, 1)
        self._sda(self.t + 0.7 * p, 1)
        self.t += p

    def _clock_byte(self, value: int, master_read: bool, ack_from_device: bool,
                    stretch: bool = False) -> None:
        for i in range(7, -1, -1):
            bit = (value >> i) & 1
            self._low_phase(bit)
        # ACK slot: master releases the line; the device may pull it low
        slot_level = 0 if ack_from_device else 1
        if master_read:
            slot_level = 1  # master NACKs the last read byte by keeping SDA high
        self._low_phase(slot_level, self.stretch if stretch else 0.0)

    # ------------------------------------------------------------- transactions
    def write(self, address: int, data: Sequence[int]) -> None:
        self._start()
        if not self._device_ack(address, False):
            # a real master aborts on an address NACK instead of clocking data
            self._clock_byte((address << 1) | 0, False, False)
            self._stop()
            return
        self._clock_byte((address << 1) | 0, False, True)
        for b in data:
            ack = self.device.write(b) if self.device else False
            self._clock_byte(b, False, ack)
            if not ack:
                break
        self._stop()

    def read(self, address: int, count: int) -> None:
        self._start()
        if not self._device_ack(address, True):
            self._clock_byte((address << 1) | 1, False, False)
            self._stop()
            return
        self._clock_byte((address << 1) | 1, False, True)
        for i in range(count):
            value = self.device.read() if self.device else 0xFF
            last = i == count - 1
            # the master ACKs all but the final byte
            self._clock_byte(value, last, not last, stretch=not last)
        self._stop()

    def write_read(self, address: int, data: Sequence[int], count: int) -> None:
        self._start()
        if not self._device_ack(address, False):
            self._clock_byte((address << 1) | 0, False, False)
            self._stop()
            return
        self._clock_byte((address << 1) | 0, False, True)
        for b in data:
            ack = self.device.write(b) if self.device else False
            self._clock_byte(b, False, ack)
            if not ack:
                self._stop()
                return
        self._start(repeated=True)
        if not self._device_ack(address, True):
            self._clock_byte((address << 1) | 1, False, False)
            self._stop()
            return
        self._clock_byte((address << 1) | 1, False, True)
        for i in range(count):
            value = self.device.read() if self.device else 0xFF
            last = i == count - 1
            self._clock_byte(value, last, not last, stretch=not last)
        self._stop()

    def _device_ack(self, address: int, read: bool) -> bool:
        if self.device is None or self.device.address != address:
            return False
        return self.device.start(read)

    def run(self, plan: dict) -> Capture:
        self.t = self.idle
        for item in plan.get("transactions", []):
            kind = item.get("type", "write")
            addr = int(item.get("address", 0x50))
            if kind == "write":
                self.write(addr, item.get("data", []))
            elif kind == "read":
                self.read(addr, int(item.get("count", 1)))
            elif kind == "write_read":
                self.write_read(addr, item.get("data", []), int(item.get("count", 1)))
            else:
                raise ValueError("unknown transaction type %r" % (kind,))
            self.t += self.idle
        # tail
        self.scl.add(self.t + self.idle, 1)
        self.sda.add(self.t + self.idle, 1)
        cap = Capture({"SCL": self.scl, "SDA": self.sda})
        cap.t0 = 0.0
        cap.t1 = max(self.scl.end, self.sda.end)
        return cap


def encode_i2c(plan: dict, device: Optional[I2CDevice] = None,
               clock_hz: int = 100_000, **kwargs) -> Capture:
    return I2CSim(device, clock_hz, **kwargs).run(plan)
