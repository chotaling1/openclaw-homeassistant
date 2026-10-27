"""Room / device / user context sent as the gateway's extraSystemPrompt."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er

from .fake_gateway import FakeGatewayProtocol


async def _turn(
    hass: HomeAssistant, gateway: FakeGatewayProtocol, agent_id: str, **kwargs: Any
) -> dict[str, Any]:
    """Run one turn and return the params of its agent request."""
    context = kwargs.pop("context", Context())
    task = hass.async_create_task(
        conversation.async_converse(
            hass, "turn off the lights", None, context, language="en",
            agent_id=agent_id, **kwargs,
        )
    )
    run_id = await gateway.next_run_id()
    params = gateway.agent_params()
    await gateway.reply(run_id, "Done.")
    await asyncio.wait_for(task, 5)
    return params


@pytest.fixture
def kitchen_satellite(hass: HomeAssistant) -> tuple[str, str]:
    """A satellite device in the Kitchen area; returns (device_id, entity_id)."""
    sat_entry = MockConfigEntry(domain="esphome")
    sat_entry.add_to_hass(hass)
    area = ar.async_get(hass).async_create("Kitchen")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=sat_entry.entry_id,
        identifiers={("esphome", "kitchen-sat")},
        name="Kitchen Satellite",
    )
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)
    entity = er.async_get(hass).async_get_or_create(
        "assist_satellite", "esphome", "kitchen-sat", device_id=device.id,
        suggested_object_id="kitchen", original_name="Assist satellite",
    )
    return device.id, entity.entity_id


async def test_device_and_area_from_device_id(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol, kitchen_satellite
) -> None:
    device_id, _ = kitchen_satellite
    params = await _turn(hass, gateway, agent_entity_id, device_id=device_id)

    prompt = params["extraSystemPrompt"]
    assert "- Device: Kitchen Satellite" in prompt
    assert "- Area (room): Kitchen" in prompt
    assert params["message"] == "turn off the lights"


async def test_entity_area_overrides_device_area(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol, kitchen_satellite
) -> None:
    _, entity_id = kitchen_satellite
    den = ar.async_get(hass).async_create("Den")
    er.async_get(hass).async_update_entity(entity_id, area_id=den.id)

    params = await _turn(hass, gateway, agent_entity_id, satellite_id=entity_id)
    assert "- Area (room): Den" in params["extraSystemPrompt"]


async def test_user_name_from_context(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol
) -> None:
    user = await hass.auth.async_create_user("Chuck")
    params = await _turn(
        hass, gateway, agent_entity_id, context=Context(user_id=user.id)
    )
    assert "- User: Chuck" in params["extraSystemPrompt"]


async def test_ha_extra_system_prompt_forwarded(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol
) -> None:
    params = await _turn(
        hass, gateway, agent_entity_id,
        extra_system_prompt="You asked whether to close the garage door.",
    )
    assert params["extraSystemPrompt"] == "You asked whether to close the garage door."


async def test_no_context_sends_no_field(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol
) -> None:
    params = await _turn(hass, gateway, agent_entity_id)
    assert "extraSystemPrompt" not in params


async def test_gateway_rejecting_field_falls_back(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol, kitchen_satellite
) -> None:
    from custom_components.openclaw.exceptions import ProtocolError

    device_id, _ = kitchen_satellite
    gateway.fail_next_agent = ProtocolError("Request failed: unknown field extraSystemPrompt")
    params = await _turn(hass, gateway, agent_entity_id, device_id=device_id)

    # The retried request went out without the field, and the reply worked.
    assert "extraSystemPrompt" not in params
    agent_calls = [p for m, p in gateway.requests if m == "agent"]
    assert len(agent_calls) == 2
    assert "extraSystemPrompt" in agent_calls[0]

    # Later requests skip it until the next reconnect.
    params = await _turn(hass, gateway, agent_entity_id, device_id=device_id)
    assert "extraSystemPrompt" not in params


class TestOptionOff:
    @pytest.fixture
    def entry_options(self) -> dict[str, Any]:
        return {
            "agent_id": "voice",
            "session_key": "voice",
            "background_enabled": False,
            "send_ha_context": False,
        }

    async def test_only_ha_prompt_is_sent(
        self, hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol, kitchen_satellite
    ) -> None:
        device_id, _ = kitchen_satellite
        params = await _turn(hass, gateway, agent_entity_id, device_id=device_id)
        assert "extraSystemPrompt" not in params

        params = await _turn(
            hass, gateway, agent_entity_id, device_id=device_id,
            extra_system_prompt="Context from an automation.",
        )
        assert params["extraSystemPrompt"] == "Context from an automation."


class TestSessionPerDevice:
    @pytest.fixture
    def entry_options(self) -> dict[str, Any]:
        return {
            "agent_id": "voice",
            "session_key": "voice",
            "background_enabled": False,
            "session_per_device": True,
        }

    async def test_device_gets_own_session(
        self, hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol, kitchen_satellite
    ) -> None:
        device_id, entity_id = kitchen_satellite
        params = await _turn(hass, gateway, agent_entity_id, device_id=device_id)
        assert params["sessionKey"] == "agent:voice:voice-kitchen_satellite"

        # Same device via its satellite entity -> same session.
        params = await _turn(hass, gateway, agent_entity_id, satellite_id=entity_id)
        assert params["sessionKey"] == "agent:voice:voice-kitchen_satellite"

    async def test_request_without_device_uses_configured_session(
        self, hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol
    ) -> None:
        params = await _turn(hass, gateway, agent_entity_id)
        assert params["sessionKey"] == "agent:voice:voice"

    async def test_set_session_service_changes_the_base(
        self, hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol, kitchen_satellite
    ) -> None:
        device_id, _ = kitchen_satellite
        await hass.services.async_call(
            "openclaw", "set_session", {"session_key": "guest"}, blocking=True
        )
        params = await _turn(hass, gateway, agent_entity_id, device_id=device_id)
        assert params["sessionKey"] == "agent:voice:guest-kitchen_satellite"


async def test_shared_session_by_default(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol, kitchen_satellite
) -> None:
    device_id, _ = kitchen_satellite
    params = await _turn(hass, gateway, agent_entity_id, device_id=device_id)
    assert params["sessionKey"] == "agent:voice:voice"
