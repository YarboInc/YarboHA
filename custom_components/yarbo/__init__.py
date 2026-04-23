"""The Yarbo integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import YarboDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Yarbo from a config entry."""
    coordinator = YarboDataUpdateCoordinator(hass, entry)
    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_set_nogozone_enabled(hass)

    # Reload integration when options change (e.g. device selection)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration when device selection changes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: YarboDataUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
        if not hass.data.get(DOMAIN):
            hass.services.async_remove(DOMAIN, "set_nogozone_enabled")
    return unload_ok


def _register_set_nogozone_enabled(hass: HomeAssistant) -> None:
    """Register the yarbo.set_nogozone_enabled service (idempotent)."""
    if hass.services.has_service(DOMAIN, "set_nogozone_enabled"):
        return

    import voluptuous as vol
    from homeassistant.core import ServiceCall
    from homeassistant.exceptions import ServiceValidationError
    from homeassistant.helpers import config_validation as cv
    from homeassistant.helpers import device_registry as dr

    schema = vol.Schema({
        vol.Required("device_id"): cv.string,
        vol.Required("zone_id"): vol.Any(int, str),
        vol.Required("enabled"): cv.boolean,
    })

    async def handle(call: ServiceCall) -> None:
        device_id = call.data["device_id"]
        zone_id = call.data["zone_id"]
        enabled = call.data["enabled"]
        ha_device = dr.async_get(hass).async_get(device_id)
        if ha_device is None:
            raise ServiceValidationError(f"Device {device_id} not found")
        if not ha_device.config_entries:
            raise ServiceValidationError(
                f"Device {device_id} has no config entry"
            )
        entry_id = next(iter(ha_device.config_entries))
        coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            raise ServiceValidationError(
                f"Device {device_id} not managed by yarbo"
            )
        sn = next(
            (i[1] for i in ha_device.identifiers if i[0] == DOMAIN), None,
        )
        if sn is None:
            raise ServiceValidationError(
                f"Device {device_id} has no Yarbo identifier"
            )
        yarbo_dev = next(
            (d for d in coordinator.devices if d.sn == sn), None
        )
        if yarbo_dev is None:
            raise ServiceValidationError(
                f"Device {sn} not in coordinator.devices"
            )
        await coordinator.async_set_nogozone_enabled(
            sn, yarbo_dev.type_id, zone_id, enabled,
        )

    hass.services.async_register(
        DOMAIN, "set_nogozone_enabled", handle, schema=schema,
    )
