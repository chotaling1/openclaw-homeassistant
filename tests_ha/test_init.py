"""Entry lifecycle and services against a real Home Assistant."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .fake_gateway import FakeGatewayProtocol


async def test_setup_and_unload(
    hass: HomeAssistant, setup_entry: MockConfigEntry, gateway: FakeGatewayProtocol
) -> None:
    assert setup_entry.state is ConfigEntryState.LOADED
    assert gateway.connected
    assert hass.services.has_service("openclaw", "reconnect")
    assert hass.services.has_service("openclaw", "set_session")

    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    assert setup_entry.state is ConfigEntryState.NOT_LOADED
    assert not gateway.connected


async def test_set_session_service(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    await hass.services.async_call(
        "openclaw", "set_session", {"session_key": "kitchen"}, blocking=True
    )
    assert setup_entry.runtime_data.session_key == "kitchen"


async def test_reconnect_service(
    hass: HomeAssistant, setup_entry: MockConfigEntry, gateway: FakeGatewayProtocol
) -> None:
    await hass.services.async_call("openclaw", "reconnect", {}, blocking=True)
    assert gateway.connected
