"""Sensor platform for SIG Blood Pressure Monitor BLE.

Creates one sensor entity for each value exposed by the Blood Pressure
Measurement characteristic (0x2A35):
  • Systolic pressure
  • Diastolic pressure
  • Mean arterial pressure
  • Pulse rate
  • Measurement status flags (body movement, irregular pulse, etc.)

All entities derive from CoordinatorEntity so they update automatically
whenever the coordinator fires async_set_updated_data().
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfPressure,
    ATTR_ATTRIBUTION,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, UNIT_MMHG, UNIT_KPA
from .coordinator import BPMCoordinator
from .parser import BloodPressureMeasurement

_ATTRIBUTION = "Bluetooth SIG Blood Pressure Service (0x1810)"

# ── Entity descriptions ────────────────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class BPMSensorEntityDescription(SensorEntityDescription):
    """Extends the standard description with a value accessor."""
    value_fn: Callable[[BloodPressureMeasurement], object] = lambda _: None


# The native unit is decided at runtime from the measurement's .unit field,
# but we pre-declare the SensorDeviceClass so HA can convert between units.
_PRESSURE_DESCRIPTIONS: tuple[BPMSensorEntityDescription, ...] = (
    BPMSensorEntityDescription(
        key="systolic",
        name="Systolic Pressure",
        icon="mdi:heart-pulse",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda m: m.systolic,
    ),
    BPMSensorEntityDescription(
        key="diastolic",
        name="Diastolic Pressure",
        icon="mdi:heart",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda m: m.diastolic,
    ),
    BPMSensorEntityDescription(
        key="mean_arterial_pressure",
        name="Mean Arterial Pressure",
        icon="mdi:gauge",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda m: m.mean_arterial_pressure,
    ),
)

_PULSE_DESCRIPTION = BPMSensorEntityDescription(
    key="pulse_rate",
    name="Pulse Rate",
    icon="mdi:heart-flash",
    native_unit_of_measurement="bpm",
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    value_fn=lambda m: m.pulse_rate,
)

_STATUS_DESCRIPTIONS: tuple[BPMSensorEntityDescription, ...] = (
    BPMSensorEntityDescription(
        key="body_movement",
        name="Body Movement Detected",
        icon="mdi:run",
        value_fn=lambda m: m.body_movement_detected,
    ),
    BPMSensorEntityDescription(
        key="irregular_pulse",
        name="Irregular Pulse Detected",
        icon="mdi:heart-broken",
        value_fn=lambda m: m.irregular_pulse,
    ),
    BPMSensorEntityDescription(
        key="cuff_too_loose",
        name="Cuff Too Loose",
        icon="mdi:bandage",
        value_fn=lambda m: m.cuff_too_loose,
    ),
    BPMSensorEntityDescription(
        key="measurement_position_error",
        name="Measurement Position Error",
        icon="mdi:arm-flex",
        value_fn=lambda m: m.measurement_position_error,
    ),
)

_TIMESTAMP_DESCRIPTION = BPMSensorEntityDescription(
    key="measurement_time",
    name="Last Measurement Time",
    icon="mdi:clock-outline",
    device_class=SensorDeviceClass.TIMESTAMP,
    value_fn=lambda m: m.timestamp,
)

_USER_ID_DESCRIPTION = BPMSensorEntityDescription(
    key="user_id",
    name="User ID",
    icon="mdi:account",
    value_fn=lambda m: m.user_id,
)


# ── Platform setup ─────────────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create sensor entities from a config entry."""
    coordinator: BPMCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BPMSensor] = []

    for desc in _PRESSURE_DESCRIPTIONS:
        entities.append(BPMPressureSensor(coordinator, entry, desc))

    entities.append(BPMSensor(coordinator, entry, _PULSE_DESCRIPTION))

    for desc in _STATUS_DESCRIPTIONS:
        entities.append(BPMSensor(coordinator, entry, desc))

    entities.append(BPMSensor(coordinator, entry, _TIMESTAMP_DESCRIPTION))
    entities.append(BPMSensor(coordinator, entry, _USER_ID_DESCRIPTION))

    async_add_entities(entities)


# ── Entity classes ─────────────────────────────────────────────────────────────

class BPMSensor(CoordinatorEntity[BPMCoordinator], SensorEntity):
    """Generic sensor entity backed by the BPM coordinator."""

    entity_description: BPMSensorEntityDescription
    _attr_has_entity_name = True
    _attr_attribution = _ATTRIBUTION

    def __init__(
        self,
        coordinator: BPMCoordinator,
        entry: ConfigEntry,
        description: BPMSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._entry = entry

        # Unique ID guarantees the entity persists across restarts
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.device_name,
            manufacturer="Bluetooth SIG",
            model="Blood Pressure Monitor (0x1810)",
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_state()
        self.async_write_ha_state()

    def _update_state(self) -> None:
        measurement = self.coordinator.data
        if measurement is None:
            self._attr_native_value = None
            return
        value = self.entity_description.value_fn(measurement)
        self._attr_native_value = value

    @property
    def native_value(self) -> object:  # type: ignore[override]
        measurement = self.coordinator.data
        if measurement is None:
            return None
        return self.entity_description.value_fn(measurement)

    @property
    def extra_state_attributes(self) -> dict:
        measurement = self.coordinator.data
        attrs: dict = {}
        if measurement is not None:
            attrs["raw_hex"] = measurement.raw.hex()
            attrs["unit_from_device"] = measurement.unit
            if measurement.timestamp:
                attrs["device_timestamp"] = measurement.timestamp.isoformat()
            if measurement.user_id is not None:
                attrs["user_id"] = measurement.user_id
        return attrs


class BPMPressureSensor(BPMSensor):
    """Pressure sensor that resolves its native unit from the measurement."""

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit reported by the device (mmHg or kPa)."""
        measurement = self.coordinator.data
        if measurement is None:
            return UNIT_MMHG  # default until we have real data
        # Map device string → HA constant for proper unit conversion
        if measurement.unit == UNIT_KPA:
            return UnitOfPressure.KPA
        return "mmHg"  # HA doesn't have a constant for mmHg; use string directly
