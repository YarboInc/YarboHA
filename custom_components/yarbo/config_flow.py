"""Config flow for Yarbo integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

from .const import (
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

                return self.async_create_entry(
                    title=email,
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                        DATA_ACCESS_TOKEN: token,
                        DATA_REFRESH_TOKEN: refresh_token,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=USER_SCHEMA,
            errors=errors,
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


class InvalidAuth(Exception):
    """Error to indicate invalid credentials."""


class CannotConnect(Exception):
    """Error to indicate connection failure."""
