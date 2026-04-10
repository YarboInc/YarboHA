"""Number platform for Yarbo integration — configuration-driven + Plan Start Percent."""

from __future__ import annotations

import logging
import time

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import YarboDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

COMMAND_COOLDOWN_SECONDS = 5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yarbo number entities from SDK control field definitions."""
    from yarbo_robot_sdk import get_control_field_definitions

    coordinator: YarboDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[NumberEntity] = []
    for device in coordinator.devices:
        ctrl_defs = await hass.async_add_executor_job(
            get_control_field_definitions, device.type_id
        )
        for ctrl_def in ctrl_defs:
            if ctrl_def.entity_type == "number":
                entities.append(YarboConfigNumber(coordinator, device, ctrl_def))

        # Hardcoded Plan Start Percent (local-only, no MQTT state)
        entities.append(YarboPlanStartPercent(coordinator, device))

    async_add_entities(entities)


class YarboConfigNumber(
    CoordinatorEntity[YarboDataUpdateCoordinator], NumberEntity
):
    """Configuration-driven number entity for volume control."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator, device, ctrl_def) -> None:
        super().__init__(coordinator)
        self._device = device
        self._ctrl_def = ctrl_def
        self._command_sent_at: float = 0
        self._optimistic_value: float | None = None

        path_key = ctrl_def.path.replace(".", "_").replace("__", "").lower()
        self._attr_unique_id = f"{device.sn}_{path_key}_number"
        self._attr_name = ctrl_def.name
        self._attr_entity_registry_enabled_default = ctrl_def.enabled_by_default

        if ctrl_def.min_value is not None:
            self._attr_native_min_value = ctrl_def.min_value
        if ctrl_def.max_value is not None:
            self._attr_native_max_value = ctrl_def.max_value
        if ctrl_def.step is not None:
            self._attr_native_step = ctrl_def.step
        if ctrl_def.unit:
            self._attr_native_unit_of_measurement = ctrl_def.unit
        if ctrl_def.icon:
            self._attr_icon = ctrl_def.icon

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device.sn)},
            name=self._device.name,
            manufacturer="Yarbo",
            model=self._device.model,
            serial_number=self._device.sn,
        )

    @property
    def native_value(self) -> float | None:
        # During cooldown, return optimistic value to prevent flicker
        if (
            self._optimistic_value is not None
            and time.monotonic() - self._command_sent_at < COMMAND_COOLDOWN_SECONDS
        ):
            return self._optimistic_value
        raw = self._get_state_value()
        if raw is None:
            return None
        val = float(raw)
        # Volume is reported as 0-1 float, scale to 0-100 for display
        if self._ctrl_def.command_builder == "sound_volume":
            val = int(val * 100)
        return val

    async def async_set_native_value(self, value: float) -> None:
        """Send the new value to the device."""
        self._optimistic_value = value
        self._command_sent_at = time.monotonic()
        # Volume UI is 0-100, device expects 0-1 float
        device_value = value
        if self._ctrl_def.command_builder == "sound_volume":
            device_value = value / 100.0
        payload = self._build_payload(device_value)
        try:
            await self.hass.async_add_executor_job(
                self.coordinator._client.mqtt_publish_command,
                self._device.sn,
                self._device.type_id,
                self._ctrl_def.command_topic,
                payload,
            )
        except Exception as exc:
            _LOGGER.error("[number] mqtt_publish_command FAILED: %s", exc)

    def _build_payload(self, value: float) -> dict:
        """Build command payload based on command_builder type."""
        builder = self._ctrl_def.command_builder
        if builder == "sound_volume":
            current_enable = self._get_sibling_value("StateMSG.enable_sound")
            enable = bool(current_enable) if current_enable is not None else True
            return {"enable": enable, "vol": round(value, 1), "mode": 0}
        return {}

    def _get_state_value(self):
        if not self.coordinator.data:
            return None
        device_data = self.coordinator.data.get(self._device.sn)
        if device_data is None:
            return None
        from yarbo_robot_sdk.device_helpers import extract_field
        return extract_field(device_data, self._ctrl_def.path)

    def _get_sibling_value(self, field_path: str):
        if not self.coordinator.data:
            return None
        device_data = self.coordinator.data.get(self._device.sn)
        if device_data is None:
            return None
        from yarbo_robot_sdk.device_helpers import extract_field
        return extract_field(device_data, field_path)


class YarboPlanStartPercent(RestoreEntity, NumberEntity):
    """Plan start percent — local-only number entity for Start Plan input."""

    _attr_has_entity_name = True
    _attr_name = "Plan Start Percent"
    _attr_native_min_value = 0
    _attr_native_max_value = 99
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:percent"

    def __init__(self, coordinator, device) -> None:
        self._coordinator = coordinator
        self._device = device
        self._attr_unique_id = f"{device.sn}_plan_start_percent"
        self._attr_native_value: float = 0

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device.sn)},
            name=self._device.name,
            manufacturer="Yarbo",
            model=self._device.model,
            serial_number=self._device.sn,
        )

    async def async_added_to_hass(self) -> None:
        """Restore previous value on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            try:
                self._attr_native_value = float(last_state.state)
            except ValueError:
                pass

    async def async_set_native_value(self, value: float) -> None:
        """Store the value locally (no MQTT command)."""
        self._attr_native_value = value
        self.async_write_ha_state()
