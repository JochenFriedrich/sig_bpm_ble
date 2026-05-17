"""Constants for the SIG Blood Pressure Monitor BLE integration."""

DOMAIN = "sig_bpm_ble"

# ── Bluetooth SIG standard UUIDs ──────────────────────────────────────────────
# Blood Pressure Service
BP_SERVICE_UUID = "00001810-0000-1000-8000-00805f9b34fb"
# Blood Pressure Measurement characteristic  (notify)
BP_MEASUREMENT_UUID = "00002a35-0000-1000-8000-00805f9b34fb"
# Intermediate Cuff Pressure characteristic  (notify, optional)
INTERMEDIATE_CUFF_UUID = "00002a36-0000-1000-8000-00805f9b34fb"
# Blood Pressure Feature characteristic  (read, optional)
BP_FEATURE_UUID = "00002a49-0000-1000-8000-00805f9b34fb"
# Client Characteristic Configuration Descriptor  (enables notifications)
CCCD_UUID = "00002902-0000-1000-8000-00805f9b34fb"

# ── Measurement flag bits (octet 0 of 0x2A35) ─────────────────────────────────
FLAG_UNIT_KPA        = 0x01   # 0 = mmHg, 1 = kPa
FLAG_TIMESTAMP       = 0x02
FLAG_PULSE_RATE      = 0x04
FLAG_USER_ID         = 0x08
FLAG_MEASUREMENT_STATUS = 0x10

# ── Unit labels ───────────────────────────────────────────────────────────────
UNIT_MMHG = "mmHg"
UNIT_KPA  = "kPa"

# ── Misc ──────────────────────────────────────────────────────────────────────
CONNECT_TIMEOUT = 15.0        # seconds to wait for BleakClient.connect()
PAIR_TIMEOUT = 30.0           # seconds to wait for SMP pairing to complete
NOTIFICATION_TIMEOUT = 20.0  # seconds to wait for the first BP measurement notification
IDLE_AFTER_LAST_RECORD_TIMEOUT = 3.0 # seconds of silence after last record before declaring done
