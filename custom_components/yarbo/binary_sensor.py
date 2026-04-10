"""Binary sensor platform for Yarbo integration — configuration-driven + Online."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import YarboDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yarbo binary sensors dynamically from SDK field definitions."""
    from yarbo_robot_sdk import get_field_definitions

    coordinator: YarboDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = []
    for device in coordinator.devices:
        # Hardcoded Online binary sensor (heartbeat-driven)
        entities.append(YarboOnlineBinarySensor(coordinator, device))

        # Config-driven binary sensors from JSON field definitions
        field_defs = await hass.async_add_executor_job(
            get_field_definitions, device.type_id
        )
        for field_def in field_defs:
            if field_def.entity_type == "binary_sensor":
                entities.append(
                    YarboConfigBinarySensor(coordinator, device, field_def)
                )

    async_add_entities(entities)


class YarboOnlineBinarySensor(
    CoordinatorEntity[YarboDataUpdateCoordinator], BinarySensorEntity
):
    """Online status binary sensor driven by heartbeat timeout."""

    _attr_has_entity_name = True
    _attr_name = "Online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.sn}_online"

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
    def is_on(self) -> bool | None:
        if self.coordinator.data and self._device.sn in self.coordinator.data:
            return self.coordinator.data[self._device.sn].get("__online__")
        return None


class YarboConfigBinarySensor(
    CoordinatorEntity[YarboDataUpdateCoordinator], BinarySensorEntity
):
    """Configuration-driven binary sensor."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device, field_def) -> None:
        super().__init__(coordinator)
        self._device = device
        self._field_def = field_def

        path_key = field_def.path.replace(".", "_").replace("__", "").lower()
        self._attr_unique_id = f"{device.sn}_{path_key}"
        self._attr_name = field_def.name
        self._attr_entity_registry_enabled_default = field_def.enabled_by_default

        if field_def.device_class:
            try:
                self._attr_device_class = BinarySensorDeviceClass(
                    field_def.device_class
                )
            except ValueError:
                pass

        if field_def.icon:
            self._attr_icon = field_def.icon

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
    def is_on(self) -> bool | None:
        raw = self._extract(self._field_def.path)
        if raw is None:
            return None
        # Custom extractor logic
        if self._field_def.custom_extractor == "charging_threshold":
            # BatteryMSG.status: >1 means charging
            if isinstance(raw, (int, float)):
                return raw > 1
            return None
        if self._field_def.custom_extractor == "positive_threshold":
            # Value > 0 means on (e.g. LedInfoMSG.led_head: 255=on, 0=off)
            if isinstance(raw, (int, float)):
                return raw > 0
            return None
        if self._field_def.value_map:
            mapped = self._field_def.value_map.get(str(raw))
            if mapped is None:
                return None
            return mapped.lower() in ("true", "1", "on", "yes")
        return bool(raw)

    def _extract(self, field_path: str):
        """Extract a field value from coordinator data."""
        data = self._get_device_data()
        if data is None:
            return None
        from yarbo_robot_sdk.device_helpers import extract_field
        return extract_field(data, field_path)

    def _get_device_data(self) -> dict | None:
        if self.coordinator.data and self._device.sn in self.coordinator.data:
            return self.coordinator.data[self._device.sn]
        return None
