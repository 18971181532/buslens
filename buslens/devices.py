"""Virtual slave devices used to synthesise realistic bus traffic.

These are small behavioural models, not cycle-accurate ones: they implement
exactly the register semantics a firmware engineer cares about, which is enough
to generate a capture that a decoder must be able to reconstruct.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .i2c import I2CDevice

__all__ = ["RegisterDevice", "EEPROM24C02", "TMP102", "AT24C32", "device_by_name"]


class RegisterDevice(I2CDevice):
    """A slave with a register pointer, auto-increment and optional page wrap."""

    def __init__(self, address: int, registers: Optional[Dict[int, int]] = None,
                 size: int = 256, page_size: int = 0, writable: bool = True,
                 name: str = "register device"):
        self.address = address
        self.size = size
        self.page_size = page_size
        self.writable = writable
        self.name = name
        self.registers: Dict[int, int] = {i: 0 for i in range(size)}
        if registers:
            for k, v in registers.items():
                self.registers[k % size] = v & 0xFF
        self.pointer = 0
        self._first = True
        self.writes: List[tuple] = []

    # ------------------------------------------------------------------ slave
    def start(self, read: bool) -> bool:
        self._first = True
        return True

    def write(self, value: int) -> bool:
        value &= 0xFF
        if self._first:
            self.pointer = value % self.size
            self._first = False
        else:
            if not self.writable:
                return False
            self.registers[self.pointer] = value
            self.writes.append((self.pointer, value))
            self._advance()
        return True

    def read(self) -> int:
        value = self.registers.get(self.pointer, 0)
        self._advance()
        return value

    def _advance(self) -> None:
        nxt = self.pointer + 1
        if self.page_size and self.pointer // self.page_size != nxt // self.page_size:
            nxt = (self.pointer // self.page_size) * self.page_size
        self.pointer = nxt % self.size

    def snapshot(self) -> Dict[int, int]:
        return dict(self.registers)


class EEPROM24C02(RegisterDevice):
    """24C02: 256 x 8, page size 8, address 0x50, erased state 0xFF."""

    def __init__(self, address: int = 0x50, page_size: int = 8):
        super().__init__(address, size=256, page_size=page_size, name="24C02 EEPROM")
        self.registers = {i: 0xFF for i in range(256)}


class AT24C32(EEPROM24C02):
    """24C32: 4 KiB, 32-byte pages, 2-byte address (simplified to 12 bits)."""

    def __init__(self, address: int = 0x50):
        super().__init__(address, page_size=32)
        self.size = 4096
        self.registers = {i: 0xFF for i in range(4096)}


class TMP102(RegisterDevice):
    """TMP102-style temperature sensor at 0x48/0x49.

    Register 0x00 returns a 12-bit left-justified temperature (0.0625 degC/LSB),
    register 0x01 is the configuration word.  Writing the configuration while
    the sensor is in shutdown lets you inject a temperature for demos.
    """

    def __init__(self, address: int = 0x48, celsius: float = 25.0625):
        super().__init__(address, size=5, page_size=0, name="TMP102 sensor")
        self.celsius = celsius
        # register map: 0x00 temp MSB, 0x01 temp LSB, 0x02 config, 0x03/0x04 limits
        self.registers[0x02] = 0x60
        self.registers[0x03] = 0xA0
        self._refresh()

    def _refresh(self) -> None:
        # 12-bit left-justified: byte0 = bits 11..4, byte1 = bits 3..0 << 4
        raw = int(round(self.celsius / 0.0625)) & 0x0FFF
        self.registers[0x00] = (raw >> 4) & 0xFF
        self.registers[0x01] = (raw & 0x0F) << 4


def device_by_name(name: str) -> Optional[RegisterDevice]:
    name = (name or "").lower()
    if name in ("eeprom", "24c02", "eeprom24c02"):
        return EEPROM24C02()
    if name in ("24c32", "at24c32"):
        return AT24C32()
    if name in ("tmp102", "sensor", "temp"):
        return TMP102()
    if name in ("none", "missing", ""):
        return None
    raise ValueError("unknown device %r" % (name,))
