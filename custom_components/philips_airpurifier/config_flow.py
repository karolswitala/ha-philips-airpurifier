"""The Philips AirPurifier component."""

import contextlib
import ipaddress
import logging
import re
from typing import Any

from philips_airctrl import CoAPClient
import voluptuous as vol

from homeassistant import config_entries, exceptions
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .client import (
    async_create_client,
    async_fetch_device_info,
    async_fetch_status,
    async_fetch_status_with_nudge,
)
from .const import (
    CONF_DEVICE_ID,
    CONF_MAC,
    CONF_MODEL,
    CONF_PROTOCOL,
    CONF_STATUS,
    DOMAIN,
    PhilipsApi,
    Protocol,
)
from .device_models import DEVICE_MODELS
from .helpers import extract_model, extract_name
from .http_client import ATTR_MAC

_LOGGER = logging.getLogger(__name__)


def host_valid(host: str) -> bool:
    """Return True if hostname or IP address is valid."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    disallowed = re.compile(r"[^a-zA-Z\d\-]")
    return all(x and not disallowed.search(x) for x in host.split("."))


class PhilipsAirPurifierConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Philips AirPurifier."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._host: str | None = None
        self._mac: str | None = None
        self._model: Any = None
        self._name: Any = None
        self._device_id: str | None = None
        self._wifi_version: Any = None
        self._status: Any = None
        self._protocol: Protocol = Protocol.COAP

    def _get_schema(self, user_input: dict[str, Any]) -> vol.Schema:
        """Provide schema for user input."""
        return vol.Schema({vol.Required(CONF_HOST, default=user_input.get(CONF_HOST, "")): cv.string})

    async def _async_probe_host(self, host: str) -> dict[str, Any]:
        """Fetch status from host and validate basic connectivity.

        Tries CoAP first, since that is what all current-firmware devices speak,
        and falls back to the legacy HTTP API. The transport that answered is
        recorded on ``self._protocol`` and stored on the config entry, so setup
        never has to probe again.
        """
        if not host_valid(host):
            raise InvalidHost

        self._host = host
        _LOGGER.debug("trying to configure host: %s", self._host)

        try:
            _LOGGER.debug("trying to get status")
            status = await async_fetch_status(
                host,
                connect_timeout=30,
                status_timeout=30,
                create_client=CoAPClient.create,
            )
            _LOGGER.debug("got status")
            self._protocol = Protocol.COAP
            return status
        except TimeoutError:
            # Some firmwares never answer a status read and only push status on a
            # state change. Identify the model via the plaintext sys/dev/info
            # resource and, if it declares a status nudge, retry with that.
            nudged = await self._async_probe_host_with_nudge(host)
            if nudged is not None:
                self._protocol = Protocol.COAP
                return nudged
            # Older firmware has no CoAP stack at all and only serves the
            # legacy HTTP API, so a timeout is expected there rather than fatal.
            http_status = await self._async_probe_host_http(host)
            if http_status is not None:
                return http_status
            _LOGGER.warning(r"Timeout, host %s doesn't answer", self._host)
            raise
        except Exception as ex:
            _LOGGER.warning(r"CoAP connection failed: %s", ex)
            http_status = await self._async_probe_host_http(host)
            if http_status is not None:
                return http_status
            raise CannotConnect from ex

    async def _async_probe_host_http(self, host: str) -> dict[str, Any] | None:
        """Probe the legacy HTTP API, returning its status or None if unreachable.

        Also captures the MAC address the HTTP API reports, so the device is
        registered with a network-MAC connection and stays re-discoverable after
        a DHCP lease change (issue #8) -- the CoAP path only gets a MAC when the
        flow was started by DHCP discovery.
        """
        _LOGGER.debug("trying the HTTP API for host %s", host)
        try:
            client = await async_create_client(
                host,
                timeout=30,
                protocol=Protocol.HTTP,
                session=async_get_clientsession(self.hass),
            )
        except Exception as ex:  # noqa: BLE001
            _LOGGER.debug("HTTP API not available on %s: %s", host, ex)
            return None

        try:
            info = await client.get_device_info()
            if not self._mac and info.get(ATTR_MAC):
                self._mac = format_mac(str(info[ATTR_MAC]))
            status, _ = await client.get_status(observe=False)
        except Exception as ex:  # noqa: BLE001
            _LOGGER.debug("HTTP status read failed for %s: %s", host, ex)
            return None
        finally:
            with contextlib.suppress(Exception):
                await client.shutdown()

        _LOGGER.info("Host %s answered over the legacy HTTP API", host)
        self._protocol = Protocol.HTTP
        return status

    async def _async_probe_host_with_nudge(self, host: str) -> dict[str, Any] | None:
        """Retry the status read with a nudge for devices that need one.

        Returns the status dict on success, or None if the device's model does
        not declare a status nudge (so the caller falls back to the timeout).
        """
        try:
            info = await async_fetch_device_info(host)
        except Exception as ex:  # noqa: BLE001
            _LOGGER.debug("Could not read sys/dev/info from %s: %s", host, ex)
            return None

        # sys/dev/info reports the model under the plaintext "modelid" key.
        model = str(info.get(PhilipsApi.MODEL_ID, ""))[:9]
        config = DEVICE_MODELS.get(model) or DEVICE_MODELS.get(model[:6])
        if config is None or config.status_nudge is None:
            return None

        _LOGGER.info("Status read timed out for %s; retrying with nudge", model)
        try:
            return await async_fetch_status_with_nudge(
                host,
                config.status_nudge,
                connect_timeout=30,
                status_timeout=30,
                create_client=CoAPClient.create,
            )
        except Exception as ex:  # noqa: BLE001
            _LOGGER.warning("Nudge-based status read failed for %s: %s", host, ex)
            return None

    def _match_model(self, source: str) -> str | None:
        """Return the DEVICE_MODELS key for the detected model, or None if unsupported.

        Candidates are tried most specific first: the exact model, then the
        firmware-qualified name (e.g. ``AC0850/11 AWS_Philips_AIR``), then the
        six-character family.

        The firmware-qualified candidate is skipped when the device reports no
        ``WifiVersion``. Legacy HTTP firmware has no such field, and building the
        candidate unconditionally raised ``AttributeError`` on None.
        """
        model = str(self._model)
        model_family = model[:6]

        candidates = [model]
        if self._wifi_version:
            candidates.append(f"{model} {self._wifi_version.split('@')[0]}")
        candidates.append(model_family)

        for candidate in candidates:
            if candidate in DEVICE_MODELS:
                _LOGGER.info("Model %s supported", candidate)
                return candidate

        _LOGGER.warning(
            "Model %s of family %s not supported in %s",
            model,
            model_family,
            source,
        )
        return None

    def _async_find_matching_entry(self) -> config_entries.ConfigEntry | None:
        """Find an existing entry for the discovered device by MAC or host."""
        for entry in self._async_current_entries(include_ignore=False):
            if self._mac and entry.data.get(CONF_MAC) == self._mac:
                return entry
            if entry.data.get(CONF_HOST) == self._host:
                return entry
        return None

    def _async_update_existing_entry(self, entry: config_entries.ConfigEntry) -> None:
        """Update host and MAC of an existing entry from discovery info."""
        updates: dict[str, Any] = {CONF_HOST: self._host}
        if self._mac:
            updates[CONF_MAC] = self._mac
        if all(entry.data.get(key) == value for key, value in updates.items()):
            return
        _LOGGER.debug(
            "Updating entry %s with host %s and mac %s",
            entry.entry_id,
            self._host,
            self._mac,
        )
        self.hass.config_entries.async_update_entry(entry, data={**entry.data, **updates})
        self.hass.config_entries.async_schedule_reload(entry.entry_id)

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        """Handle initial step of auto discovery flow."""
        _LOGGER.debug("async_step_dhcp: called, found: %s", discovery_info)

        self._host = discovery_info.ip
        # Capture the MAC so the device is registered with a network-MAC
        # connection, enabling DHCP re-discovery after an IP change (issue #8).
        if discovery_info.macaddress:
            self._mac = format_mac(discovery_info.macaddress)
        _LOGGER.debug("trying to configure host: %s (mac: %s)", self._host, self._mac)

        # Match the discovery against existing entries by MAC (stable across
        # IP changes) or host before opening a CoAP connection. The purifiers
        # only serve a single CoAP client, so probing an already configured
        # device can disrupt its active connection, and a device that just
        # changed IP may not answer in time at all (issue #8).
        if (entry := self._async_find_matching_entry()) is not None:
            self._async_update_existing_entry(entry)
            return self.async_abort(reason="already_configured")

        # let's try and connect to an AirPurifier
        try:
            status = await self._async_probe_host(self._host)

            # Log the keys from the fetched status payload for debugging.
            _LOGGER.debug("status keys for host %s: %s", self._host, list(status.keys()))

        except TimeoutError:
            _LOGGER.warning(
                r"Timeout, host %s looks like a Philips AirPurifier but doesn't answer, aborting",
                self._host,
            )
            return self.async_abort(reason="cannot_connect")

        except InvalidHost, CannotConnect:
            return self.async_abort(reason="cannot_connect")

        # autodetect model
        self._model = extract_model(status)

        # autodetect Wifi version
        self._wifi_version = status.get(PhilipsApi.WIFI_VERSION)

        self._name = extract_name(status)
        self._device_id = status[PhilipsApi.DEVICE_ID]
        _LOGGER.debug(
            "Detected host %s as model %s with name: %s and firmware %s",
            self._host,
            self._model,
            self._name,
            self._wifi_version,
        )
        self._status = status

        # check if model is supported
        matched = self._match_model("DHCP discovery")
        if matched is None:
            return self.async_abort(reason="model_unsupported")
        self._model = matched

        # use the device ID as unique_id
        unique_id = self._device_id
        _LOGGER.debug("async_step_dhcp: unique_id=%s", unique_id)

        # set the unique id for the entry, abort if it already exists.
        # Update the stored host (and MAC) so a re-discovered device with a new
        # IP keeps working instead of erroring out (issue #8).
        await self.async_set_unique_id(unique_id)
        updates: dict[str, Any] = {CONF_HOST: self._host}
        if self._mac:
            updates[CONF_MAC] = self._mac
        self._abort_if_unique_id_configured(updates=updates)

        # store the data for the next step to get confirmation
        self.context.update(
            {
                "title_placeholders": {
                    CONF_NAME: self._model + " " + self._name,
                }
            }
        )

        # show the confirmation form to the user
        _LOGGER.debug("waiting for async_step_confirm")
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm the dhcp discovered data."""
        _LOGGER.debug("async_step_confirm called with user_input: %s", user_input)

        # user input was provided, so check and save it
        if user_input is not None:
            _LOGGER.debug(
                "entered creation for model %s with name '%s' at %s",
                self._model,
                self._name,
                self._host,
            )
            user_input[CONF_MODEL] = self._model
            user_input[CONF_NAME] = self._name
            user_input[CONF_DEVICE_ID] = self._device_id
            user_input[CONF_HOST] = self._host
            user_input[CONF_STATUS] = self._status
            user_input[CONF_PROTOCOL] = self._protocol
            if self._mac:
                user_input[CONF_MAC] = self._mac

            config_entry_name = f"{self._model} {self._name}"

            return self.async_create_entry(title=config_entry_name, data=user_input)

        _LOGGER.debug("showing confirmation form")
        # show the form to the user
        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"model": self._model, "name": self._name},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle initial step of user config flow."""
        errors: dict[str, str] = {}
        config_entry_data = user_input

        # user input was provided, so check and save it
        if config_entry_data is not None:
            try:
                host_input = config_entry_data[CONF_HOST]
                if not isinstance(host_input, str):
                    raise InvalidHost  # noqa: TRY301  # pragma: no cover

                # Don't probe a device that is already configured at this
                # host: the purifiers only serve a single CoAP client, so a
                # probe can disrupt the active connection.
                self._async_abort_entries_match({CONF_HOST: host_input})

                status = await self._async_probe_host(host_input)

                # autodetect model
                self._model = extract_model(status)

                # autodetect Wifi version
                self._wifi_version = status.get(PhilipsApi.WIFI_VERSION)

                self._name = extract_name(status)
                self._device_id = status[PhilipsApi.DEVICE_ID]
                config_entry_data[CONF_MODEL] = self._model
                config_entry_data[CONF_NAME] = self._name
                config_entry_data[CONF_DEVICE_ID] = self._device_id
                config_entry_data[CONF_HOST] = self._host
                config_entry_data[CONF_STATUS] = status
                config_entry_data[CONF_PROTOCOL] = self._protocol
                if self._mac:
                    config_entry_data[CONF_MAC] = self._mac

                _LOGGER.debug(
                    "Detected host %s as model %s with name: %s and firmware: %s",
                    self._host,
                    self._model,
                    self._name,
                    self._wifi_version,
                )

                # check if model is supported
                matched = self._match_model("user discovery")
                if matched is None:
                    return self.async_abort(reason="model_unsupported")
                # Only CONF_MODEL takes the matched key; the entry title keeps
                # the full detected model, as it always has.
                config_entry_data[CONF_MODEL] = matched

                # use the device ID as unique_id
                config_entry_unique_id = self._device_id
                config_entry_name = f"{self._model} {self._name}"

                # set the unique id for the entry, abort if it already exists
                await self.async_set_unique_id(config_entry_unique_id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})

                # compile a name and return the config entry
                return self.async_create_entry(title=config_entry_name, data=config_entry_data)

            except InvalidHost:
                errors[CONF_HOST] = "invalid_host"
            except TimeoutError, CannotConnect:
                errors[CONF_HOST] = "cannot_connect"

        if config_entry_data is None:
            config_entry_data = {}

        # no user_input so far
        schema = self._get_schema(config_entry_data)

        # show the form to the user
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle reconfiguration to update host while keeping device identity."""
        errors: dict[str, str] = {}

        entry_id = self.context.get("entry_id")
        if not isinstance(entry_id, str):
            return self.async_abort(reason="cannot_connect")  # pragma: no cover

        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            return self.async_abort(reason="cannot_connect")

        if user_input is not None:
            host_input = user_input.get(CONF_HOST)
            if not isinstance(host_input, str):
                errors[CONF_HOST] = "invalid_host"  # pragma: no cover
            else:
                try:
                    status = await self._async_probe_host(host_input)
                    detected_device_id = status.get(PhilipsApi.DEVICE_ID)
                    if detected_device_id != entry.data.get(CONF_DEVICE_ID):
                        return self.async_abort(reason="different_device")

                    updated_data = {
                        **entry.data,
                        CONF_HOST: host_input,
                        CONF_STATUS: status,
                        CONF_PROTOCOL: self._protocol,
                    }
                    self.hass.config_entries.async_update_entry(entry, data=updated_data)
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reconfigure_successful")
                except InvalidHost:
                    errors[CONF_HOST] = "invalid_host"
                except TimeoutError, CannotConnect:
                    errors[CONF_HOST] = "cannot_connect"

        schema = self._get_schema({CONF_HOST: entry.data.get(CONF_HOST, "")})
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)


class InvalidHost(exceptions.HomeAssistantError):
    """Error to indicate that hostname/IP address is invalid."""


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate that the device could not be reached."""
