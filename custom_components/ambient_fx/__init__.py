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
from .effects import STREAM_EFFECTS
from .engine import LinkButtonNotPressed, StreamEngine

_LOGGER = logging.getLogger(__name__)

COMPANIONS_SCHEMA = vol.All(cv.ensure_list, [vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("x"): vol.Coerce(float),
    vol.Required("y"): vol.Coerce(float),
    vol.Required("z"): vol.Coerce(float),
})])

SERVICE_START_SCHEMA = vol.Schema({
    vol.Required("effect"): vol.In(sorted(EFFECTS)),
    vol.Required("group"): cv.string,
    vol.Optional("speed"): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
    vol.Optional("brightness"): vol.All(vol.Coerce(float), vol.Range(min=1, max=100)),
    vol.Optional("companions"): COMPANIONS_SCHEMA,
})

SERVICE_STOP_SCHEMA = vol.Schema({
    vol.Required("group"): cv.string,
    vol.Optional("turn_off", default=False): cv.boolean,
})

SERVICE_START_STREAM_SCHEMA = vol.Schema({
    vol.Required("effect"): vol.In(sorted(STREAM_EFFECTS)),
    vol.Optional("area"): cv.string,
    vol.Optional("brightness"): vol.All(vol.Coerce(float), vol.Range(min=1, max=150)),
    vol.Optional("immersive", default=False): cv.boolean,
    vol.Optional("companions"): COMPANIONS_SCHEMA,
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
        # Companion lights can't join a bridge scene, so mirror its
        # palette on them with a matching slow cycle.
        companions = call.data.get("companions")
        if companions:
            engine.start_palette(effect, companions, call.data.get("brightness"))
        else:
            engine.stop_palette()
        _LOGGER.info("Started effect %s on %s", call.data["effect"], group["name"])

    async def handle_stop(call: ServiceCall) -> None:
        group = await _resolve_group(call)
        engine.stop_palette()
        await bridge.stop_effect(group, turn_off=call.data["turn_off"])

    engine = StreamEngine(hass, bridge, entry)
    hass.data[DOMAIN][f"{entry.entry_id}_engine"] = engine

    async def handle_start_stream(call: ServiceCall) -> None:
        try:
            await engine.start(
                call.data["effect"],
                call.data.get("area"),
                call.data.get("brightness"),
                immersive=call.data["immersive"],
                companions=call.data.get("companions"),
            )
        except LinkButtonNotPressed as err:
            raise HomeAssistantError(str(err)) from err
        except RuntimeError as err:
            raise HomeAssistantError(str(err)) from err

    async def handle_stop_stream(call: ServiceCall) -> None:
        await engine.stop()

    hass.services.async_register(DOMAIN, "start", handle_start, schema=SERVICE_START_SCHEMA)
    hass.services.async_register(DOMAIN, "stop", handle_stop, schema=SERVICE_STOP_SCHEMA)
    hass.services.async_register(DOMAIN, "start_stream", handle_start_stream,
                                 schema=SERVICE_START_STREAM_SCHEMA)
    hass.services.async_register(DOMAIN, "stop_stream", handle_stop_stream)

    async def _shutdown(event) -> None:
        await engine.stop()

    entry.async_on_unload(
        hass.bus.async_listen_once("homeassistant_stop", _shutdown))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    engine = hass.data.get(DOMAIN, {}).pop(f"{entry.entry_id}_engine", None)
    if engine:
        await engine.stop()
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    for service in ("start", "stop", "start_stream", "stop_stream"):
        hass.services.async_remove(DOMAIN, service)
    return True
