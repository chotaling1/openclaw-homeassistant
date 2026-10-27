"""The OpenClaw integration."""

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue, async_delete_issue
from homeassistant.helpers.service import async_register_admin_service

from .const import (
    CONF_AGENT_ID,
    CONF_MODEL,
    CONF_SESSION_KEY,
    CONF_STRIP_EMOJIS,
    CONF_THINKING,
    CONF_TIMEOUT,
    CONF_TTS_MAX_CHARS,
    CONF_USE_SSL,
    DEFAULT_AGENT_ID,
    DEFAULT_MODEL,
    DEFAULT_SESSION_KEY,
    DEFAULT_THINKING,
    DEFAULT_TIMEOUT,
    DEFAULT_USE_SSL,
    DOMAIN,
)
from .exceptions import (
    DevicePairingRequiredError,
    GatewayAuthenticationError,
    GatewayConnectionError,
    GatewayTimeoutError,
)
from .gateway_client import OpenClawGatewayClient

_LOGGER = logging.getLogger(__name__)

type OpenClawConfigEntry = ConfigEntry[OpenClawGatewayClient]

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.CONVERSATION, Platform.SENSOR]
SERVICE_RECONNECT = "reconnect"
SERVICE_SET_SESSION = "set_session"
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
_RECONNECT_SCHEMA = vol.Schema({vol.Optional("entry_id"): str})
_SESSION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SESSION_KEY): vol.All(str, vol.Length(min=1)),
        vol.Optional("entry_id"): str,
    }
)

_OPTION_KEYS = {
    CONF_TOKEN,
    CONF_USE_SSL,
    CONF_TIMEOUT,
    CONF_SESSION_KEY,
    CONF_AGENT_ID,
    CONF_MODEL,
    CONF_THINKING,
    CONF_STRIP_EMOJIS,
    CONF_TTS_MAX_CHARS,
}


def _target_clients(
    hass: HomeAssistant, entry_id: str | None
) -> list[OpenClawGatewayClient]:
    """Return the gateway clients a service call applies to.

    One loaded entry when ``entry_id`` is given, otherwise every loaded entry.
    """
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if entry_id:
        entries = [entry for entry in entries if entry.entry_id == entry_id]
        if not entries:
            _LOGGER.warning("Service call for unknown or unloaded entry: %s", entry_id)
    return [entry.runtime_data for entry in entries]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the integration's services once, for all entries."""

    async def _async_handle_reconnect(call: ServiceCall) -> None:
        for client in _target_clients(hass, call.data.get("entry_id")):
            await client.disconnect()
            await client.connect()

    async def _async_handle_set_session(call: ServiceCall) -> None:
        for client in _target_clients(hass, call.data.get("entry_id")):
            client.set_session_key(call.data[CONF_SESSION_KEY])

    async_register_admin_service(
        hass, DOMAIN, SERVICE_RECONNECT, _async_handle_reconnect, _RECONNECT_SCHEMA
    )
    async_register_admin_service(
        hass, DOMAIN, SERVICE_SET_SESSION, _async_handle_set_session, _SESSION_SCHEMA
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: OpenClawConfigEntry) -> bool:
    """Set up OpenClaw from a config entry."""
    _LOGGER.info("Setting up OpenClaw integration")

    # Migrate option-like keys from data to options for existing entries.
    if not entry.options:
        migrated = {
            key: entry.data.get(key)
            for key in _OPTION_KEYS
            if key in entry.data
        }
        if migrated:
            hass.config_entries.async_update_entry(entry, options=migrated)

    options = entry.options

    # Create Gateway client with config from entry.data
    gateway_client = OpenClawGatewayClient(
        hass=hass,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        token=options.get(CONF_TOKEN, entry.data.get(CONF_TOKEN)),
        use_ssl=options.get(
            CONF_USE_SSL, entry.data.get(CONF_USE_SSL, DEFAULT_USE_SSL)
        ),
        timeout=options.get(
            CONF_TIMEOUT, entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
        ),
        session_key=options.get(
            CONF_SESSION_KEY,
            entry.data.get(CONF_SESSION_KEY, DEFAULT_SESSION_KEY),
        ),
        agent_id=options.get(
            CONF_AGENT_ID, entry.data.get(CONF_AGENT_ID, DEFAULT_AGENT_ID)
        ),
        model=options.get(CONF_MODEL, entry.data.get(CONF_MODEL, DEFAULT_MODEL)),
        thinking=options.get(
            CONF_THINKING, entry.data.get(CONF_THINKING, DEFAULT_THINKING)
        ),
    )

    # Connect to Gateway
    try:
        await gateway_client.connect()
        _LOGGER.info(
            "Connected to OpenClaw Gateway at %s:%s",
            entry.data[CONF_HOST],
            entry.data[CONF_PORT],
        )
    except GatewayAuthenticationError as err:
        raise ConfigEntryAuthFailed(err) from err
    except (GatewayConnectionError, GatewayTimeoutError) as err:
        raise ConfigEntryNotReady(err) from err
    except Exception as err:
        raise ConfigEntryNotReady(err) from err

    # Register runtime fatal error callback for repair issues
    def _on_fatal_error(err: Exception) -> None:
        if isinstance(err, DevicePairingRequiredError):
            async_create_issue(
                hass,
                DOMAIN,
                "device_pairing_required",
                is_fixable=False,
                severity=IssueSeverity.WARNING,
                translation_key="device_pairing_required",
            )
        elif isinstance(err, GatewayAuthenticationError):
            async_create_issue(
                hass,
                DOMAIN,
                "gateway_auth_failed",
                is_fixable=False,
                severity=IssueSeverity.ERROR,
                translation_key="gateway_auth_failed",
            )

    gateway_client.set_fatal_error_handler(_on_fatal_error)

    # Clear any stale repair issue from a previous session
    async_delete_issue(hass, DOMAIN, "gateway_auth_failed")

    entry.runtime_data = gateway_client

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        _LOGGER.exception("Failed to set up platforms")
        await gateway_client.disconnect()
        return False

    # Register reload listener
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: OpenClawConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading OpenClaw integration")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Always disconnect, even if a platform failed to unload: a dangling
    # connection would keep receiving events for an entry that is going away.
    await entry.runtime_data.disconnect()
    _LOGGER.info("Disconnected from OpenClaw Gateway")

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: OpenClawConfigEntry) -> None:
    """Reload config entry.

    Delegate to Home Assistant's config-entry machinery rather than calling
    async_unload_entry/async_setup_entry directly. The manual path bypassed the
    entry's async_on_unload cleanup, so each reload leaked another update
    listener; the next options change then fired several concurrent reloads and
    platforms were set up twice ("already been setup").
    """
    await hass.config_entries.async_reload(entry.entry_id)
