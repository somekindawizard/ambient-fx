"""Constants and the effect library for Ambient FX."""

DOMAIN = "ambient_fx"

SCENE_PREFIX = "FX "  # bridge scene names: "FX Fireplace" etc. (max 32 chars)

# Each effect is a palette the bridge animates natively (API v2 dynamic
# scenes). Colors are CIE xy + brightness; speed 0..1 is the bridge's
# dynamic-palette cycle speed. This is the same mechanism the official
# Hue app uses for its animated scenes, so it is smooth and runs entirely
# on the bridge — no streaming session, no conflict with a Sync Box.
#
# xy values chosen inside the wide Gamut C of modern Hue color lamps.
EFFECTS: dict[str, dict] = {
    "fireplace": {
        "name": "Fireplace",
        "speed": 0.75,
        "brightness": 60,
        "colors": [
            {"xy": (0.60, 0.38), "bri": 70},   # deep orange
            {"xy": (0.55, 0.41), "bri": 45},   # amber
            {"xy": (0.65, 0.33), "bri": 55},   # red-orange
            {"xy": (0.52, 0.43), "bri": 30},   # dim gold
            {"xy": (0.58, 0.39), "bri": 80},   # bright flame
        ],
    },
    "ocean": {
        "name": "Ocean",
        "speed": 0.35,
        "brightness": 55,
        "colors": [
            {"xy": (0.155, 0.150), "bri": 55},  # deep blue
            {"xy": (0.170, 0.280), "bri": 65},  # teal
            {"xy": (0.150, 0.200), "bri": 40},  # indigo swell
            {"xy": (0.200, 0.330), "bri": 70},  # aqua crest
            {"xy": (0.160, 0.170), "bri": 45},  # blue depth
        ],
    },
    "aurora": {
        "name": "Aurora",
        "speed": 0.30,
        "brightness": 50,
        "colors": [
            {"xy": (0.210, 0.560), "bri": 60},  # arctic green
            {"xy": (0.170, 0.400), "bri": 45},  # green-cyan
            {"xy": (0.245, 0.115), "bri": 40},  # violet
            {"xy": (0.190, 0.480), "bri": 65},  # bright green band
            {"xy": (0.280, 0.140), "bri": 35},  # magenta fringe
        ],
    },
    "sunset": {
        "name": "Sunset",
        "speed": 0.20,
        "brightness": 65,
        "colors": [
            {"xy": (0.580, 0.395), "bri": 75},  # orange glow
            {"xy": (0.500, 0.330), "bri": 60},  # coral
            {"xy": (0.420, 0.250), "bri": 50},  # rose
            {"xy": (0.330, 0.180), "bri": 40},  # dusk purple
            {"xy": (0.550, 0.415), "bri": 70},  # golden hour
        ],
    },
    "forest": {
        "name": "Forest",
        "speed": 0.25,
        "brightness": 50,
        "colors": [
            {"xy": (0.290, 0.590), "bri": 55},  # leaf green
            {"xy": (0.350, 0.520), "bri": 45},  # moss
            {"xy": (0.250, 0.480), "bri": 60},  # canopy light
            {"xy": (0.400, 0.480), "bri": 35},  # olive shade
            {"xy": (0.310, 0.560), "bri": 50},  # fern
        ],
    },
    "candlelight": {
        "name": "Candlelight",
        "speed": 0.55,
        "brightness": 25,
        "colors": [
            {"xy": (0.560, 0.405), "bri": 30},
            {"xy": (0.540, 0.415), "bri": 18},
            {"xy": (0.580, 0.395), "bri": 25},
            {"xy": (0.550, 0.410), "bri": 35},
        ],
    },
    "thunderstorm": {
        "name": "Thunderstorm",
        "speed": 0.85,
        "brightness": 40,
        "colors": [
            {"xy": (0.220, 0.210), "bri": 30},  # storm blue-grey
            {"xy": (0.280, 0.280), "bri": 15},  # dark cloud
            {"xy": (0.320, 0.330), "bri": 95},  # lightning flash
            {"xy": (0.230, 0.230), "bri": 25},  # rolling grey
            {"xy": (0.190, 0.190), "bri": 40},  # cold blue
        ],
    },
    "lava": {
        "name": "Lava Lamp",
        "speed": 0.15,
        "brightness": 55,
        "colors": [
            {"xy": (0.640, 0.330), "bri": 60},  # molten red
            {"xy": (0.470, 0.240), "bri": 45},  # magenta blob
            {"xy": (0.590, 0.380), "bri": 65},  # orange swirl
            {"xy": (0.380, 0.170), "bri": 40},  # purple cool zone
            {"xy": (0.620, 0.350), "bri": 55},  # ember
        ],
    },
    "party": {
        "name": "Party",
        "speed": 0.90,
        "brightness": 80,
        "colors": [
            {"xy": (0.640, 0.330), "bri": 85},  # red
            {"xy": (0.170, 0.700), "bri": 85},  # green
            {"xy": (0.150, 0.060), "bri": 85},  # blue
            {"xy": (0.440, 0.500), "bri": 85},  # yellow
            {"xy": (0.385, 0.155), "bri": 85},  # pink
            {"xy": (0.170, 0.360), "bri": 85},  # cyan
        ],
    },
    "zen": {
        "name": "Zen",
        "speed": 0.10,
        "brightness": 35,
        "colors": [
            {"xy": (0.320, 0.330), "bri": 40},  # soft white
            {"xy": (0.280, 0.290), "bri": 30},  # cool calm
            {"xy": (0.380, 0.380), "bri": 35},  # warm calm
            {"xy": (0.300, 0.310), "bri": 25},  # low neutral
        ],
    },
}
