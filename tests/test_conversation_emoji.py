"""Tests for the TTS text helpers (custom_components/openclaw/text.py)."""

import importlib.util
from pathlib import Path

_PATH = Path(__file__).parent.parent / "custom_components" / "openclaw" / "text.py"
_spec = importlib.util.spec_from_file_location("openclaw_text", _PATH)
text = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(text)
strip_emojis = text.strip_emojis


class TestEmojiStripping:
    def test_removes_common_emoji(self) -> None:
        assert strip_emojis("Hello \U0001F600") == "Hello"

    def test_preserves_text_emoticon(self) -> None:
        assert strip_emojis("Hi :)") == "Hi :)"

    def test_plain_text_unchanged(self) -> None:
        assert strip_emojis("Plain text") == "Plain text"

    def test_removes_supplemental_symbols_block(self) -> None:
        # U+1F900-1F9FF was missing from the old pattern.
        assert strip_emojis("Sure 🤔 let me check 🥳🧀") == "Sure let me check"

    def test_removes_extended_a_block(self) -> None:
        assert strip_emojis("Plants 🪴 watered") == "Plants watered"

    def test_removes_dingbats_and_symbols(self) -> None:
        assert strip_emojis("❤️ done ✅") == "done"
        assert strip_emojis("Sunny ☀️ today ⭐") == "Sunny today"

    def test_removes_zwj_sequences_whole(self) -> None:
        assert strip_emojis("Family 👨‍👩‍👧 night") == "Family night"

    def test_removes_flags(self) -> None:
        assert strip_emojis("Go 🇺🇸 team") == "Go team"

    def test_keycap_keeps_digit(self) -> None:
        assert strip_emojis("Step 1️⃣ first") == "Step 1 first"

    def test_no_space_left_before_punctuation(self) -> None:
        assert strip_emojis("All done 🎉.") == "All done."
        assert strip_emojis("Nice 👍, thanks") == "Nice, thanks"

    def test_preserves_cjk_and_hangul(self) -> None:
        assert strip_emojis("東京 is sunny") == "東京 is sunny"
        assert strip_emojis("안녕하세요 👋") == "안녕하세요"

    def test_preserves_ligatures_and_fullwidth(self) -> None:
        assert strip_emojis("café ﬁ ligature") == "café ﬁ ligature"
        assert strip_emojis("ＡＢＣ") == "ＡＢＣ"

    def test_preserves_common_typography(self) -> None:
        s = "It's 84°F — sunny… © ½ → done"
        assert strip_emojis(s) == s

    def test_does_not_collapse_spacing_without_emoji(self) -> None:
        assert strip_emojis("a  b") == "a  b"


def test_trim_tts_text() -> None:
    assert text.trim_tts_text("short", 10) == "short"
    assert text.trim_tts_text("1234567890", 0) == "1234567890"
    assert text.trim_tts_text("1234567890", 6) == "123..."


def _stream(chunks, *, strip=True, max_chars=0) -> str:
    f = text.SpeechFilter(strip=strip, max_chars=max_chars)
    return "".join(f.feed(c) for c in chunks)


class TestSpeechFilter:
    def test_matches_whole_text_stripping(self) -> None:
        assert _stream(["Done ✅ ", "enjoy 🧀!"]) == "Done enjoy!"
        assert _stream(["🎉 ", "Party ", "time"]) == "Party time"

    def test_keeps_spaces_between_plain_chunks(self) -> None:
        assert _stream(["It's sunny ", "and warm."]) == "It's sunny and warm."

    def test_drops_trailing_space(self) -> None:
        assert _stream(["All set ", "👍"]) == "All set"

    def test_emoji_split_across_chunks(self) -> None:
        assert _stream(["Family 👨‍", "👩‍👧 night"]) == "Family night"

    def test_no_strip_passes_through(self) -> None:
        assert _stream(["Hi 👋 ", "there"], strip=False) == "Hi 👋 there"

    def test_cap_matches_trim_tts_text(self) -> None:
        full = "The quick brown fox jumps over the lazy dog."
        chunks = ["The quick ", "brown fox ", "jumps over ", "the lazy dog."]
        assert _stream(chunks, max_chars=20) == text.trim_tts_text(full, 20)

    def test_cap_stops_further_output(self) -> None:
        f = text.SpeechFilter(strip=False, max_chars=8)
        assert f.feed("1234567890") == "12345..."
        assert len("12345...") <= 8
        assert f.done
        assert f.feed("more") == ""
