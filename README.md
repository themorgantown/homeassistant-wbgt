# Heat Stress Guidance — Home Assistant Integration

Reads local weather conditions, calls the [Heat Guidance Calculator API](https://heat-guidance-calculator.pages.dev), and exposes work/rest ratios, hydration guidance, and stop-work alerts as Home Assistant sensor entities. Guidance is drawn from 76 international occupational heat standards (NIOSH, ACGIH, ISO 7243, OSHA, and country-specific rules) and always uses the most protective applicable limit.

---

## Quickstart — up and running in 5 minutes

This is the fastest path if you don't have a local weather station.

**1. Install**

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=themorgantown&repository=homeassistant-wbgt&category=integration)

Click the button above → **Download** → restart Home Assistant.

**2. Add the integration**

[![Configure integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=heat_stress_guidance)

Or: **Settings → Integrations → Add Integration → Heat Stress Guidance**

**3. Step 1 — API**: leave the API URL as the default and click Next.

**4. Step 2 — Weather source**: choose **`location`**, enter your latitude and longitude ([find yours here](https://www.latlong.net)), click Next.

**5. Step 3 — Worker profile**: pick the work intensity that best describes the job, set shift start/end times, and click Submit.

Eight entities will appear under the integration. That's it.

---

## Reading the output

| Entity | What it tells you |
|---|---|
| `sensor.heat_stress_wbgt` | Wet Bulb Globe Temperature in °C — the primary heat stress index. Combines air temperature, humidity, sun, and wind. Values above 28°C begin to recommend/require work/rest limits. |
| `sensor.heat_stress_risk_level` | Plain-language risk tier: **safe** / **low** / **moderate** / **high** / **extreme** / **critical** |
| `sensor.heat_stress_work_minutes` | How many minutes of work are allowed per hour. E.g., `45` means work 45 min, then rest. |
| `sensor.heat_stress_rest_minutes` | Required rest minutes per hour. Complements `work_minutes` — they always sum to 60. |
| `sensor.heat_stress_hydration` | Target fluid intake in mL per hour of work. Typically 250–500 mL/h depending on conditions. |
| `sensor.heat_stress_hydration_ounces` | Same hydration target converted to US fluid ounces per hour (1 fl oz = 29.6 mL). Easier for workers who think in ounces rather than milliliters. |
| `sensor.heat_stress_break_ml` | How much to drink at each rest break — `hydration ÷ (60 ÷ work_minutes)`. |
| `binary_sensor.heat_stress_stop_work` | **On** when one or more applicable standards require all work to stop. Trigger alerts and automations off this. |

All entities carry extra state attributes: `contributing_standards` (which standards drove the result), `acclimatization`, `clothing`, and `effective_wbgt_c` (WBGT after clothing adjustment factor).

**Example:** WBGT 31°C, heavy work, unacclimatized → `work_minutes: 30`, `rest_minutes: 30`, `hydration: 500 mL/h`. A worker does 30 minutes of work, then rests 30 minutes in shade and drinks ~250 mL.

---

## Installation

### Via HACS (recommended)

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=themorgantown&repository=homeassistant-wbgt&category=integration)

1. Click the button above (requires [HACS](https://hacs.xyz) to be installed)
2. Click **Download** in the HACS panel
3. Restart Home Assistant
4. **Settings → Integrations → Add Integration → Heat Stress Guidance**

Updates are delivered automatically through HACS when new versions are released.

### Manual install

1. Copy `custom_components/heat_stress_guidance/` from this repo into your HA `<config>/custom_components/` directory
2. Restart Home Assistant
3. **Settings → Integrations → Add Integration → Heat Stress Guidance**

---

## Configuration

Setup is a 3-step wizard. All settings can be changed later via **Settings → Integrations → Heat Stress Guidance → Configure**.

### Step 1 — API

| Field | Default | Notes |
|---|---|---|
| API URL | `https://heat-guidance-calculator.pages.dev` | Leave as default unless you're running a local server |
| Update interval | `15` minutes | How often to poll. 15 min is appropriate for field use; reduce to 5 min for real-time monitoring. |

### Step 2 — Weather source

Three modes — choose the one that matches your setup:

| Mode | Best when | What you'll enter |
|---|---|---|
| `location` | No local weather station | Latitude and longitude |
| `ha_sensors` | You have an Ambient Weather, Ecowitt, Davis, or similar station already in HA | Entity IDs for temperature and humidity sensors |
| `manual_wbgt` | You have a hardware WBGT sensor in HA, or you've set up a NOAA REST sensor (see below) | Entity ID of the WBGT sensor |

---

#### `location` mode

Uses Open-Meteo forecast data for your coordinates. Good accuracy for planning; slightly lags fast-changing field conditions.

Enter your latitude and longitude as decimal degrees (e.g., `37.7749`, `-122.4194`). [Find your coordinates.](https://www.latlong.net)

---

#### `ha_sensors` mode

Uses temperature and humidity readings already in Home Assistant to estimate WBGT via the Stull (2011) approximation. Good if you have a local weather station.

**Finding your entity IDs:**

1. Go to **Developer Tools → States** in the HA sidebar
2. Use the filter box to search for your station name (e.g., `ambient`, `ecowitt`, `davis`)
3. Copy the full entity ID — it looks like `sensor.ambient_weather_outdoor_temperature`

**Ambient Weather example:**

| Field | Entity ID to enter |
|---|---|
| Temperature | `sensor.ambient_weather_outdoor_temperature` |
| Humidity | `sensor.ambient_weather_outdoor_humidity` |
| Globe temperature | `sensor.ambient_weather_solar_radiation` *(optional — see note)* |

> **Note on globe temperature:** If you don't have a black-globe temperature sensor, leave that field blank. The integration will substitute dry-bulb temperature for Tg, which understates radiant heat load. Treat the result as a conservative lower bound — actual heat stress may be higher in direct sun.

WBGT is estimated using: `WBGT = 0.7·Tnwb + 0.3·Tg` (indoor/shade formula). If workers are in direct sunlight, `location` mode or a hardware WBGT sensor will give more accurate results.

---

#### `manual_wbgt` mode

Use this if you have a hardware WBGT sensor (Davis, Kestrel, Extech, etc.) already appearing as an entity in HA, or if you've created a REST sensor pulling WBGT from an external source.

Enter the entity ID of the sensor that provides the WBGT reading directly (in °C or °F — the integration auto-converts).

**Getting WBGT from NOAA's forecast (US only):**

NOAA's National Digital Forecast Database provides WBGT forecasts. Add this REST sensor to `configuration.yaml`, replacing `YOUR_LAT` and `YOUR_LON` with your coordinates:

```yaml
sensor:
  - platform: rest
    name: wbgt_noaa
    scan_interval: 3600  # NOAA forecast updates hourly; don't poll more often
    resource_template: >-
      https://digital.mdl.nws.noaa.gov/xml/sample_products/browser_interface/ndfdXMLclient.php
      ?whichClient=NDFDgen
      &lat=YOUR_LAT
      &lon=YOUR_LON
      &product=time-series
      &XMLformat=DWML
      &begin={{ now().replace(microsecond=0).isoformat() }}
      &end={{ (now() + timedelta(hours=1)).replace(microsecond=0).isoformat() }}
      &Unit=e
      &wbgt=wbgt
    value_template: "{{ value_json['dwml']['data']['parameters']['temperature']['value'] }}"
    device_class: temperature
```

> **Note:** The NDFD endpoint returns XML (DWML format), so `value_json` may not parse correctly depending on your HA version. If `sensor.wbgt_noaa` shows `unknown`, check the raw response in **Developer Tools → Template**: `{{ states.sensor.wbgt_noaa.attributes }}`. You may need to adjust the `value_template` to parse the XML response for your specific HA setup.

After restart, the entity `sensor.wbgt_noaa` will appear. In the integration setup, choose `manual_wbgt` mode and enter `sensor.wbgt_noaa` as the WBGT entity ID.

---

### Step 3 — Worker profile

| Field | Options | Notes |
|---|---|---|
| **Workload detection mode** | `static` / `mqtt` | See below |
| Work intensity | `light` / `moderate` / `heavy` / `very_heavy` | Used when mode is `static`, or as fallback if MQTT stream is silent |
| MQTT topic | e.g. `opensensor/sensor/accelerometer` | Required when mode is `mqtt` |
| Acclimatization | `unacclimatized` / `partial` / `acclimatized` | New and returning workers: unacclimatized. Full acclimatization takes ~7–14 days of heat exposure. |
| Shift start | HH:MM (e.g. `07:00`) | Used by standards that apply time-of-day limits |
| Shift end | HH:MM (e.g. `15:00`) | |
| Clothing / PPE | Standard work / SMS coveralls / Polyolefin / Double-layer / Vapor-barrier suit | Heavier PPE traps heat — this applies a clothing adjustment factor to WBGT |

**Work intensity reference:**

| Level | Typical tasks |
|---|---|
| Light | Desk work, light assembly, driving |
| Moderate | Sustained walking, lifting <20 kg, tool use |
| Heavy | Pick-and-shovel work, carrying loads, sustained climbing |
| Very heavy | Maximum sustained exertion — rare; rescue operations, fire suppression |

When in doubt, choose the next level up. Standards default to the more protective limit when intensity is uncertain.

---

#### MQTT workload mode — automatic detection via open-sensor

When `workload_mode` is set to `mqtt`, the integration subscribes to an MQTT topic published by the [open-sensor](https://github.com/open-development-team/open-sensor) Android app and derives work intensity automatically from the accelerometer stream. Work/rest ratios and hydration guidance then update in real time as activity changes.

**Prerequisites:**
- The [MQTT integration](https://www.home-assistant.io/integrations/mqtt/) must be configured in Home Assistant (with a broker like Mosquitto)
- The open-sensor app must be installed on an Android device worn or carried by the worker, connected to the same MQTT broker

**How it works:**

The open-sensor app publishes accelerometer data as JSON every time the reading changes:
```json
{"x": 1.23, "y": -0.45, "z": 9.76}
```

The integration computes the net motion above the gravity baseline:
```
excess = abs(sqrt(x² + y² + z²) - 9.81)
```

And maps it to a workload tier:

| Excess acceleration | Detected workload |
|---|---|
| < 1.0 m/s² | light (stationary / minimal movement) |
| 1.0 – 3.0 m/s² | moderate |
| 3.0 – 7.0 m/s² | heavy |
| > 7.0 m/s² | very heavy |

When the detected workload changes, the coordinator immediately re-fetches guidance from the API — no need to wait for the next scheduled poll.

**Setting up open-sensor:**

1. Install the open-sensor app and open Settings
2. Set **Broker URL** to your MQTT broker (e.g. `tcp://homeassistant.local:1883`)
3. Set **Username** and **Password** if your broker requires them
4. Note the **Accelerometer topic** (default: `opensensor/sensor/accelerometer`) — enter this exact value in the integration setup
5. Enable the accelerometer sensor and start publishing

**Monitoring and verification:**

The new entity `sensor.heat_stress_active_workload` shows the workload currently being sent to the API. In MQTT mode, watch this entity change as the worker moves. If it stays fixed, check that open-sensor is publishing and the topic matches.

You can also verify MQTT messages are arriving in **Developer Tools → MQTT** (if the MQTT integration's debug panel is available) or by subscribing to the topic from another MQTT client.

**Fallback behavior:** If no MQTT message has been received yet (e.g. the phone is off), the integration uses the static `Work intensity` setting as a fallback. Workload updates to MQTT-derived values as soon as the first message arrives.

---

## Lovelace dashboard

A pre-built dashboard is included at `lovelace/heat_stress_dashboard.yaml`.

**To add it to Home Assistant:**

1. In the HA sidebar, go to **Overview** and click the pencil (edit) icon
2. Click **⋮ (three dots) → Edit in YAML**
3. Paste the full contents of `lovelace/heat_stress_dashboard.yaml`
4. Click Save

Alternatively, add it as a manual card by going to any dashboard → **Add Card → Manual** and pasting the YAML.

The dashboard includes:
- WBGT gauge (green < 28°C / yellow < 32°C / red ≥ 32°C)
- Guidance entity rows: risk level, work/rest minutes, hydration target
- Conditional red "STOP WORK" banner — only visible when `binary_sensor.heat_stress_stop_work` is `on`

---

## Automation examples

### Stop-work TTS announcement

Announces a verbal alert on all media players when the stop-work threshold is crossed.

```yaml
automation:
  alias: "Heat stress stop-work announcement"
  trigger:
    - platform: state
      entity_id: binary_sensor.heat_stress_stop_work
      to: "on"
  action:
    - action: tts.speak
      target:
        entity_id: media_player.all_speakers
      data:
        message: >
          Heat emergency. Stop work immediately and move to shade or a cool area.
          Current WBGT is {{ states('sensor.heat_stress_wbgt') }} degrees Celsius.
```

### Mobile notification to supervisor when risk reaches high

```yaml
automation:
  alias: "Heat alert — notify supervisor at high risk"
  trigger:
    - platform: state
      entity_id: sensor.heat_stress_risk_level
      to: "high"
  action:
    - action: notify.mobile_app_supervisor_phone
      data:
        title: "Heat Alert — High Risk"
        message: >
          Work/rest ratio: {{ states('sensor.heat_stress_work_minutes') }} work /
          {{ states('sensor.heat_stress_rest_minutes') }} rest min per hour.
          Fluid target: {{ states('sensor.heat_stress_hydration') }} mL/hr.
          Standards: {{ state_attr('sensor.heat_stress_risk_level', 'contributing_standards') | join(', ') }}
```

---

## Troubleshooting

**Entities show `unavailable`**
The integration cannot reach the API. Check that your HA instance has internet access and that the API URL in the integration settings is reachable. To test: open `https://heat-guidance-calculator.pages.dev/api/v1/health` in a browser from the same network as HA.

**WBGT reading seems too low on a sunny day**
In `ha_sensors` mode without a globe temperature sensor, the integration substitutes dry-bulb temperature for Tg. This understates radiant heat in direct sunlight. For outdoor sunny conditions, use `location` mode or a hardware globe/WBGT sensor.

**Risk level stays `safe` when it should be higher**
Check the WBGT value in `sensor.heat_stress_wbgt`. If it reads correctly, verify the work intensity setting — light-work limits are significantly higher than heavy-work limits. Also confirm acclimatization status; acclimatized workers have higher limits, so an unacclimatized worker would require a higher protection setting.

**Config flow fails at the API check step**
The integration pings the API during setup. If you see a connection error, verify the API URL (default: `https://heat-guidance-calculator.pages.dev`) is reachable from your HA host, not just your browser.

**`sensor.wbgt_noaa` shows `unknown` or `unavailable`**
The NDFD endpoint returns XML (DWML format). If `value_json` fails to parse it, inspect the raw response: go to **Developer Tools → Template** and enter `{{ states.sensor.wbgt_noaa }}` to see the current state and attributes. You may need to adjust the `value_template` to parse the XML string directly for your HA version.

**Shift times rejected during setup**
Times must be entered in 24-hour `HH:MM` format with a leading zero (e.g., `07:00`, `15:30`). Values like `7:00` or `3:30 PM` will return a form error.

---

## Standards covered

The API evaluates 76 international occupational heat stress standards including NIOSH 2016, ACGIH TLV, ISO 7243, OSHA, and country-specific rules from 40+ countries. The composite result always uses the most protective (lowest work, highest rest) schedule across all applicable standards — standards are never averaged.

See the [Heat Guidance Calculator](https://heat-guidance-calculator.pages.dev) for the full standards list with thresholds and citations. This page also provides further guidance on work/rest ratios, hydration, and acclimatization.
