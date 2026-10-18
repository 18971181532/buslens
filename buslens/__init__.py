"""BusLens - an embedded bus signal workbench.

Decode logic-analyser captures into transactions, registers and timing
verdicts; synthesise realistic captures from device models to test the
decoders against a known-good reference.
"""

from __future__ import annotations

__version__ = "1.0.0"
__author__ = "BusLens contributors"

from .signal import DigitalSignal, Capture, parse_vcd, parse_csv, write_vcd
from .uart import decode_uart, encode_uart, estimate_baud
from .i2c import decode_i2c, I2CSim, I2CDevice
from .spi import decode_spi, encode_spi
from .pwm import decode_pwm, decode_ws2812, encode_pwm, encode_ws2812
from .devices import EEPROM24C02, TMP102, AT24C32, RegisterDevice
from .analyze import analyze, quality_score, i2c_registers
from .demos import demo_list, build_demo

__all__ = [
    "__version__",
    "DigitalSignal", "Capture", "parse_vcd", "parse_csv", "write_vcd",
    "decode_uart", "encode_uart", "estimate_baud",
    "decode_i2c", "I2CSim", "I2CDevice",
    "decode_spi", "encode_spi",
    "decode_pwm", "decode_ws2812", "encode_pwm", "encode_ws2812",
    "EEPROM24C02", "TMP102", "AT24C32", "RegisterDevice",
    "analyze", "quality_score", "i2c_registers",
    "demo_list", "build_demo",
]
