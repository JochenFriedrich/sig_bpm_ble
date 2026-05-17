"""Parser for the Bluetooth SIG Blood Pressure Measurement characteristic (0x2A35).

The characteristic byte layout is defined in the GATT Specification Supplement
and the Blood Pressure Service specification v1.1.1:

  Octet 0      – Flags
  Octets 1-2   – Systolic      (SFLOAT, units per flag bit 0)
  Octets 3-4   – Diastolic     (SFLOAT)
  Octets 5-6   – Mean Arterial Pressure (SFLOAT)
  [Octets 7-13 – Date/Time, if FLAG_TIMESTAMP set]
  [Octets n+0..1 – Pulse Rate (SFLOAT), if FLAG_PULSE_RATE set]
  [Octet  n+2  – User ID (uint8), if FLAG_USER_ID set]
  [Octets n+3..4 – Measurement Status (uint16), if FLAG_MEASUREMENT_STATUS set]

SFLOAT: IEEE-11073 16-bit float. High nibble = signed exponent, low 12 bits = mantissa.
Special values: 0x07FF = NaN, 0x0800 = NRes, 0x07FE = +Inf, 0x0802 = -Inf.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import logging

from .const import (
    FLAG_UNIT_KPA,
    FLAG_TIMESTAMP,
    FLAG_PULSE_RATE,
    FLAG_USER_ID,
    FLAG_MEASUREMENT_STATUS,
    UNIT_MMHG,
    UNIT_KPA,
)

_LOGGER = logging.getLogger(__name__)

# SFLOAT special-value sentinels (raw 16-bit unsigned)
_SFLOAT_NAN  = 0x07FF
_SFLOAT_NRES = 0x0800
_SFLOAT_POS_INF = 0x07FE
_SFLOAT_NEG_INF = 0x0802


def _sfloat_to_float(raw: int) -> Optional[float]:
    """Convert an IEEE-11073 SFLOAT (16-bit) to a Python float, or None for specials."""
    # Mask to 16 bits
    raw &= 0xFFFF
    if raw in (_SFLOAT_NAN, _SFLOAT_NRES, _SFLOAT_POS_INF, _SFLOAT_NEG_INF):
        return None

    # Exponent: upper 4 bits (signed)
    exponent = raw >> 12
    if exponent >= 8:          # two's complement for 4-bit signed
        exponent -= 16

    # Mantissa: lower 12 bits (signed)
    mantissa = raw & 0x0FFF
    if mantissa >= 0x0800:     # two's complement for 12-bit signed
        mantissa -= 0x1000

    return round(mantissa * (10 ** exponent), 4)


@dataclass
class BloodPressureMeasurement:
    """Parsed data from a single Blood Pressure Measurement notification."""

    # Core pressure values
    systolic: Optional[float] = None
    diastolic: Optional[float] = None
    mean_arterial_pressure: Optional[float] = None
    unit: str = UNIT_MMHG

    # Optional fields
    pulse_rate: Optional[float] = None
    timestamp: Optional[datetime] = None
    user_id: Optional[int] = None

    # Measurement status bits (raw uint16 from spec)
    body_movement_detected: Optional[bool] = None
    cuff_too_loose: Optional[bool] = None
    irregular_pulse: Optional[bool] = None
    pulse_rate_out_of_range: Optional[bool] = None
    measurement_position_error: Optional[bool] = None

    # Raw bytes for debugging
    raw: bytes = field(default_factory=bytes, repr=False)

    @property
    def is_valid(self) -> bool:
        """True when at least systolic and diastolic are present."""
        return self.systolic is not None and self.diastolic is not None


def parse_blood_pressure_measurement(data: bytes) -> BloodPressureMeasurement:
    """Parse raw bytes from GATT characteristic 0x2A35 into a structured result.

    Raises ValueError if the data is too short to be valid.
    """
    if len(data) < 7:
        raise ValueError(
            f"Blood Pressure Measurement data too short: {len(data)} bytes (need ≥7)"
        )

    result = BloodPressureMeasurement(raw=data)
    flags = data[0]
    result.unit = UNIT_KPA if (flags & FLAG_UNIT_KPA) else UNIT_MMHG

    # Pressure values – octets 1-6 (three SFLOATs, little-endian)
    sys_raw, dia_raw, map_raw = struct.unpack_from("<HHH", data, 1)
    result.systolic             = _sfloat_to_float(sys_raw)
    result.diastolic            = _sfloat_to_float(dia_raw)
    result.mean_arterial_pressure = _sfloat_to_float(map_raw)

    offset = 7  # bytes consumed so far

    # Optional timestamp (7 bytes: year uint16, month/day/h/m/s each uint8)
    if flags & FLAG_TIMESTAMP:
        if len(data) < offset + 7:
            _LOGGER.warning("Timestamp flag set but data truncated at offset %d", offset)
        else:
            year, month, day, hour, minute, second = struct.unpack_from(
                "<HBBBBB", data, offset
            )
            try:
                result.timestamp = datetime(year, month, day, hour, minute, second).astimezone()
            except ValueError:
                _LOGGER.warning(
                    "Invalid timestamp in BP measurement: %d-%d-%d %d:%d:%d",
                    year, month, day, hour, minute, second,
                )
            offset += 7

    # Optional pulse rate (1 SFLOAT = 2 bytes)
    if flags & FLAG_PULSE_RATE:
        if len(data) < offset + 2:
            _LOGGER.warning("Pulse rate flag set but data truncated at offset %d", offset)
        else:
            (pr_raw,) = struct.unpack_from("<H", data, offset)
            result.pulse_rate = _sfloat_to_float(pr_raw)
            offset += 2

    # Optional user ID (1 byte)
    if flags & FLAG_USER_ID:
        if len(data) > offset:
            result.user_id = data[offset]
            offset += 1

    # Optional measurement status (2 bytes)
    if flags & FLAG_MEASUREMENT_STATUS:
        if len(data) >= offset + 2:
            (status,) = struct.unpack_from("<H", data, offset)
            result.body_movement_detected     = bool(status & 0x0001)
            result.cuff_too_loose             = bool(status & 0x0002)
            result.irregular_pulse            = bool(status & 0x0004)
            result.pulse_rate_out_of_range    = bool(status & 0x0018)
            result.measurement_position_error = bool(status & 0x0020)

    return result
