"""Home Assistant request context sent to the agent as extraSystemPrompt."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests._conversation_loader import load_conversation_module

_conv = load_conversation_module()
import sys  # noqa: E402

ctx = sys.modules["custom_components.openclaw.context"]
_exceptions = sys.modules["custom_components.openclaw.exceptions"]
_gateway_client = sys.modules["custom_components.openclaw.gateway_client"]


class _EntityRegistry:
    def __init__(self, entities: dict[str, SimpleNamespace]) -> None:
        self._entities = entities

    def async_get(self, entity_id: str):
        return self._entities.get(entity_id)


class _DeviceRegistry:
    def __init__(self, devices: dict[str, SimpleNamespace]) -> None:
        self._devices = devices

    def async_get(self, device_id: str):
        return self._devices.get(device_id)


class _AreaRegistry:
    def __init__(self, areas: dict[str, SimpleNamespace]) -> None:
        self._areas = areas

    def async_get_area(self, area_id: str):
        return self._areas.get(area_id)


class _Auth:
    def __init__(self, users: dict[str, SimpleNamespace]) -> None:
        self._users = users

    async def async_get_user(self, user_id: str):
        return self._users.get(user_id)


@pytest.fixture
def hass(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    entities = {
        "assist_satellite.office_hub": SimpleNamespace(
            device_id="dev-office", area_id=None
        ),
        "assist_satellite.moved": SimpleNamespace(
            device_id="dev-office", area_id="area-kitchen"
        ),
    }
    devices = {
        "dev-office": SimpleNamespace(
            name="kiosk-satellite-44dd4b", name_by_user="Office Hub", area_id="area-office"
        ),
        "dev-bare": SimpleNamespace(name="Bare Speaker", name_by_user=None, area_id=None),
    }
    areas = {
        "area-office": SimpleNamespace(name="Office"),
        "area-kitchen": SimpleNamespace(name="Kitchen"),
    }
    monkeypatch.setattr(ctx.er, "async_get", lambda _h: _EntityRegistry(entities), raising=False)
    monkeypatch.setattr(ctx.dr, "async_get", lambda _h: _DeviceRegistry(devices), raising=False)
    monkeypatch.setattr(ctx.ar, "async_get", lambda _h: _AreaRegistry(areas), raising=False)
    users = {
        "u-chuck": SimpleNamespace(name="Chuck", system_generated=False),
        "u-system": SimpleNamespace(name="Supervisor", system_generated=True),
    }
    return SimpleNamespace(auth=_Auth(users))


def _input(**kw) -> SimpleNamespace:
    base = {
        "text": "show me pictures of bulbasaur",
        "device_id": None,
        "satellite_id": None,
        "context": SimpleNamespace(user_id=None),
        "extra_system_prompt": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


async def test_device_and_area_from_device_id(hass) -> None:
    prompt = await ctx.async_build_extra_system_prompt(
        hass, _input(device_id="dev-office"), include_ha_context=True
    )
    assert prompt == (
        "Home Assistant request context:\n- Device: Office Hub\n- Area (room): Office"
    )


async def test_satellite_entity_resolves_device_and_its_own_area_wins(hass) -> None:
    prompt = await ctx.async_build_extra_system_prompt(
        hass, _input(satellite_id="assist_satellite.moved"), include_ha_context=True
    )
    assert "- Device: Office Hub" in prompt
    assert "- Area (room): Kitchen" in prompt


async def test_device_without_area_or_user_name_falls_back(hass) -> None:
    prompt = await ctx.async_build_extra_system_prompt(
        hass, _input(device_id="dev-bare"), include_ha_context=True
    )
    assert prompt == "Home Assistant request context:\n- Device: Bare Speaker"


async def test_user_named_and_system_users_skipped(hass) -> None:
    prompt = await ctx.async_build_extra_system_prompt(
        hass,
        _input(context=SimpleNamespace(user_id="u-chuck")),
        include_ha_context=True,
    )
    assert prompt == "Home Assistant request context:\n- User: Chuck"
    assert (
        await ctx.async_build_extra_system_prompt(
            hass,
            _input(context=SimpleNamespace(user_id="u-system")),
            include_ha_context=True,
        )
        is None
    )


async def test_ha_prompt_is_forwarded_even_with_context_off(hass) -> None:
    prompt = await ctx.async_build_extra_system_prompt(
        hass,
        _input(device_id="dev-office", extra_system_prompt="  The user is answering: lock up?  "),
        include_ha_context=False,
    )
    assert prompt == "The user is answering: lock up?"


async def test_context_and_ha_prompt_are_joined(hass) -> None:
    prompt = await ctx.async_build_extra_system_prompt(
        hass,
        _input(device_id="dev-office", extra_system_prompt="Answer briefly."),
        include_ha_context=True,
    )
    assert prompt.endswith("\n\nAnswer briefly.")
    assert prompt.startswith("Home Assistant request context:")


async def test_nothing_known_sends_nothing(hass) -> None:
    assert (
        await ctx.async_build_extra_system_prompt(hass, _input(), include_ha_context=True)
        is None
    )
    # Unknown ids and non-string mock values are ignored rather than crashing.
    assert (
        await ctx.async_build_extra_system_prompt(
            hass,
            _input(device_id="missing", satellite_id=object()),
            include_ha_context=True,
        )
        is None
    )


async def test_no_hass_still_forwards_ha_prompt() -> None:
    prompt = await ctx.async_build_extra_system_prompt(
        None, _input(device_id="dev-office", extra_system_prompt="Hi"), include_ha_context=True
    )
    assert prompt == "Hi"


def _agent_calls(mock: AsyncMock) -> list[dict]:
    return [
        c.kwargs["params"] for c in mock.call_args_list if c.kwargs.get("method") == "agent"
    ]


async def _start(client, **kw):
    run = await client.begin_agent_run("hello", **kw)
    client._agent_runs.pop(run.run_id, None)
    return run


async def test_gateway_client_sends_extra_system_prompt() -> None:
    client = _gateway_client.OpenClawGatewayClient("localhost", 1, None, agent_id="voice")
    client._gateway.send_request = AsyncMock(return_value={"payload": {"runId": "r1"}})
    await _start(client, extra_system_prompt="ctx")
    assert _agent_calls(client._gateway.send_request)[-1]["extraSystemPrompt"] == "ctx"
    await _start(client)
    assert "extraSystemPrompt" not in _agent_calls(client._gateway.send_request)[-1]


async def test_gateway_rejection_resends_without_and_stops_until_reconnect() -> None:
    client = _gateway_client.OpenClawGatewayClient("localhost", 1, None, agent_id="voice")
    calls: list[dict] = []

    async def send_request(*, method, params, timeout):
        if method == "agent":
            calls.append(dict(params))
            if "extraSystemPrompt" in params:
                raise _exceptions.ProtocolError("unexpected property extraSystemPrompt")
        return {"payload": {"runId": f"r{len(calls)}"}}

    client._gateway.send_request = send_request
    await _start(client, extra_system_prompt="ctx")
    assert [("extraSystemPrompt" in c) for c in calls] == [True, False]

    await _start(client, extra_system_prompt="ctx")
    assert "extraSystemPrompt" not in calls[-1]

    client._on_gateway_connected()
    await asyncio.sleep(0)
    await _start(client, extra_system_prompt="ctx")
    assert "extraSystemPrompt" in calls[-2]
