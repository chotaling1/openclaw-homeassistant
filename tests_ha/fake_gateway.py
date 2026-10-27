"""In-memory stand-in for GatewayProtocol, the WebSocket layer.

Everything above the socket — OpenClawGatewayClient, the conversation entity,
Home Assistant's conversation and chat-log machinery — runs for real. Tests
drive the gateway side by answering requests and emitting events.
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
from typing import Any, Callable

_run_ids = itertools.count(1)


class FakeGatewayProtocol:
    """Implements the GatewayProtocol surface OpenClawGatewayClient uses."""

    instances: list["FakeGatewayProtocol"] = []

    def __init__(self, host: str, port: int, token: str | None, use_ssl: bool = False, hass: Any = None) -> None:
        self._host = host
        self._port = port
        self._connected = False
        self._connected_event = asyncio.Event()
        self._handlers: dict[str, list[Callable]] = {}
        self._on_connected: Callable[[], None] | None = None
        self._on_fatal_error: Callable[[Exception], None] | None = None
        self._presence: dict[str, Any] = {}
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.agent_requested = asyncio.Event()
        self.fail_next_agent: Exception | None = None
        self.run_ids: list[str] = []
        FakeGatewayProtocol.instances.append(self)

    # --- connection -------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def connect_snapshot(self) -> dict[str, Any]:
        return {}

    @property
    def presence(self) -> dict[str, Any]:
        return self._presence

    def set_presence(self, presence: dict[str, Any]) -> None:
        self._presence = presence

    @property
    def fatal_error(self) -> Exception | None:
        return None

    @property
    def address(self) -> str:
        return f"{self._host}:{self._port}"

    async def connect(self) -> None:
        self._connected = True
        self._connected_event.set()
        if self._on_connected:
            self._on_connected()

    async def disconnect(self) -> None:
        self._connected = False
        self._connected_event.clear()

    async def wait_connected(self, timeout: float) -> None:
        await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)

    def set_connected_callback(self, callback: Callable[[], None] | None) -> None:
        self._on_connected = callback

    def set_fatal_error_callback(self, callback: Callable[[Exception], None] | None) -> None:
        self._on_fatal_error = callback

    def on_event(self, event_name: str, handler: Callable) -> None:
        self._handlers.setdefault(event_name, []).append(handler)

    # --- requests ---------------------------------------------------------
    async def send_request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
        params = params or {}
        self.requests.append((method, dict(params)))
        if method == "agent":
            if self.fail_next_agent is not None:
                err, self.fail_next_agent = self.fail_next_agent, None
                raise err
            run_id = f"run-{next(_run_ids)}"
            self.run_ids.append(run_id)
            self.agent_requested.set()
            return {"ok": True, "payload": {"runId": run_id}}
        if method == "agents.list":
            return {"ok": True, "payload": {"defaultId": "main"}}
        return {"ok": True, "payload": {}}

    def agent_params(self, index: int = -1) -> dict[str, Any]:
        """Params of an ``agent`` request (the latest by default)."""
        return [p for m, p in self.requests if m == "agent"][index]

    # --- gateway -> client events ----------------------------------------
    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        for handler in self._handlers.get(event, []):
            result = handler({"type": "event", "event": event, "payload": payload})
            if inspect.isawaitable(result):
                await result
        await asyncio.sleep(0)

    async def reply(self, run_id: str, *chunks: str) -> None:
        """Stream ``chunks`` as delta-only assistant events, then end the run."""
        text = ""
        for i, chunk in enumerate(chunks):
            text += chunk
            data: dict[str, Any] = {"delta": chunk, "itemId": "msg-1"}
            if i == 0:
                data["text"] = chunk
            await self.emit("agent", {"runId": run_id, "stream": "assistant", "data": data})
        await self.end(run_id)

    async def end(self, run_id: str) -> None:
        await self.emit("agent", {"runId": run_id, "stream": "lifecycle", "data": {"phase": "end"}})

    async def fail(self, run_id: str, error: str = "boom") -> None:
        await self.emit("agent", {"runId": run_id, "stream": "lifecycle", "data": {"phase": "error", "error": error}})

    async def next_run_id(self) -> str:
        """Wait for the next ``agent`` request and return its run id."""
        await asyncio.wait_for(self.agent_requested.wait(), timeout=5)
        self.agent_requested.clear()
        return self.run_ids[-1]
