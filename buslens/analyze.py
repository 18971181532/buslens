"""Turn raw captures into an engineering view: transactions, registers,
timing quality and a single normalised result object for the UI / CLI."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from . import i2c, pwm, spi, uart
from .signal import Capture, DigitalSignal

__all__ = ["analyze", "i2c_registers", "quality_score", "PROTOCOLS"]

PROTOCOLS = ("i2c", "spi", "uart", "pwm", "ws2812")


def analyze(capture: Capture, protocol: str, config: Optional[dict] = None,
            source: str = "", description: str = "") -> dict:
    config = dict(config or {})
    protocol = protocol.lower()
    if protocol not in PROTOCOLS:
        raise ValueError("unsupported protocol %r" % (protocol,))

    if protocol == "i2c":
        scl_name = config.get("scl", "SCL")
        sda_name = config.get("sda", "SDA")
        decoded = i2c.decode_i2c(capture.get(scl_name), capture.get(sda_name),
                                 stretch_ns=float(config.get("stretchNs", 50_000.0)))
        decoded["registers"] = i2c_registers(decoded["transactions"])
        decoded["settled"] = _i2c_register_map(decoded["registers"])

    elif protocol == "spi":
        sck = capture.get(config.get("sck", "SCK"))
        mosi = capture.get(config["mosi"]) if config.get("mosi", "MOSI") in capture.channels else None
        miso = capture.get(config["miso"]) if config.get("miso", "MISO") in capture.channels else None
        cs = capture.get(config["cs"]) if config.get("cs", "CS") in capture.channels else None
        decoded = spi.decode_spi(sck, mosi, miso, cs,
                                 cpol=int(config.get("cpol", 0)),
                                 cpha=int(config.get("cpha", 0)),
                                 bits=int(config.get("bits", 8)),
                                 order=config.get("order", "msb"))

    elif protocol == "uart":
        channels = config.get("channels") or [config.get("channel", "TX")]
        streams = []
        annotations: List[dict] = []
        errors: List[dict] = []
        frames_total = 0
        for name in channels:
            if name not in capture.channels:
                continue
            dec = uart.decode_uart(capture.get(name), baud=config.get("baud"),
                                   data_bits=int(config.get("dataBits", 8)),
                                   parity=config.get("parity", "none"),
                                   stop_bits=float(config.get("stopBits", 1.0)),
                                   channel=name)
            streams.append(dec)
            annotations.extend(dec["annotations"])
            errors.extend(dec["errors"])
            frames_total += dec["stats"]["frames"]
        if not streams:
            raise KeyError("no configured UART channel found in capture")
        bauds = sorted({s["config"]["baud"] for s in streams})
        decoded = {
            "protocol": "uart",
            "channels": channels,
            "streams": streams,
            "config": {"baud": bauds[0] if len(bauds) == 1 else bauds,
                       "parity": config.get("parity", "none")},
            "baudSource": streams[0]["baudSource"] if len(streams) == 1 else "mixed",
            "frames": [f for s in streams for f in s["frames"]],
            "annotations": annotations,
            "errors": errors,
            "text": {s["channel"]: s["text"] for s in streams},
            "stats": {
                "frames": frames_total,
                "bytes": sum(s["stats"]["bytes"] for s in streams),
                "errors": len(errors),
                "bitTimeNs": streams[0]["stats"]["bitTimeNs"],
                "baudConfidence": min(s["baudConfidence"] for s in streams),
                "throughputBps": round(sum(s["stats"]["bytes"] for s in streams) * 10.0
                                       / max(1e-9, capture.duration / 1e9), 1),
            },
        }
        decoded["registers"] = []

    elif protocol == "pwm":
        decoded = pwm.decode_pwm(capture.get(config.get("channel", "PWM")))
        decoded["registers"] = []

    else:  # ws2812
        decoded = pwm.decode_ws2812(capture.get(config.get("channel", "DIN")),
                                    threshold_ns=float(config.get("thresholdNs", 550.0)),
                                    reset_ns=float(config.get("resetNs", 50_000.0)))
        decoded["registers"] = []

    annotations = decoded.get("annotations", [])
    errors = decoded.get("errors", [])
    stats = decoded.get("stats", {})

    warnings = [e for e in errors if e.get("level") == "warning"]
    real_errors = [e for e in errors if e.get("level") != "warning"]
    quality = quality_score(decoded, capture)

    result = {
        "ok": True,
        "protocol": protocol,
        "source": source,
        "description": description,
        "capture": capture.to_json(),
        "config": decoded.get("config", config),
        "decoded": decoded,
        "annotations": annotations,
        "errors": errors,
        "warnings": warnings,
        "quality": quality,
        "stats": stats,
        "registers": decoded.get("registers", []),
        "registerMap": decoded.get("settled", {}),
        "summary": _summary(protocol, decoded),
    }
    return result


# ---------------------------------------------------------------------------
# I2C register reconstruction
# ---------------------------------------------------------------------------

def i2c_registers(transactions: Sequence[dict]) -> List[dict]:
    """Infer a register view from write/read transaction pairs.

    This is a heuristic on purpose: I2C itself has no notion of registers.  The
    common convention (first written byte = register pointer, subsequent bytes
    = data) is applied, and every row is tagged with how it was obtained so the
    UI can label reads as *inferred* rather than measured.
    """
    out: List[dict] = []
    pointer: Dict[int, int] = {}
    last_write: Optional[dict] = None

    for tx in transactions:
        addr = tx.get("address")
        if addr is None:
            continue
        data = tx.get("bytes", [])
        if not tx["read"]:
            if data:
                pointer[addr] = data[0]
                for i, value in enumerate(data[1:]):
                    reg = (data[0] + i) % 256
                    out.append({"address": "0x%02X" % addr, "register": "0x%02X" % reg,
                                "value": "0x%02X" % value, "decimal": value,
                                "source": "write", "t": tx["t0"]})
                    pointer[addr] = (reg + 1) % 256
                last_write = tx
        else:
            reg = pointer.get(addr, 0)
            for i, value in enumerate(data):
                out.append({"address": "0x%02X" % addr,
                            "register": "0x%02X" % ((reg + i) % 256),
                            "value": "0x%02X" % value, "decimal": value,
                            "source": "read", "t": tx["t0"]})
            pointer[addr] = (reg + len(data)) % 256
            last_write = None
    return out


def _i2c_register_map(rows: Sequence[dict]) -> Dict[str, Dict[str, dict]]:
    table: Dict[str, Dict[str, dict]] = {}
    for row in rows:
        table.setdefault(row["address"], {})[row["register"]] = {
            "value": row["value"], "source": row["source"], "t": row["t"]}
    return table


# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------

def quality_score(decoded: dict, capture: Capture) -> dict:
    score = 100.0
    reasons: List[str] = []
    errors = decoded.get("errors", [])
    real = [e for e in errors if e.get("level") != "warning"]
    warn = [e for e in errors if e.get("level") == "warning"]
    score -= 12.0 * len(real)
    score -= 3.0 * len(warn)
    if real:
        reasons.append("%d protocol error(s)" % len(real))
    if warn:
        reasons.append("%d warning(s)" % len(warn))

    stats = decoded.get("stats", {})
    protocol = decoded.get("protocol")
    if protocol == "uart":
        conf = decoded.get("baudConfidence", 1.0)
        if conf < 0.9:
            score -= min(15.0, (0.9 - conf) * 100)
            reasons.append("weak baud-rate estimate (%.0f%%)" % (conf * 100))
    if protocol == "i2c":
        margin = stats.get("setupMargin")
        if margin is not None and margin < 0.08:
            score -= 10.0
            reasons.append("SDA setup margin only %.1f%% of a bit" % (margin * 100))
        if stats.get("stretches", 0):
            score -= min(6.0, stats["stretches"] * 1.5)
            reasons.append("%d clock stretch(es)" % stats["stretches"])
    if protocol == "pwm":
        jitter = stats.get("frequencyJitterPct", 0)
        if jitter > 1.0:
            score -= min(20.0, jitter * 2)
            reasons.append("period jitter %.2f%%" % jitter)
        if stats.get("missingPulses", 0):
            score -= min(15.0, stats["missingPulses"] * 5)
            reasons.append("%d missing pulse(s)" % stats["missingPulses"])
    if protocol == "ws2812" and stats.get("ambiguousBits", 0):
        score -= min(10.0, stats["ambiguousBits"] * 0.5)
        reasons.append("%d bit(s) near the decision threshold" % stats["ambiguousBits"])
    if protocol == "spi" and any(w.get("partial") for w in decoded.get("words", [])):
        score -= 8.0
        reasons.append("incomplete SPI word")

    score = max(0.0, min(100.0, score))
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
    return {"score": round(score, 1), "grade": grade, "reasons": reasons}


def _summary(protocol: str, decoded: dict) -> dict:
    stats = decoded.get("stats", {})
    if protocol == "i2c":
        return {
            "primary": "%d 事务 · %d 字节" % (stats.get("transactions", 0), stats.get("bytes", 0)),
            "secondary": ["时钟 %s" % _hz(stats.get("clockHz")),
                          "NACK %d" % stats.get("nacks", 0),
                          "占空比 %.1f%%" % stats.get("utilization", 0)],
        }
    if protocol == "spi":
        return {
            "primary": "%d 帧 · %d 字" % (stats.get("frames", 0), stats.get("words", 0)),
            "secondary": ["SCK %s" % _hz(stats.get("clockHz")),
                          "Mode %d" % (decoded.get("config", {}).get("cpol", 0) * 2
                                       + decoded.get("config", {}).get("cpha", 0))],
        }
    if protocol == "uart":
        return {
            "primary": "%d 帧 · %d 字节" % (stats.get("frames", 0), stats.get("bytes", 0)),
            "secondary": ["%.1f kbaud" % (1e6 / stats.get("bitTimeNs", 1e6)),
                          "校验 %s" % decoded.get("config", {}).get("parity", "none")],
        }
    if protocol == "pwm":
        return {
            "primary": "%s · %.2f Hz" % (("舵机脉冲" if decoded.get("kind") == "servo" else "PWM"),
                                          stats.get("frequencyHz", 0)),
            "secondary": ["占空比 %.2f%%" % stats.get("dutyPct", 0),
                          "抖动 %.2f%%" % stats.get("frequencyJitterPct", 0)],
        }
    return {
        "primary": "%d 像素" % stats.get("pixels", 0),
        "secondary": ["%s bit/s" % _hz(stats.get("bitRateHz")),
                      "T0H %.0f ns" % (stats.get("zeroHighNs") or 0)],
    }


def _hz(value) -> str:
    if not value:
        return "—"
    value = float(value)
    if value >= 1e6:
        return "%.2f MHz" % (value / 1e6)
    if value >= 1e3:
        return "%.1f kHz" % (value / 1e3)
    return "%.0f Hz" % value
