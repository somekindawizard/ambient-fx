"""Spatial streaming effect renderers for Ambient FX.

Each renderer maps (time, channel position) -> linear RGB (0..1floats).
Coordinate space is Hue's entertainment space: x -1(left)..1(right),
y -1(rear)..1(front/TV wall), z -1(floor)..1(ceiling).

Channels flagged `near` (rear/low — e.g. lights flanking the couch) are
rendered gently: reduced intensity, no hard flashes. The engine applies
an additional low-pass filter to them.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


@dataclass
class Channel:
    channel_id: int
    x: float
    y: float
    z: float
    # Near-field light (beside the viewer, e.g. couch ends): rendered
    # gently. The engine leaves this False in immersive mode, making
    # every light a full scene participant.
    near: bool = False

    @property
    def is_near_position(self) -> bool:
        """Whether this channel sits in the near-field (rear + low)."""
        return self.y < 0 and self.z < 0


def _noise(t: float, seed: float) -> float:
    """Cheap smooth 1D noise in 0..1 (sum of incommensurate sines)."""
    return (
        math.sin(t * 1.7 + seed * 12.9) * 0.5
        + math.sin(t * 3.1 + seed * 78.2) * 0.3
        + math.sin(t * 5.3 + seed * 37.7) * 0.2
    ) * 0.5 + 0.5


def _hash01(n: float) -> float:
    """Deterministic pseudo-random 0..1 from a float."""
    x = math.sin(n * 127.1 + 311.7) * 43758.5453
    return x - math.floor(x)


def _vnoise(t: float, seed: float) -> float:
    """1D value noise in 0..1: random lattice values with smoothstep
    interpolation. Non-repeating (unlike sine sums) — richer motion."""
    i = math.floor(t)
    f = t - i
    f = f * f * (3.0 - 2.0 * f)  # smoothstep
    a = _hash01(i + seed * 57.0)
    b = _hash01(i + 1.0 + seed * 57.0)
    return a + (b - a) * f


def _fbm(t: float, seed: float) -> float:
    """Two-octave fractal value noise, 0..1."""
    return _vnoise(t, seed) * 0.65 + _vnoise(t * 2.7, seed + 13.0) * 0.35


def _mix(a: tuple, b: tuple, f: float) -> tuple:
    f = max(0.0, min(1.0, f))
    return tuple(a[i] + (b[i] - a[i]) * f for i in range(3))


class Effect:
    """Base class. Subclasses implement render()."""

    # Whether slow companion lights (BLE, ~1s cadence) should join this
    # effect. Fast positional effects look wrong sampled that slowly.
    companion_friendly = True

    def __init__(self) -> None:
        self.rng = random.Random()

    def render(self, t: float, ch: Channel) -> tuple[float, float, float]:
        raise NotImplementedError

    def tick(self, t: float) -> None:
        """Called once per frame before per-channel rendering."""


class Fireplace(Effect):
    EMBER = (0.55, 0.10, 0.0)
    FLAME = (1.0, 0.35, 0.02)
    HOT = (1.0, 0.6, 0.12)

    def render(self, t, ch):
        # Minutes-scale swell: fire grows and dies down over time.
        meta = 0.65 + 0.35 * _vnoise(t * 0.03, 3.0)
        if ch.near:
            # Couch ends: slow candle-like breathing embers.
            f = _fbm(t * 0.5, ch.channel_id * 1.7)
            return _mix(self.EMBER, self.FLAME, f * 0.5 * meta)
        # TV wall: lively flame, each channel its own non-repeating
        # flicker; sharp spark layer with cubic easing on top.
        base = _fbm(t * 2.0, ch.channel_id * 0.31 + ch.x * 7.0)
        spark = _vnoise(t * 6.5, ch.channel_id * 0.77) ** 3
        color = _mix(self.EMBER, self.FLAME, base * meta)
        return _mix(color, self.HOT, spark * 0.65 * meta)


class Ocean(Effect):
    DEEP = (0.0, 0.08, 0.30)
    TEAL = (0.0, 0.35, 0.45)
    CREST = (0.55, 0.85, 0.90)

    def render(self, t, ch):
        # Tide layer: sea state rises and calms over minutes.
        tide = 0.6 + 0.4 * _vnoise(t * 0.025, 8.0)
        # Swell travels rear -> front with noise-varied amplitude, then
        # breaks across the strip x.
        amp = 0.55 + 0.45 * _fbm(t * 0.15, ch.channel_id * 0.4)
        phase = math.sin(t * 0.5 - ch.y * 1.6 - ch.x * 2.2) * amp
        f = (phase * 0.5 + 0.5) * tide
        color = _mix(self.DEEP, self.TEAL, f)
        if not ch.near and phase > 0.72:
            # Foam crest on far lights / strip, eased in and out.
            crest = (phase - 0.72) / 0.28
            color = _mix(color, self.CREST, crest * crest * tide)
        if ch.near:
            color = _mix(self.DEEP, self.TEAL,
                         _fbm(t * 0.3, ch.channel_id * 1.3) * 0.7 * tide)
        return color


class Aurora(Effect):
    GREEN = (0.05, 0.80, 0.25)
    CYAN = (0.02, 0.45, 0.45)
    VIOLET = (0.35, 0.05, 0.60)
    DARK = (0.01, 0.03, 0.08)

    def render(self, t, ch):
        # Solar activity: displays strengthen and fade over minutes.
        activity = 0.5 + 0.5 * _vnoise(t * 0.02, 11.0)
        # Curtains drifting left -> right with noise-wandering path,
        # violet fringe trailing behind each band.
        wander = (_vnoise(t * 0.1, 4.0) - 0.5) * 3.0
        band = math.sin(ch.x * 2.5 - t * 0.35 + wander)
        shimmer = _fbm(t * 1.2, ch.channel_id * 0.5)
        f = max(0.0, band) * (0.5 + shimmer * 0.5) * (0.4 + activity * 0.6)
        color = _mix(self.DARK, _mix(self.GREEN, self.CYAN, shimmer), f)
        fringe = max(0.0, -band - 0.3)
        return _mix(color, self.VIOLET, fringe * 0.8 * activity)


class Thunderstorm(Effect):
    STORM = (0.06, 0.08, 0.14)
    CLOUD = (0.10, 0.11, 0.16)
    BOLT = (0.9, 0.92, 1.0)

    def __init__(self):
        super().__init__()
        self.next_strike = 5.0
        self.strike_t = -10.0
        self.strike_x = 0.0

    def tick(self, t):
        if t >= self.next_strike:
            self.strike_t = t
            self.strike_x = self.rng.uniform(-1, 1)
            self.next_strike = t + self.rng.uniform(6.0, 18.0)

    def render(self, t, ch):
        rain = _fbm(t * 1.6, ch.channel_id * 0.9)
        # Storm intensity waxes and wanes over minutes.
        storm = 0.55 + 0.45 * _vnoise(t * 0.03, 7.0)
        color = _mix(self.STORM, self.CLOUD, rain * storm)
        dt = t - self.strike_t
        if 0 <= dt < 1.2 and not ch.near:
            # Bolt hits at strike_x, propagates outward along the wall.
            # Real lightning double-flashes: main stroke + re-strike.
            dist = abs(ch.x - self.strike_x)
            local = dt - dist * 0.15
            if 0 <= local < 0.6:
                flash = math.exp(-local * 14.0)
                if local > 0.12:
                    flash += math.exp(-(local - 0.12) * 16.0) * 0.6
                color = _mix(color, self.BOLT, min(1.0, flash))
        elif 0 <= dt < 1.5 and ch.near:
            # Couch lights: only a soft distant glow, never a hard flash.
            color = _mix(color, (0.3, 0.32, 0.4), math.exp(-dt * 3.0) * 0.4)
        return color


class MeteorShower(Effect):
    SKY = (0.01, 0.01, 0.06)
    STARLIGHT = (0.05, 0.06, 0.14)
    METEOR = (0.85, 0.9, 1.0)

    def __init__(self):
        super().__init__()
        self.next_meteor = 3.0
        self.meteor_t = -10.0
        self.direction = 1

    def tick(self, t):
        if t >= self.next_meteor:
            self.meteor_t = t
            self.direction = self.rng.choice([-1, 1])
            self.next_meteor = t + self.rng.uniform(4.0, 11.0)

    def render(self, t, ch):
        tw = _fbm(t * 0.7, ch.channel_id * 1.3)
        color = _mix(self.SKY, self.STARLIGHT, tw)
        dt = t - self.meteor_t
        if 0 <= dt < 2.2 and not ch.near:
            # Head sweeps across x in ~1.2s with an asymmetric glow:
            # tight leading edge, long fading trail behind it.
            head = (-1.1 + dt / 1.2 * 2.2) * self.direction
            offset = (ch.x - head) * self.direction
            if offset > 0:
                glow = math.exp(-(offset * offset) * 24.0)          # ahead
            else:
                glow = math.exp(offset * 3.5) * math.exp(-dt * 1.2)  # trail
            color = _mix(color, self.METEOR, min(1.0, glow))
        return color


class Lava(Effect):
    COOL = (0.12, 0.0, 0.10)
    RED = (0.75, 0.05, 0.02)
    ORANGE = (1.0, 0.35, 0.0)
    MAGENTA = (0.55, 0.02, 0.35)

    def render(self, t, ch):
        # Blobs wander on non-repeating noise paths instead of sine orbits.
        p1 = (_vnoise(t * 0.06, 21.0) - 0.5) * 2.2
        p2 = (_vnoise(t * 0.045, 34.0) - 0.5) * 2.2
        blob1 = math.exp(-((ch.x - p1) ** 2) * 3.0)
        blob2 = math.exp(-((ch.x - p2) ** 2) * 3.0)
        color = _mix(self.COOL, self.RED, blob1)
        color = _mix(color, self.MAGENTA, blob2 * 0.8)
        heat = _fbm(t * 0.35, ch.channel_id * 0.8)
        return _mix(color, self.ORANGE, blob1 * blob2 * heat)


class VolcanoFlow(Effect):
    """Molten flows radiating from a vent behind the TV, with periodic
    eruptions that surge outward through the room (eased, layered)."""

    BLACK = (0.03, 0.0, 0.01)
    CRUST = (0.30, 0.02, 0.0)
    LAVA = (0.85, 0.10, 0.0)
    SURGE = (1.0, 0.45, 0.05)
    VENT = (0.0, 1.0, -1.0)  # x, y, z: floor level, center of TV wall

    def __init__(self):
        super().__init__()
        self.next_eruption = 12.0
        self.eruption_t = -100.0

    def tick(self, t):
        if t >= self.next_eruption:
            self.eruption_t = t
            self.next_eruption = t + self.rng.uniform(25.0, 60.0)

    def _dist(self, ch: Channel) -> float:
        dx, dy, dz = ch.x - self.VENT[0], ch.y - self.VENT[1], ch.z - self.VENT[2]
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def render(self, t, ch):
        d = self._dist(ch)
        # Base layer: slow molten flow — long-period fbm noise, hotter
        # near the vent, with a minutes-scale swell so it evolves.
        flow = _fbm(t * 0.18 + d * 1.4, ch.channel_id * 0.9)
        meta = 0.6 + 0.4 * _vnoise(t * 0.02, 5.0)
        heat = max(0.0, (1.6 - d) / 1.6) * 0.5 + flow * 0.5
        color = _mix(self.BLACK, self.CRUST, min(1.0, heat * 1.4 * meta))
        color = _mix(color, self.LAVA, max(0.0, heat - 0.35) * 1.3 * meta)

        # Event layer: eruption surge travels outward from the vent with
        # an eased front (fast attack, slow decay).
        dt = t - self.eruption_t
        if 0 <= dt < 6.0:
            arrival = d * 0.9
            local = dt - arrival
            if local >= 0:
                envelope = min(local * 4.0, 1.0) * math.exp(-local * 0.9)
                if ch.near:
                    envelope *= 0.5  # gentle swell beside the couch
                color = _mix(color, self.SURGE, envelope)
        return color


class RefreshingRain(Effect):
    """Cool rain: soft grey-blue base, individual droplet hits with fast
    attack / slow decay, and occasional gust waves sweeping across."""

    BASE = (0.04, 0.07, 0.12)
    MIST = (0.10, 0.16, 0.22)
    DROP = (0.45, 0.75, 0.85)

    def __init__(self):
        super().__init__()
        self.drops: list[tuple[float, int]] = []  # (start_t, channel_id)
        self.next_drop = 0.5
        self.gust_t = -100.0
        self.next_gust = 20.0

    def tick(self, t):
        if t >= self.next_drop:
            self.drops.append((t, self.rng.randint(0, 15)))
            self.next_drop = t + self.rng.uniform(0.15, 0.7)
            self.drops = [d for d in self.drops if t - d[0] < 2.5]
        if t >= self.next_gust:
            self.gust_t = t
            self.next_gust = t + self.rng.uniform(18.0, 45.0)

    def render(self, t, ch):
        # Base layer: misty drift.
        mist = _fbm(t * 0.35, ch.channel_id * 1.1)
        color = _mix(self.BASE, self.MIST, mist)

        # Gust layer: a soft brightness wave sweeping left -> right.
        gdt = t - self.gust_t
        if 0 <= gdt < 3.0:
            front = -1.2 + gdt * 1.0
            g = math.exp(-((ch.x - front) ** 2) * 6.0) * (1.0 - gdt / 3.0)
            color = _mix(color, self.MIST, g * 0.9)

        # Droplet layer: sharp attack, ~1.5s shimmer decay. Couch lights
        # get far fewer, softer hits.
        for start, target in self.drops:
            if target % 16 != ch.channel_id % 16:
                continue
            dt = t - start
            if 0 <= dt < 2.0:
                envelope = min(dt * 14.0, 1.0) * math.exp(-dt * 2.6)
                if ch.near:
                    envelope *= 0.35
                color = _mix(color, self.DROP, envelope)
        return color


def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[float, float, float]:
    """h 0..1, s 0..1, v 0..1 -> linear RGB 0..1."""
    h = (h % 1.0) * 6.0
    i = int(h)
    f = h - i
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    return [(v, t, p), (q, v, p), (p, v, t),
            (p, q, v), (t, p, v), (v, p, q)][i % 6]


class Swirl(Effect):
    """A comet-like arc of color orbiting the room, trailing a fading
    tail, slowly cycling through the color wheel as it circles."""

    ORBIT_PERIOD = 12.0  # seconds per revolution
    HUE_PERIOD = 45.0    # seconds per full color-wheel cycle

    # Sampled at ~1s over BLE, an orbit reads as random pulses — skip.
    companion_friendly = False

    HUE_SLEW = 0.06  # max hue drift per second (fraction of the wheel)

    def __init__(self):
        super().__init__()
        self._shown_hue: dict[int, float] = {}
        self._last_t: dict[int, float] = {}

    def _eased_hue(self, ch_id: int, t: float, target: float) -> float:
        """Slew-limit each light's displayed hue so a new color can only
        dissolve in, never snap over what the light is showing."""
        prev = self._shown_hue.get(ch_id)
        if prev is None:
            self._shown_hue[ch_id] = target % 1.0
            self._last_t[ch_id] = t
            return target
        dt = max(0.0, min(0.5, t - self._last_t[ch_id]))
        self._last_t[ch_id] = t
        # Shortest signed distance around the hue wheel.
        d = (target - prev + 0.5) % 1.0 - 0.5
        step = max(-self.HUE_SLEW * dt, min(self.HUE_SLEW * dt, d))
        shown = (prev + step) % 1.0
        self._shown_hue[ch_id] = shown
        return shown

    def render(self, t, ch):
        # Angle of this light around the room center, and the swirl head.
        light_angle = math.atan2(ch.y, ch.x)
        head_angle = (t / self.ORBIT_PERIOD) * 2.0 * math.pi
        behind = (head_angle - light_angle) % (2.0 * math.pi)
        ahead = (light_angle - head_angle) % (2.0 * math.pi)

        hue = t / self.HUE_PERIOD
        # Long symmetric approach and departure: each light spends ~2s
        # fading up ahead of the head and ~3s fading down behind it.
        rise = math.exp(-(ahead * ahead) * 1.1)
        fall = math.exp(-(behind * behind) * 0.7)
        tail = math.exp(-behind * 0.9) * 0.4
        glow = min(1.0, max(rise, fall) + tail)

        # The engine applies a 2.2 gamma for color depth; that turns a
        # linear fade into dark-dark-dark-POP. Pre-compensate so the
        # fade is perceptually linear — this is what makes lights feel
        # like they swell and recede rather than switch.
        glow = glow ** 0.45

        # Base glow high enough that lamps never cross their visible
        # on/off threshold.
        base = 0.14 + 0.04 * _fbm(t * 0.3, ch.channel_id * 0.9)
        level = base + (1.0 - base) * glow

        # Rainbow wake (continuous around the wrap), then slew-limit the
        # displayed hue so the head's color dissolves over the tail's
        # instead of overwriting it as it sweeps past.
        wake = math.sin(behind * 0.5) * 0.08
        shown = self._eased_hue(ch.channel_id, t, hue - wake)
        r, g, b = _hsv_to_rgb(shown, 0.85, 1.0)
        return (r * level, g * level, b * level)


class Breathing(Effect):
    """Coherent-breathing guide: the whole room inhales and exhales at
    ~5.5 breaths/min (5.5s in, 5.5s out — the HRV-resonance cadence used
    in calming breathwork), colored like Apple's Breathe flower: deep
    blue-teal at rest opening into pale mint at full breath. The breath
    rises through the room: floor lights lead the inhale, ceiling peaks
    last, and it settles back down on the exhale."""

    CYCLE = 11.0  # seconds per full breath

    # Apple Breathe flower palette (HSV): deep teal-blue -> pale mint.
    REST_H, REST_S = 0.53, 0.80
    FULL_H, FULL_S = 0.42, 0.45

    def render(self, t, ch):
        # Vertical phase lead: the inhale sweeps floor -> ceiling over
        # ~0.6s; reversed feel on the exhale comes free from the cosine.
        phase = (t - (ch.z + 1.0) * 0.3) / self.CYCLE
        # Raised cosine: perfectly smooth at both turnarounds.
        breath = 0.5 - 0.5 * math.cos(phase * 2.0 * math.pi)

        # Perceptual compensation (engine applies 2.2 gamma) so the
        # swell reads linear — same trick as the swirl.
        level = 0.12 + 0.88 * (breath ** 0.45)

        h = self.REST_H + (self.FULL_H - self.REST_H) * breath
        s = self.REST_S + (self.FULL_S - self.REST_S) * breath
        r, g, b = _hsv_to_rgb(h, s, 1.0)
        return (r * level, g * level, b * level)


STREAM_EFFECTS: dict[str, type[Effect]] = {
    "swirl": Swirl,
    "breathing": Breathing,
    "fireplace": Fireplace,
    "ocean": Ocean,
    "aurora": Aurora,
    "thunderstorm": Thunderstorm,
    "meteor_shower": MeteorShower,
    "lava": Lava,
    "volcano_flow": VolcanoFlow,
    "refreshing_rain": RefreshingRain,
}

# Near-field (couch) intensity ceiling and smoothing factor.
NEAR_GAIN = 0.55
NEAR_SMOOTH = 0.12  # exponential low-pass per frame
