"""Config flow for MySensors."""
from __future__ import annotations

import os
from typing import Any
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.config_validation import positive_int
from .const import (
    CONF_BAUD_RATE,
    CONF_DEVICE,
    CONF_GATEWAY_TYPE,
    CONF_GATEWAY_TYPE_MQTT,
    CONF_GATEWAY_TYPE_SERIAL,
    CONF_GATEWAY_TYPE_TCP,
    CONF_PERSISTENCE_FILE,
    CONF_RETAIN,
    CONF_TCP_PORT,
    CONF_TOPIC_IN_PREFIX,
    CONF_TOPIC_OUT_PREFIX,
    CONF_VERSION,
    DOMAIN,
    ConfGatewayType,
)
from .gateway import MQTT_COMPONENT, is_serial_port, is_socket_address, try_connect

DEFAULT_BAUD_RATE = 115200
DEFAULT_TCP_PORT = 5003
DEFAULT_VERSION = "1.4"

_PORT_SELECTOR = vol.All(
    selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1, max=65535, mode=selector.NumberSelectorMode.BOX
        ),
    ),
    vol.Coerce(int),
)

def validate_persistence_file(value: str) -> str:
    """Validate that persistence file path ends in either .pickle or .json."""
    if value.endswith((".json", ".pickle")):
        return value
    raise vol.Invalid(f"Invalid file format. Please use `.json` or `.pickle`.")

class MySensorsConfigFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    def __init__(self) -> None:
        """Set up config flow."""
        self._gw_type: str | None = None

    async def async_step_user(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Create a config entry from frontend user input."""
        return self.async_show_menu(
            step_id="select_gateway_type",
            menu_options=["gw_serial", "gw_tcp", "gw_mqtt"],
        )

    async def async_step_gw_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Create config entry for a serial gateway."""
        return await self._async_create_gateway_entry(
            user_input, CONF_GATEWAY_TYPE_SERIAL, "gw_serial"
        )

    async def async_step_gw_tcp(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Create a config entry for a tcp gateway."""
        return await self._async_create_gateway_entry(
            user_input, CONF_GATEWAY_TYPE_TCP, "gw_tcp"
        )

    async def async_step_gw_mqtt(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Create a config entry for a mqtt gateway."""
        if MQTT_COMPONENT not in self.hass.config.components:
            return self.async_abort(reason="mqtt_required")
        
        return await self._async_create_gateway_entry(
            user_input, CONF_GATEWAY_TYPE_MQTT, "gw_mqtt"
        )

    async def _async_create_gateway_entry(
        self, user_input: dict[str, Any], gw_type: str, step_id: str
    ) -> FlowResult:
        """Create a config entry for a gateway."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._validate_topic(user_input, errors, CONF_TOPIC_IN_PREFIX)
            self._validate_topic(user_input, errors, CONF_TOPIC_OUT_PREFIX)
            errors.update(await self.validate_common(gw_type, errors, user_input))
            if not errors:
                return self._async_create_entry(user_input, gw_type)

        user_input = user_input or {}
        schema = self._get_gateway_schema(user_input, gw_type)
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

    def _validate_topic(self, user_input, errors, topic_key):
        """Validate MQTT topic."""
        try:
            valid_subscribe_topic(user_input[topic_key])
        except vol.Invalid:
            errors[topic_key] = f"invalid_{topic_key}_topic"
        else:
            if self._check_topic_exists(user_input[topic_key]):
                errors[topic_key] = f"duplicate_{topic_key}"

    def _get_gateway_schema(self, user_input, gw_type):
        """Create schema for a specific gateway type."""
        base_schema = {
            vol.Required(
                CONF_DEVICE,
                default=user_input.get(CONF_DEVICE, self._default_device(gw_type)),
            ): str,
            vol.Optional(CONF_BAUD_RATE, default=self._default_baud_rate(gw_type)): positive_int,
        }
        return vol.Schema(base_schema, **self._get_common_schema(user_input))

    def _default_device(self, gw_type):
        """Return default device based on gateway type."""
        return "/dev/ttyACM0" if gw_type == CONF_GATEWAY_TYPE_SERIAL else "127.0.0.1"

    def _default_baud_rate(self, gw_type):
        """Return default baud rate based on gateway type."""
        return DEFAULT_BAUD_RATE if gw_type == CONF_GATEWAY_TYPE_SERIAL else DEFAULT_TCP_PORT

    def _check_topic_exists(self, topic: str) -> bool:
        for other_config in self._async_current_entries():
            if topic == other_config.data.get(CONF_TOPIC_IN_PREFIX) or topic == other_config.data.get(CONF_TOPIC_OUT_PREFIX):
                return True
        return False

    @callback
    def _async_create_entry(self, user_input: dict[str, Any], gw_type: str) -> FlowResult:
        """Create the config entry."""
        return self.async_create_entry(
            title=f"{user_input[CONF_DEVICE]}",
            data={**user_input, CONF_GATEWAY_TYPE: gw_type},
        )

    def _get_common_schema(self, user_input):
        """Create a schema with options common to all gateway types."""
        schema = {
            vol.Required(
                CONF_VERSION,
                description={
                    "suggested_value": user_input.get(CONF_VERSION, DEFAULT_VERSION)
                },
            ): str,
            vol.Optional(CONF_PERSISTENCE_FILE, default=user_input.get(CONF_PERSISTENCE_FILE, "")): vol.All(str, validate_persistence_file),
        }
        return schema

    async def validate_common(
        self,
        gw_type: ConfGatewayType,
        errors: dict[str, str],
        user_input: dict[str, Any],
    ) -> dict[str, str]:
        """Validate parameters common to all gateway types."""
        errors.update(_validate_version(user_input[CONF_VERSION]))

        if gw_type != CONF_GATEWAY_TYPE_MQTT:
            verification_func = is_socket_address if gw_type == CONF_GATEWAY_TYPE_TCP else is_serial_port

            try:
                await self.hass.async_add_executor_job(
                    verification_func, user_input[CONF_DEVICE]
                )
            except vol.Invalid:
                errors[CONF_DEVICE] = "invalid_ip" if gw_type == CONF_GATEWAY_TYPE_TCP else "invalid_serial"
        
        persistence_file = user_input.get(CONF_PERSISTENCE_FILE)
        if persistence_file:
            self._validate_persistence_file(persistence_file, errors)

        if not errors:
            for other_entry in self._async_current_entries():
                if _is_same_device(gw_type, user_input, other_entry):
                    errors["base"] = "already_configured"
                    break

        if not errors and not await try_connect(self.hass, gw_type, user_input):
            errors["base"] = "cannot_connect"

        return errors
