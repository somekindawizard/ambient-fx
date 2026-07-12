"""Ambient FX — animated dynamic scenes for Hue lights.

Registers two services:
  ambient_fx.start  {effect, group, speed?, brightness?}
  ambient_fx.stop   {group, turn_off?}
Effects animate on the bridge itself (API v2 dynamic palettes), so they
are smooth, survive HA restarts, and never conflict with a Hue Sync Box.
"""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .bridge import HueFxBridge
from .const import DOMAIN, EFFECTS

_LOGGER = logging.getLogger(__name__)

SERVICE_START_SCHEMA = vol.Schema({
    vol.Required("effect"): vol.In(sorted(EFFECTS)),
    vol.Required("group"): cv.string,
    vol.Optional("speed"): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
    vol.Optional("brightness"): vol.All(vol.Coerce(float), vol.Range(min=1, max=100)),
})

SERVICE_STOP_SCHEMA = vol.Schema({
    vol.Required("group"): cv.string,
    vol.Optional("turn_off", default=False): cv.boolean,
})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    bridge = HueFxBridge(hass, entry.data["host"], entry.data["api_key"])
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = bridge

    async def _resolve_group(call: ServiceCall):
        name = call.data["group"]
        group = await bridge.find_group(name)
        if group is None:
            names = ", ".join(sorted(g["name"] for g in await bridge.get_groups()))
            raise HomeAssistantError(
                f"No Hue room/zone named '{name}'. Available: {names}")
        return group

    async def handle_start(call: ServiceCall) -> None:
        group = await _resolve_group(call)
        effect = EFFECTS[call.data["effect"]]
        await bridge.apply_effect(
            group, effect,
            speed=call.data.get("speed"),
            brightness=call.data.get("brightness"),
        )
        _LOGGER.info("Started effect %s on %s", call.data["effect"], group["name"])

    async def handle_stop(call: ServiceCall) -> None:
        group = await _resolve_group(call)
        await bridge.stop_effect(group, turn_off=call.data["turn_off"])

    hass.services.async_register(DOMAIN, "start", handle_start, schema=SERVICE_START_SCHEMA)
    hass.services.async_register(DOMAIN, "stop", handle_stop, schema=SERVICE_STOP_SCHEMA)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    hass.services.async_remove(DOMAIN, "start")
    hass.services.async_remove(DOMAIN, "stop")
    return True
