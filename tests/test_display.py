"""Display directives: kept out of speech, recorded as chat log tool results."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from types import ModuleType, SimpleNamespace
from typing import Any, AsyncIterable, AsyncIterator
from unittest.mock import MagicMock

import pytest

from tests._conversation_loader import load_conversation_module

conv = load_conversation_module(streaming="chatlog")
display = sys.modules["custom_components.openclaw.display"]
const = sys.modules["custom_components.openclaw.const"]

IMG = "https://upload.wikimedia.org/wikipedia/en/2/28/Bulbasaur.png"
DIRECTIVE = (
    '[[ha_display {"query": "Bulbasaur", "images": '
    f'[{{"url": "{IMG}", "title": "Bulbasaur"}}]}}]]'
)

# ── Parsing ───────────────────────────────────────────────────────────────


def test_directive_first_is_removed_and_parsed() -> None:
    text, results = display.strip_directives(DIRECTIVE + "\nHere's Bulbasaur.")
    assert text == "Here's Bulbasaur."
    assert len(results) == 1
    assert results[0].tool_args == {"query": "Bulbasaur"}
    assert results[0].data == {
        "results": [{"image_url": IMG, "title": "Bulbasaur"}],
        "auto_display": True,
    }


def test_directive_mid_text_is_removed() -> None:
    text, results = display.strip_directives("Here you go. " + DIRECTIVE + " Enjoy!")
    assert text == "Here you go.  Enjoy!"
    assert len(results) == 1


@pytest.mark.parametrize("size", [1, 2, 3, 7, 50])
def test_streamed_in_pieces_gives_the_same_result(size: int) -> None:
    full = DIRECTIVE + "\nHere's Bulbasaur. [not a directive] [[also not]]"
    stripper = display.DirectiveStripper()
    out = "".join(
        stripper.feed(full[i : i + size]) for i in range(0, len(full), size)
    ) + stripper.finish()
    assert out == "Here's Bulbasaur. [not a directive] [[also not]]"
    assert len(stripper.results) == 1


def test_early_flag_tracks_visible_text() -> None:
    stripper = display.DirectiveStripper()
    assert stripper.feed(DIRECTIVE[:20]) == ""
    assert stripper.early
    stripper.feed(DIRECTIVE[20:])
    assert stripper.results and stripper.early
    assert stripper.feed("\n  Hi") == "Hi"
    assert not stripper.early


def test_only_http_urls_and_at_most_eight_images() -> None:
    images = [{"url": f"https://x.test/{i}.png"} for i in range(12)]
    images[0] = {"url": "javascript:alert(1)"}
    images[1] = {"url": "file:///etc/passwd"}
    images[2] = "https://x.test/plain-string.png"
    body = {"images": images}
    result = display.parse_directive_body(body)
    urls = [r["image_url"] for r in result.data["results"]]
    assert urls[0] == "https://x.test/plain-string.png"
    assert len(urls) == 8
    assert all(u.startswith("https://") for u in urls)


def test_invalid_directive_is_dropped_not_spoken() -> None:
    text, results = display.strip_directives('[[ha_display {"images": "nope"}]] Hi')
    assert (text, results) == ("Hi", [])
    text, results = display.strip_directives("[[ha_display {broken json]] Hi")
    assert (text, results) == ("Hi", [])
    text, results = display.strip_directives('[[ha_display {"images": [')
    assert (text, results) == ("", [])


def test_text_without_directives_is_untouched() -> None:
    text = "Prices [[ in brackets ]] and a [ link ] stay. [[ha_displayed]]"
    out, results = display.strip_directives(text)
    assert results == []
    assert out == text


def test_close_inside_a_string_does_not_end_the_directive() -> None:
    body = '[[ha_display {"images": [{"url": "https://x.test/a.png", "title": "a]]b"}]}]] ok'
    text, results = display.strip_directives(body)
    assert text == "ok"
    assert results[0].data["results"][0]["title"] == "a]]b"


# ── Chat log integration (fakes follow HA 2026.11's ChatLog) ──────────────


@dataclass
class ToolInput:
    tool_name: str
    tool_args: dict
    id: str = "call-1"
    external: bool = False


@dataclass
class ToolResult:
    data: dict
    error: bool = False


class AssistantContent:
    def __init__(self, agent_id, content=None, tool_calls=None) -> None:
        self.agent_id = agent_id
        self.content = content
        self.tool_calls = tool_calls
        self.role = "assistant"


class ToolResultContent:
    def __init__(self, agent_id, tool_call_id, tool_name, result) -> None:
        self.agent_id = agent_id
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name
        self.result = result
        self.role = "tool_result"


class ChatLog:
    def __init__(self) -> None:
        self.content: list[Any] = []

    def async_add_assistant_content_without_tools(self, content) -> None:
        if isinstance(content, AssistantContent) and content.tool_calls:
            assert all(c.external for c in content.tool_calls)
        self.content.append(content)

    async def async_add_delta_content_stream(
        self, agent_id: str, stream: AsyncIterable[dict]
    ) -> AsyncIterator[Any]:
        current: AssistantContent | None = None

        def close():
            if current is not None:
                self.async_add_assistant_content_without_tools(current)
                return current
            return None

        async for delta in stream:
            if delta.get("role") == "tool_result":
                done = close()
                if done is not None:
                    yield done
                current = None
                result = ToolResultContent(
                    agent_id, delta["tool_call_id"], delta["tool_name"], delta["result"]
                )
                self.async_add_assistant_content_without_tools(result)
                yield result
                continue
            if delta.get("role") == "assistant":
                done = close()
                if done is not None:
                    yield done
                current = AssistantContent(agent_id, delta.get("content"))
                continue
            assert current is not None, "delta before role"
            if "content" in delta:
                current.content = (current.content or "") + delta["content"]
            if "tool_calls" in delta:
                current.tool_calls = (current.tool_calls or []) + delta["tool_calls"]
        done = close()
        if done is not None:
            yield done


def _result_from_chat_log(user_input, chat_log):
    last = chat_log.content[-1]
    assert isinstance(last, AssistantContent), "last content must be the answer"
    response = conv.intent.IntentResponse(language=user_input.language)
    response.async_set_speech(last.content or "")
    return conv.conversation.ConversationResult(
        response=response, conversation_id=user_input.conversation_id
    )


@pytest.fixture
def ha(monkeypatch: pytest.MonkeyPatch):
    llm = ModuleType("homeassistant.helpers.llm")
    llm.ToolInput = ToolInput
    llm.ToolResult = ToolResult
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.llm", llm)
    monkeypatch.setattr(sys.modules["homeassistant.helpers"], "llm", llm, raising=False)
    monkeypatch.setattr(conv.conversation, "AssistantContent", AssistantContent)
    monkeypatch.setattr(conv.conversation, "ToolResultContent", ToolResultContent, raising=False)
    monkeypatch.setattr(conv.conversation, "ChatLog", ChatLog)
    monkeypatch.setattr(
        conv.conversation, "async_get_result_from_chat_log", _result_from_chat_log
    )
    return llm


def _entity(**options):
    entry = MagicMock()
    entry.entry_id = "e1"
    entry.data = {}
    entry.options = {
        const.CONF_BACKGROUND_ENABLED: False,
        const.CONF_DISPLAY_RESULTS: True,
        **options,
    }
    gateway = MagicMock()
    gateway.connected = True
    return conv.OpenClawConversationEntity(entry, gateway), gateway


def _user_input():
    return SimpleNamespace(
        text="show me Bulbasaur",
        language="en",
        conversation_id="c1",
        agent_id="conversation.openclaw",
        device_id=None,
        satellite_id=None,
        extra_system_prompt=None,
        context=None,
    )


def _tool_entries(chat_log):
    calls = [
        c for c in chat_log.content if isinstance(c, AssistantContent) and c.tool_calls
    ]
    results = [c for c in chat_log.content if isinstance(c, ToolResultContent)]
    return calls, results


async def test_plain_reply_records_results_before_the_answer(ha) -> None:
    entity, gateway = _entity()

    async def fake_send(_message, **_kw):
        return DIRECTIVE + "\nHere's Bulbasaur."

    gateway.send_agent_request = fake_send
    chat_log = ChatLog()
    result = await entity._async_handle_message(_user_input(), chat_log)

    assert result.response.speech == "Here's Bulbasaur."
    calls, results = _tool_entries(chat_log)
    assert calls[0].tool_calls[0].external is True
    assert calls[0].tool_calls[0].tool_name == "search_images"
    assert results[0].result.data["results"][0]["image_url"] == IMG
    assert chat_log.content[-1].content == "Here's Bulbasaur."


async def test_streamed_reply_records_results_then_streams_answer(ha) -> None:
    entity, gateway = _entity(**{const.CONF_STREAMING_ENABLED: True})

    async def fake_stream(_message, **_kw):
        full = DIRECTIVE + "\nHere's Bulbasaur."
        for i in range(0, len(full), 9):
            yield full[i : i + 9]

    gateway.stream_agent_request = fake_stream
    chat_log = ChatLog()
    result = await entity._async_handle_message(_user_input(), chat_log)

    assert result.response.speech == "Here's Bulbasaur."
    roles = [c.role for c in chat_log.content]
    assert roles == ["assistant", "tool_result", "assistant"]
    assert chat_log.content[0].tool_calls[0].tool_name == "search_images"
    assert "ha_display" not in chat_log.content[-1].content


async def test_late_directive_is_slotted_in_before_the_answer(ha) -> None:
    entity, gateway = _entity(**{const.CONF_STREAMING_ENABLED: True})

    async def fake_stream(_message, **_kw):
        yield "Here's Bulbasaur. "
        yield DIRECTIVE

    gateway.stream_agent_request = fake_stream
    chat_log = ChatLog()
    result = await entity._async_handle_message(_user_input(), chat_log)

    assert result.response.speech.strip() == "Here's Bulbasaur."
    assert [c.role for c in chat_log.content] == ["assistant", "tool_result", "assistant"]


async def test_option_off_leaves_reply_alone(ha) -> None:
    entity, gateway = _entity(**{const.CONF_DISPLAY_RESULTS: False})

    async def fake_send(_message, **_kw):
        return "Plain answer [[ha_display {}]]"

    gateway.send_agent_request = fake_send
    chat_log = ChatLog()
    result = await entity._async_handle_message(_user_input(), chat_log)
    assert result.response.speech == "Plain answer [[ha_display {}]]"
    assert _tool_entries(chat_log) == ([], [])


async def test_no_external_tool_support_still_strips(monkeypatch) -> None:
    # Older cores: no llm.ToolInput -> nothing recorded, nothing spoken.
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.llm", ModuleType("x"))
    monkeypatch.setattr(sys.modules["homeassistant.helpers"], "llm", sys.modules["homeassistant.helpers.llm"], raising=False)
    entity, gateway = _entity()

    async def fake_send(_message, **_kw):
        return DIRECTIVE + " Hi"

    gateway.send_agent_request = fake_send
    chat_log = conv.conversation.ChatLog()
    result = await entity._async_handle_message(_user_input(), chat_log)
    assert result.response.speech == "Hi"
