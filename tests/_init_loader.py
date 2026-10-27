"""Shared helper: stub homeassistant and load the integration's __init__.py.

Used by the service / setup / unload tests, which exercise
`custom_components/openclaw/__init__.py` without a Home Assistant install.
"""

from __future__ import annotations

import enum
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock

_BASE = Path(__file__).parent.parent / "custom_components" / "openclaw"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _stub(name: str) -> ModuleType:
    module = ModuleType(name)
    sys.modules[name] = module
    return module


class ConfigEntryState(enum.Enum):
    LOADED = "loaded"
    NOT_LOADED = "not_loaded"


class FakeClient:
    """Stand-in for OpenClawGatewayClient."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.connect = AsyncMock()
        self.disconnect = AsyncMock()
        self.connected = True
        self.set_fatal_error_handler = MagicMock()
        self.set_session_key = MagicMock()


def load_integration(client_cls: type = FakeClient) -> ModuleType:
    """Stub homeassistant.* and load __init__.py with ``client_cls``."""
    sys.modules.setdefault("homeassistant", ModuleType("homeassistant"))
    sys.modules.setdefault("homeassistant.helpers", ModuleType("homeassistant.helpers"))
    config_entries_mod = _stub("homeassistant.config_entries")
    const_mod = _stub("homeassistant.const")
    core_mod = _stub("homeassistant.core")
    exceptions_mod = _stub("homeassistant.exceptions")
    cv_mod = _stub("homeassistant.helpers.config_validation")
    issue_mod = _stub("homeassistant.helpers.issue_registry")
    service_mod = _stub("homeassistant.helpers.service")
    sys.modules["homeassistant.helpers"].config_validation = cv_mod

    class Platform:
        BINARY_SENSOR = "binary_sensor"
        CONVERSATION = "conversation"
        SENSOR = "sensor"

    class ConfigEntry:
        def __class_getitem__(cls, _item: Any) -> type:
            return cls

    const_mod.CONF_HOST = "host"
    const_mod.CONF_PORT = "port"
    const_mod.CONF_TOKEN = "token"
    const_mod.Platform = Platform
    config_entries_mod.ConfigEntry = ConfigEntry
    config_entries_mod.ConfigEntryState = ConfigEntryState
    core_mod.HomeAssistant = object
    core_mod.ServiceCall = object
    exceptions_mod.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
    exceptions_mod.ConfigEntryNotReady = type("ConfigEntryNotReady", (Exception,), {})
    cv_mod.config_entry_only_config_schema = lambda domain: None
    issue_mod.IssueSeverity = type(
        "IssueSeverity", (), {"ERROR": "error", "WARNING": "warning"}
    )()
    issue_mod.async_create_issue = lambda *args, **kwargs: None
    issue_mod.async_delete_issue = lambda *args, **kwargs: None

    def _register_admin_service(hass, domain, service, handler, schema) -> None:
        hass.services.async_register(domain, service, handler, schema=schema)

    service_mod.async_register_admin_service = MagicMock(
        side_effect=_register_admin_service
    )

    sys.modules.setdefault("custom_components", ModuleType("custom_components"))
    sys.modules.setdefault(
        "custom_components.openclaw", ModuleType("custom_components.openclaw")
    )
    _load_module("custom_components.openclaw.const", _BASE / "const.py")
    _load_module("custom_components.openclaw.exceptions", _BASE / "exceptions.py")
    gateway_client_mod = _stub("custom_components.openclaw.gateway_client")
    gateway_client_mod.OpenClawGatewayClient = client_cls

    module = _load_module("custom_components.openclaw.__init__", _BASE / "__init__.py")
    module._test_service_registry = service_mod.async_register_admin_service
    return module


def make_hass(entries: list[Any] | None = None) -> MagicMock:
    """A hass mock whose config entry registry returns ``entries``."""
    hass = MagicMock()
    hass.services.async_register = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.config_entries.async_entries = MagicMock(return_value=entries or [])
    return hass


def make_entry(entry_id: str, *, loaded: bool = True, client: Any = None) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.data = {"host": "localhost", "port": 1, "token": None}
    entry.options = {}
    entry.state = ConfigEntryState.LOADED if loaded else ConfigEntryState.NOT_LOADED
    entry.runtime_data = client if client is not None else FakeClient()
    return entry


def service_handler(hass: MagicMock, service: str):
    """Return the handler registered for ``service``."""
    for call in hass.services.async_register.call_args_list:
        if call.args[1] == service:
            return call.args[2]
    raise AssertionError(f"service {service} not registered")
