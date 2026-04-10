"""Device tracker platform for Yarbo integration — real-time GPS location."""

from __future__ import annotations

import logging

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
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
    """Set up Yarbo device tracker entities."""
    coordinator: YarboDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        YarboDeviceTracker(coordinator, device)
        for device in coordinator.devices
    ]
    async_add_entities(entities)


class YarboDeviceTracker(
    CoordinatorEntity[YarboDataUpdateCoordinator], TrackerEntity
):
    """Device tracker entity that converts local odometry to GPS coordinates."""

    _attr_has_entity_name = True
    _attr_name = "Location"
    _attr_icon = "mdi:map-marker"

    def __init__(self, coordinator: YarboDataUpdateCoordinator, device) -> None:
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.sn}_device_tracker"
        self._computed_lat: float | None = None
        self._computed_lon: float | None = None

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
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        return self._computed_lat

    @property
    def longitude(self) -> float | None:
        return self._computed_lon

    @property
    def available(self) -> bool:
        """Available only when GPS ref is valid (rtkFixType == 1)."""
        gps_ref = self.coordinator.gps_refs.get(self._device.sn)
        if gps_ref is None:
            return False
        if gps_ref.get("rtkFixType") != 1:
            return False
        return True

    @property
    def extra_state_attributes(self) -> dict:
        """Expose raw position data and GPS reference as attributes."""
        attrs = {}
        gps_ref = self.coordinator.gps_refs.get(self._device.sn, {})
        ref = gps_ref.get("ref", {})
        attrs["gps_ref_latitude"] = ref.get("latitude")
        attrs["gps_ref_longitude"] = ref.get("longitude")
        attrs["rtk_fix_type"] = gps_ref.get("rtkFixType")

        device_data = (self.coordinator.data or {}).get(self._device.sn, {})
        from yarbo_robot_sdk.device_helpers import extract_field
        attrs["position_x"] = extract_field(device_data, "CombinedOdom.x")
        attrs["position_y"] = extract_field(device_data, "CombinedOdom.y")
        attrs["heading"] = extract_field(device_data, "CombinedOdom.phi")
        return attrs

    @callback
    def _handle_coordinator_update(self) -> None:
        """Recompute GPS position from CombinedOdom + GPS reference."""
        self._computed_lat = None
        self._computed_lon = None

        gps_ref = self.coordinator.gps_refs.get(self._device.sn)
        if gps_ref is None or gps_ref.get("rtkFixType") != 1:
            self.async_write_ha_state()
            return

        ref = gps_ref.get("ref", {})
        ref_lat = ref.get("latitude")
        ref_lon = ref.get("longitude")
        if ref_lat is None or ref_lon is None:
            self.async_write_ha_state()
            return

        device_data = (self.coordinator.data or {}).get(self._device.sn, {})
        from yarbo_robot_sdk.device_helpers import extract_field, convert_local_to_gps

        local_x = extract_field(device_data, "CombinedOdom.x")
        local_y = extract_field(device_data, "CombinedOdom.y")
        if local_x is None or local_y is None:
            self.async_write_ha_state()
            return

        try:
            self._computed_lat, self._computed_lon = convert_local_to_gps(
                ref_lat, ref_lon, float(local_x), float(local_y)
            )
        except (ValueError, TypeError) as err:
            _LOGGER.debug("Coordinate conversion failed for %s: %s", self._device.sn, err)

        self.async_write_ha_state()
