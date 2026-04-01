"""Select platform for Yarbo integration — configuration-driven."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import YarboDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yarbo select entities dynamically from SDK control field definitions."""
    from yarbo_robot_sdk import get_control_field_definitions

    coordinator: YarboDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SelectEntity] = []
    for device in coordinator.devices:
        ctrl_defs = await hass.async_add_executor_job(
            get_control_field_definitions, device.type_id
        )
        for ctrl_def in ctrl_defs:
            if ctrl_def.entity_type == "select":
                entities.append(YarboConfigSelect(coordinator, device, ctrl_def))

    async_add_entities(entities)


class YarboConfigSelect(
    CoordinatorEntity[YarboDataUpdateCoordinator], SelectEntity
):
    """Configuration-driven select entity — one class for all select control fields.

    Uses _attr_current_option as the single source of truth so HA's frontend
    receives proper state_changed events and updates the dropdown in real-time.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator, device, ctrl_def) -> None:
        super().__init__(coordinator)
        self._device = device
        self._ctrl_def = ctrl_def

        path_key = ctrl_def.path.replace(".", "_").replace("__", "").lower()
        self._attr_unique_id = f"{device.sn}_{path_key}_select"
        self._attr_name = ctrl_def.name
        self._attr_options = ctrl_def.options
        self._attr_entity_registry_enabled_default = ctrl_def.enabled_by_default
        self._attr_current_option: str | None = None

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

    @callback
    def _handle_coordinator_update(self) -> None:
        """Sync _attr_current_option from coordinator data, then write state."""
        raw = self._get_state_value()
        if raw is not None:
            mapped = self._ctrl_def.state_value_map.get(str(raw))
            if mapped is not None and mapped != self._attr_current_option:
                _LOGGER.debug(
                    "[select] %s coordinator update: %s → %s",
                    self.entity_id, self._attr_current_option, mapped,
                )
                self._attr_current_option = mapped
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Send command to device and optimistically update UI immediately."""
        raw_value = self._ctrl_def.value_map.get(option)
        if raw_value is None:
            _LOGGER.error(
                "Unknown option '%s' for %s — not in value_map", option, self._attr_name
            )
            return

        # Optimistic update — show the selected option right away
        self._attr_current_option = option
        self.async_write_ha_state()

        _LOGGER.debug(
            "[select] %s user selected '%s' (raw=%s), publishing MQTT",
            self.entity_id, option, raw_value,
        )
        try:
            await self.hass.async_add_executor_job(
                self.coordinator._client.mqtt_publish_command,
                self._device.sn,
                self._device.type_id,
                self._ctrl_def.command_topic,
                {self._ctrl_def.command_key: raw_value},
            )
        except Exception as exc:
            _LOGGER.error("[select] mqtt_publish_command FAILED: %s", exc)
            # Revert optimistic update on failure
            self._handle_coordinator_update()

    def _get_state_value(self):
        """Extract the current state value from coordinator data using ctrl_def.path."""
        if not self.coordinator.data:
            return None
        device_data = self.coordinator.data.get(self._device.sn)
        if device_data is None:
            return None
        from yarbo_robot_sdk.device_helpers import extract_field
        return extract_field(device_data, self._ctrl_def.path)
