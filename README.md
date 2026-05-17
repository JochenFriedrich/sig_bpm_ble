# 🩺 SIG Blood Pressure Monitor BLE — Home Assistant Integration

A **local_push** custom integration for Home Assistant that supports **any BLE blood pressure monitor** implementing the [Bluetooth SIG Blood Pressure Service](https://www.bluetooth.com/specifications/specs/blood-pressure-service-1-1-1/) (UUID `0x1810`).

## Compatible Devices

Any device advertising the Blood Pressure Service (`0x1810`) and Blood Pressure Measurement characteristic (`0x2A35`) should work, including:

| Brand     | Example models             |
|-----------|---------------------------|
| A&D       | UA-651BLE, UA-767PBT-C    |
| Omron     | M7 Intelli IT, M4 Intelli |
| Medisana  | BU 575, BU 546            |
| Beurer    | BM 57, BM 85              |
| Sanitas   | SBM 67                    |

> **Note:** Devices with proprietary (non-SIG) GATT services may need a device-specific parser.  Open an issue with a Wireshark/nRF Sniffer capture.

---

## Installation

### HACS (recommended)

1. Add this repo as a [custom repository](https://hacs.xyz/docs/faq/custom_repositories/) in HACS.
2. Search **SIG Blood Pressure Monitor BLE** and install.
3. Restart Home Assistant.

### Manual

```bash
cp -r custom_components/sig_bpm_ble \
      /config/custom_components/sig_bpm_ble
```

Restart Home Assistant.

---

## Setup

1. Take a blood pressure measurement on your device — the device advertises briefly after each reading.
2. Home Assistant will discover the device automatically and show a notification.
3. Click **Configure** → confirm the device.

Alternatively, go to **Settings → Devices & Services → Add Integration → SIG Blood Pressure Monitor BLE** and enter the MAC address manually.

---

## Sensors Created

| Entity                                     | Unit       | Notes                         |
|--------------------------------------------|------------|-------------------------------|
| `sensor.<name>_systolic_pressure`          | mmHg / kPa | Converted if needed by HA     |
| `sensor.<name>_diastolic_pressure`         | mmHg / kPa |                               |
| `sensor.<name>_mean_arterial_pressure`     | mmHg / kPa |                               |
| `sensor.<name>_pulse_rate`                 | bpm        | If reported by device         |
| `sensor.<name>_last_measurement_time`      | —          | Device timestamp if available |
| `sensor.<name>_user_id`                    | —          | For multi-user devices        |
| `sensor.<name>_body_movement_detected`     | —          | Measurement status flag       |
| `sensor.<name>_irregular_pulse_detected`   | —          | Measurement status flag       |
| `sensor.<name>_cuff_too_loose`             | —          | Measurement status flag       |
| `sensor.<name>_measurement_position_error` | —          | Measurement status flag       |

---

## How It Works

```
BLE device takes measurement
        │
        ▼
Device broadcasts advertisement (BLE)
        │
        ▼  (HA Bluetooth stack detects it)
async_register_callback fires
        │
        ▼
BleakClient connects
        │
        ▼
Subscribe to GATT Notification on 0x2A35
        │
        ▼
Device sends measurement (one-shot notify)
        │
        ▼
Parser decodes IEEE-11073 SFLOAT fields
        │
        ▼
DataUpdateCoordinator broadcasts to sensors
        │
        ▼
Sensor entities update in Home Assistant
```

Key design decisions:
- **local_push** — no polling; reacts to the device's own advertisement.
- **Exponential back-off** retries if the connection fails (device disconnects quickly).
- **ESP32 Bluetooth Proxy** is strongly recommended for reliable reception — see [ESPHome Bluetooth Proxies](https://esphome.io/projects/?type=bluetooth_proxy).

---

## Automation Example

```yaml
alias: Log blood pressure reading
trigger:
  - platform: state
    entity_id: sensor.my_bp_monitor_systolic_pressure
action:
  - service: notify.mobile_app_my_phone
    data:
      title: "Blood Pressure Reading"
      message: >
        {{ states('sensor.my_bp_monitor_systolic_pressure') }}/
        {{ states('sensor.my_bp_monitor_diastolic_pressure') }} mmHg,
        Pulse: {{ states('sensor.my_bp_monitor_pulse_rate') }} bpm
```

---

## Running the Tests

```bash
pip install pytest
cd sig_bpm_ble
pytest tests/ -v
```

---

## Using ESPHome Bluetooth Proxy
When using an ESPHome Bluetooth Proxy, it might be neccessary to bond the device first.
As bonding is not supported by bluetooth_proxy, we need a temporary ble_client setup:

```yaml
#bluetooth_proxy:
#  active: true

api:
  actions:
    - action: passkey_reply
      variables:
        passkey: int
      then:
        - logger.log: "Authenticating with passkey"
        - ble_client.passkey_reply:
            id: sbm70
            passkey: !lambda return passkey;
    - action: numeric_comparison_reply
      variables:
        accept: bool
      then:
        - logger.log: "Authenticating with numeric comparison"
        - ble_client.numeric_comparison_reply:
            id: sbm70
            accept: !lambda return accept;

esp32_ble:
  io_capability: keyboard_display

ble_client:
  - mac_address: aa:bb:cc:dd:ee:ff
    id: sbm70
    on_passkey_request:
      then:
        - logger.log: "Enter the passkey displayed on your BLE device"
        - logger.log: " Go to https://my.home-assistant.io/redirect/developer_services/ and select passkey_reply"
    on_passkey_notification:
      then:
        - logger.log:
            format: "Enter this passkey on your BLE device: %06d"
            args: [ passkey ]
    on_numeric_comparison_request:
      then:
        - logger.log:
            format: "Compare this passkey with the one on your BLE device: %06d"
            args: [ passkey ]
        - logger.log: " Go to https://my.home-assistant.io/redirect/developer_services/ and select numeric_comparison_reply"
    on_connect:
      then:
        - logger.log: "Connected"
        - lambda: |-
            ESP_LOGE("custom", "Connected to SBM70, trying to pair");
            id(sbm70)->pair();
```

When bonding is completed, the ble_client can be deleted again and bluetooth_proxy enabled. It will now use the security
information stored in the ESP flash.

---

## License

MIT
