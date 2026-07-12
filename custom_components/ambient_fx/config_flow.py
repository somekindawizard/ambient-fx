"""Config flow: auto-adopts the core Hue integration's bridge credentials."""

from __future__ import annotations

from homeassistant import config_entries

from .const import DOMAIN


class AmbientFxConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        hue_entries = self.hass.config_entries.async_entries("hue")
        usable = [e for e in hue_entries if "api_key" in e.data and "host" in e.data]
        if not usable:
            return self.async_abort(reason="no_hue_bridge")

        entry = usable[0]
        if user_input is not None:
            return self.async_create_entry(
                title=f"Ambient FX ({entry.title})",
                data={"host": entry.data["host"], "api_key": entry.data["api_key"]},
            )

        return self.async_show_form(step_id="user", description_placeholders={
            "bridge": entry.title,
        })
