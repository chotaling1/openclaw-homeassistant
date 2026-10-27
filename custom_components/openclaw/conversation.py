"""Conversation entity for OpenClaw integration."""

import asyncio
import logging
from typing import Any, AsyncIterator

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BACKGROUND_ERROR_PHRASE,
    BACKGROUND_MAX_SECONDS,
    BACKGROUND_TIMEOUT_PHRASE,
    CONF_BACKGROUND_ENABLED,
    CONF_BACKGROUND_GRACE,
    CONF_HOLDING_PHRASE,
    CONF_PROACTIVE_ENABLED,
    CONF_PROACTIVE_MODE,
    CONF_PROACTIVE_SATELLITE,
    CONF_STRIP_EMOJIS,
    CONF_TTS_MAX_CHARS,
    DEFAULT_BACKGROUND_ENABLED,
    DEFAULT_BACKGROUND_GRACE,
    DEFAULT_HOLDING_PHRASE,
    DEFAULT_PROACTIVE_ENABLED,
    DEFAULT_PROACTIVE_MODE,
    DEFAULT_STRIP_EMOJIS,
    DEFAULT_TTS_MAX_CHARS,
    DOMAIN,
    PROACTIVE_MODE_START_CONVERSATION,
)
from .exceptions import (
    AgentExecutionError,
    GatewayAuthenticationError,
    GatewayConnectionError,
    GatewayTimeoutError,
)
from .gateway_client import AgentRun, OpenClawGatewayClient
from .text import (
    SpeechFilter,
    response_expects_followup,
    strip_emojis,
    trim_tts_text,
)

_LOGGER = logging.getLogger(__name__)


# Spoken reply for each failure, most specific class first.
_ERROR_PHRASES: tuple[tuple[type[Exception], str], ...] = (
    (
        GatewayAuthenticationError,
        "The gateway token is no longer valid. Please update it in "
        "Settings, Devices and Services, OpenClaw, Configure.",
    ),
    (
        GatewayConnectionError,
        "I'm having trouble connecting to the Gateway. "
        "Please check your configuration.",
    ),
    (GatewayTimeoutError, "The response took too long. Please try again."),
    (
        AgentExecutionError,
        "I encountered an error while processing your request. "
        "Please try again.",
    ),
)
_UNEXPECTED_ERROR_PHRASE = "An unexpected error occurred. Please try again."


def _log_error_phrase(err: Exception) -> str:
    """Log a request failure and return the phrase to speak for it."""
    if isinstance(err, GatewayTimeoutError):
        _LOGGER.warning("Gateway timeout: %s", err)
    elif isinstance(err, GatewayAuthenticationError):
        _LOGGER.error("Gateway authentication error: %s", err)
    elif isinstance(err, GatewayConnectionError):
        _LOGGER.error("Gateway connection error: %s", err)
    elif isinstance(err, AgentExecutionError):
        _LOGGER.error("Agent execution error: %s", err)
    else:
        _LOGGER.error("Unexpected error handling message", exc_info=err)
    for err_type, phrase in _ERROR_PHRASES:
        if isinstance(err, err_type):
            return phrase
    return _UNEXPECTED_ERROR_PHRASE


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OpenClaw conversation entity."""
    gateway_client: OpenClawGatewayClient = config_entry.runtime_data

    async_add_entities([OpenClawConversationEntity(config_entry, gateway_client)])


class OpenClawConversationEntity(conversation.ConversationEntity):
    """OpenClaw conversation entity."""

    _attr_has_entity_name = True
    _attr_name = "OpenClaw"
    _attr_supported_languages = "*"
    _attr_supports_streaming = True

    def __init__(
        self, config_entry: ConfigEntry, gateway_client: OpenClawGatewayClient
    ) -> None:
        """Initialize the conversation entity."""
        self._config_entry = config_entry
        self._gateway_client = gateway_client
        self._attr_unique_id = config_entry.entry_id
        # Runs detached past the grace period, reporting back via announce.
        self._background_tasks: set[asyncio.Task] = set()

    @property
    def _config(self) -> dict[str, Any]:
        """Return the entry's settings, options overriding initial data."""
        return {**self._config_entry.data, **self._config_entry.options}

    async def async_added_to_hass(self) -> None:
        """Register the proactive-voice handler when enabled."""
        await super().async_added_to_hass()
        config = self._config
        if config.get(CONF_PROACTIVE_ENABLED, DEFAULT_PROACTIVE_ENABLED):
            self._gateway_client.set_proactive_handler(
                self._on_proactive_message
            )

    async def async_will_remove_from_hass(self) -> None:
        """Stop receiving proactive announcements and drop background runs."""
        self._gateway_client.clear_proactive_handler()
        for task in self._background_tasks:
            task.cancel()
        self._background_tasks.clear()
        await super().async_will_remove_from_hass()

    def _on_proactive_message(self, text: str) -> None:
        """Schedule a satellite announcement (called from the event loop)."""
        self.hass.async_create_task(self._async_announce(text))

    async def _async_announce(
        self, text: str, satellite: str | None = None
    ) -> None:
        """Speak an agent-initiated message on a satellite.

        Defaults to the configured proactive satellite; background reports
        pass the originating satellite explicitly.
        """
        config = self._config
        if satellite is None:
            satellite = config.get(CONF_PROACTIVE_SATELLITE)
        if not satellite:
            _LOGGER.warning(
                "Proactive voice enabled but no satellite configured"
            )
            return

        speech = text
        if config.get(CONF_STRIP_EMOJIS, DEFAULT_STRIP_EMOJIS):
            speech = strip_emojis(speech)
        speech = trim_tts_text(
            speech, config.get(CONF_TTS_MAX_CHARS, DEFAULT_TTS_MAX_CHARS)
        )
        if not speech.strip():
            return

        mode = config.get(CONF_PROACTIVE_MODE, DEFAULT_PROACTIVE_MODE)
        if mode == PROACTIVE_MODE_START_CONVERSATION:
            service, key = "start_conversation", "start_message"
        else:
            service, key = "announce", "message"

        try:
            # blocking=True so failures surface here and can be handled, rather
            # than HA core logging an unhandled "Error executing service".
            await self.hass.services.async_call(
                "assist_satellite",
                service,
                {"entity_id": satellite, key: speech},
                blocking=True,
            )
        except HomeAssistantError as err:
            # Satellite offline/busy or a transport reset are expected
            # operational conditions; log cleanly without a traceback.
            _LOGGER.warning(
                "Could not announce proactive message on %s: %s",
                satellite,
                err,
            )
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception(
                "Unexpected error announcing proactive message on %s", satellite
            )

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info for the gateway."""
        return {
            "identifiers": {(DOMAIN, self._config_entry.entry_id)},
            "name": "OpenClaw Gateway",
            "manufacturer": "OpenClaw",
            "model": "Gateway",
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes for diagnostics."""
        data = self._config
        return {
            "host": data.get("host"),
            "port": data.get("port"),
            "use_ssl": data.get("use_ssl"),
            "session_key": self._gateway_client.session_key,
            "agent_id": self._gateway_client.agent_id,
            "model": self._gateway_client.model,
            "thinking": self._gateway_client.thinking,
            "strip_emojis": data.get(CONF_STRIP_EMOJIS, DEFAULT_STRIP_EMOJIS),
            "tts_max_chars": data.get(CONF_TTS_MAX_CHARS, DEFAULT_TTS_MAX_CHARS),
            "proactive_enabled": data.get(
                CONF_PROACTIVE_ENABLED, DEFAULT_PROACTIVE_ENABLED
            ),
            "proactive_satellite": data.get(CONF_PROACTIVE_SATELLITE),
            "proactive_mode": data.get(
                CONF_PROACTIVE_MODE, DEFAULT_PROACTIVE_MODE
            ),
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._gateway_client.connected

    @property
    def supported_languages(self) -> list[str] | str:
        """Return supported languages."""
        return "*"

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Handle a user message by streaming the agent's reply into the chat log.

        Deltas added through ``chat_log.async_add_delta_content_stream`` are
        what Home Assistant's pipeline forwards to a streaming-capable TTS
        engine, so speech can start before the agent finishes.
        """
        _LOGGER.debug(
            "Processing message (%d characters; conversation_id: %s)",
            len(user_input.text),
            user_input.conversation_id,
        )
        config = self._config

        try:
            if config.get(CONF_BACKGROUND_ENABLED, DEFAULT_BACKGROUND_ENABLED):
                agent_run = await self._gateway_client.begin_agent_run(
                    user_input.text
                )
                grace = config.get(CONF_BACKGROUND_GRACE, DEFAULT_BACKGROUND_GRACE)
                try:
                    first_chunk = await agent_run.get_chunk(grace)
                except asyncio.TimeoutError:
                    return self._defer_to_background(
                        user_input, chat_log, agent_run, config
                    )
                chunks = self._resume_stream(agent_run, first_chunk)
            else:
                chunks = self._gateway_client.stream_agent_request(user_input.text)
        except Exception as err:  # pylint: disable=broad-except
            return self._reply(user_input, chat_log, _log_error_phrase(err))

        speech = await self._stream_into_chat_log(user_input, chat_log, chunks)
        return self._result(user_input, chat_log, speech)

    async def _resume_stream(
        self, agent_run: AgentRun, first_chunk: str | None
    ) -> AsyncIterator[str]:
        """Re-yield the peeked first chunk, then the rest of the run."""
        if first_chunk:
            yield first_chunk
        async for chunk in self._gateway_client.stream_run(agent_run):
            yield chunk

    async def _stream_into_chat_log(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
        chunks: AsyncIterator[str],
    ) -> str:
        """Feed agent chunks to the chat log as assistant deltas.

        Returns the text that was streamed (what TTS speaks). A failure before
        any content streams the friendly error phrase instead; a failure after
        content has started keeps what was said.
        """
        config = self._config
        speech_filter = SpeechFilter(
            strip=config.get(CONF_STRIP_EMOJIS, DEFAULT_STRIP_EMOJIS),
            max_chars=config.get(CONF_TTS_MAX_CHARS, DEFAULT_TTS_MAX_CHARS),
        )
        spoken: list[str] = []

        async def deltas() -> AsyncIterator[dict[str, Any]]:
            yield {"role": "assistant"}
            try:
                async for chunk in chunks:
                    text = speech_filter.feed(chunk)
                    if text:
                        spoken.append(text)
                        yield {"content": text}
                    if speech_filter.done:
                        break
            except Exception as err:  # pylint: disable=broad-except
                phrase = _log_error_phrase(err)
                if not spoken:
                    spoken.append(phrase)
                    yield {"content": phrase}
            finally:
                await _aclose(chunks)

        async for _content in chat_log.async_add_delta_content_stream(
            user_input.agent_id, deltas()
        ):
            pass
        if not spoken:
            # The delta stream drops an empty message; record the silent reply
            # so the chat log still ends with the assistant's turn.
            chat_log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(agent_id=user_input.agent_id, content="")
            )
        return "".join(spoken)

    def _reply(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
        text: str,
    ) -> conversation.ConversationResult:
        """Answer with a complete (non-streamed) text: errors, holding phrase."""
        config = self._config
        if config.get(CONF_STRIP_EMOJIS, DEFAULT_STRIP_EMOJIS):
            text = strip_emojis(text)
        text = trim_tts_text(
            text, config.get(CONF_TTS_MAX_CHARS, DEFAULT_TTS_MAX_CHARS)
        )
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=user_input.agent_id, content=text)
        )
        return self._result(user_input, chat_log, text)

    @staticmethod
    def _result(
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
        speech: str,
    ) -> conversation.ConversationResult:
        """Build the result HA returns to the caller and the pipeline."""
        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech(speech)
        return conversation.ConversationResult(
            response=intent_response,
            conversation_id=chat_log.conversation_id,
            continue_conversation=response_expects_followup(speech),
        )

    def _defer_to_background(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
        agent_run: AgentRun,
        config: dict[str, Any],
    ) -> conversation.ConversationResult:
        """End the turn with a holding phrase; report back when the run ends."""
        _LOGGER.debug(
            "Run %s silent past grace period; deferring to background",
            agent_run.run_id,
        )
        report = self._background_report(
            agent_run, getattr(user_input, "device_id", None)
        )
        task = self.hass.async_create_background_task(
            report, name=f"openclaw_background_{agent_run.run_id}"
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        holding_phrase = config.get(CONF_HOLDING_PHRASE, DEFAULT_HOLDING_PHRASE)
        return self._reply(user_input, chat_log, holding_phrase)

    async def _background_report(
        self, agent_run: AgentRun, device_id: str | None
    ) -> None:
        """Await a detached run and announce its result on a satellite."""
        try:
            # Overall completion budget, deliberately independent of the
            # voice-tuned agent timeout — a short one must not strangle a
            # deferred run (the whole point of deferring is "take your time").
            text = await self._gateway_client.wait_run(
                agent_run, BACKGROUND_MAX_SECONDS
            )
        except GatewayTimeoutError:
            _LOGGER.warning(
                "Background run %s did not finish within %ss",
                agent_run.run_id,
                BACKGROUND_MAX_SECONDS,
            )
            text = BACKGROUND_TIMEOUT_PHRASE
        except asyncio.CancelledError:
            raise
        except AgentExecutionError as err:
            _LOGGER.error("Background run %s failed: %s", agent_run.run_id, err)
            text = BACKGROUND_ERROR_PHRASE
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected error in background run %s", agent_run.run_id)
            text = BACKGROUND_ERROR_PHRASE

        if not text:
            _LOGGER.debug(
                "Background run %s finished with no text to announce",
                agent_run.run_id,
            )
            return

        satellite = self._resolve_report_satellite(device_id)
        if not satellite:
            _LOGGER.warning(
                "Background run %s finished but no satellite to announce on "
                "(no assist_satellite on the originating device and no "
                "proactive satellite configured)",
                agent_run.run_id,
            )
            return
        await self._async_announce(text, satellite=satellite)

    def _resolve_report_satellite(self, device_id: str | None) -> str | None:
        """Pick the satellite to report on: origin device, else proactive."""
        if device_id:
            try:
                registry = er.async_get(self.hass)
                for entry in er.async_entries_for_device(registry, device_id):
                    if entry.domain == "assist_satellite":
                        return entry.entity_id
            except Exception:  # pylint: disable=broad-except
                _LOGGER.debug(
                    "Could not resolve satellite for device %s",
                    device_id,
                    exc_info=True,
                )
        config = self._config
        return config.get(CONF_PROACTIVE_SATELLITE)


async def _aclose(iterator: AsyncIterator[str]) -> None:
    """Close an async generator so its cleanup (run-tracker removal) runs now."""
    aclose = getattr(iterator, "aclose", None)
    if aclose is not None:
        await aclose()
