"""Unit tests for the Bluetooth SIG 0x2A35 Blood Pressure Measurement parser.

Run with:  pytest tests/test_parser.py -v
"""
from __future__ import annotations

import struct
from datetime import datetime

import pytest

from custom_components.sig_bpm_ble.parser import (
    BloodPressureMeasurement,
    _sfloat_to_float,
    parse_blood_pressure_measurement,
)
from custom_components.sig_bpm_ble.const import (
    FLAG_TIMESTAMP,
    FLAG_PULSE_RATE,
    FLAG_USER_ID,
    FLAG_MEASUREMENT_STATUS,
    UNIT_MMHG,
    UNIT_KPA,
)


# ── SFLOAT helpers ──────────────────────────────────────────────────────────────

def _sfloat(mantissa: int, exponent: int) -> int:
    """Encode a value as a 16-bit SFLOAT."""
    exp4 = exponent & 0x0F
    mant12 = mantissa & 0x0FFF
    return (exp4 << 12) | mant12


class TestSfloatConversion:
    def test_integer_value(self):
        # 120 mmHg: mantissa=120, exponent=0  →  0x0078
        assert _sfloat_to_float(_sfloat(120, 0)) == pytest.approx(120.0)

    def test_decimal_value(self):
        # 12.0 kPa: mantissa=120, exponent=-1  →  120 × 10^-1
        raw = _sfloat(120, -1)
        assert _sfloat_to_float(raw) == pytest.approx(12.0)

    def test_nan_returns_none(self):
        assert _sfloat_to_float(0x07FF) is None

    def test_nres_returns_none(self):
        assert _sfloat_to_float(0x0800) is None

    def test_positive_infinity_returns_none(self):
        assert _sfloat_to_float(0x07FE) is None

    def test_negative_infinity_returns_none(self):
        assert _sfloat_to_float(0x0802) is None


# ── Parser helpers ──────────────────────────────────────────────────────────────

def _build_packet(
    systolic: int = 120,
    diastolic: int = 80,
    mean_ap: int = 93,
    exponent: int = 0,
    flags: int = 0,
    pulse_rate: int | None = None,
    timestamp: datetime | None = None,
    user_id: int | None = None,
    status: int | None = None,
) -> bytes:
    """Build a minimal 0x2A35 packet."""
    data = bytearray()
    data.append(flags)

    for v in (systolic, diastolic, mean_ap):
        data += struct.pack("<H", _sfloat(v, exponent))

    if flags & FLAG_TIMESTAMP and timestamp is not None:
        data += struct.pack(
            "<HBBBBB",
            timestamp.year, timestamp.month, timestamp.day,
            timestamp.hour, timestamp.minute, timestamp.second,
        )

    if flags & FLAG_PULSE_RATE and pulse_rate is not None:
        data += struct.pack("<H", _sfloat(pulse_rate, 0))

    if flags & FLAG_USER_ID and user_id is not None:
        data.append(user_id)

    if flags & FLAG_MEASUREMENT_STATUS and status is not None:
        data += struct.pack("<H", status)

    return bytes(data)


# ── Parser tests ────────────────────────────────────────────────────────────────

class TestParseBasic:
    def test_minimal_packet_mmhg(self):
        pkt = _build_packet(120, 80, 93)
        m = parse_blood_pressure_measurement(pkt)
        assert m.systolic == pytest.approx(120.0)
        assert m.diastolic == pytest.approx(80.0)
        assert m.mean_arterial_pressure == pytest.approx(93.0)
        assert m.unit == UNIT_MMHG
        assert m.is_valid is True

    def test_minimal_packet_kpa(self):
        pkt = _build_packet(160, 107, 124, exponent=-1, flags=0x01)
        m = parse_blood_pressure_measurement(pkt)
        assert m.unit == UNIT_KPA
        assert m.systolic == pytest.approx(16.0)

    def test_too_short_raises(self):
        with pytest.raises(ValueError, match="too short"):
            parse_blood_pressure_measurement(b"\x00\x78\x00\x50\x00")

    def test_nan_systolic_not_valid(self):
        # Build with NaN systolic
        pkt = bytearray(_build_packet(120, 80, 93))
        struct.pack_into("<H", pkt, 1, 0x07FF)  # overwrite systolic with NaN
        m = parse_blood_pressure_measurement(bytes(pkt))
        assert m.systolic is None
        assert m.is_valid is False


class TestTimestamp:
    def test_timestamp_parsed(self):
        ts = datetime(2024, 11, 5, 8, 30, 0)
        flags = FLAG_TIMESTAMP
        pkt = _build_packet(125, 82, 96, flags=flags, timestamp=ts)
        m = parse_blood_pressure_measurement(pkt)
        assert m.timestamp == ts

    def test_no_timestamp_when_flag_not_set(self):
        pkt = _build_packet(125, 82, 96)
        m = parse_blood_pressure_measurement(pkt)
        assert m.timestamp is None


class TestPulseRate:
    def test_pulse_rate_parsed(self):
        flags = FLAG_PULSE_RATE
        pkt = _build_packet(120, 80, 93, flags=flags, pulse_rate=72)
        m = parse_blood_pressure_measurement(pkt)
        assert m.pulse_rate == pytest.approx(72.0)

    def test_no_pulse_when_flag_not_set(self):
        pkt = _build_packet(120, 80, 93)
        m = parse_blood_pressure_measurement(pkt)
        assert m.pulse_rate is None


class TestUserID:
    def test_user_id_parsed(self):
        flags = FLAG_USER_ID
        pkt = _build_packet(120, 80, 93, flags=flags, user_id=2)
        m = parse_blood_pressure_measurement(pkt)
        assert m.user_id == 2


class TestMeasurementStatus:
    def test_body_movement_flag(self):
        flags = FLAG_MEASUREMENT_STATUS
        pkt = _build_packet(120, 80, 93, flags=flags, status=0x0001)
        m = parse_blood_pressure_measurement(pkt)
        assert m.body_movement_detected is True
        assert m.cuff_too_loose is False

    def test_irregular_pulse_flag(self):
        flags = FLAG_MEASUREMENT_STATUS
        pkt = _build_packet(120, 80, 93, flags=flags, status=0x0004)
        m = parse_blood_pressure_measurement(pkt)
        assert m.irregular_pulse is True

    def test_cuff_too_loose_flag(self):
        flags = FLAG_MEASUREMENT_STATUS
        pkt = _build_packet(120, 80, 93, flags=flags, status=0x0002)
        m = parse_blood_pressure_measurement(pkt)
        assert m.cuff_too_loose is True

    def test_position_error_flag(self):
        flags = FLAG_MEASUREMENT_STATUS
        pkt = _build_packet(120, 80, 93, flags=flags, status=0x0020)
        m = parse_blood_pressure_measurement(pkt)
        assert m.measurement_position_error is True


class TestFullPacket:
    """Simulate a realistic full-featured packet."""

    def test_full_packet(self):
        ts = datetime(2025, 3, 14, 9, 15, 30)
        flags = FLAG_TIMESTAMP | FLAG_PULSE_RATE | FLAG_USER_ID | FLAG_MEASUREMENT_STATUS
        pkt = _build_packet(
            systolic=122, diastolic=78, mean_ap=92,
            flags=flags,
            pulse_rate=65,
            timestamp=ts,
            user_id=1,
            status=0x0000,
        )
        m = parse_blood_pressure_measurement(pkt)
        assert m.is_valid is True
        assert m.systolic == pytest.approx(122.0)
        assert m.diastolic == pytest.approx(78.0)
        assert m.mean_arterial_pressure == pytest.approx(92.0)
        assert m.pulse_rate == pytest.approx(65.0)
        assert m.timestamp == ts
        assert m.user_id == 1
        assert m.body_movement_detected is False
