"""Map zone sensor platform for Yarbo integration — GeoJSON work zones."""

from __future__ import annotations

import logging
from collections import Counter

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
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
    """Set up Yarbo map sensor entities."""
    coordinator: YarboDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        YarboMapSensor(coordinator, device)
        for device in coordinator.devices
    ]
    async_add_entities(entities)


class YarboMapSensor(
    CoordinatorEntity[YarboDataUpdateCoordinator], SensorEntity
):
    """Sensor entity exposing map zone data as GeoJSON FeatureCollection."""

    _attr_has_entity_name = True
    _attr_name = "Map Zones"
    _attr_icon = "mdi:map"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: YarboDataUpdateCoordinator, device) -> None:
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.sn}_map_zones"

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
    def native_value(self) -> str | None:
        """Return the number of map features as the sensor value."""
        geojson = self.coordinator.map_data.get(self._device.sn)
        if geojson is None:
            return None
        return str(len(geojson.get("features", [])))

    @property
    def extra_state_attributes(self) -> dict:
        """Expose GeoJSON FeatureCollection, zone summary, and center coordinates."""
        geojson = self.coordinator.map_data.get(self._device.sn)
        if geojson is None:
            return {}

        features = geojson.get("features", [])
        type_counts = Counter(
            f.get("properties", {}).get("zone_type", "unknown")
            for f in features
        )

        attrs = {
            "geojson": geojson,
            "zone_summary": dict(type_counts),
        }

        # Use device GPS reference as center point
        gps_ref = self.coordinator.gps_refs.get(self._device.sn, {})
        ref = gps_ref.get("ref", {})
        if ref.get("latitude") is not None:
            attrs["latitude"] = ref["latitude"]
            attrs["longitude"] = ref["longitude"]

        return attrs

