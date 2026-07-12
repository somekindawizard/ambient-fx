"""Hue Entertainment streaming engine (DTLS) for Ambient FX.

Streams HueStream v2 frames at 25 fps over DTLS-PSK to the bridge's
entertainment port (2100). The DTLS transport is an `openssl s_client`
subprocess (present in the HA container) — the same approach used by
diyHue and shell-based Hue Entertainment clients — because pure-Python
DTLS-PSK is not installable on HA OS.

Streaming requires its own bridge application key with a client (PSK)
key, obtained via one link-button registration; the core Hue
integration's key has no PSK and cannot stream.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time

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
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None
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
                    brightness: float | None) -> None:
        await self.stop()

        key, psk = await self._ensure_stream_key()

        config = await self._bridge.find_entertainment_configuration(area_name)
        if config is None:
            raise RuntimeError("No entertainment area found on the bridge")
        self._config_id = config["id"]

        channels = [
            Channel(
                channel_id=c["channel_id"],
                x=c["position"]["x"],
                y=c["position"]["y"],
                z=c["position"]["z"],
            )
            for c in config["channels"]
        ]
        if not channels:
            raise RuntimeError("Entertainment area has no channels")

        # Take ownership of the streaming slot (fails if e.g. the Sync
        # Box is actively syncing — we never steal an active stream).
        await self._bridge.set_stream_state(self._config_id, key, start=True)

        try:
            await self._open_dtls(key, psk)
        except Exception:
            await self._bridge.set_stream_state(self._config_id, key, start=False)
            raise

        effect = STREAM_EFFECTS[effect_name]()
        gain = max(0.05, min(1.5, (brightness or 100) / 100))
        self.active_effect = effect_name
        self._task = asyncio.create_task(
            self._render_loop(effect, channels, gain))
        _LOGGER.info("Streaming '%s' to area '%s' (%d channels)",
                     effect_name, config["name"], len(channels))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._proc:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass
            self._proc = None
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

    # MARK: DTLS transport

    async def _open_dtls(self, key: str, psk_hex: str) -> None:
        host = self._bridge.host
        self._proc = await asyncio.create_subprocess_exec(
            "openssl", "s_client",
            "-dtls1_2",
            "-cipher", "PSK-AES128-GCM-SHA256",
            "-psk_identity", key,
            "-psk", psk_hex,
            "-connect", f"{host}:2100",
            "-quiet",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        # Give the handshake a moment; fail fast if openssl exited.
        await asyncio.sleep(0.8)
        if self._proc.returncode is not None:
            stderr = (await self._proc.stderr.read()).decode(errors="replace")
            self._proc = None
            raise RuntimeError(f"DTLS handshake failed: {stderr.strip()[:300]}")

    def _frame(self, seq: int, colors: dict[int, tuple]) -> bytes:
        buf = bytearray(b"HueStream")
        buf += bytes([0x02, 0x00, seq & 0xFF, 0x00, 0x00, 0x00, 0x00])
        buf += self._config_id.encode("ascii")
        for channel_id, (r, g, b) in colors.items():
            buf += bytes([channel_id])
            buf += struct.pack(
                ">HHH",
                int(max(0.0, min(1.0, r)) * 65535),
                int(max(0.0, min(1.0, g)) * 65535),
                int(max(0.0, min(1.0, b)) * 65535),
            )
        return bytes(buf)

    # MARK: Render loop

    async def _render_loop(self, effect, channels: list[Channel],
                           gain: float) -> None:
        start = time.monotonic()
        seq = 0
        smoothed: dict[int, tuple] = {}
        interval = 1.0 / FPS
        try:
            while True:
                t = time.monotonic() - start
                effect.tick(t)
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

                frame = self._frame(seq, colors)
                seq += 1
                if self._proc is None or self._proc.returncode is not None:
                    _LOGGER.warning("DTLS transport closed; stopping stream")
                    break
                self._proc.stdin.write(frame)
                await self._proc.stdin.drain()
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except (BrokenPipeError, ConnectionResetError):
            _LOGGER.warning("Bridge closed the stream (another app may have "
                            "taken the entertainment slot)")
        finally:
            self.active_effect = None
