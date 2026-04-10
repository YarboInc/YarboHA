"""Config flow for Yarbo integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_SELECTED_DEVICES,
    DATA_ACCESS_TOKEN,
    DATA_REFRESH_TOKEN,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): str,
    }
)


class YarboConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Yarbo."""

    VERSION = 1

    _reauth_entry: ConfigEntry | None = None
    _email: str | None = None
    _password: str | None = None
    _token: str | None = None
    _refresh_token: str | None = None
    _available_devices: list = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> YarboOptionsFlow:
        """Get the options flow for this handler."""
        return YarboOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step — user enters email and password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]

            try:
                token, refresh_token = await self._async_login(email, password)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(email.lower())
                self._abort_if_unique_id_configured()

                # Store credentials temporarily and fetch device list
                self._email = email
                self._password = password
                self._token = token
                self._refresh_token = refresh_token

                try:
                    self._available_devices = await self._async_fetch_devices(
                        email, token, refresh_token
                    )
                except CannotConnect:
                    errors["base"] = "fetch_devices_failed"
                else:
                    if not self._available_devices:
                        errors["base"] = "no_devices_found"
                    else:
                        return await self.async_step_select_devices()

        return self.async_show_form(
            step_id="user",
            data_schema=USER_SCHEMA,
            errors=errors,
        )

    async def async_step_select_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle device selection step — user picks which devices to add."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected = user_input.get(CONF_SELECTED_DEVICES, [])
            if not selected:
                errors["base"] = "no_devices_selected"
            else:
                return self.async_create_entry(
                    title=self._email,
                    data={
                        CONF_EMAIL: self._email,
                        CONF_PASSWORD: self._password,
                        DATA_ACCESS_TOKEN: self._token,
                        DATA_REFRESH_TOKEN: self._refresh_token,
                    },
                    options={CONF_SELECTED_DEVICES: selected},
                )

        return self.async_show_form(
            step_id="select_devices",
            data_schema=self._build_device_schema(),
            errors=errors,
        )

    def _build_device_schema(self) -> vol.Schema:
        """Build multi-select schema from available devices."""
        device_options = {
            device.sn: f"{device.name} ({device.model}) - {device.sn}"
            for device in self._available_devices
        }
        return vol.Schema(
            {
                vol.Optional(CONF_SELECTED_DEVICES, default=[]): cv.multi_select(
                    device_options
                ),
            }
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth when refresh token expires."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask user for new password during reauth."""
        errors: dict[str, str] = {}

        if user_input is not None and self._reauth_entry is not None:
            email = self._reauth_entry.data[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]

            try:
                token, refresh_token = await self._async_login(email, password)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_PASSWORD: password,
                        DATA_ACCESS_TOKEN: token,
                        DATA_REFRESH_TOKEN: refresh_token,
                    },
                )
                await self.hass.config_entries.async_reload(
                    self._reauth_entry.entry_id
                )
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
        )

    async def _async_login(self, email: str, password: str) -> tuple[str, str]:
        """Login via SDK. Returns (access_token, refresh_token).

        Raises InvalidAuth or CannotConnect.
        """
        import os

        from yarbo_robot_sdk import AuthenticationError, YarboClient, YarboSDKError

        def _login():
            api_url = os.environ.get("YARBO_API_BASE_URL")
            client = YarboClient(api_base_url=api_url) if api_url else YarboClient()
            client.login(email, password)
            token = client.token
            refresh_token = client.refresh_token
            client.close()
            return token, refresh_token

        try:
            token, refresh_token = await self.hass.async_add_executor_job(_login)
            if not token or not refresh_token:
                raise InvalidAuth
            return token, refresh_token
        except AuthenticationError as err:
            raise InvalidAuth from err
        except YarboSDKError as err:
            raise CannotConnect from err

    async def _async_fetch_devices(
        self, email: str, token: str, refresh_token: str
    ) -> list:
        """Fetch device list using provided tokens.

        Creates a temporary SDK client, restores the session, fetches devices,
        then closes the client. Raises CannotConnect on failure.
        """
        import os

        from yarbo_robot_sdk import YarboClient, YarboSDKError

        def _fetch():
            api_url = os.environ.get("YARBO_API_BASE_URL")
            client = YarboClient(api_base_url=api_url) if api_url else YarboClient()
            try:
                client.restore_session(email, token, refresh_token)
                return client.get_devices()
            finally:
                client.close()

        try:
            return await self.hass.async_add_executor_job(_fetch)
        except YarboSDKError as err:
            _LOGGER.error("Failed to fetch devices: %s", err)
            raise CannotConnect from err


class YarboOptionsFlow(OptionsFlow):
    """Handle options flow for Yarbo — manage device selection."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show device selection form with current state."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected = user_input.get(CONF_SELECTED_DEVICES, [])
            if not selected:
                errors["base"] = "no_devices_selected"
            else:
                return self.async_create_entry(
                    data={CONF_SELECTED_DEVICES: selected}
                )

        # Fetch fresh device list from API via coordinator's client
        coordinator = self.hass.data.get(DOMAIN, {}).get(
            self.config_entry.entry_id
        )
        if coordinator and coordinator._client:
            try:
                devices = await self.hass.async_add_executor_job(
                    coordinator._client.get_devices
                )
            except Exception as err:
                _LOGGER.error("Failed to fetch devices in options flow: %s", err)
                errors["base"] = "fetch_devices_failed"
                devices = []
        else:
            errors["base"] = "fetch_devices_failed"
            devices = []

        if not devices and not errors:
            errors["base"] = "no_devices_found"

        # Build multi-select with current selection pre-checked
        current_selected = self.config_entry.options.get(
            CONF_SELECTED_DEVICES, []
        )
        # Filter out stale SNs no longer returned by API
        valid_sns = {d.sn for d in devices}
        current_selected = [sn for sn in current_selected if sn in valid_sns]

        device_options = {
            d.sn: f"{d.name} ({d.model}) - {d.sn}" for d in devices
        }
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SELECTED_DEVICES, default=current_selected
                ): cv.multi_select(device_options),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )


class InvalidAuth(Exception):
    """Error to indicate invalid credentials."""


class CannotConnect(Exception):
    """Error to indicate connection failure."""
