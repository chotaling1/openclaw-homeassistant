"""Streaming replies through HA's chat log (what the pipeline feeds to TTS)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from homeassistant.components import conversation
from homeassistant.components.conversation.agent_manager import async_get_agent
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import chat_session

from .fake_gateway import FakeGatewayProtocol


def _input(text: str, agent_id: str, **kwargs: Any) -> conversation.ConversationInput:
    return conversation.ConversationInput(
        text=text,
        context=Context(),
        conversation_id=None,
        device_id=kwargs.get("device_id"),
        satellite_id=kwargs.get("satellite_id"),
        language="en",
        agent_id=agent_id,
        extra_system_prompt=kwargs.get("extra_system_prompt"),
    )


class _Session:
    """Run one turn the way the assist pipeline does, recording deltas."""

    def __init__(self, hass: HomeAssistant, agent_id: str) -> None:
        self.hass = hass
        self.agent_id = agent_id
        self.deltas: list[dict[str, Any]] = []
        self.chat_log: conversation.ChatLog | None = None

    async def start(self, text: str) -> asyncio.Task:
        entity = async_get_agent(self.hass, self.agent_id)
        user_input = _input(text, self.agent_id)

        async def run() -> conversation.ConversationResult:
            with (
                chat_session.async_get_chat_session(self.hass) as session,
                conversation.async_get_chat_log(
                    self.hass,
                    session,
                    user_input,
                    chat_log_delta_listener=lambda _log, delta: self.deltas.append(delta),
                ) as chat_log,
            ):
                self.chat_log = chat_log
                return await entity._async_handle_message(user_input, chat_log)

        return self.hass.async_create_task(run())

    @property
    def streamed(self) -> str:
        return "".join(d.get("content") or "" for d in self.deltas)


async def test_entity_declares_streaming(
    hass: HomeAssistant, agent_entity_id: str
) -> None:
    assert async_get_agent(hass, agent_entity_id).supports_streaming is True


async def test_chunks_reach_chat_log_before_run_ends(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol
) -> None:
    turn = _Session(hass, agent_entity_id)
    task = await turn.start("weather?")
    run_id = await gateway.next_run_id()

    await gateway.emit(
        "agent",
        {"runId": run_id, "stream": "assistant", "data": {"text": "It's sunny ", "delta": "It's sunny "}},
    )
    await asyncio.sleep(0.05)
    # The first sentence is already in the chat log stream while the run is open.
    assert turn.streamed == "It's sunny"
    assert not task.done()

    await gateway.emit(
        "agent", {"runId": run_id, "stream": "assistant", "data": {"delta": "and warm."}}
    )
    await gateway.end(run_id)
    result = await asyncio.wait_for(task, 5)

    assert turn.streamed == "It's sunny and warm."
    assert result.response.speech["plain"]["speech"] == "It's sunny and warm."
    assert turn.deltas[0] == {"role": "assistant"}


async def test_result_uses_chat_log_conversation_id(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol
) -> None:
    task = hass.async_create_task(
        conversation.async_converse(
            hass, "hello", None, Context(), language="en", agent_id=agent_entity_id
        )
    )
    run_id = await gateway.next_run_id()
    await gateway.reply(run_id, "Hi there.")
    result = await asyncio.wait_for(task, 5)

    # A first turn has no conversation id of its own; HA must get the chat
    # log's id back so a follow-up continues the same conversation.
    assert result.conversation_id

    follow = hass.async_create_task(
        conversation.async_converse(
            hass, "again", result.conversation_id, Context(), language="en",
            agent_id=agent_entity_id,
        )
    )
    run_id = await gateway.next_run_id()
    await gateway.reply(run_id, "Hello again.")
    second = await asyncio.wait_for(follow, 5)
    assert second.conversation_id == result.conversation_id


async def test_error_after_partial_content_keeps_partial(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol
) -> None:
    turn = _Session(hass, agent_entity_id)
    task = await turn.start("long story")
    run_id = await gateway.next_run_id()
    await gateway.emit(
        "agent",
        {"runId": run_id, "stream": "assistant", "data": {"text": "Once upon a time.", "delta": "Once upon a time."}},
    )
    await gateway.fail(run_id)
    result = await asyncio.wait_for(task, 5)

    assert result.response.speech["plain"]["speech"] == "Once upon a time."
    assert "error" not in turn.streamed


async def test_empty_reply_is_silent(
    hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol
) -> None:
    turn = _Session(hass, agent_entity_id)
    task = await turn.start("NO_REPLY please")
    run_id = await gateway.next_run_id()
    await gateway.end(run_id)
    result = await asyncio.wait_for(task, 5)

    assert result.response.speech["plain"]["speech"] == ""
    assert turn.chat_log.content[-1].role == "assistant"


class TestCap:
    @pytest.fixture
    def entry_options(self) -> dict[str, Any]:
        return {
            "agent_id": "voice",
            "session_key": "voice",
            "background_enabled": False,
            "tts_max_chars": 20,
        }

    async def test_stream_stops_at_cap(
        self, hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol
    ) -> None:
        turn = _Session(hass, agent_entity_id)
        task = await turn.start("talk")
        run_id = await gateway.next_run_id()
        await gateway.reply(run_id, "The quick ", "brown fox ", "jumps over ", "the lazy dog.")
        result = await asyncio.wait_for(task, 5)

        speech = result.response.speech["plain"]["speech"]
        assert speech == "The quick brown f..."
        assert turn.streamed == speech


class TestBackground:
    @pytest.fixture
    def entry_options(self) -> dict[str, Any]:
        return {
            "agent_id": "voice",
            "session_key": "voice",
            "background_enabled": True,
            "background_grace": 0.05,
            "holding_phrase": "Give me a moment",
            "proactive_satellite": "assist_satellite.kitchen",
        }

    async def test_fast_reply_streams_inline(
        self, hass: HomeAssistant, agent_entity_id: str, gateway: FakeGatewayProtocol
    ) -> None:
        turn = _Session(hass, agent_entity_id)
        task = await turn.start("hi")
        run_id = await gateway.next_run_id()
        await gateway.reply(run_id, "Hello ", "there.")
        result = await asyncio.wait_for(task, 5)

        assert result.response.speech["plain"]["speech"] == "Hello there."

    async def test_slow_run_defers_and_announces(
        self,
        hass: HomeAssistant,
        setup_entry: MockConfigEntry,
        agent_entity_id: str,
        gateway: FakeGatewayProtocol,
    ) -> None:
        announce = async_mock_service(hass, "assist_satellite", "announce")
        turn = _Session(hass, agent_entity_id)
        task = await turn.start("research something")
        run_id = await gateway.next_run_id()
        result = await asyncio.wait_for(task, 5)

        assert result.response.speech["plain"]["speech"] == "Give me a moment"
        assert result.continue_conversation is False

        await gateway.reply(run_id, "Here's what I found.")
        await hass.async_block_till_done()

        assert len(announce) == 1
        assert announce[0].data == {
            "entity_id": "assist_satellite.kitchen",
            "message": "Here's what I found.",
        }
