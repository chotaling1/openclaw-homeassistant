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
