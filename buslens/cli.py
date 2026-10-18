"""Command line interface for BusLens.

    python run.py demos
    python run.py decode --demo i2c_eeprom
    python run.py decode --vcd capture.vcd --protocol spi --cpol 0 --cpha 0
    python run.py export --demo uart_at --out uart.vcd
    python run.py simulate --plan examples/i2c_plan.json --device eeprom --out cap.vcd
    python run.py serve --port 8777
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional, Sequence

from . import __version__, demos, devices, i2c, pwm, spi, uart
from .analyze import analyze as analyze_capture
from .signal import Capture, parse_csv, parse_vcd, write_vcd

__all__ = ["main", "build_parser"]


def _load_capture(args) -> tuple:
    """Return (capture, protocol, config, source, description)."""
    if getattr(args, "demo", None):
        built = demos.build_demo(args.demo)
        cfg = _override(built["config"], args)
        return built["capture"], built["protocol"], cfg, "demo:" + args.demo, built["description"]

    if getattr(args, "vcd", None):
        with open(args.vcd, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        cap = parse_vcd(text)
        source = os.path.basename(args.vcd)
    elif getattr(args, "csv", None):
        with open(args.csv, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        cap = parse_csv(text, sample_rate_hz=args.sample_rate)
        source = os.path.basename(args.csv)
    else:
        raise SystemExit("error: provide --demo, --vcd or --csv")

    protocol = args.protocol
    if not protocol:
        guess = _guess_protocol(cap)
        if not guess:
            raise SystemExit("error: cannot guess the protocol, pass --protocol")
        protocol = guess
    cfg = _override(_default_config(protocol), args)
    return cap, protocol, cfg, source, ""


def _default_config(protocol: str) -> dict:
    if protocol == "i2c":
        return {"scl": "SCL", "sda": "SDA"}
    if protocol == "spi":
        return {"sck": "SCK", "mosi": "MOSI", "miso": "MISO", "cs": "CS",
                "cpol": 0, "cpha": 0}
    if protocol == "uart":
        return {"channels": ["TX"], "baud": None}
    if protocol == "pwm":
        return {"channel": "PWM"}
    return {"channel": "DIN"}


def _override(config: dict, args) -> dict:
    cfg = dict(config)
    if getattr(args, "scl", None):
        cfg["scl"] = args.scl
    if getattr(args, "sda", None):
        cfg["sda"] = args.sda
    if getattr(args, "sck", None):
        cfg["sck"] = args.sck
    if getattr(args, "mosi", None):
        cfg["mosi"] = args.mosi
    if getattr(args, "miso", None):
        cfg["miso"] = args.miso
    if getattr(args, "cs", None):
        cfg["cs"] = args.cs
    if getattr(args, "channel", None):
        cfg["channel"] = args.channel
        if cfg.get("channels"):
            cfg["channels"] = [args.channel]
    if getattr(args, "baud", None):
        cfg["baud"] = args.baud
    if getattr(args, "parity", None):
        cfg["parity"] = args.parity
    if getattr(args, "cpol", None) is not None:
        cfg["cpol"] = args.cpol
    if getattr(args, "cpha", None) is not None:
        cfg["cpha"] = args.cpha
    if getattr(args, "channels", None):
        cfg["channels"] = args.channels
    if getattr(args, "stretch_ns", None):
        cfg["stretchNs"] = args.stretch_ns
    return cfg


def _guess_protocol(cap: Capture) -> Optional[str]:
    names = {n.upper() for n in cap.channels}
    if {"SCL", "SDA"} <= names:
        return "i2c"
    if {"SCK", "MOSI"} <= names:
        return "spi"
    if names & {"TX", "RX", "UART"}:
        return "uart"
    if names & {"DIN", "LED"}:
        return "ws2812"
    if len(cap.channels) == 1:
        return "uart"
    return None


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _hexbytes(values: Sequence[int]) -> str:
    return " ".join("%02X" % v for v in values[:24]) + (" …" if len(values) > 24 else "")


def _print_i2c(result: dict) -> None:
    stats = result["stats"]
    print("I2C  %s  ·  %d transaction(s)  ·  %d byte(s)  ·  quality %s (%.0f)"
          % (_hz(stats.get("clockHz")), stats.get("transactions", 0),
             stats.get("bytes", 0), result["quality"]["grade"], result["quality"]["score"]))
    print("-" * 78)
    print("%-3s %-4s %-6s %-28s %-5s %10s" % ("#", "R/W", "ADDR", "DATA", "ACK", "TIME"))
    for tx in result["decoded"]["transactions"]:
        data = _hexbytes(tx["bytes"]) if tx["bytes"] else "—"
        print("%-3d %-4s %-6s %-28s %-5s %9.1f us"
              % (tx["index"], "R" if tx["read"] else "W",
                 "0x%02X" % tx["address"] if tx["address"] is not None else "—",
                 data, "ACK" if tx["acked"] else "NACK", tx["durationUs"]))
    if result["registers"]:
        print("-" * 78)
        print("inferred register view:")
        for row in result["registers"][:16]:
            print("  %s[%s] = %s   (%s)"
                  % (row["address"], row["register"], row["value"], row["source"]))
    if stats.get("stretches"):
        print("  clock stretching: %d event(s)" % stats["stretches"])


def _print_spi(result: dict) -> None:
    stats = result["stats"]
    cfg = result["config"]
    print("SPI  %s  ·  Mode %d%d  ·  %d frame(s)  ·  quality %s (%.0f)"
          % (_hz(stats.get("clockHz")), cfg.get("cpol", 0), cfg.get("cpha", 0),
             stats.get("frames", 0), result["quality"]["grade"], result["quality"]["score"]))
    print("-" * 78)
    for frame in result["decoded"]["frames"]:
        print("frame %d  (%.1f us)" % (frame["index"], (frame["t1"] - frame["t0"]) / 1000.0))
        for w in frame["words"]:
            print("   #%-3d MOSI %s   MISO %s"
                  % (w["index"],
                     "0x%02X" % w["mosi"] if w["mosi"] is not None else "  -- ",
                     "0x%02X" % w["miso"] if w["miso"] is not None else "  -- "))


def _print_uart(result: dict) -> None:
    stats = result["stats"]
    print("UART  %.1f kbaud  ·  %d frame(s)  ·  %d byte(s)  ·  quality %s (%.0f)"
          % (1e6 / stats.get("bitTimeNs", 1e6), stats.get("frames", 0),
             stats.get("bytes", 0), result["quality"]["grade"], result["quality"]["score"]))
    print("-" * 78)
    for name, text in result["decoded"].get("text", {}).items():
        print("%s: %r" % (name, text))


def _print_pwm(result: dict) -> None:
    stats = result["stats"]
    print("%s  %.3f Hz  ·  duty %.2f%%  ·  high %.1f us  ·  jitter %.3f%%"
          % ("Servo" if result["decoded"].get("kind") == "servo" else "PWM",
             stats.get("frequencyHz", 0), stats.get("dutyPct", 0),
             stats.get("highUs", 0), stats.get("frequencyJitterPct", 0)))
    if stats.get("missingPulses"):
        print("  missing pulses: %d" % stats["missingPulses"])


def _print_ws2812(result: dict) -> None:
    stats = result["stats"]
    print("WS2812  %s  ·  %d pixel(s)  ·  T0H %.0f ns / T1H %.0f ns"
          % (_hz(stats.get("bitRateHz")), stats.get("pixels", 0),
             stats.get("zeroHighNs") or 0, stats.get("oneHighNs") or 0))
    for frame in result["decoded"]["frames"]:
        print("  frame %d: %s" % (frame["index"],
                                  " ".join(p["hex"] for p in frame["pixels"])))


def _hz(value) -> str:
    if not value:
        return "—"
    value = float(value)
    if value >= 1e6:
        return "%.2f MHz" % (value / 1e6)
    if value >= 1e3:
        return "%.1f kHz" % (value / 1e3)
    return "%.0f Hz" % value


PRINTERS = {"i2c": _print_i2c, "spi": _print_spi, "uart": _print_uart,
            "pwm": _print_pwm, "ws2812": _print_ws2812}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_demos(args) -> int:
    for item in demos.demo_list():
        print("%-12s %-7s %-28s %s"
              % (item["id"], item["protocol"], item["name"], ",".join(item["channels"])))
    return 0


def cmd_decode(args) -> int:
    cap, protocol, cfg, source, description = _load_capture(args)
    result = analyze_capture(cap, protocol, cfg, source=source, description=description)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    PRINTERS[protocol](result)
    if result["errors"]:
        print("-" * 78)
        for err in result["errors"][:8]:
            print("  ! %-52s @ %.1f us" % (err["message"], err["t"] / 1000.0))
    print("-" * 78)
    print("capture: %d channel(s), %.1f us, %d transitions"
          % (len(cap.channels), cap.duration / 1000.0,
             sum(len(s) for s in cap.channels.values())))
    if args.save:
        with open(args.save, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)
        print("result written to %s" % args.save)
    return 0


def cmd_export(args) -> int:
    if args.demo:
        built = demos.build_demo(args.demo)
        cap = built["capture"]
    else:
        cap, _p, _c, _s, _d = _load_capture(args)
    text = write_vcd(cap, args.out)
    print("wrote %s (%d channels, %d transitions)"
          % (args.out, len(cap.channels), sum(len(s) for s in cap.channels.values())))
    if args.csv:
        with open(args.csv, "w", encoding="utf-8") as fh:
            names = list(cap.channels)
            start, end = cap.t0, cap.t1
            step = max(1.0, (end - start) / 2000.0)
            fh.write("t_ns," + ",".join(names) + "\n")
            t = start
            while t <= end:
                fh.write("%.1f," % t + ",".join(str(cap.channels[n].level_at(t)) for n in names) + "\n")
                t += step
        print("wrote %s" % args.csv)
    return 0


def cmd_simulate(args) -> int:
    with open(args.plan, "r", encoding="utf-8") as fh:
        plan = json.load(fh)
    dev = devices.device_by_name(args.device) if args.device else None
    source = plan.get("protocol", "i2c")
    if source == "i2c":
        cap = i2c.I2CSim(dev, clock_hz=args.clock, rise_ns=args.rise).run(plan)
    elif source == "spi":
        cap = spi.encode_spi(plan.get("mosi", []), plan.get("miso"),
                             sck_hz=args.clock, cpol=plan.get("cpol", 0),
                             cpha=plan.get("cpha", 0))
    elif source == "uart":
        tx = uart.encode_uart(bytes(plan.get("tx", [])), baud=args.clock)
        rx = uart.encode_uart(bytes(plan.get("rx", [])), baud=args.clock, name="RX")
        cap = Capture({"TX": tx, "RX": rx})
    elif source == "ws2812":
        cap = Capture({"DIN": pwm.encode_ws2812(plan.get("pixels", []))})
    else:
        raise SystemExit("error: plan protocol must be i2c/spi/uart/ws2812")
    write_vcd(cap, args.out)
    print("wrote %s (%d channels, %.1f us)"
          % (args.out, len(cap.channels), cap.duration / 1000.0))
    return 0


def cmd_serve(args) -> int:
    from .server import serve
    serve(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="buslens",
                                description="BusLens - embedded bus signal workbench",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__)
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("demos", help="list the built-in demo captures")
    d.set_defaults(func=cmd_demos)

    d = sub.add_parser("decode", help="decode a capture")
    d.add_argument("--demo", choices=[x["id"] for x in demos.demo_list()])
    d.add_argument("--vcd")
    d.add_argument("--csv")
    d.add_argument("--protocol", choices=["i2c", "spi", "uart", "pwm", "ws2812"])
    d.add_argument("--scl"), d.add_argument("--sda")
    d.add_argument("--sck"), d.add_argument("--mosi")
    d.add_argument("--miso"), d.add_argument("--cs")
    d.add_argument("--channel")
    d.add_argument("--channels", nargs="+")
    d.add_argument("--baud", type=int)
    d.add_argument("--parity", choices=["none", "even", "odd"])
    d.add_argument("--cpol", type=int, choices=[0, 1])
    d.add_argument("--cpha", type=int, choices=[0, 1])
    d.add_argument("--stretch-ns", type=float, dest="stretch_ns")
    d.add_argument("--sample-rate", type=float, dest="sample_rate",
                   help="sample rate for single-column CSV input")
    d.add_argument("--json", action="store_true")
    d.add_argument("--save", help="write the analysis as JSON")
    d.set_defaults(func=cmd_decode)

    d = sub.add_parser("export", help="export a capture as VCD/CSV")
    d.add_argument("--demo", choices=[x["id"] for x in demos.demo_list()])
    d.add_argument("--vcd"), d.add_argument("--csv")
    d.add_argument("--out", required=True)
    d.set_defaults(func=cmd_export)

    d = sub.add_parser("simulate", help="generate a capture from a transaction plan")
    d.add_argument("--plan", required=True)
    d.add_argument("--device", default="eeprom")
    d.add_argument("--clock", type=float, default=400_000)
    d.add_argument("--rise", type=float, default=80.0)
    d.add_argument("--out", required=True)
    d.set_defaults(func=cmd_simulate)

    d = sub.add_parser("serve", help="start the local web workbench")
    d.add_argument("--host", default="127.0.0.1")
    d.add_argument("--port", type=int, default=8777)
    d.add_argument("--no-browser", action="store_true")
    d.set_defaults(func=cmd_serve)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
