"""Ready-made captures used by the UI, the CLI and the test-suite.

Every demo is generated on demand from a device model plus a transaction plan,
so a 3 kB VCD never has to be committed - and the same code path is what the
round-trip tests exercise.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from . import pwm, spi, uart
from .devices import EEPROM24C02, TMP102, device_by_name
from .i2c import I2CSim
from .signal import Capture, DigitalSignal

__all__ = ["DEMOS", "demo_list", "build_demo"]


def _shift(sig: DigitalSignal, dt: float) -> DigitalSignal:
    return DigitalSignal(sig.name, [(t + dt, v) for t, v in sig.transitions], sig.initial)


def _merge(caps: Sequence[Capture], gap_ns: float = 2000.0) -> Capture:
    """Concatenate captures in time, keeping every channel name."""
    out = Capture()
    offset = 0.0
    for cap in caps:
        length = cap.t1
        for name, sig in cap.channels.items():
            target = out.channels.get(name)
            if target is None:
                target = DigitalSignal(name, initial=sig.initial)
                out.channels[name] = target
            for t, v in sig.transitions:
                target.add(t + offset, v)
        offset += length + gap_ns
    out.t0 = 0.0
    out.t1 = offset
    return out


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _i2c_eeprom() -> dict:
    dev = EEPROM24C02()
    plan = {"transactions": [
        {"type": "write", "address": 0x50, "data": [0x00, 0x4C, 0x4E, 0x53]},   # magic
        {"type": "write", "address": 0x50, "data": [0x10, 0x11, 0x22, 0x33, 0x44]},
        {"type": "write_read", "address": 0x50, "data": [0x10], "count": 4},
        {"type": "write", "address": 0x51, "data": [0x00]},                      # missing device
    ]}
    cap = I2CSim(dev, clock_hz=400_000).run(plan)
    return {
        "capture": cap,
        "protocol": "i2c",
        "config": {"scl": "SCL", "sda": "SDA"},
        "description": "24C02 EEPROM：写配置块 → 回读 → 访问不存在的 0x51（观察 NACK）。",
        "device": dev,
    }


def _i2c_sensor() -> dict:
    dev = TMP102(celsius=26.5)
    plan = {"transactions": [
        {"type": "write", "address": 0x48, "data": [0x02, 0x60]},
        {"type": "write_read", "address": 0x48, "data": [0x00], "count": 2},
        {"type": "write_read", "address": 0x48, "data": [0x00], "count": 2},
    ]}
    cap = I2CSim(dev, clock_hz=100_000, stretch_ns=120_000).run(plan)
    return {
        "capture": cap,
        "protocol": "i2c",
        "config": {"scl": "SCL", "sda": "SDA", "stretchNs": 50_000},
        "description": "TMP102 温度传感器：写配置寄存器、连续读两次温度；从机在 ACK 后拉低 SCL 做时钟拉伸。",
        "device": dev,
    }


def _spi_flash() -> dict:
    jedec = spi.encode_spi([0x9F, 0x00, 0x00], [0xEF, 0x40, 0x18], sck_hz=4_000_000)
    status = spi.encode_spi([0x05, 0x00], [0x00, 0x00], sck_hz=4_000_000)
    cap = _merge([jedec, status])
    return {
        "capture": cap,
        "protocol": "spi",
        "config": {"sck": "SCK", "mosi": "MOSI", "miso": "MISO", "cs": "CS",
                   "cpol": 0, "cpha": 0, "bits": 8, "order": "msb"},
        "description": "SPI NOR Flash：Mode 0 读 JEDEC ID（EF 40 18）与状态寄存器。",
    }


def _uart_at() -> dict:
    tx = uart.encode_uart(b"AT+VER?\r\n", 115200, name="TX")
    rx = uart.encode_uart(b"BusLens 1.0\r\nOK\r\n", 115200, name="RX")
    rx = _shift(rx, 900_000)          # ~0.9 ms response latency
    cap = Capture({"TX": tx, "RX": rx})
    cap.t0 = 0.0
    cap.t1 = max(tx.end, rx.end)
    return {
        "capture": cap,
        "protocol": "uart",
        "config": {"channels": ["TX", "RX"], "baud": None, "parity": "none"},
        "description": "UART 115200 8N1：AT 指令与模块应答，自动波特率不会用到配置值。",
    }


def _pwm_servo() -> dict:
    period = 20_000_000.0  # 50 Hz
    sig = DigitalSignal("PWM", initial=0)
    t = 0.0
    for i in range(24):
        width = 1_000_000.0 + i * (1_000_000.0 / 23.0)   # 1.0 ms -> 2.0 ms
        high = width + (12_000.0 if i % 5 == 0 else 0.0)
        sig.add(t, 1)
        sig.add(t + high, 0)
        t += period
    cap = Capture({"PWM": sig})
    return {
        "capture": cap,
        "protocol": "pwm",
        "config": {"channel": "PWM"},
        "description": "50 Hz 舵机扫掠：脉宽 1.0 → 2.0 ms，每 5 个脉冲注入 12 µs 抖动。",
    }


def _ws2812() -> dict:
    pixels = [
        (255, 0, 0), (255, 64, 0), (255, 160, 0), (128, 255, 0),
        (0, 255, 64), (0, 128, 255), (64, 0, 255), (255, 0, 128),
    ]
    cap = Capture({"DIN": pwm.encode_ws2812(pixels)})
    return {
        "capture": cap,
        "protocol": "ws2812",
        "config": {"channel": "DIN", "thresholdNs": 550},
        "description": "WS2812B：8 颗灯珠的 GRB 码流，T0H=350 ns / T1H=700 ns。",
    }


def _i2c_stretch_nack() -> dict:
    dev = device_by_name("eeprom")
    plan = {"transactions": [
        {"type": "write_read", "address": 0x50, "data": [0x20], "count": 6},
        {"type": "write", "address": 0x50, "data": [0x40, 0xAA, 0xBB]},
    ]}
    cap = I2CSim(dev, clock_hz=100_000, stretch_ns=90_000).run(plan)
    return {
        "capture": cap,
        "protocol": "i2c",
        "config": {"scl": "SCL", "sda": "SDA", "stretchNs": 40_000},
        "description": "长时间时钟拉伸：从机每读完一个字节把 SCL 拉低 90 µs，用于检验拉伸统计与超时判定。",
        "device": dev,
    }


_BUILDERS = {
    "i2c_eeprom": _i2c_eeprom,
    "i2c_sensor": _i2c_sensor,
    "i2c_stretch": _i2c_stretch_nack,
    "spi_flash": _spi_flash,
    "uart_at": _uart_at,
    "pwm_servo": _pwm_servo,
    "ws2812": _ws2812,
}

DEMOS: List[dict] = [
    {"id": "i2c_eeprom", "protocol": "i2c", "name": "I²C · EEPROM 写入与回读",
     "channels": ["SCL", "SDA"]},
    {"id": "i2c_sensor", "protocol": "i2c", "name": "I²C · 温度传感器 + 时钟拉伸",
     "channels": ["SCL", "SDA"]},
    {"id": "spi_flash", "protocol": "spi", "name": "SPI · Flash JEDEC ID",
     "channels": ["CS", "SCK", "MOSI", "MISO"]},
    {"id": "uart_at", "protocol": "uart", "name": "UART · AT 指令往返",
     "channels": ["TX", "RX"]},
    {"id": "pwm_servo", "protocol": "pwm", "name": "PWM · 舵机扫掠",
     "channels": ["PWM"]},
    {"id": "ws2812", "protocol": "ws2812", "name": "WS2812 · 8 像素灯带",
     "channels": ["DIN"]},
    {"id": "i2c_stretch", "protocol": "i2c", "name": "I²C · 长时钟拉伸",
     "channels": ["SCL", "SDA"]},
]


def demo_list() -> List[dict]:
    return [dict(item) for item in DEMOS]


def build_demo(demo_id: str) -> dict:
    if demo_id not in _BUILDERS:
        raise KeyError("unknown demo %r (have: %s)"
                       % (demo_id, ", ".join(sorted(_BUILDERS))))
    built = _BUILDERS[demo_id]()
    built["id"] = demo_id
    return built
