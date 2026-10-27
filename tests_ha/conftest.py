"""Fixtures for tests against a real Home Assistant instance."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import custom_components

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from .fake_gateway import FakeGatewayProtocol

pytest_plugins = "pytest_homeassistant_custom_component"

# The plugin imports its own testing_config/custom_components package first;
# add this repo's integrations to it so HA's loader finds openclaw.
_REPO_COMPONENTS = str(Path(__file__).parent.parent / "custom_components")
if _REPO_COMPONENTS not in custom_components.__path__:
    custom_components.__path__.append(_REPO_COMPONENTS)

DOMAIN = "openclaw"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load custom_components/openclaw."""


@pytest.fixture
def fake_gateway() -> Generator[None]:
    from custom_components.openclaw import gateway_client

    FakeGatewayProtocol.instances.clear()
    with patch.object(gateway_client, "GatewayProtocol", FakeGatewayProtocol):
        yield


@pytest.fixture
def entry_options() -> dict[str, Any]:
    """Options for the config entry; override in a test module to vary them."""
    return {
        "agent_id": "voice",
        "session_key": "voice",
        "background_enabled": False,
        "strip_emojis": True,
    }


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, fake_gateway: None, entry_options: dict[str, Any]
) -> MockConfigEntry:
    """Set up the integration against the fake gateway."""
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="OpenClaw Gateway (test)",
        data={"host": "gateway.test", "port": 18789, "token": "t"},
        options=entry_options,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
def gateway(setup_entry: MockConfigEntry) -> FakeGatewayProtocol:
    return FakeGatewayProtocol.instances[-1]


@pytest.fixture
def agent_entity_id(hass: HomeAssistant, setup_entry: MockConfigEntry) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "conversation", DOMAIN, setup_entry.entry_id
    )
    assert entity_id
    return entity_id
