"""Background (deferred) runs and their satellite reports, on real HA."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .fake_gateway import FakeGatewayProtocol

BASE_OPTIONS = {
    "agent_id": "voice",
    "session_key": "voice",
    "background_enabled": True,
    "background_grace": 0.05,
}


@pytest.fixture
def entry_options() -> dict[str, Any]:
    return {**BASE_OPTIONS, "proactive_satellite": "assist_satellite.kitchen"}


@pytest.fixture
def announce(hass: HomeAssistant):
    return async_mock_service(hass, "assist_satellite", "announce")


async def _ask(
    hass: HomeAssistant, agent_id: str, text: str = "do something slow", **kwargs: Any
) -> conversation.ConversationResult:
    return await asyncio.wait_for(
        conversation.async_converse(
            hass, text, None, Context(), language="en", agent_id=agent_id, **kwargs
        ),
        5,
    )


def _speech(result: conversation.ConversationResult) -> str:
    return result.response.speech["plain"]["speech"]


async def test_report_targets_originating_satellite(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol, announce
) -> None:
    sat_entry = MockConfigEntry(domain="esphome")
    sat_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=sat_entry.entry_id, identifiers={("esphome", "study-sat")}
    )
    er.async_get(hass).async_get_or_create(
        "assist_satellite", "esphome", "study-sat", device_id=device.id,
        suggested_object_id="study",
    )

    result = await _ask(hass, agent_entity_id, device_id=device.id)
    assert _speech(result) == "On it — I'll let you know when it's done."

    await gateway.reply(gateway.run_ids[-1], "Done")
    await hass.async_block_till_done()

    assert [c.data["entity_id"] for c in announce] == ["assist_satellite.study"]


async def test_detached_error_announces_failure_phrase(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol, announce
) -> None:
    from custom_components.openclaw.const import BACKGROUND_ERROR_PHRASE

    await _ask(hass, agent_entity_id)
    await gateway.fail(gateway.run_ids[-1])
    await hass.async_block_till_done()

    assert [c.data["message"] for c in announce] == [BACKGROUND_ERROR_PHRASE]


async def test_detached_timeout_announces_timeout_phrase(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol, announce
) -> None:
    from custom_components.openclaw.const import BACKGROUND_TIMEOUT_PHRASE

    with patch("custom_components.openclaw.conversation.BACKGROUND_MAX_SECONDS", 0.1):
        await _ask(hass, agent_entity_id)
        await asyncio.sleep(0.3)
        await hass.async_block_till_done()

    assert [c.data["message"] for c in announce] == [BACKGROUND_TIMEOUT_PHRASE]


async def test_report_applies_emoji_strip(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol, announce
) -> None:
    await _ask(hass, agent_entity_id)
    await gateway.reply(gateway.run_ids[-1], "All done 🎉")
    await hass.async_block_till_done()

    assert [c.data["message"] for c in announce] == ["All done"]


async def test_two_runs_announce_independently_out_of_order(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol, announce
) -> None:
    await _ask(hass, agent_entity_id, "first")
    first = gateway.run_ids[-1]
    await _ask(hass, agent_entity_id, "second")
    second = gateway.run_ids[-1]

    await gateway.reply(second, "Second result")
    await gateway.reply(first, "First result")
    await hass.async_block_till_done()

    assert [c.data["message"] for c in announce] == ["Second result", "First result"]


class TestNoSatellite:
    @pytest.fixture
    def entry_options(self) -> dict[str, Any]:
        return dict(BASE_OPTIONS)

    async def test_report_dropped(
        self, hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol, announce
    ) -> None:
        await _ask(hass, agent_entity_id)
        await gateway.reply(gateway.run_ids[-1], "Done")
        await hass.async_block_till_done()
        assert announce == []


class TestShortAgentTimeout:
    @pytest.fixture
    def entry_options(self) -> dict[str, Any]:
        return {**BASE_OPTIONS, "proactive_satellite": "assist_satellite.kitchen", "timeout": 0.05}

    async def test_detached_run_outlives_agent_timeout(
        self, hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol, announce
    ) -> None:
        await _ask(hass, agent_entity_id)
        await asyncio.sleep(0.3)
        await gateway.reply(gateway.run_ids[-1], "Slow but done")
        await hass.async_block_till_done()
        assert [c.data["message"] for c in announce] == ["Slow but done"]


class TestBackgroundDisabled:
    @pytest.fixture
    def entry_options(self) -> dict[str, Any]:
        return {"agent_id": "voice", "session_key": "voice", "background_enabled": False, "timeout": 0.05}

    async def test_timeout_speaks_friendly_message(
        self, hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol, announce
    ) -> None:
        result = await _ask(hass, agent_entity_id)
        assert _speech(result) == "The response took too long. Please try again."
        assert result.continue_conversation is False
        await hass.async_block_till_done()
        assert announce == []
