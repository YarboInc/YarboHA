"""Button platform for Yarbo integration — data refresh and plan control."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
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
    """Set up Yarbo button entities."""
    coordinator: YarboDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device in coordinator.devices:
        # Data refresh buttons
        entities.append(YarboRefreshGpsRefButton(coordinator, device))
        entities.append(YarboRefreshMapDataButton(coordinator, device))
        entities.append(YarboRefreshDeviceMsgButton(coordinator, device))
        entities.append(YarboRefreshPlansButton(coordinator, device))
        # Plan control buttons
        entities.append(YarboStartPlanButton(coordinator, device))
        entities.append(YarboPausePlanButton(coordinator, device))
        entities.append(YarboResumePlanButton(coordinator, device))
        entities.append(YarboStopPlanButton(coordinator, device))
        # Recharge button
        entities.append(YarboRechargeButton(coordinator, device))
    async_add_entities(entities)


def _device_info(device) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, device.sn)},
        name=device.name,
        manufacturer="Yarbo",
        model=device.model,
        serial_number=device.sn,
    )


# ---- Data refresh buttons ----


class YarboRefreshGpsRefButton(
    CoordinatorEntity[YarboDataUpdateCoordinator], ButtonEntity
):
    """Button to refresh GPS reference origin from the device."""

    _attr_has_entity_name = True
    _attr_name = "Refresh GPS Reference"
    _attr_icon = "mdi:crosshairs-gps"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.sn}_refresh_gps_ref"

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._device)

    async def async_press(self) -> None:
        _LOGGER.info("Refreshing GPS reference for %s", self._device.sn)
        await self.coordinator.async_refresh_gps_ref(
            self._device.sn, self._device.type_id
        )


class YarboRefreshMapDataButton(
    CoordinatorEntity[YarboDataUpdateCoordinator], ButtonEntity
):
    """Button to refresh map/zone data from the device."""

    _attr_has_entity_name = True
    _attr_name = "Refresh Map Data"
    _attr_icon = "mdi:map-marker-radius"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.sn}_refresh_map_data"

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._device)

    async def async_press(self) -> None:
        _LOGGER.info("Refreshing map data for %s", self._device.sn)
        await self.coordinator.async_refresh_map_data(
            self._device.sn, self._device.type_id
        )


class YarboRefreshDeviceMsgButton(
    CoordinatorEntity[YarboDataUpdateCoordinator], ButtonEntity
):
    """Button to refresh full DeviceMSG snapshot from the device."""

    _attr_has_entity_name = True
    _attr_name = "Refresh Device Data"
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.sn}_refresh_device_msg"

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._device)

    async def async_press(self) -> None:
        _LOGGER.info("Refreshing DeviceMSG for %s", self._device.sn)
        await self.coordinator.async_refresh_device_msg(
            self._device.sn, self._device.type_id
        )


class YarboRefreshPlansButton(
    CoordinatorEntity[YarboDataUpdateCoordinator], ButtonEntity
):
    """Button to refresh auto plan list from the device."""

    _attr_has_entity_name = True
    _attr_name = "Refresh Plans"
    _attr_icon = "mdi:clipboard-list"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.sn}_refresh_plans"

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._device)

    async def async_press(self) -> None:
        _LOGGER.info("Refreshing plans for %s", self._device.sn)
        await self.coordinator.async_refresh_plans(
            self._device.sn, self._device.type_id
        )


# ---- Plan control buttons ----


class YarboStartPlanButton(
    CoordinatorEntity[YarboDataUpdateCoordinator], ButtonEntity
):
    """Button to start a selected auto plan."""

    _attr_has_entity_name = True
    _attr_name = "Start Plan"
    _attr_icon = "mdi:play"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.sn}_start_plan"

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._device)

    async def async_press(self) -> None:
        sn = self._device.sn
        data = self.coordinator.data.get(sn, {}) if self.coordinator.data else {}

        # Check 1: Device must be online
        if not data.get("__online__"):
            raise HomeAssistantError("Cannot start plan: device is offline")

        # Check 2: Plan must be selected
        plan_id = self.coordinator.get_selected_plan(sn)
        if plan_id is None:
            raise HomeAssistantError("Cannot start plan: no plan selected")

        # Check 3: Not wired charging (BodyMsg.rechargeState: 1=wired charging, 3=wired locked)
        recharge_state = (data.get("BodyMsg") or {}).get("rechargeState")
        if isinstance(recharge_state, (int, float)) and recharge_state in (1, 3):
            raise HomeAssistantError(
                "Cannot start plan: device is wired charging"
            )

        # Check 4: Not wireless charging (BatteryMSG.status > 1 means charging)
        battery_status = (data.get("BatteryMSG") or {}).get("status")
        if isinstance(battery_status, (int, float)) and battery_status > 1:
            raise HomeAssistantError(
                "Cannot start plan: device is charging"
            )

        # Check 5: RTK signal must not be weak (4=Strong, 5=Medium)
        rtk_status = (data.get("RTKMSG") or {}).get("status")
        rtk_val = int(rtk_status) if rtk_status is not None else 0
        if rtk_val not in (4, 5):
            raise HomeAssistantError(
                "Cannot start plan: RTK/GPS signal is weak"
            )

        # Check 6: No plan already running (on_going_planning > 0 and != 5 means active)
        planning = (data.get("StateMSG") or {}).get("on_going_planning", 0)
        if isinstance(planning, (int, float)) and planning > 0 and planning != 5:
            raise HomeAssistantError(
                "Cannot start plan: a plan is already running"
            )

        # Check 7: Not returning to charge (on_going_recharging > 0 and != 4)
        recharging = (data.get("StateMSG") or {}).get("on_going_recharging", 0)
        if isinstance(recharging, (int, float)) and recharging > 0 and recharging != 4:
            raise HomeAssistantError(
                "Cannot start plan: device is returning to charge"
            )

        # All checks passed — build payload and send
        payload: dict = {"id": plan_id}

        # Read percent from Plan Start Percent entity via entity registry
        percent = self._get_plan_percent()
        if percent is not None and percent > 0:
            payload["percent"] = int(percent)

        _LOGGER.info("Starting plan %s for %s: %s", plan_id, sn, payload)
        try:
            await self.hass.async_add_executor_job(
                self.coordinator._client.mqtt_publish_command,
                sn,
                self._device.type_id,
                "start_plan",
                payload,
            )
        except Exception as exc:
            _LOGGER.error("Failed to start plan: %s", exc)

    def _get_plan_percent(self) -> float | None:
        """Read plan start percent from the entity state registry."""
        entity_id = f"number.{self._device.name.lower().replace(' ', '_')}_plan_start_percent"
        state = self.hass.states.get(entity_id)
        if state and state.state not in (None, "unknown", "unavailable"):
            try:
                return float(state.state)
            except ValueError:
                pass
        return None


class YarboPausePlanButton(
    CoordinatorEntity[YarboDataUpdateCoordinator], ButtonEntity
):
    """Button to pause the current plan."""

    _attr_has_entity_name = True
    _attr_name = "Pause Plan"
    _attr_icon = "mdi:pause"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.sn}_pause_plan"

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._device)

    async def async_press(self) -> None:
        _LOGGER.info("Pausing plan for %s", self._device.sn)
        try:
            await self.hass.async_add_executor_job(
                self.coordinator._client.mqtt_publish_command,
                self._device.sn,
                self._device.type_id,
                "pause",
                {},
            )
        except Exception as exc:
            _LOGGER.error("Failed to pause plan: %s", exc)


class YarboResumePlanButton(
    CoordinatorEntity[YarboDataUpdateCoordinator], ButtonEntity
):
    """Button to resume the paused plan."""

    _attr_has_entity_name = True
    _attr_name = "Resume Plan"
    _attr_icon = "mdi:play"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.sn}_resume_plan"

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._device)

    async def async_press(self) -> None:
        _LOGGER.info("Resuming plan for %s", self._device.sn)
        try:
            await self.hass.async_add_executor_job(
                self.coordinator._client.mqtt_publish_command,
                self._device.sn,
                self._device.type_id,
                "resume",
                {},
            )
        except Exception as exc:
            _LOGGER.error("Failed to resume plan: %s", exc)


class YarboStopPlanButton(
    CoordinatorEntity[YarboDataUpdateCoordinator], ButtonEntity
):
    """Button to stop the current plan."""

    _attr_has_entity_name = True
    _attr_name = "Stop Plan"
    _attr_icon = "mdi:stop"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.sn}_stop_plan"

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._device)

    async def async_press(self) -> None:
        _LOGGER.info("Stopping plan for %s", self._device.sn)
        try:
            await self.hass.async_add_executor_job(
                self.coordinator._client.mqtt_publish_command,
                self._device.sn,
                self._device.type_id,
                "stop",
                {},
            )
        except Exception as exc:
            _LOGGER.error("Failed to stop plan: %s", exc)


# ---- Recharge button ----


class YarboRechargeButton(
    CoordinatorEntity[YarboDataUpdateCoordinator], ButtonEntity
):
    """Button to send the device back to the charging station."""

    _attr_has_entity_name = True
    _attr_name = "Return to Charge"
    _attr_icon = "mdi:battery-charging"

    def __init__(self, coordinator, device) -> None:
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{device.sn}_recharge"

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._device)

    async def async_press(self) -> None:
        sn = self._device.sn
        data = self.coordinator.data.get(sn, {}) if self.coordinator.data else {}

        # Check 1: Device must be online
        if not data.get("__online__"):
            raise HomeAssistantError(
                "Cannot return to charge: device is offline"
            )

        # Check 2: Not currently charging (BatteryMSG.status > 1 means charging)
        battery_status = (data.get("BatteryMSG") or {}).get("status")
        if isinstance(battery_status, (int, float)) and battery_status > 1:
            raise HomeAssistantError(
                "Cannot return to charge: device is already charging"
            )

        # Check 3: Not already recharging (on_going_recharging > 0 and != 4)
        recharging = (data.get("StateMSG") or {}).get("on_going_recharging", 0)
        if isinstance(recharging, (int, float)) and recharging > 0 and recharging != 4:
            raise HomeAssistantError(
                "Cannot return to charge: device is already returning to charge"
            )

        # Check 4: RTK signal must not be weak (4=Strong, 5=Medium, else=Weak)
        rtk_status = (data.get("RTKMSG") or {}).get("status")
        rtk_val = int(rtk_status) if rtk_status is not None else 0
        if rtk_val not in (4, 5):
            raise HomeAssistantError(
                "Cannot return to charge: RTK/GPS signal is weak"
            )

        _LOGGER.info("Starting recharge for %s", sn)
        try:
            # Step 1: Disable wireless charging
            await self.hass.async_add_executor_job(
                self.coordinator._client.mqtt_publish_command,
                sn,
                self._device.type_id,
                "wireless_charging_cmd",
                {"cmd": 0},
            )
            # Step 2: Send recharge command
            await self.hass.async_add_executor_job(
                self.coordinator._client.mqtt_publish_command,
                sn,
                self._device.type_id,
                "cmd_recharge",
                {"cmd": 2},
            )
        except Exception as exc:
            _LOGGER.error("Failed to send recharge command: %s", exc)
