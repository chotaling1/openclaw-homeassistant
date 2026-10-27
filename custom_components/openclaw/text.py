"""Text helpers for spoken replies.

Pure Python with no Home Assistant imports, so the tests exercise this code
directly instead of a copy of it.
"""

import re

# Pictographic emoji and the invisible characters that build emoji sequences.
# Deliberately explicit: a broad range such as U+24C2-U+1F251 also matches
# CJK, Hangul, Latin ligatures and fullwidth forms, deleting real words.
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # mahjong/cards, enclosed supplements, flags,
    #                          pictographs, emoticons, transport, extended-A
    "☀-➿"  # miscellaneous symbols, dingbats
    "⬀-⯿"  # miscellaneous symbols and arrows (stars, squares)
    "⌚⌛⌨⏏⏩-⏳⏸-⏺"  # watch, hourglass...
    "Ⓜ"  # circled M
    "‍"  # zero-width joiner
    "︎️"  # text/emoji variation selectors
    "⃣"  # combining enclosing keycap
    "\U000E0020-\U000E007F"  # tag characters (subdivision flags)
    "]+"
)

# Whitespace left in front of punctuation once an emoji between them is gone.
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+(?=[.,!?;:])")
_RUNS_OF_SPACES = re.compile(r"[ \t]{2,}")


def strip_emojis(text: str) -> str:
    """Remove emoji from text for TTS and tidy the gaps they leave."""
    cleaned, count = EMOJI_PATTERN.subn(" ", text)
    if count:
        cleaned = _SPACE_BEFORE_PUNCT.sub("", cleaned)
        cleaned = _RUNS_OF_SPACES.sub(" ", cleaned)
    return cleaned.strip()


def trim_tts_text(text: str, max_chars: int) -> str:
    """Trim TTS text to a max character limit."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."


# Characters that can trail a question without ending it: closing quotes and
# brackets, markdown emphasis, and ellipsis-style dots after the "?".
_TRAILING_CLOSERS = " \t\r\n\"'”’»)]}*_~`"
_ENDS_WITH_QUESTION = re.compile(r"\?[?!‽]*$")


def response_expects_followup(text: str) -> bool:
    """Return True if the response ends by asking the user something.

    Keeps the satellite mic open so the user can reply without re-triggering
    the wake word. Only the end of the reply counts: a "?" inside a URL, a
    quoted question, or a question followed by more sentences doesn't.
    """
    if not text:
        return False
    tail = strip_emojis(text).rstrip(_TRAILING_CLOSERS)
    return bool(_ENDS_WITH_QUESTION.search(tail))


class SpeechFilter:
    """Apply emoji stripping and the length cap to a reply streamed in chunks.

    Streaming TTS speaks each chunk as it arrives, so the cleanup that
    ``strip_emojis`` / ``trim_tts_text`` do on a whole reply has to work across
    chunk boundaries: whitespace at the end of a chunk is held back until the
    next one shows whether it is needed, and the cap ends the stream with "...".
    """

    def __init__(self, *, strip: bool, max_chars: int) -> None:
        self._strip = strip
        self._max_chars = max_chars
        self._pending_space = ""
        self._emitted = 0
        self._started = False
        self.done = False

    def feed(self, chunk: str) -> str:
        """Return the speakable part of ``chunk`` (possibly empty)."""
        if self.done or not chunk:
            return ""
        text = chunk
        if self._strip:
            text, count = EMOJI_PATTERN.subn(" ", text)
            text = self._pending_space + text
            self._pending_space = ""
            if count or "  " in text:
                text = _RUNS_OF_SPACES.sub(" ", text)
            text = _SPACE_BEFORE_PUNCT.sub("", text)
            if not self._started:
                text = text.lstrip()
            stripped = text.rstrip(" \t")
            self._pending_space = text[len(stripped):]
            text = stripped
        if not text:
            return ""
        self._started = True
        return self._cap(text)

    def _cap(self, text: str) -> str:
        if self._max_chars <= 0:
            self._emitted += len(text)
            return text
        if self._max_chars <= 3:
            budget = self._max_chars
            ellipsis = ""
        else:
            # Emitted text can't be taken back, so reserve room for "..." up
            # front. A reply between max-3 and max chars is trimmed slightly
            # earlier than trim_tts_text would; the cap is never exceeded.
            budget = self._max_chars - 3
            ellipsis = "..."
        if self._emitted + len(text) <= budget:
            self._emitted += len(text)
            return text
        self.done = True
        room = max(budget - self._emitted, 0)
        return text[:room].rstrip() + ellipsis
