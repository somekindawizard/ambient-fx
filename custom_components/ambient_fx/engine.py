"""Hue Entertainment streaming engine for Ambient FX.

Streams HueStream v2 frames at 25 fps over DTLS-PSK to the bridge's
entertainment port (2100) using the bundled pure-Python DTLS client
(the HA container has no openssl binary and no installable DTLS lib).
The blocking socket work runs in an executor thread.

Streaming requires its own bridge application key with a client (PSK)
key, obtained via one link-button registration; the core Hue
integration's key has no PSK and cannot stream.
"""

from __future__ import annotations

import logging
import struct
import threading
import time

from .dtls import DTLSPSKConnection
from .effects import NEAR_GAIN, NEAR_SMOOTH, STREAM_EFFECTS, Channel

_LOGGER = logging.getLogger(__name__)

FPS = 25


class LinkButtonNotPressed(Exception):
    """Bridge registration needs the physical link button pressed."""


class StreamEngine:
    def __init__(self, hass, bridge, entry) -> None:
        self._hass = hass
        self._bridge = bridge  # HueFxBridge (REST client)
        self._entry = entry
        self._stop_event: threading.Event | None = None
        self._job = None
        self._config_id: str | None = None
        self.active_effect: str | None = None

    # MARK: Credentials

    async def _ensure_stream_key(self) -> tuple[str, str]:
        """Return (application_key, psk_hex), registering if needed."""
        data = self._entry.data
        if data.get("stream_key") and data.get("stream_psk"):
            return data["stream_key"], data["stream_psk"]

        result = await self._bridge.register_streaming_app()
        if result is None:
            raise LinkButtonNotPressed(
                "Press the round link button on the Hue bridge, then start "
                "the effect again within 30 seconds."
            )
        key, psk = result
        self._hass.config_entries.async_update_entry(
            self._entry, data={**data, "stream_key": key, "stream_psk": psk}
        )
        return key, psk

    # MARK: Lifecycle

    async def start(self, effect_name: str, area_name: str | None,
                    brightness: float | None,
                    immersive: bool = False,
                    companions: list[dict] | None = None) -> None:
        await self.stop()

        key, psk = await self._ensure_stream_key()

        config = await self._bridge.find_entertainment_configuration(area_name)
        if config is None:
            raise RuntimeError("No entertainment area found on the bridge")
        self._config_id = config["id"]

        channels = []
        for c in config["channels"]:
            ch = Channel(
                channel_id=c["channel_id"],
                x=c["position"]["x"],
                y=c["position"]["y"],
                z=c["position"]["z"],
            )
            # Cozy mode softens near-field (couch) lights; immersive
            # mode makes every light a full scene participant.
            ch.near = ch.is_near_position and not immersive
            channels.append(ch)
        if not channels:
            raise RuntimeError("Entertainment area has no channels")

        # Take ownership of the streaming slot (fails if e.g. the Sync
        # Box is actively syncing — we never steal an active stream).
        await self._bridge.set_stream_state(self._config_id, key, start=True)

        # Companion lights: non-Hue HA light entities that participate in
        # the effect at a low update rate (BLE/HomeKit can't take 25 fps).
        comp_channels: list[tuple[str, Channel]] = []
        for i, comp in enumerate(companions or []):
            ch = Channel(channel_id=200 + i, x=float(comp["x"]),
                         y=float(comp["y"]), z=float(comp["z"]))
            ch.near = ch.is_near_position and not immersive
            comp_channels.append((comp["entity_id"], ch))

        effect = STREAM_EFFECTS[effect_name]()
        gain = max(0.05, min(1.5, (brightness or 100) / 100))
        stop_event = threading.Event()
        self._stop_event = stop_event
        self.active_effect = effect_name
        config_id = self._config_id
        host = self._bridge.host

        def _thread() -> None:
            self._stream_thread(host, config_id, key, psk, effect,
                                channels, gain, stop_event, comp_channels)

        self._job = self._hass.async_add_executor_job(_thread)
        _LOGGER.info("Streaming '%s' to area '%s' (%d channels)",
                     effect_name, config["name"], len(channels))

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
            self._stop_event = None
        if self._job:
            try:
                await self._job
            except Exception as err:
                _LOGGER.debug("Stream thread ended with: %s", err)
            self._job = None
        if self._config_id:
            key = self._entry.data.get("stream_key")
            if key:
                try:
                    await self._bridge.set_stream_state(
                        self._config_id, key, start=False)
                except Exception as err:  # bridge may already have stopped it
                    _LOGGER.debug("Stream stop: %s", err)
            self._config_id = None
        self.active_effect = None

    # MARK: Blocking stream thread (runs in executor)

    COMPANION_INTERVAL = 1.5  # seconds between HA light updates (BLE pace)

    def _push_companion(self, entity_id: str, r: float, g: float,
                        b: float) -> None:
        """Send a color to an HA light entity from the stream thread."""
        m = max(r, g, b)
        if m < 0.02:
            data = {"entity_id": entity_id, "brightness": 3,
                    "transition": self.COMPANION_INTERVAL}
        else:
            data = {
                "entity_id": entity_id,
                "rgb_color": [int(r / m * 255), int(g / m * 255), int(b / m * 255)],
                "brightness": max(3, int(min(1.0, m) * 255)),
                "transition": self.COMPANION_INTERVAL,
            }

        def _call() -> None:
            self._hass.async_create_task(
                self._hass.services.async_call("light", "turn_on", data))

        self._hass.loop.call_soon_threadsafe(_call)

    def _stream_thread(self, host: str, config_id: str, key: str,
                       psk_hex: str, effect, channels: list[Channel],
                       gain: float, stop_event: threading.Event,
                       comp_channels: list[tuple[str, Channel]] | None = None) -> None:
        conn = DTLSPSKConnection(host, 2100, key, bytes.fromhex(psk_hex))
        try:
            conn.handshake()
        except Exception:
            conn.close()
            self.active_effect = None
            raise

        start = time.monotonic()
        seq = 0
        smoothed: dict[int, tuple] = {}
        interval = 1.0 / FPS
        last_comp_push = -10.0
        last_comp_color: dict[str, tuple] = {}
        try:
            while not stop_event.is_set():
                t = time.monotonic() - start
                effect.tick(t)

                # Companion lights: sample the effect at their position
                # on a slow cadence, pushing only meaningful changes.
                if comp_channels and t - last_comp_push >= self.COMPANION_INTERVAL:
                    last_comp_push = t
                    for entity_id, cch in comp_channels:
                        cr, cg, cb = effect.render(t, cch)
                        if cch.near:
                            cr, cg, cb = (cr * NEAR_GAIN, cg * NEAR_GAIN,
                                          cb * NEAR_GAIN)
                        cr, cg, cb = (min(1.0, cr * gain), min(1.0, cg * gain),
                                      min(1.0, cb * gain))
                        prev = last_comp_color.get(entity_id)
                        if prev and sum(abs(prev[i] - c) for i, c in
                                        enumerate((cr, cg, cb))) < 0.05:
                            continue
                        last_comp_color[entity_id] = (cr, cg, cb)
                        self._push_companion(entity_id, cr, cg, cb)
                colors: dict[int, tuple] = {}
                for ch in channels:
                    r, g, b = effect.render(t, ch)
                    if ch.near:
                        # Near-field (couch) lights: cap + low-pass.
                        r, g, b = r * NEAR_GAIN, g * NEAR_GAIN, b * NEAR_GAIN
                        prev = smoothed.get(ch.channel_id, (r, g, b))
                        r = prev[0] + (r - prev[0]) * NEAR_SMOOTH
                        g = prev[1] + (g - prev[1]) * NEAR_SMOOTH
                        b = prev[2] + (b - prev[2]) * NEAR_SMOOTH
                        smoothed[ch.channel_id] = (r, g, b)
                    # Perceptual gamma so dim scenes don't posterize.
                    colors[ch.channel_id] = (
                        (r * gain) ** 2.2, (g * gain) ** 2.2, (b * gain) ** 2.2)

                conn.send(self._frame(config_id, seq, colors))
                seq += 1
                # Drift-free pacing.
                next_t = start + seq * interval
                delay = next_t - time.monotonic()
                if delay > 0:
                    stop_event.wait(delay)
        except OSError as err:
            _LOGGER.warning("Stream transport closed (%s) — another app may "
                            "have taken the entertainment slot", err)
        finally:
            conn.close()
            self.active_effect = None

    @staticmethod
    def _frame(config_id: str, seq: int, colors: dict[int, tuple]) -> bytes:
        buf = bytearray(b"HueStream")
        buf += bytes([0x02, 0x00, seq & 0xFF, 0x00, 0x00, 0x00, 0x00])
        buf += config_id.encode("ascii")
        for channel_id, (r, g, b) in colors.items():
            buf += bytes([channel_id])
            buf += struct.pack(
                ">HHH",
                int(max(0.0, min(1.0, r)) * 65535),
                int(max(0.0, min(1.0, g)) * 65535),
                int(max(0.0, min(1.0, b)) * 65535),
            )
        return bytes(buf)
