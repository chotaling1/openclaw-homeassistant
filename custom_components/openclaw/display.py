"""Results the agent asks a voice satellite's screen to show.

The gateway does not send tool results to Home Assistant, so an agent that
wants a screen to show pictures says so in its reply with a display
directive::

    [[ha_display {"images": [{"url": "https://...", "title": "Bulbasaur"}]}]]
    Here's Bulbasaur.

The integration removes the directive from what is spoken and shown, and
records it in Home Assistant's chat log as an external ``search_images`` tool
call and its result. Screens that read the chat log, such as Kiosk Satellite,
turn a result whose ``results`` items carry an ``image_url`` into an image
panel.

This module is free of Home Assistant imports so it can be tested alone.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

_LOGGER = logging.getLogger(__name__)

MARKER = "[[ha_display"
CLOSE = "]]"
TOOL_NAME = "search_images"
MAX_IMAGES = 8
# A directive longer than this is not one; its text is passed through.
MAX_DIRECTIVE_CHARS = 8000
_DECODER = json.JSONDecoder()


@dataclass
class DisplayResult:
    """One parsed directive, in the shape the chat log records."""

    tool_args: dict[str, Any]
    data: dict[str, Any]


def _http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return value
    return None


def parse_directive_body(body: Any) -> DisplayResult | None:
    """Turn a decoded directive object into a chat log result, or None."""
    if not isinstance(body, dict):
        return None
    images = body.get("images")
    if not isinstance(images, list):
        return None
    results: list[dict[str, str]] = []
    for image in images:
        if isinstance(image, str):
            image = {"url": image}
        if not isinstance(image, dict):
            continue
        url = _http_url(image.get("url") or image.get("image_url"))
        if not url:
            continue
        item = {"image_url": url}
        thumb = _http_url(image.get("thumbnail_url") or image.get("thumbnail"))
        if thumb:
            item["thumbnail_url"] = thumb
        title = image.get("title")
        if isinstance(title, str) and title.strip():
            item["title"] = title.strip()[:200]
        results.append(item)
        if len(results) == MAX_IMAGES:
            break
    if not results:
        return None
    query = body.get("query")
    tool_args = {"query": query.strip()[:200]} if isinstance(query, str) and query.strip() else {}
    return DisplayResult(
        tool_args=tool_args,
        data={"results": results, "auto_display": True},
    )


class DirectiveStripper:
    """Remove display directives from text that may arrive in chunks.

    ``feed`` returns the text that is safe to speak so far; text that might
    be the start of a directive is held back until it is decided. ``finish``
    releases what is left. Parsed directives collect in ``results``;
    ``early`` is True while no visible text has been released, so a caller
    can record a directive before the answer it belongs to.
    """

    def __init__(self) -> None:
        self._pending = ""
        self.results: list[DisplayResult] = []
        self.released_text = False

    @property
    def early(self) -> bool:
        return not self.released_text

    def _release(self, text: str) -> str:
        if not self.released_text:
            text = text.lstrip()
        if text:
            self.released_text = True
        return text

    def feed(self, chunk: str) -> str:
        self._pending += chunk
        out: list[str] = []
        while self._pending:
            start = self._pending.find("[[")
            if start == -1:
                # Hold a trailing "[" that may open a directive.
                keep = 1 if self._pending.endswith("[") else 0
                out.append(self._release(self._pending[: len(self._pending) - keep]))
                self._pending = self._pending[len(self._pending) - keep :]
                break
            if start:
                out.append(self._release(self._pending[:start]))
                self._pending = self._pending[start:]
            rest = self._pending
            if len(rest) < len(MARKER):
                if MARKER.startswith(rest):
                    break  # Could still become a directive.
                out.append(self._release(rest[:2]))
                self._pending = rest[2:]
                continue
            if not rest.startswith(MARKER):
                out.append(self._release(rest[:2]))
                self._pending = rest[2:]
                continue
            if len(rest) == len(MARKER):
                break  # Wait for the character after the name.
            if not _opens_body(rest[len(MARKER)]):
                out.append(self._release(rest[:2]))  # e.g. "[[ha_displayed"
                self._pending = rest[2:]
                continue
            consumed = self._try_consume(rest)
            if consumed is None:
                if len(rest) > MAX_DIRECTIVE_CHARS:
                    out.append(self._release(rest[:2]))
                    self._pending = rest[2:]
                    continue
                break  # Wait for the rest of the directive.
            self._pending = rest[consumed:]
        return "".join(out)

    def _try_consume(self, rest: str) -> int | None:
        """Parse a directive at the start of ``rest``; return its length.

        None means it is not complete yet. A complete but invalid directive
        is still consumed: it is markup, never something to say aloud.
        """
        pos = len(MARKER)
        while pos < len(rest) and rest[pos].isspace():
            pos += 1
        if pos >= len(rest):
            return None
        try:
            body, end = _DECODER.raw_decode(rest, pos)
        except json.JSONDecodeError:
            close = rest.find(CLOSE, pos)
            if close == -1:
                return None
            # A close exists but the JSON before it does not parse. It may be
            # a "]]" inside an unfinished string, so only give up once more
            # text cannot help: the stream has ended (see finish).
            return None
        while end < len(rest) and rest[end].isspace():
            end += 1
        if len(rest) - end < len(CLOSE):
            return None
        if not rest.startswith(CLOSE, end):
            _LOGGER.debug("Display directive without a closing ]]; dropped")
            return end
        result = parse_directive_body(body)
        if result is None:
            _LOGGER.debug("Display directive had no usable images; dropped")
        else:
            self.results.append(result)
        return end + len(CLOSE)

    def finish(self) -> str:
        """Release held text at the end of the reply."""
        rest, self._pending = self._pending, ""
        if rest.startswith(MARKER) and len(rest) > len(MARKER) and _opens_body(
            rest[len(MARKER)]
        ):
            close = rest.rfind(CLOSE)
            if close != -1:
                # Unparseable directive: drop it, keep anything after it.
                return self._release(rest[close + len(CLOSE) :])
            return ""
        return self._release(rest)


def _opens_body(char: str) -> bool:
    """Whether the character after the directive name starts its body."""
    return char == "{" or char.isspace()


def strip_directives(text: str) -> tuple[str, list[DisplayResult]]:
    """Remove every display directive from a complete reply."""
    stripper = DirectiveStripper()
    clean = stripper.feed(text) + stripper.finish()
    return clean, stripper.results
