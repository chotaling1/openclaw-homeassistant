"""openclaw.reconnect service (HA-free)."""

from unittest.mock import MagicMock

import pytest

from tests._init_loader import load_integration, make_entry, make_hass, service_handler


@pytest.mark.asyncio
async def test_services_registered_once_in_async_setup() -> None:
    integration = load_integration()
    hass = make_hass()

    assert await integration.async_setup(hass, {}) is True

    registered = {c.args[1] for c in hass.services.async_register.call_args_list}
    assert registered == {integration.SERVICE_RECONNECT, integration.SERVICE_SET_SESSION}

    # Setting up an entry registers nothing more.
    entry = make_entry("entry-1")
    await integration.async_setup_entry(hass, entry)
    assert hass.services.async_register.call_count == 2


@pytest.mark.asyncio
async def test_reconnect_all_loaded_entries() -> None:
    integration = load_integration()
    loaded = make_entry("entry-1")
    other = make_entry("entry-2")
    unloaded = make_entry("entry-3", loaded=False)
    hass = make_hass([loaded, other, unloaded])
    await integration.async_setup(hass, {})

    call = MagicMock()
    call.data = {}
    await service_handler(hass, integration.SERVICE_RECONNECT)(call)

    for entry in (loaded, other):
        entry.runtime_data.disconnect.assert_awaited_once()
        entry.runtime_data.connect.assert_awaited_once()
    unloaded.runtime_data.connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconnect_single_entry() -> None:
    integration = load_integration()
    target = make_entry("entry-1")
    other = make_entry("entry-2")
    hass = make_hass([target, other])
    await integration.async_setup(hass, {})

    call = MagicMock()
    call.data = {"entry_id": "entry-2"}
    await service_handler(hass, integration.SERVICE_RECONNECT)(call)

    other.runtime_data.connect.assert_awaited_once()
    target.runtime_data.connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconnect_unknown_entry_is_a_no_op() -> None:
    integration = load_integration()
    entry = make_entry("entry-1")
    hass = make_hass([entry])
    await integration.async_setup(hass, {})

    call = MagicMock()
    call.data = {"entry_id": "missing"}
    await service_handler(hass, integration.SERVICE_RECONNECT)(call)

    entry.runtime_data.connect.assert_not_awaited()
