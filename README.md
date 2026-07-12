# Ambient FX

Animated dynamic scenes for Philips Hue lights in Home Assistant — fireplace, ocean, aurora, thunderstorm, and more, in the spirit of the old Ambee iOS app.

Effects run as **Hue API v2 dynamic scenes**: the bridge animates the color palette itself, so animations are smooth, survive HA restarts, add zero network chatter, and never conflict with a Hue Sync Box.

## Setup

1. Install via HACS (custom repository) and restart Home Assistant.
2. Settings → Devices & Services → Add Integration → **Ambient FX**. It reuses your existing Hue bridge credentials automatically.

## Services

```yaml
service: ambient_fx.start
data:
  effect: fireplace   # fireplace | ocean | aurora | sunset | forest |
                      # candlelight | thunderstorm | lava | party | zen
  group: Living Room  # Hue room or zone name
  speed: 0.6          # optional, 0–1
  brightness: 50      # optional, 1–100

service: ambient_fx.stop
data:
  group: Living Room
  turn_off: false     # true = lights off, false = freeze to warm white
```

Works great in automations and scripts — e.g. start `fireplace` when Movie Night ends, or `thunderstorm` when it's actually raining.
