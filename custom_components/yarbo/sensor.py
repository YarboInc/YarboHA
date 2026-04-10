"""Sensor platform for Yarbo integration — configuration-driven."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import YarboDataUpdateCoordinator

# Sensor device_classes that represent a numeric measurement
MEASUREMENT_CLASSES = {"battery", "temperature", "humidity", "distance", "pressure"}

# on_going_planning status code → display text
PLANNING_STATUS_MAP: dict[int, str] = {
    0: "Not Started",
    1: "Cleaning",
    2: "Calculating Route",
    3: "Heading to Area",
    5: "Completed",
    11: "Waypoint Navigation",
    12: "Waypoint Complete",
    -2: "Error: Create Plan History Failed (WP002)",
    -10: "Error: Plan Not Found (WP003)",
    -11: "Error: Failed to Read Plan (WP004)",
    -12: "Error: Failed to Calculate Route (WP005)",
    -20: "Error: Outside Mapped Area (WP006)",
    -21: "Error: Area Data Error (WP007)",
    -22: "Error: Route Data Error (WP008)",
    -23: "Error: In No-Go Zone",
    -24: "Error: Low Battery",
    -26: "Error: Module Position Failure (WP012)",
    -30: "Error: Location Data Exception (WP013)",
    -31: "Error: Docking Station Exception (WP014)",
    -40: "Error: Obstacle Mark Failed",
    -42: "Error: Out of Boundary",
    -43: "Error: Unable to Navigate Obstacle (WP016)",
    -44: "Error: Exceeded Boundary (WP017)",
    -47: "Error: Out of Boundary >1.5m",
    -88: "Error: In No-Go Zone",
    -92: "Error: Out of Boundary (WP025)",
}

# on_going_recharging status code → display text
RECHARGING_STATUS_MAP: dict[int, str] = {
    0: "Not Started",
    1: "Returning on Path",
    2: "Returning in Area",
    3: "Repositioning",
    4: "Charging",
    99: "Verifying",
    -2: "Error: Server Error",
    -3: "Error: Direction Uninitialized",
    -4: "Error: Docking Station Uninitialized",
    -5: "Error: Recharge Failed (REC005)",
    -6: "Error: Failed to Park",
    -8: "Error: Docking Connection Failed",
    -9: "Error: Stuck",
    -20: "Error: Outside Mapped Area",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yarbo sensors dynamically from SDK field definitions."""
    from yarbo_robot_sdk import get_field_definitions

    coordinator: YarboDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []
    for device in coordinator.devices:
        field_defs = await hass.async_add_executor_job(
            get_field_definitions, device.type_id
        )
        for field_def in field_defs:
            if field_def.entity_type == "sensor":
                entities.append(YarboConfigSensor(coordinator, device, field_def))

    # Add map zone sensors
    from .map_sensor import YarboMapSensor

    for device in coordinator.devices:
        entities.append(YarboMapSensor(coordinator, device))

    async_add_entities(entities)


class YarboConfigSensor(
    CoordinatorEntity[YarboDataUpdateCoordinator], SensorEntity
):
    """Configuration-driven sensor — one class for all sensor fields."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device, field_def) -> None:
        super().__init__(coordinator)
        self._device = device
        self._field_def = field_def

        # Unique ID from SN + normalized path
        path_key = field_def.path.replace(".", "_").replace("__", "").lower()
        self._attr_unique_id = f"{device.sn}_{path_key}"
        self._attr_name = field_def.name
        self._attr_entity_registry_enabled_default = field_def.enabled_by_default

        # Device class
        if field_def.value_map:
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(dict.fromkeys(field_def.value_map.values()))
        elif field_def.device_class:
            try:
                self._attr_device_class = SensorDeviceClass(field_def.device_class)
            except ValueError:
                pass

        # State class for numeric measurements
        if (
            field_def.device_class in MEASUREMENT_CLASSES
            and not field_def.value_map
        ):
            self._attr_state_class = SensorStateClass.MEASUREMENT

        # Unit and icon
        if field_def.unit:
            self._attr_native_unit_of_measurement = field_def.unit
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
    def native_value(self):
        # Special extraction for custom_extractor fields (e.g. network_priority)
        if self._field_def.custom_extractor:
            return self._extract_custom()
        raw = self._extract(self._field_def.path)
        if raw is None:
            return None
        if self._field_def.value_map:
            mapped = self._field_def.value_map.get(str(raw))
            if mapped is not None:
                return mapped
            # For numeric values, check if a negative fallback exists (e.g. all negatives → "Error")
            if isinstance(raw, (int, float)) and raw < 0:
                return self._field_def.value_map.get("-1")
            return None
        return raw

    def _extract_custom(self):
        """Handle fields with custom_extractor logic."""
        data = self._get_device_data()
        if data is None:
            return None
        if self._field_def.custom_extractor == "network_priority":
            from yarbo_robot_sdk.device_helpers import extract_active_network, extract_field
            route_priority = extract_field(data, self._field_def.path)
            return extract_active_network(route_priority)
        if self._field_def.custom_extractor == "volume_scale":
            from yarbo_robot_sdk.device_helpers import extract_field
            raw = extract_field(data, self._field_def.path)
            if raw is None:
                return None
            return int(float(raw) * 100)
        if self._field_def.custom_extractor == "rtk_signal":
            from yarbo_robot_sdk.device_helpers import extract_field
            raw = extract_field(data, self._field_def.path)
            # APP logic: 4=Strong, 5=Medium, everything else=Weak
            raw_int = int(raw) if raw is not None else None
            if raw_int == 4:
                return "Strong"
            if raw_int == 5:
                return "Medium"
            return "Weak"
        if self._field_def.custom_extractor == "planning_status":
            from yarbo_robot_sdk.device_helpers import extract_field
            raw = extract_field(data, self._field_def.path)
            if raw is None:
                return None
            code = int(raw)
            if code in PLANNING_STATUS_MAP:
                return PLANNING_STATUS_MAP[code]
            return "Error" if code < 0 else None
        if self._field_def.custom_extractor == "recharging_status":
            from yarbo_robot_sdk.device_helpers import extract_field
            raw = extract_field(data, self._field_def.path)
            if raw is None:
                return None
            code = int(raw)
            if code in RECHARGING_STATUS_MAP:
                return RECHARGING_STATUS_MAP[code]
            return "Error" if code < 0 else None
        return None

    def _extract(self, field_path: str):
        """Extract a field value from MQTT data."""
        data = self._get_device_data()
        if data is None:
            return None
        from yarbo_robot_sdk.device_helpers import extract_field
        return extract_field(data, field_path)

    def _get_device_data(self) -> dict | None:
        if self.coordinator.data and self._device.sn in self.coordinator.data:
            return self.coordinator.data[self._device.sn]
        return None
