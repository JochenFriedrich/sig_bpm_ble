"""Config flow for SIG Blood Pressure Monitor BLE.

Discovery:  HA's Bluetooth integration scans and passes us ServiceInfo objects
            for devices advertising the Blood Pressure Service (0x1810).
Manual add: User can also enter a MAC address directly if auto-discovery fails.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, BP_SERVICE_UUID

_LOGGER = logging.getLogger(__name__)


class SIGBPMConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for SIG BPM BLE integration."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_devices: dict[str, str] = {}  # address → name
        self._discovery_info: BluetoothServiceInfoBleak | None = None

    # ── Bluetooth auto-discovery entry point ──────────────────────────────────

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a device discovered by HA's Bluetooth stack."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {
            "name": discovery_info.name or discovery_info.address
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovered device with user."""
        assert self._discovery_info is not None
        info = self._discovery_info

        bonded_source = None

        if user_input is not None:
            return self.async_create_entry(
                title=info.name or info.address,
                data={
                    CONF_ADDRESS: address,
                },
            )

        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": info.name or "Unknown",
                "address": info.address,
            },
        )

    # ── Manual / fallback flow ─────────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show discovered SIG BP devices or allow manual MAC entry."""
        errors: dict[str, str] = {}

        # Collect devices advertising the Blood Pressure Service
        current_addresses = self._async_current_ids()
        for service_info in async_discovered_service_info(self.hass, connectable=True):
            address = service_info.address
            if address in current_addresses:
                continue
            if BP_SERVICE_UUID in (service_info.service_uuids or []):
                self._discovered_devices[address] = (
                    service_info.name or address
                )

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address.upper(), raise_on_progress=False)
            self._abort_if_unique_id_configured()
            name = self._discovered_devices.get(address, address)

            scanner_devices = bluetooth.async_scanner_devices_by_address(
                self.hass, address, connectable=True
            )
            return self.async_create_entry(
                title=name,
                data={
                    CONF_ADDRESS: address,
                },
            )

        # Build a selector: discovered devices + manual option
        device_options = {
            addr: f"{name} ({addr})"
            for addr, name in self._discovered_devices.items()
        }

        if device_options:
            schema = vol.Schema(
                {vol.Required(CONF_ADDRESS): vol.In(device_options)}
            )
        else:
            # No devices discovered – ask for manual MAC
            schema = vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): str,
                }
            )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "count": str(len(device_options)),
            },
        )
