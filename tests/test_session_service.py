"""openclaw.set_session service (HA-free)."""

from unittest.mock import MagicMock

import pytest

from tests._init_loader import load_integration, make_entry, make_hass, service_handler


@pytest.mark.asyncio
async def test_set_session_updates_all_loaded_clients() -> None:
    integration = load_integration()
    first, second = make_entry("entry-1"), make_entry("entry-2")
    hass = make_hass([first, second])
    await integration.async_setup(hass, {})

    call = MagicMock()
    call.data = {"session_key": "voice-assistant"}
    await service_handler(hass, integration.SERVICE_SET_SESSION)(call)

    first.runtime_data.set_session_key.assert_called_once_with("voice-assistant")
    second.runtime_data.set_session_key.assert_called_once_with("voice-assistant")


@pytest.mark.asyncio
async def test_set_session_single_entry() -> None:
    integration = load_integration()
    first, second = make_entry("entry-1"), make_entry("entry-2")
    hass = make_hass([first, second])
    await integration.async_setup(hass, {})

    call = MagicMock()
    call.data = {"session_key": "kitchen", "entry_id": "entry-1"}
    await service_handler(hass, integration.SERVICE_SET_SESSION)(call)

    first.runtime_data.set_session_key.assert_called_once_with("kitchen")
    second.runtime_data.set_session_key.assert_not_called()
