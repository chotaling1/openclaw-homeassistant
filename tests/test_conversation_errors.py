"""The exception -> spoken phrase table shared by both reply paths."""

import pytest

from tests._conversation_loader import load_conversation_module


@pytest.fixture(scope="module")
def conv():
    return load_conversation_module()


def test_each_gateway_error_has_its_phrase(conv) -> None:
    exc = conv.__dict__
    assert "token is no longer valid" in conv._log_error_phrase(
        exc["GatewayAuthenticationError"]("x")
    )
    assert "trouble connecting" in conv._log_error_phrase(
        exc["GatewayConnectionError"]("x")
    )
    assert "took too long" in conv._log_error_phrase(exc["GatewayTimeoutError"]("x"))
    assert "error while processing" in conv._log_error_phrase(
        exc["AgentExecutionError"]("x")
    )


def test_pairing_error_uses_auth_phrase(conv) -> None:
    import custom_components.openclaw.exceptions as exceptions

    phrase = conv._log_error_phrase(exceptions.DevicePairingRequiredError("x"))
    assert "token is no longer valid" in phrase


def test_unknown_error_uses_generic_phrase(conv) -> None:
    assert conv._log_error_phrase(ValueError("boom")) == conv._UNEXPECTED_ERROR_PHRASE
