"""Entry setup / unload lifecycle (HA-free)."""

from unittest.mock import AsyncMock

import pytest

from tests._init_loader import FakeClient, load_integration, make_entry, make_hass


@pytest.mark.asyncio
async def test_setup_stores_client_in_runtime_data() -> None:
    integration = load_integration()
    hass = make_hass()
    entry = make_entry("entry-1")
    entry.runtime_data = None

    assert await integration.async_setup_entry(hass, entry) is True

    assert isinstance(entry.runtime_data, FakeClient)
    entry.runtime_data.connect.assert_awaited_once()
    entry.runtime_data.set_fatal_error_handler.assert_called_once()


@pytest.mark.asyncio
async def test_setup_disconnects_when_platforms_fail() -> None:
    integration = load_integration()
    hass = make_hass()
    hass.config_entries.async_forward_entry_setups = AsyncMock(
        side_effect=RuntimeError("boom")
    )
    entry = make_entry("entry-1")

    assert await integration.async_setup_entry(hass, entry) is False
    entry.runtime_data.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_unload_disconnects_and_returns_platform_result() -> None:
    integration = load_integration()
    hass = make_hass()
    entry = make_entry("entry-1")

    assert await integration.async_unload_entry(hass, entry) is True
    entry.runtime_data.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_unload_disconnects_even_if_platform_unload_fails() -> None:
    integration = load_integration()
    hass = make_hass()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)
    entry = make_entry("entry-1")

    assert await integration.async_unload_entry(hass, entry) is False
    entry.runtime_data.disconnect.assert_awaited_once()
