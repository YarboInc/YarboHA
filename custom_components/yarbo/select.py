"""Select platform for Yarbo integration — configuration-driven + Plan Select."""

from __future__ import annotations

import logging
import time

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
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

        # Hardcoded Plan Select (dynamic options from plan list)
        entities.append(YarboPlanSelect(coordinator, device))

    async_add_entities(entities)


class YarboConfigSelect(
    CoordinatorEntity[YarboDataUpdateCoordinator], SelectEntity
):
    """Configuration-driven select entity.

    Uses _attr_current_option as the single source of truth so HA's frontend
    receives proper state_changed events and updates the dropdown in real-time.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator, device, ctrl_def) -> None:
        super().__init__(coordinator)
        self._device = device
        self._ctrl_def = ctrl_def

        self._command_sent_at: float = 0

        path_key = ctrl_def.path.replace(".", "_").replace("__", "").lower()
        self._attr_unique_id = f"{device.sn}_{path_key}_select"
        self._attr_name = ctrl_def.name
        self._attr_options = ctrl_def.options or []
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
        """Sync _attr_current_option from coordinator data.

        Skips state sync during cooldown after a command to prevent flicker.
        """
        if time.monotonic() - self._command_sent_at < COMMAND_COOLDOWN_SECONDS:
            self.async_write_ha_state()
            return

        raw = self._get_state_value()
        if raw is not None and self._ctrl_def.state_value_map:
            mapped = self._ctrl_def.state_value_map.get(str(raw))
            if mapped is not None and mapped != self._attr_current_option:
                self._attr_current_option = mapped
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Send command to device and optimistically update UI immediately."""
        if not self._ctrl_def.value_map:
            return
        raw_value = self._ctrl_def.value_map.get(option)
        if raw_value is None:
            _LOGGER.error(
                "Unknown option '%s' for %s — not in value_map", option, self._attr_name
            )
            return

        # Optimistic update
        self._attr_current_option = option
        self.async_write_ha_state()

        self._command_sent_at = time.monotonic()

        # Build payload with command_key + extra_payload
        payload = {}
        if self._ctrl_def.command_key:
            payload[self._ctrl_def.command_key] = raw_value
        if self._ctrl_def.extra_payload:
            payload.update(self._ctrl_def.extra_payload)

        try:
            await self.hass.async_add_executor_job(
                self.coordinator._client.mqtt_publish_command,
                self._device.sn,
                self._device.type_id,
                self._ctrl_def.command_topic,
                payload,
            )
        except Exception as exc:
            _LOGGER.error("[select] mqtt_publish_command FAILED: %s", exc)
            self._handle_coordinator_update()
            return

        # Notify coordinator about standby state changes
        if self._ctrl_def.command_topic == "set_working_state":
            self.coordinator.set_user_standby(
                self._device.sn, option == "standby"
            )
            if option == "working":
                # Trigger immediate wake-up
                await self.coordinator._async_send_wakeup(
                    self._device.sn, self._device.type_id
                )

    def _get_state_value(self):
        if not self.coordinator.data:
            return None
        device_data = self.coordinator.data.get(self._device.sn)
        if device_data is None:
            return None
        from yarbo_robot_sdk.device_helpers import extract_field
        return extract_field(device_data, self._ctrl_def.path)


class YarboPlanSelect(
    CoordinatorEntity[YarboDataUpdateCoordinator], SelectEntity
):
    """Plan selector — dynamic options from coordinator plan_data."""

    _attr_has_entity_name = True
    _attr_name = "Plan Select"
    _attr_icon = "mdi:clipboard-list"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.sn}_plan_select"
        self._attr_current_option: str | None = None
        self._plan_id_map: dict[str, int] = {}

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
    def options(self) -> list[str]:
        plans = self.coordinator.plan_data.get(self._device.sn, [])
        self._plan_id_map = {p["name"]: p["id"] for p in plans if "name" in p and "id" in p}
        return list(self._plan_id_map.keys())

    async def async_select_option(self, option: str) -> None:
        """Record plan selection (no MQTT command — Start Plan button sends it)."""
        self._attr_current_option = option
        plan_id = self._plan_id_map.get(option)
        self.coordinator.set_selected_plan(self._device.sn, plan_id)
        self.async_write_ha_state()
