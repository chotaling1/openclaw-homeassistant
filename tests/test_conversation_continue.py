"""Follow-up detection helper (end-to-end behavior is in tests_ha/)."""

from tests._conversation_loader import load_conversation_module


def test_response_expects_followup_helper() -> None:
    conv = load_conversation_module()
    assert conv.response_expects_followup("What do you think?") is True
    assert conv.response_expects_followup("Maybe? sure.") is False
    assert conv.response_expects_followup("See https://x.example/?q=1 for more.") is False
    assert conv.response_expects_followup('He asked "why?" and left.') is False
    assert conv.response_expects_followup("Want me to add it? 🙂") is True
    assert conv.response_expects_followup('Should I say "yes?"') is True
    assert conv.response_expects_followup("**Shall I?**") is True
    assert conv.response_expects_followup("Really?!") is True
    assert conv.response_expects_followup("Anything else?\n") is True
    assert conv.response_expects_followup("Done.") is False
    assert conv.response_expects_followup("") is False
    assert conv.response_expects_followup(None) is False  # type: ignore[arg-type]
