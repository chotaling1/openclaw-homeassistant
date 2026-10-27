"""Per-request context sent to the agent as the gateway's extraSystemPrompt."""

from __future__ import annotations

import logging

from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

_HEADER = "Home Assistant request context:"


async def async_build_extra_system_prompt(
    hass: HomeAssistant,
    user_input: conversation.ConversationInput,
    *,
    include_ha_context: bool,
) -> str | None:
    """Return the extra system prompt for one request, or None if empty.

    With ``include_ha_context`` the prompt names the device the request came
    from, its area, and the Home Assistant user who sent it, when known. HA's
    own ``extra_system_prompt`` (set by automations and
    ``assist_satellite.start_conversation`` to explain what the user is
    answering) is always appended.
    """
    parts: list[str] = []
    if include_ha_context:
        lines = _device_lines(hass, user_input) + await _user_lines(hass, user_input)
        if lines:
            parts.append("\n".join([_HEADER, *lines]))
    if user_input.extra_system_prompt:
        parts.append(user_input.extra_system_prompt.strip())
    return "\n\n".join(parts) or None


def _device_lines(
    hass: HomeAssistant, user_input: conversation.ConversationInput
) -> list[str]:
    """Describe the originating device and its area."""
    device_id = user_input.device_id
    area_id: str | None = None
    entity_area_id: str | None = None

    if not device_id and user_input.satellite_id:
        entity = er.async_get(hass).async_get(user_input.satellite_id)
        if entity is not None:
            device_id = entity.device_id
            entity_area_id = entity.area_id
    if not device_id:
        return []

    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return []
    area_id = entity_area_id or device.area_id

    lines = [f"- Device: {device.name_by_user or device.name or device_id}"]
    if area_id and (area := ar.async_get(hass).async_get_area(area_id)):
        lines.append(f"- Area (room): {area.name}")
    return lines


async def _user_lines(
    hass: HomeAssistant, user_input: conversation.ConversationInput
) -> list[str]:
    """Name the Home Assistant user who sent the request, if any."""
    user_id = user_input.context.user_id if user_input.context else None
    if not user_id:
        return []
    try:
        user = await hass.auth.async_get_user(user_id)
    except Exception:  # pylint: disable=broad-except
        _LOGGER.debug("Could not look up user %s", user_id, exc_info=True)
        return []
    if user is None or user.system_generated or not user.name:
        return []
    return [f"- User: {user.name}"]
