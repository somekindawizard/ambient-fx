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

    @property
    def near(self) -> bool:
        """Near-field light (beside the viewer, e.g. couch ends)."""
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
        if ch.near:
            # Couch ends: slow candle-like breathing embers.
            f = _noise(t * 0.6, ch.channel_id)
            return _mix(self.EMBER, self.FLAME, f * 0.5)
        # TV wall: lively flame, each channel its own flicker phase.
        base = _noise(t * 2.2, ch.channel_id * 0.31 + ch.x)
        spike = _noise(t * 6.0, ch.channel_id * 0.77) ** 3
        color = _mix(self.EMBER, self.FLAME, base)
        return _mix(color, self.HOT, spike * 0.6)


class Ocean(Effect):
    DEEP = (0.0, 0.08, 0.30)
    TEAL = (0.0, 0.35, 0.45)
    CREST = (0.55, 0.85, 0.90)

    def render(self, t, ch):
        # Swell travels rear -> front, then breaks across the strip x.
        phase = math.sin(t * 0.5 - ch.y * 1.6 - ch.x * 2.2)
        f = phase * 0.5 + 0.5
        color = _mix(self.DEEP, self.TEAL, f)
        if not ch.near and phase > 0.93:
            # Foam crest only on far lights / strip.
            color = _mix(color, self.CREST, (phase - 0.93) / 0.07)
        if ch.near:
            color = _mix(self.DEEP, self.TEAL, _noise(t * 0.35, ch.channel_id) * 0.7)
        return color


class Aurora(Effect):
    GREEN = (0.05, 0.80, 0.25)
    CYAN = (0.02, 0.45, 0.45)
    VIOLET = (0.35, 0.05, 0.60)
    DARK = (0.01, 0.03, 0.08)

    def render(self, t, ch):
        # Curtains drifting left -> right, violet fringe trailing.
        band = math.sin(ch.x * 2.5 - t * 0.35 + math.sin(t * 0.12) * 2.0)
        shimmer = _noise(t * 1.4, ch.channel_id * 0.5)
        f = max(0.0, band) * (0.6 + shimmer * 0.4)
        color = _mix(self.DARK, _mix(self.GREEN, self.CYAN, shimmer), f)
        fringe = max(0.0, -band - 0.3)
        return _mix(color, self.VIOLET, fringe * 0.8)


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
        rain = _noise(t * 1.8, ch.channel_id * 0.9)
        color = _mix(self.STORM, self.CLOUD, rain)
        dt = t - self.strike_t
        if 0 <= dt < 0.9 and not ch.near:
            # Bolt hits at strike_x, propagates outward along the wall.
            dist = abs(ch.x - self.strike_x)
            delay = dist * 0.15
            local = dt - delay
            if 0 <= local < 0.35:
                flash = math.exp(-local * 12.0)
                color = _mix(color, self.BOLT, flash)
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
        tw = _noise(t * 0.8, ch.channel_id * 1.3)
        color = _mix(self.SKY, self.STARLIGHT, tw)
        dt = t - self.meteor_t
        if 0 <= dt < 1.4 and not ch.near:
            # Head sweeps across x in ~1.2s; gaussian brightness around it.
            head = (-1.1 + dt / 1.2 * 2.2) * self.direction
            d = abs(ch.x - head)
            color = _mix(color, self.METEOR, math.exp(-(d * d) * 18.0))
        return color


class Lava(Effect):
    COOL = (0.12, 0.0, 0.10)
    RED = (0.75, 0.05, 0.02)
    ORANGE = (1.0, 0.35, 0.0)
    MAGENTA = (0.55, 0.02, 0.35)

    def render(self, t, ch):
        blob1 = math.exp(-((ch.x - math.sin(t * 0.11) * 0.9) ** 2) * 3.0)
        blob2 = math.exp(-((ch.x - math.sin(t * 0.07 + 2.1) * 0.9) ** 2) * 3.0)
        color = _mix(self.COOL, self.RED, blob1)
        color = _mix(color, self.MAGENTA, blob2 * 0.8)
        heat = _noise(t * 0.4, ch.channel_id)
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


STREAM_EFFECTS: dict[str, type[Effect]] = {
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
