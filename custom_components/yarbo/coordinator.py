"""Data coordinator for Yarbo integration — REST polling + MQTT push."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DATA_ACCESS_TOKEN,
    DATA_REFRESH_TOKEN,
    DOMAIN,
    UPDATE_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)


class YarboDataUpdateCoordinator(DataUpdateCoordinator[dict[str, dict]]):
    """Coordinate data from Yarbo SDK.

    Primary data channel: MQTT push (real-time).
    Fallback: REST polling every 5 minutes.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=UPDATE_INTERVAL_MINUTES),
        )
        self.entry = entry
        self._client = None
        self.devices: list = []

    async def async_setup(self) -> None:
        """Initialize SDK client, restore session, connect MQTT, subscribe."""
        from yarbo_robot_sdk import (
            AuthenticationError,
            TokenExpiredError,
            YarboClient,
            YarboSDKError,
        )
        from yarbo_robot_sdk.models import Device

        import os
        api_url = os.environ.get("YARBO_API_BASE_URL")

        def _create_client():
            return YarboClient(api_base_url=api_url) if api_url else YarboClient()

        client = await self.hass.async_add_executor_job(_create_client)
        self._client = client

        # Try to restore session from stored tokens
        token = self.entry.data.get(DATA_ACCESS_TOKEN)
        refresh_token = self.entry.data.get(DATA_REFRESH_TOKEN)

        try:
            if token and refresh_token:
                await self.hass.async_add_executor_job(
                    client.restore_session,
                    self.entry.data[CONF_EMAIL],
                    token,
                    refresh_token,
                )
            else:
                await self.hass.async_add_executor_job(
                    client.login,
                    self.entry.data[CONF_EMAIL],
                    self.entry.data[CONF_PASSWORD],
                )
        except (AuthenticationError, TokenExpiredError) as err:
            # Tokens invalid and no password fallback worked — need reauth
            raise ConfigEntryAuthFailed from err

        # Get device list
        try:
            self.devices = await self.hass.async_add_executor_job(client.get_devices)
        except TokenExpiredError as err:
            raise ConfigEntryAuthFailed from err
        except YarboSDKError as err:
            raise UpdateFailed(f"Failed to get devices: {err}") from err

        # Connect MQTT and subscribe to all devices
        try:
            await self.hass.async_add_executor_job(client.mqtt_connect)
            for device in self.devices:
                await self.hass.async_add_executor_job(
                    client.subscribe_device_message,
                    device.sn,
                    device.type_id,
                    self._on_device_status,
                )
                try:
                    await self.hass.async_add_executor_job(
                        client.subscribe_heart_beat,
                        device.sn,
                        device.type_id,
                        self._on_heart_beat,
                    )
                except YarboSDKError as err:
                    _LOGGER.warning(
                        "Heart beat subscription failed for %s: %s", device.sn, err
                    )
        except YarboSDKError as err:
            _LOGGER.warning("MQTT connection failed, using REST polling only: %s", err)

        # Update stored tokens (may have been refreshed during restore)
        self._update_stored_tokens()

    def _on_device_status(self, topic: str, data: dict[str, Any]) -> None:
        """Handle MQTT real-time status push — update coordinator data."""
        parts = topic.split("/")
        if len(parts) >= 2:
            sn = parts[1]
            if self.data is None:
                self.data = {}
            # Preserve HeartBeatMSG from heart_beat push — DeviceMSG must not overwrite it
            heart_beat_data = self.data.get(sn, {}).get("HeartBeatMSG")
            if sn not in self.data:
                self.data[sn] = {}
            self.data[sn].update(data)
            if heart_beat_data is not None:
                self.data[sn]["HeartBeatMSG"] = heart_beat_data
            # Schedule entity update on HA event loop (called from MQTT thread)
            self.hass.loop.call_soon_threadsafe(
                self.async_set_updated_data, self.data
            )

    def _on_heart_beat(self, topic: str, data: dict[str, Any]) -> None:
        """Handle heart beat push — merge HeartBeatMSG into device data."""
        parts = topic.split("/")
        if len(parts) >= 2:
            sn = parts[1]
            if self.data is None:
                self.data = {}
            if sn not in self.data:
                self.data[sn] = {}
            # Store heart beat data under a dedicated namespace to avoid key collisions
            self.data[sn]["HeartBeatMSG"] = data
            _LOGGER.debug(
                "[heart_beat] sn=%s data=%s → HeartBeatMSG=%s",
                sn, data, self.data[sn].get("HeartBeatMSG"),
            )
            self.hass.loop.call_soon_threadsafe(
                self.async_set_updated_data, self.data
            )

    async def _async_update_data(self) -> dict[str, dict]:
        """REST fallback polling — refresh device list and tokens."""
        from yarbo_robot_sdk import TokenExpiredError, YarboSDKError

        if self._client is None:
            return self.data or {}

        try:
            self.devices = await self.hass.async_add_executor_job(
                self._client.get_devices
            )
            self._update_stored_tokens()
        except TokenExpiredError as err:
            raise ConfigEntryAuthFailed from err
        except YarboSDKError as err:
            raise UpdateFailed(f"Error fetching data: {err}") from err

        return self.data or {}

    def _update_stored_tokens(self) -> None:
        """Persist current tokens to config_entry if changed."""
        if self._client is None:
            return
        current_token = self._client.token
        current_refresh = self._client.refresh_token
        stored_token = self.entry.data.get(DATA_ACCESS_TOKEN)
        stored_refresh = self.entry.data.get(DATA_REFRESH_TOKEN)

        if current_token != stored_token or current_refresh != stored_refresh:
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={
                    **self.entry.data,
                    DATA_ACCESS_TOKEN: current_token,
                    DATA_REFRESH_TOKEN: current_refresh,
                },
            )

    async def async_shutdown(self) -> None:
        """Clean up SDK client on unload."""
        if self._client:
            await self.hass.async_add_executor_job(self._client.close)
            self._client = None
