"""Thin Hue CLIP v2 client for Ambient FX.

Talks directly to the bridge's REST API (reusing the credentials of the
existing core Hue integration) to create and recall dynamic scenes —
the bridge animates the palette itself, so effects are smooth and cost
no streaming slot (the Sync Box keeps working).
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import SCENE_PREFIX

_LOGGER = logging.getLogger(__name__)


class HueFxBridge:
    """Manages FX dynamic scenes on a Hue bridge."""

    def __init__(self, hass: HomeAssistant, host: str, api_key: str) -> None:
        self._hass = hass
        self._host = host
        self._api_key = api_key
        # The bridge uses a self-signed cert; the core hue integration
        # does the same thing (verify_ssl disabled for local bridges).
        self._session = async_get_clientsession(hass, verify_ssl=False)

    async def _request(self, method: str, path: str,
                       json: dict | None = None) -> dict[str, Any]:
        url = f"https://{self._host}/clip/v2/{path}"
        headers = {"hue-application-key": self._api_key}
        async with self._session.request(
            method, url, headers=headers, json=json,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise RuntimeError(f"Hue API {resp.status}: {data}")
            for error in data.get("errors", []):
                _LOGGER.warning("Hue API error: %s", error)
            return data

    async def get_resources(self, rtype: str) -> list[dict[str, Any]]:
        data = await self._request("GET", f"resource/{rtype}")
        return data.get("data", [])

    # MARK: Groups

    async def get_groups(self) -> list[dict[str, Any]]:
        """All rooms and zones: [{id, name, rtype, children}]."""
        groups = []
        for rtype in ("room", "zone"):
            for group in await self.get_resources(rtype):
                groups.append({
                    "id": group["id"],
                    "rtype": rtype,
                    "name": group["metadata"]["name"],
                    "children": group.get("children", []),
                })
        return groups

    async def find_group(self, name: str) -> dict[str, Any] | None:
        wanted = name.strip().casefold()
        for group in await self.get_groups():
            if group["name"].strip().casefold() == wanted:
                return group
        return None

    async def group_light_services(self, group: dict[str, Any]) -> list[dict]:
        """Resolve a room/zone's children to light service references."""
        lights: list[dict] = []
        if group["rtype"] == "zone":
            # Zone children are light services directly.
            return [c for c in group["children"] if c["rtype"] == "light"]
        # Room children are devices; map each device to its light service.
        devices = {d["id"]: d for d in await self.get_resources("device")}
        for child in group["children"]:
            device = devices.get(child["rid"])
            if not device:
                continue
            for service in device.get("services", []):
                if service["rtype"] == "light":
                    lights.append(service)
        return lights

    # MARK: Dynamic scenes

    async def apply_effect(self, group: dict[str, Any], effect: dict,
                           speed: float | None, brightness: float | None) -> str:
        """Create/update the FX scene for this effect on the group and
        recall it with a dynamic palette. Returns the scene id."""
        lights = await self.group_light_services(group)
        if not lights:
            raise RuntimeError(f"No lights found in group '{group['name']}'")

        scene_name = (SCENE_PREFIX + effect["name"])[:32]
        bri_scale = 1.0
        if brightness is not None:
            base = effect.get("brightness") or 50
            bri_scale = max(0.01, min(2.0, brightness / base))

        palette_colors = [
            {
                "color": {"xy": {"x": c["xy"][0], "y": c["xy"][1]}},
                "dimming": {"brightness": max(1.0, min(100.0, c["bri"] * bri_scale))},
            }
            for c in effect["colors"]
        ]

        # Seed each light with a palette color so the recall starts varied.
        actions = []
        for i, light in enumerate(lights):
            color = palette_colors[i % len(palette_colors)]
            actions.append({
                "target": {"rid": light["rid"], "rtype": "light"},
                "action": {
                    "on": {"on": True},
                    "color": color["color"],
                    "dimming": color["dimming"],
                },
            })

        body: dict[str, Any] = {
            "actions": actions,
            "palette": {
                "color": palette_colors,
                "dimming": [],
                "color_temperature": [],
            },
            "speed": max(0.0, min(1.0, speed if speed is not None else effect["speed"])),
            "auto_dynamic": True,
        }

        scene_id = await self._find_scene(group, scene_name)
        if scene_id:
            await self._request("PUT", f"resource/scene/{scene_id}", json=body)
        else:
            body["metadata"] = {"name": scene_name}
            body["group"] = {"rid": group["id"], "rtype": group["rtype"]}
            data = await self._request("POST", "resource/scene", json=body)
            scene_id = data["data"][0]["rid"]

        await self._request("PUT", f"resource/scene/{scene_id}",
                            json={"recall": {"action": "dynamic_palette"}})
        return scene_id

    async def _find_scene(self, group: dict[str, Any], name: str) -> str | None:
        for scene in await self.get_resources("scene"):
            if (scene.get("group", {}).get("rid") == group["id"]
                    and scene["metadata"]["name"] == name):
                return scene["id"]
        return None

    async def stop_effect(self, group: dict[str, Any],
                          turn_off: bool = False) -> None:
        """Stop the animation: freeze to static (or turn the group off)."""
        grouped_light = await self._grouped_light(group)
        if grouped_light is None:
            return
        if turn_off:
            await self._request("PUT", f"resource/grouped_light/{grouped_light}",
                                json={"on": {"on": False}})
        else:
            # Recalling any active FX scene statically stops the palette
            # cycle; simplest universal stop is a gentle warm-white set.
            await self._request(
                "PUT", f"resource/grouped_light/{grouped_light}",
                json={
                    "on": {"on": True},
                    "dimming": {"brightness": 50},
                    "color_temperature": {"mirek": 366},
                },
            )

    async def _grouped_light(self, group: dict[str, Any]) -> str | None:
        for gl in await self.get_resources("grouped_light"):
            if gl.get("owner", {}).get("rid") == group["id"]:
                return gl["id"]
        return None
