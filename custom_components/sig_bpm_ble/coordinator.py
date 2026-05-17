"""Coordinator for SIG Blood Pressure Monitor BLE integration.

Connection strategy
───────────────────
We use establish_connection() from bleak-retry-connector rather than BleakClient
directly.  establish_connection() handles everything we previously did by hand:

  • Proxy / scanner selection (habluetooth picks best available path by RSSI)
  • Retries with backoff (max_attempts controls this)
  • ESP32-specific error codes (GATT error 133, disconnects during connect, etc.)
  • Connection slot management across ESPHome proxies
  • Fresh BLEDevice on each attempt

There is no point trying to pin a specific proxy — habluetooth always routes by
RSSI score regardless of which BLEDevice we pass.  If a device is bonded to a
specific proxy's ESP32, the solution is to re-pair through whichever proxy
habluetooth consistently chooses (highest RSSI), not to fight the router.

Protocol flow (SIG Blood Pressure Service 0x1810)
──────────────────────────────────────────────────
Unlike glucose, BP monitors stream records automatically on subscribe — no RACP
write needed.  The device sends all stored records as GATT indications, then
disconnects.  We drain all records (drain-all pattern, same as glucose) and
publish the most-recent one.

Drain-all / Err-6 avoidance
────────────────────────────
Keep the session open until the device disconnects or an idle timer fires.
Never disconnect after the first record — the device has more to send and
expects ATT Confirmations for each indication (sent automatically by bleak's
BlueZ backend) before queuing the next.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from bleak import BleakError
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection, BleakClientWithServiceCache

from homeassistant.components.bluetooth import async_clear_advertisement_history
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DOMAIN,
    BP_SERVICE_UUID,
    BP_MEASUREMENT_UUID,
    INTERMEDIATE_CUFF_UUID,
    NOTIFICATION_TIMEOUT,
    IDLE_AFTER_LAST_RECORD_TIMEOUT,
    PAIR_TIMEOUT,
)
from .parser import BloodPressureMeasurement, parse_blood_pressure_measurement

_LOGGER = logging.getLogger(__name__)
_POLL_INTERVAL = timedelta(hours=24)


def _is_auth_error(exc: BleakError) -> bool:
    msg = str(exc).lower()
    return (
        "insufficient authentication" in msg
        or "insufficient encryption" in msg
        or "error=5" in msg or "error=8" in msg or "error=15" in msg
    )


class BPMCoordinator(DataUpdateCoordinator[BloodPressureMeasurement | None]):
    """Coordinate BLE connections and data parsing for a single BP monitor."""

    def __init__(self, hass: HomeAssistant, address: str, name: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{address}",
            update_interval=_POLL_INTERVAL,
        )
        self.address = address
        self.device_name = name
        self._connecting = False
        self._last_measurement: BloodPressureMeasurement | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def handle_advertisement(self, service_info: Any) -> None:
        if self._connecting:
            _LOGGER.debug("[%s] Already connecting – skipping", self.address)
            return
        _LOGGER.debug("[%s] Advertisement received – scheduling connection", self.address)
        self.hass.async_create_task(self._connect_and_read(service_info))

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _connect_and_read(self, service_info: Any) -> None:
        self._connecting = True
        try:
            await self._do_connect_and_read(service_info)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("[%s] Session failed: %s", self.address, err)
        finally:
            self._connecting = False
            try:
                async_clear_advertisement_history(self.hass, self.address)
            except Exception:  # noqa: BLE001
                pass

    async def _do_connect_and_read(self, service_info: Any) -> None:
        received_measurements: list[BloodPressureMeasurement] = []
        first_record_event: asyncio.Event = asyncio.Event()
        done_event: asyncio.Event = asyncio.Event()
        idle_handle: list[asyncio.TimerHandle | None] = [None]

        def _reschedule_idle() -> None:
            if idle_handle[0] is not None:
                idle_handle[0].cancel()
            idle_handle[0] = asyncio.get_event_loop().call_later(
                IDLE_AFTER_LAST_RECORD_TIMEOUT, done_event.set
            )

        def _notification_handler(sender: Any, data: bytearray) -> None:
            _LOGGER.debug("[%s] BP indication handle=%s data=%s",
                          self.address, sender, data.hex())
            _reschedule_idle()
            try:
                parsed = parse_blood_pressure_measurement(bytes(data))
                if parsed.is_valid:
                    received_measurements.append(parsed)
                    _LOGGER.debug("[%s] Record #%d: sys=%s dia=%s %s",
                                  self.address, len(received_measurements),
                                  parsed.systolic, parsed.diastolic, parsed.unit)
                    first_record_event.set()
            except ValueError as exc:
                _LOGGER.warning("[%s] Parse error: %s", self.address, exc)

        def _intermediate_handler(sender: Any, data: bytearray) -> None:
            _LOGGER.debug("[%s] Intermediate cuff pressure handle=%s data=%s",
                          self.address, sender, data.hex())
            _reschedule_idle()

        def _disconnected_callback(_client: Any) -> None:
            _LOGGER.debug("[%s] Device disconnected – signalling done", self.address)
            if idle_handle[0] is not None:
                idle_handle[0].cancel()
            done_event.set()

        _LOGGER.info("[%s] Connecting …", self.address)
        client = await establish_connection(
            BleakClientWithServiceCache,
            service_info.device,
            self.device_name,
            disconnected_callback=_disconnected_callback,
            max_attempts=3,
        )
        try:
            _LOGGER.info("[%s] Connected – pairing …", self.address)
            await self._ensure_paired(client)

            # Resolve handles from the BP service to avoid UUID ambiguity
            handles = self._resolve_characteristics(client)

            _LOGGER.info("[%s] Enabling BP indications …", self.address)
            try:
                await client.start_notify(handles["measurement"], _notification_handler)
            except BleakError as exc:
                if _is_auth_error(exc):
                    raise BleakError(
                        f"[{self.address}] Auth error after pairing – "
                        f"try: bluetoothctl remove {self.address} then re-pair. {exc}"
                    ) from exc
                raise

            if handles["intermediate"] is None:
                _LOGGER.debug("[%s] Intermediate Cuff Pressure not available", self.address)
            else:
                try:
                    await client.start_notify(handles["intermediate"], _intermediate_handler)
                except BleakError:
                    _LOGGER.debug("[%s] Intermediate Cuff Pressure not available", self.address)

            # Phase 1 — wait for first record
            try:
                await asyncio.wait_for(first_record_event.wait(),
                                       timeout=NOTIFICATION_TIMEOUT)
            except asyncio.TimeoutError:
                _LOGGER.warning("[%s] No BP record within %ds",
                                self.address, NOTIFICATION_TIMEOUT)
                if idle_handle[0] is not None:
                    idle_handle[0].cancel()
                return

            # Phase 2 — drain all remaining records
            _LOGGER.debug("[%s] Draining remaining records …", self.address)
            await done_event.wait()
            if idle_handle[0] is not None:
                idle_handle[0].cancel()

            _LOGGER.info("[%s] Transfer complete – %d record(s)",
                         self.address, len(received_measurements))

            try:
                await client.stop_notify(handles["measurement"])
            except BleakError:
                pass
        finally:
            try:
                await client.disconnect()
            except BleakError:
                pass

        if not received_measurements:
            return

        measurements_with_ts = [m for m in received_measurements
                                 if m.timestamp is not None]
        latest = (
            max(measurements_with_ts, key=lambda m: m.timestamp)
            if measurements_with_ts else received_measurements[-1]
        )
        _LOGGER.info(
            "[%s] ✓ Publishing latest of %d: sys=%s dia=%s %s MAP=%s pulse=%s ts=%s",
            self.address, len(received_measurements),
            latest.systolic, latest.diastolic, latest.unit,
            latest.mean_arterial_pressure, latest.pulse_rate, latest.timestamp,
        )
        self._last_measurement = latest
        self.async_set_updated_data(latest)

    def _resolve_characteristics(self, client: Any) -> dict[str, int]:
        """Resolve BP characteristic handles from within the BP service (0x1810)."""
        def _n(u: str) -> str:
            return str(u).lower()

        bp_svc  = _n(BP_SERVICE_UUID)
        meas    = _n(BP_MEASUREMENT_UUID)
        inter   = _n(INTERMEDIATE_CUFF_UUID)

        handles: dict[str, int | None] = {"measurement": None, "intermediate": None}

        for svc in client.services:
            if _n(svc.uuid) != bp_svc:
                continue
            for char in svc.characteristics:
                u = _n(char.uuid)
                if u == meas:
                    handles["measurement"] = char.handle
                elif u == inter:
                    handles["intermediate"] = char.handle

        # Fallback: scan all services
        if handles["measurement"] is None:
            for svc in client.services:
                for char in svc.characteristics:
                    u = _n(char.uuid)
                    if u == meas and handles["measurement"] is None:
                        handles["measurement"] = char.handle
                    elif u == inter and handles["intermediate"] is None:
                        handles["intermediate"] = char.handle

        if handles["measurement"] is None:
            raise BleakError(
                f"[{self.address}] BP Measurement characteristic (0x2A35) not found"
            )
        return handles  # type: ignore[return-value]

    async def _ensure_paired(self, client: Any) -> None:
        """Attempt to pair/bond with the device; handle proxies and failures gracefully.

        On BlueZ (Linux / HAOS):
          - If already bonded, pair() returns almost instantly.
          - If not bonded, BlueZ performs the SMP exchange ("Just Works" for most
            BP monitors) and stores the Long Term Key (LTK) for future connections.

        On ESPHome Bluetooth Proxies:
          - pair() raises NotImplementedError or BleakError.  We log a warning
            and continue; if the device is already bonded at the adapter level
            the GATT ops will succeed anyway.
        """
        try:
            await asyncio.wait_for(client.pair(), timeout=PAIR_TIMEOUT)
            _LOGGER.info("[%s] Paired/bonded successfully (or already bonded)", self.address)
            self._paired_successfully = True
        except NotImplementedError:
            # ESPHome proxy backend does not implement pair()
            _LOGGER.debug(
                "[%s] pair() not supported on this backend (ESPHome proxy?). "
                "Continuing without explicit pairing.",
                self.address,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "[%s] Pairing timed out after %ds. "
                "The device may require a button press to confirm pairing. "
                "Attempting to continue — GATT ops may fail with error=5.",
                self.address, PAIR_TIMEOUT,
            )
        except BleakError as exc:
            # pair() itself failed (e.g. already paired and BlueZ returned fast,
            # or the ESPHome proxy raised BleakError instead of NotImplementedError).
            if "already" in str(exc).lower() or "paired" in str(exc).lower():
                _LOGGER.debug("[%s] Device reports already paired: %s", self.address, exc)
                self._paired_successfully = True
            else:
                _LOGGER.warning(
                    "[%s] pair() failed: %s. "
                    "If you see GATT error=5 next, run: "
                    "bluetoothctl; agent on; pair %s",
                    self.address, exc, self.address,
                )

    # ── DataUpdateCoordinator override ────────────────────────────────────────

    async def _async_update_data(self) -> BloodPressureMeasurement | None:
        return self._last_measurement
