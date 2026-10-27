"""Conversation entity end to end: HA conversation API -> client -> fake gateway."""

from __future__ import annotations

import asyncio

from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant

from .fake_gateway import FakeGatewayProtocol


async def _converse(hass: HomeAssistant, agent_id: str, text: str, **kwargs):
    return hass.async_create_task(
        conversation.async_converse(
            hass, text, None, Context(), language="en", agent_id=agent_id, **kwargs
        )
    )


def _speech(result: conversation.ConversationResult) -> str:
    return result.response.speech["plain"]["speech"]


async def test_reply_is_spoken(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol
) -> None:
    task = await _converse(hass, agent_entity_id, "what's the weather")
    run_id = await gateway.next_run_id()

    params = gateway.agent_params()
    assert params["message"] == "what's the weather"
    assert params["sessionKey"] == "agent:voice:voice"

    await gateway.reply(run_id, "It's sunny ", "and warm.")
    result = await asyncio.wait_for(task, 5)

    assert _speech(result) == "It's sunny and warm."
    assert result.continue_conversation is False


async def test_question_keeps_mic_open(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol
) -> None:
    task = await _converse(hass, agent_entity_id, "add milk")
    run_id = await gateway.next_run_id()
    await gateway.reply(run_id, "Milk is already on the list. ", "Add another?")
    result = await asyncio.wait_for(task, 5)

    assert result.continue_conversation is True


async def test_emoji_stripped_from_speech(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol
) -> None:
    task = await _converse(hass, agent_entity_id, "hi")
    run_id = await gateway.next_run_id()
    await gateway.reply(run_id, "Done ✅ ", "enjoy 🧀!")
    result = await asyncio.wait_for(task, 5)

    assert _speech(result) == "Done enjoy!"


async def test_agent_error_speaks_friendly_message(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol
) -> None:
    task = await _converse(hass, agent_entity_id, "hi")
    run_id = await gateway.next_run_id()
    await gateway.fail(run_id, "stopReason: model_error")
    result = await asyncio.wait_for(task, 5)

    speech = _speech(result)
    assert "error while processing" in speech
    assert "model_error" not in speech


async def test_request_survives_gateway_restart(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol
) -> None:
    from custom_components.openclaw.exceptions import GatewayConnectionError

    gateway.fail_next_agent = GatewayConnectionError("Connection lost")
    task = await _converse(hass, agent_entity_id, "what's on the calendar")
    run_id = await gateway.next_run_id()
    await gateway.reply(run_id, "Nothing today.")
    result = await asyncio.wait_for(task, 5)

    assert _speech(result) == "Nothing today."
    keys = [p["idempotencyKey"] for m, p in gateway.requests if m == "agent"]
    assert len(keys) == 2 and keys[0] == keys[1]
