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

        # Per-zone no-go metadata (id / name / enable) so consumers
        # can expose a toggle UI. Lightweight — the heavy 'range' and
        # 'ref' fields stay hidden behind map_raw.
        raw_map = getattr(self.coordinator, "map_raw", {}).get(
            self._device.sn
        ) or {}
        nogo_list = [
            {
                "id": z.get("id"),
                "name": z.get("name"),
                "enable": bool(z.get("enable", True)),
            }
            for z in raw_map.get("nogozones") or []
        ]
        if nogo_list:
            attrs["nogo_zones"] = nogo_list

        # Dynamic obstacles from cloud_points_feedback, GPS-projected.
        cp = getattr(self.coordinator, "cloud_points", {}).get(
            self._device.sn
        ) or {}
        barriers = cp.get("tmp_barrier_points") or []
        ref_lat = ref.get("latitude") if ref else None
        ref_lon = ref.get("longitude") if ref else None
        if barriers and ref_lat is not None and ref_lon is not None:
            try:
                from yarbo_robot_sdk.device_helpers import convert_local_to_gps
                features = []
                for i, cluster in enumerate(barriers):
                    if not isinstance(cluster, list) or not cluster:
                        continue
                    coords = []
                    for pt in cluster:
                        try:
                            lat, lon = convert_local_to_gps(
                                ref_lat,
                                ref_lon,
                                float(pt.get("x", 0)),
                                float(pt.get("y", 0)),
                            )
                            coords.append(
                                [round(lon, 7), round(lat, 7)]
                            )
                        except Exception:
                            pass
                    if not coords:
                        continue
                    geom = (
                        {"type": "Point", "coordinates": coords[0]}
                        if len(coords) == 1
                        else {"type": "MultiPoint", "coordinates": coords}
                    )
                    features.append({
                        "type": "Feature",
                        "geometry": geom,
                        "properties": {"obstacle_index": i},
                    })
                if features:
                    attrs["obstacles_geojson"] = {
                        "type": "FeatureCollection",
                        "features": features,
                    }
                    attrs["obstacle_count"] = len(features)
            except Exception as err:
                _LOGGER.warning(
                    "obstacles_geojson build failed: %s", err,
                )

        return attrs

