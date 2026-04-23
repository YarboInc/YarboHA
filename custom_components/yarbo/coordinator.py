"""Data coordinator for Yarbo integration — MQTT push only, no polling."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_SELECTED_DEVICES,
    DATA_ACCESS_TOKEN,
    DATA_REFRESH_TOKEN,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

def _deep_merge(target: dict, source: dict) -> None:
    """Deep merge source into target, preserving existing nested dict values.

    For nested dicts, merges recursively instead of replacing. Special keys
    '__online__' and 'HeartBeatMSG' in target are always preserved (not
    overwritten by device status pushes).
    """
    for key, value in source.items():
        if key in ("__online__", "HeartBeatMSG"):
            continue  # Never overwrite these from device status
        if (
            key in target
            and isinstance(target[key], dict)
            and isinstance(value, dict)
        ):
            target[key].update(value)
        else:
            target[key] = value


HEARTBEAT_TIMEOUT_SECONDS = 15
HEARTBEAT_CHECK_INTERVAL = timedelta(seconds=5)
WAKEUP_RENEWAL_INTERVAL = timedelta(minutes=4)




def _decode_map_data(raw, sn: str):
    """Decode get_map response's ``data`` field to a dict.

    Accepts a dict (normal case) or a string — some firmware serializes
    the response with ``data`` as a zlib-compressed binary blob that
    Python's json reader returns as a latin-1 str. Falls back through
    json-parse then zlib-decompress then base64+zlib.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        import json as _json
        import zlib as _zlib
        # 1) plain JSON string?
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        # 2) zlib-compressed binary, bytes-as-str (latin-1)?
        try:
            decompressed = _zlib.decompress(raw.encode("latin-1"))
            parsed = _json.loads(decompressed.decode("utf-8"))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        # 3) base64-wrapped zlib?
        try:
            import base64 as _b64
            decompressed = _zlib.decompress(_b64.b64decode(raw))
            parsed = _json.loads(decompressed.decode("utf-8"))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        _LOGGER.warning(
            "Map data for %s: could not decode 'data' string (len=%d)",
            sn, len(raw),
        )
    elif raw is not None:
        _LOGGER.warning(
            "Map data for %s: unexpected data type %s",
            sn, type(raw).__name__,
        )
    return {}

class YarboDataUpdateCoordinator(DataUpdateCoordinator[dict[str, dict]]):
    """Coordinate data from Yarbo SDK.

    Data channel: MQTT push (real-time) only.
    Token refresh: handled on-demand by SDK RestClient (auto-refresh on 401).
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,  # No polling — MQTT is the only data channel
        )
        self.entry = entry
        self._client = None
        self.devices: list = []
        self._gps_refs: dict[str, dict] = {}
        self._map_data: dict[str, dict] = {}
        # Raw zone data (with enable flags, names, range points, etc.)
        # as returned by get_map. Persisted so entities can expose
        # per-zone metadata without re-fetching.
        self._map_raw: dict[str, dict] = {}
        self._plan_data: dict[str, list[dict]] = {}
        self._last_heartbeat: dict[str, float] = {}
        self._user_standby: dict[str, bool] = {}
        self._selected_plan: dict[str, int | None] = {}
        # Live plan state (projected path, actual_clean_area, areaIds)
        # from the undocumented plan_feedback topic.
        self._plan_feedback: dict[str, dict] = {}
        # Dynamic obstacles (tmp_barrier_points) from the undocumented
        # cloud_points_feedback topic.
        self._cloud_points: dict[str, dict] = {}
        self._unsub_heartbeat_check: CALLBACK_TYPE | None = None
        self._unsub_wakeup_renewal: CALLBACK_TYPE | None = None

    async def async_setup(self) -> None:
        """Initialize SDK client, restore session, connect MQTT, subscribe."""
        from yarbo_robot_sdk import (
            AuthenticationError,
            TokenExpiredError,
            YarboClient,
            YarboSDKError,
        )

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
            raise ConfigEntryAuthFailed from err

        # Get device list and filter by selection
        try:
            all_devices = await self.hass.async_add_executor_job(client.get_devices)
        except TokenExpiredError as err:
            raise ConfigEntryAuthFailed from err
        except YarboSDKError as err:
            raise UpdateFailed(f"Failed to get devices: {err}") from err

        selected_sns = set(
            self.entry.options.get(CONF_SELECTED_DEVICES, [])
        )
        if selected_sns:
            self.devices = [d for d in all_devices if d.sn in selected_sns]
        else:
            self.devices = all_devices

        # Connect MQTT and subscribe to selected devices only
        try:
            await self.hass.async_add_executor_job(client.mqtt_connect)
            for device in self.devices:
                _LOGGER.info(
                    "Subscribing MQTT for %s (type_id=%s)",
                    device.sn, device.type_id,
                )
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
            _LOGGER.warning("MQTT connection failed: %s", err)

        # Subscribe to data_feedback for selected devices
        for device in self.devices:
            try:
                await self.hass.async_add_executor_job(
                    client.subscribe_data_feedback,
                    device.sn,
                    device.type_id,
                    None,
                )
                plan_topic = f"snowbot/{device.sn}/device/plan_feedback"

                def _on_plan_feedback(topic_str, payload, _sn=device.sn):
                    from yarbo_robot_sdk.codec import decode_mqtt_payload
                    try:
                        data = decode_mqtt_payload(payload)
                    except Exception as err:
                        _LOGGER.warning(
                            "plan_feedback decode failed for %s: %s", _sn, err,
                        )
                        return
                    if not isinstance(data, dict):
                        return
                    self._plan_feedback[_sn] = data
                    if self.data is not None:
                        self.hass.loop.call_soon_threadsafe(
                            self.async_set_updated_data, self.data,
                        )

                try:
                    await self.hass.async_add_executor_job(
                        client.mqtt_subscribe, plan_topic, _on_plan_feedback,
                    )
                except Exception as err:
                    _LOGGER.warning(
                        "plan_feedback subscribe failed: %s", err,
                    )

                cloud_topic = f"snowbot/{device.sn}/device/cloud_points_feedback"

                def _on_cloud_points(topic_str, payload, _sn=device.sn):
                    from yarbo_robot_sdk.codec import decode_mqtt_payload
                    try:
                        data = decode_mqtt_payload(payload)
                    except Exception as err:
                        _LOGGER.warning(
                            "cloud_points decode failed for %s: %s", _sn, err,
                        )
                        return
                    if not isinstance(data, dict):
                        return
                    self._cloud_points[_sn] = data
                    if self.data is not None:
                        self.hass.loop.call_soon_threadsafe(
                            self.async_set_updated_data, self.data,
                        )

                try:
                    await self.hass.async_add_executor_job(
                        client.mqtt_subscribe, cloud_topic, _on_cloud_points,
                    )
                except Exception as err:
                    _LOGGER.warning(
                        "cloud_points subscribe failed: %s", err,
                    )
            except YarboSDKError as err:
                _LOGGER.warning(
                    "data_feedback subscription failed for %s: %s", device.sn, err
                )

        # Auto wake-up: send working state immediately after MQTT connect
        for device in self.devices:
            self._user_standby[device.sn] = False
            await self._async_send_wakeup(device.sn, device.type_id)

        # Initial data fetch: plans, full DeviceMSG, GPS ref, map
        for device in self.devices:
            await self._async_fetch_plans(device.sn, device.type_id)
            await self._async_fetch_device_msg(device.sn, device.type_id)
            await self._async_fetch_gps_ref(device.sn, device.type_id)
            await self._async_fetch_map_data(device.sn, device.type_id)

        # Start heartbeat check timer (every 5s)
        self._unsub_heartbeat_check = async_track_time_interval(
            self.hass, self._async_check_heartbeats, HEARTBEAT_CHECK_INTERVAL
        )

        # Start wake-up renewal timer (every 4min)
        self._unsub_wakeup_renewal = async_track_time_interval(
            self.hass, self._async_renew_wakeup, WAKEUP_RENEWAL_INTERVAL
        )

        # Persist tokens (may have been refreshed during restore)
        self._update_stored_tokens()

    # ---- MQTT callbacks ----

    def _on_device_status(self, topic: str, data: dict[str, Any]) -> None:
        """Handle MQTT real-time status push — deep merge into coordinator data.

        Real-time pushes may contain only a subset of fields within nested dicts
        (e.g. StateMSG with only changed fields). A top-level update() would
        overwrite the entire nested dict, losing fields from the initial snapshot.
        Deep merge preserves existing nested values while updating changed ones.
        """
        parts = topic.split("/")
        if len(parts) >= 2:
            sn = parts[1]
            if self.data is None:
                self.data = {}
            if sn not in self.data:
                self.data[sn] = {}
            _deep_merge(self.data[sn], data)
            self.hass.loop.call_soon_threadsafe(
                self.async_set_updated_data, self.data
            )

    def _on_heart_beat(self, topic: str, data: dict[str, Any]) -> None:
        """Handle heart beat push — update timestamp and online state."""
        parts = topic.split("/")
        if len(parts) >= 2:
            sn = parts[1]
            self._last_heartbeat[sn] = time.monotonic()
            if self.data is None:
                self.data = {}
            if sn not in self.data:
                self.data[sn] = {}
            self.data[sn]["HeartBeatMSG"] = data
            # Mark online immediately on heartbeat
            self.data[sn]["__online__"] = True
            _LOGGER.debug("[heart_beat] sn=%s → online", sn)
            self.hass.loop.call_soon_threadsafe(
                self.async_set_updated_data, self.data
            )

    # ---- Heartbeat online detection ----

    async def _async_check_heartbeats(self, _now=None) -> None:
        """Check heartbeat timestamps and mark devices offline if timed out."""
        if self.data is None:
            return
        now = time.monotonic()
        changed = False
        for device in self.devices:
            sn = device.sn
            last = self._last_heartbeat.get(sn)
            was_online = self.data.get(sn, {}).get("__online__")
            if last is None or (now - last) > HEARTBEAT_TIMEOUT_SECONDS:
                if was_online is not False:
                    if sn not in self.data:
                        self.data[sn] = {}
                    self.data[sn]["__online__"] = False
                    _LOGGER.debug("[heartbeat_check] sn=%s → offline", sn)
                    changed = True
        if changed:
            self.async_set_updated_data(self.data)

    # ---- Auto wake-up and renewal ----

    async def _async_send_wakeup(self, sn: str, type_id: str) -> None:
        """Send set_working_state {state:1, source:smart_home} to wake device."""
        if self._client is None:
            return
        try:
            await self.hass.async_add_executor_job(
                self._client.mqtt_publish_command,
                sn,
                type_id,
                "set_working_state",
                {"state": 1, "source": "smart_home"},
            )
            _LOGGER.debug("[wakeup] Sent wake-up to %s", sn)
        except Exception as err:
            _LOGGER.warning("Failed to send wake-up to %s: %s", sn, err)

    async def _async_renew_wakeup(self, _now=None) -> None:
        """Renew wake-up for all non-standby devices (called every 4min)."""
        for device in self.devices:
            if not self._user_standby.get(device.sn, False):
                await self._async_send_wakeup(device.sn, device.type_id)

    def set_user_standby(self, sn: str, is_standby: bool) -> None:
        """Mark whether the user has manually set a device to standby."""
        self._user_standby[sn] = is_standby
        _LOGGER.debug("[standby] sn=%s standby=%s", sn, is_standby)

    # ---- Plan list storage ----

    @property
    def plan_data(self) -> dict[str, list[dict]]:
        """Auto plan list per device: {sn: [{id, name, areaIds, ...}]}."""
        return self._plan_data

    def set_selected_plan(self, sn: str, plan_id: int | None) -> None:
        """Record the user's plan selection for Start Plan button."""
        self._selected_plan[sn] = plan_id

    def get_selected_plan(self, sn: str) -> int | None:
        """Get the currently selected plan ID for a device."""
        return self._selected_plan.get(sn)

    async def _async_fetch_plans(self, sn: str, type_id: str) -> None:
        """Fetch auto plan list for a device. Non-blocking on failure."""
        if self._client is None:
            return
        try:
            result = await self.hass.async_add_executor_job(
                self._client.read_all_plan, sn, type_id
            )
            plans = result.get("data", {}).get("data", [])
            self._plan_data[sn] = plans
            _LOGGER.info("Plans for %s: %d plans loaded", sn, len(plans))
        except TimeoutError:
            _LOGGER.warning(
                "Plan list request timed out for %s. "
                "Plan selection will be unavailable.",
                sn,
            )
        except Exception as err:
            _LOGGER.warning("Failed to fetch plans for %s: %s", sn, err)

    async def async_refresh_plans(self, sn: str, type_id: str) -> None:
        """Re-fetch plan list and trigger entity update."""
        await self._async_fetch_plans(sn, type_id)
        if self.data is not None:
            self.async_set_updated_data(self.data)

    # ---- Full DeviceMSG snapshot ----

    async def _async_fetch_device_msg(self, sn: str, type_id: str) -> None:
        """Fetch full DeviceMSG snapshot and merge into coordinator data."""
        if self._client is None:
            return
        try:
            result = await self.hass.async_add_executor_job(
                self._client.get_device_msg, sn, type_id
            )
            msg_data = result.get("data", {})
            if self.data is None:
                self.data = {}
            if sn not in self.data:
                self.data[sn] = {}
            _deep_merge(self.data[sn], msg_data)
            _LOGGER.info(
                "Full DeviceMSG snapshot for %s loaded (%d top-level keys: %s)",
                sn, len(msg_data), list(msg_data.keys()),
            )
            # Debug: check specific fields
            state_msg = msg_data.get("StateMSG", {})
            _LOGGER.debug(
                "DeviceMSG snapshot StateMSG keys: %s, enable_sound=%s, volume=%s",
                list(state_msg.keys()) if isinstance(state_msg, dict) else "not-dict",
                state_msg.get("enable_sound") if isinstance(state_msg, dict) else "N/A",
                state_msg.get("volume") if isinstance(state_msg, dict) else "N/A",
            )
        except TimeoutError:
            _LOGGER.warning(
                "DeviceMSG request timed out for %s. "
                "Using real-time push data only.",
                sn,
            )
        except Exception as err:
            _LOGGER.warning("Failed to fetch DeviceMSG for %s: %s", sn, err)

    async def async_refresh_device_msg(self, sn: str, type_id: str) -> None:
        """Re-fetch full DeviceMSG snapshot and trigger entity update."""
        await self._async_fetch_device_msg(sn, type_id)
        if self.data is not None:
            self.async_set_updated_data(self.data)

    # ---- GPS reference ----

    @property
    def gps_refs(self) -> dict[str, dict]:
        """GPS reference origins per device."""
        return self._gps_refs

    async def _async_fetch_gps_ref(self, sn: str, type_id: str) -> None:
        """Fetch GPS reference origin for a device. Non-blocking on failure."""
        if self._client is None:
            return
        try:
            result = await self.hass.async_add_executor_job(
                self._client.read_gps_ref, sn, type_id
            )
            gps_data = result.get("data", {})
            self._gps_refs[sn] = gps_data
            rtk_fix = gps_data.get("rtkFixType")
            if rtk_fix != 1:
                _LOGGER.warning(
                    "GPS reference for %s has rtkFixType=%s (not fixed). "
                    "Device tracker will be unavailable until device is "
                    "initialized via the Yarbo app.",
                    sn, rtk_fix,
                )
            else:
                ref = gps_data.get("ref", {})
                _LOGGER.info(
                    "GPS reference for %s: lat=%s, lon=%s",
                    sn, ref.get("latitude"), ref.get("longitude"),
                )
        except TimeoutError:
            _LOGGER.warning(
                "GPS reference request timed out for %s. "
                "Device tracker will be unavailable.",
                sn,
            )
        except Exception as err:
            _LOGGER.warning("Failed to fetch GPS reference for %s: %s", sn, err)

    async def async_refresh_gps_ref(self, sn: str, type_id: str) -> None:
        """Re-fetch GPS reference origin and trigger entity update."""
        await self._async_fetch_gps_ref(sn, type_id)
        if self.data is not None:
            self.async_set_updated_data(self.data)

    # ---- Map data ----

    @property
    def map_raw(self) -> dict[str, dict]:
        """Raw get_map response per device (with enable flags, names, etc)."""
        return self._map_raw

    @property
    def plan_feedback(self) -> dict[str, dict]:
        """Latest plan_feedback payload per device (may be empty)."""
        return self._plan_feedback

    @property
    def cloud_points(self) -> dict[str, dict]:
        """Latest cloud_points_feedback (dynamic obstacles) per device."""
        return self._cloud_points

    @property
    def map_data(self) -> dict[str, dict]:
        """Map zone data per device: {sn: GeoJSON FeatureCollection}."""
        return self._map_data

    async def _async_fetch_map_data(self, sn: str, type_id: str) -> None:
        """Fetch map/zone data for a device. Non-blocking on failure."""
        if self._client is None:
            return
        try:
            result = await self.hass.async_add_executor_job(
                self._client.get_map, sn, type_id
            )
            raw_data = _decode_map_data(result.get("data", {}), sn)
            from yarbo_robot_sdk.device_helpers import convert_map_to_geojson
            fallback_ref = self._gps_refs.get(sn)
            geojson = convert_map_to_geojson(raw_data, fallback_ref)
            self._map_data[sn] = geojson
            self._map_raw[sn] = raw_data
            feature_count = len(geojson.get("features", []))
            _LOGGER.info("Map data for %s: %d features loaded", sn, feature_count)
        except TimeoutError:
            _LOGGER.warning(
                "Map data request timed out for %s. "
                "Map zones will be unavailable.",
                sn,
            )
        except Exception as err:
            _LOGGER.warning("Failed to fetch map data for %s: %s", sn, err)

    async def async_refresh_map_data(self, sn: str, type_id: str) -> None:
        """Re-fetch map data and trigger entity update."""
        await self._async_fetch_map_data(sn, type_id)
        if self.data is not None:
            self.async_set_updated_data(self.data)

    # ---- Internal ----

    async def _async_update_data(self) -> dict[str, dict]:
        """No-op — MQTT is the sole data channel. Polling is disabled."""
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
        """Clean up SDK client and timers on unload."""
        if self._unsub_heartbeat_check:
            self._unsub_heartbeat_check()
            self._unsub_heartbeat_check = None
        if self._unsub_wakeup_renewal:
            self._unsub_wakeup_renewal()
            self._unsub_wakeup_renewal = None
        if self._client:
            await self.hass.async_add_executor_job(self._client.close)
            self._client = None
