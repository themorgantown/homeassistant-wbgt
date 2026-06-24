# Heat Stress Guidance — Home Assistant Integration

This tool is designed for workers, supervisors, and safety officers to monitor heat stress conditions in real time. It can be useful if you're working outdoors, in hot warehouses, or in any environment where heat stress is a concern. Not only does it provide real-time guidance, but it also helps you comply with occupational safety standards for your region. There are as of June 2026 over 76 international occupational heat stress standards included in the guidance, including NIOSH, ACGIH, ISO 7243, OSHA, and country-specific rules.

This tool reads local weather conditions, calls the [Heat Guidance Calculator API](https://heat-guidance-calculator.pages.dev), and exposes work/rest ratios, hydration guidance, and stop-work alerts as Home Assistant sensor entities.  

 
![alt text](sky.jpg)
---

## Quickstart — up and running in 5 minutes

This is the fastest path if you don't have a local weather station.

**1. Install**

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=themorgantown&repository=homeassistant-wbgt&category=integration)

Click the button above → add the repository as an **Integration** → **Download** → restart Home Assistant.

**2. Add the integration**

[![Configure integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=heat_stress_guidance)

Or: **Settings → Integrations → Add Integration → Heat Stress Guidance**

**3. Confirm and submit**: your latitude/longitude are pre-filled from Home Assistant. Optionally pick a phone/tablet under **Alert device** to receive push alerts. Click Submit — everything else uses sensible defaults.

That's it. The integration's entities appear immediately. Want to change the data source, work intensity, shift times, or region? Expand **Advanced** during setup, or adjust it any time under **Configure**.

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
| `sensor.heat_stress_forecast_peak_wbgt` | Highest forecast WBGT in the **next 24 hours** (°C). Attributes carry `peak_time`, `risk_level_at_peak`, and `stop_work_at_peak`. Only populated in `location` / `tracked_entity` modes. |
| `sensor.heat_stress_forecast_peak_time` | Timestamp of that next-24-hour WBGT peak — use it to schedule shift adjustments before conditions worsen. |
| `sensor.heat_stress_forecast_peak_risk_level` | Risk tier the peak WBGT would produce for *this* worker profile (`safe`…`critical`). The `stop_work_at_peak` attribute flags a forecast stop-work. |

The three `forecast_peak_*` entities give a 24-hour lookahead so a supervisor can plan around the hottest part of the day. They are computed from the hourly forecast (`location` / `tracked_entity` modes only) and stay empty in `ha_sensors` / `manual_wbgt` modes, which have no forecast. The peak's risk level is evaluated against the same standards and worker profile as the live guidance.

All entities carry extra state attributes: `contributing_standards` (which standards drove the result), `triggered_by` (the single binding standard), `jurisdiction_scope` (the country/state the guidance is scoped to), `acclimatization`, `clothing`, and `effective_wbgt_c` (WBGT after clothing adjustment factor).

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

If HACS says **Repository themorgantown/homeassistant-wbgt not found**, add it as a custom repository instead of searching the default HACS catalog:

1. Open **HACS → Integrations**
2. Click **⋮ → Custom repositories**
3. Repository: `https://github.com/themorgantown/homeassistant-wbgt`
4. Category: **Integration**
5. Click **Add**, then select **Heat Stress Guidance → Download**

The repository is public, but it is a custom HACS repository. A default-catalog search can report "not found" until the custom repository URL has been added.

### Manual install

1. Copy `custom_components/heat_stress_guidance/` from this repo into your HA `<config>/custom_components/` directory
2. Restart Home Assistant
3. **Settings → Integrations → Add Integration → Heat Stress Guidance**

---

## Configuration

Setup is a **single screen**. The only things shown up front are your **location** (pre-filled from Home Assistant) and an optional **Alert device** — because the headline job of this integration is simply *"tell me when it's dangerously hot."* Everything else lives under a collapsed **Advanced** section with sensible defaults, so a first-time user can just click Submit.

All settings can be changed later via **Settings → Integrations → Heat Stress Guidance → Configure** (same layout: essentials up front, the rest under Advanced).

### Essentials (always shown)

| Field | Default | Notes |
|---|---|---|
| Latitude / Longitude | Your Home Assistant location | Used to fetch the heat forecast. Override for a specific job site. |
| Alert device | *(none)* | Optional. A phone/tablet running the Home Assistant app that receives a rich push when a heat restriction begins. See [Heat alert notifications](#heat-alert-notifications). |
| Worker's phone | *(none)* | Optional second device. Lets the worker's own phone get the same alert as the (often supervisor-held) Alert device. Alerts go to **both**, de-duplicated if they're the same device. |

Everything below is under **Advanced** — open it only if you want to change it.

### Advanced — API

| Field | Default | Notes |
|---|---|---|
| API URL | `https://heat-guidance-calculator.pages.dev` | Leave as default unless you're running a local server |
| Update interval | `15` minutes | How often to poll. 15 min is appropriate for field use; reduce to 5 min for real-time monitoring. |

### Advanced — Weather source

Four modes — choose the one that matches your setup (defaults to `location` using your Home Assistant coordinates):

| Mode | Best when | What you'll enter |
|---|---|---|
| `location` | Fixed job site, facility, farm, or home location | Latitude and longitude |
| `tracked_entity` | Worker/supervisor location should follow a mobile device or Home Assistant user/person | A `person.*` or `device_tracker.*` entity with GPS latitude/longitude attributes |
| `ha_sensors` | You have an Ambient Weather, Ecowitt, Davis, or similar station already in HA | Entity IDs for temperature and humidity sensors |
| `manual_wbgt` | You have a hardware WBGT sensor in HA, or you've set up a NOAA REST sensor (see below) | Entity ID of the WBGT sensor |

---

#### `location` mode

Uses forecast WBGT data for your coordinates. Good accuracy for planning; slightly lags fast-changing field conditions.

The setup form defaults to the latitude and longitude configured in Home Assistant under **Settings → System → General → Location**. You can keep those values or enter job-site coordinates manually as decimal degrees (e.g., `37.7749`, `-122.4194`). [Find coordinates manually.](https://www.latlong.net)

---

#### `tracked_entity` mode

Uses latitude and longitude from an existing Home Assistant location entity, then fetches forecast WBGT for that current position. This is the best option when the relevant work location moves, or when you want guidance based on a supervisor's phone, a worker's phone, or a Home Assistant user/person entity.

Enter an entity that exposes `latitude` and `longitude` attributes, such as:

| Source | Example entity |
|---|---|
| Home Assistant person | `person.alex` |
| Home Assistant mobile app device tracker | `device_tracker.alex_iphone` |
| GPS tracker integration | `device_tracker.work_truck` |

To find a valid entity, go to **Developer Tools → States**, open the `person.*` or `device_tracker.*` entity, and confirm the attributes include `latitude` and `longitude`.

If the mobile device has location sharing disabled, has not reported yet, or only reports a zone name without GPS attributes, the integration will show `unavailable` until Home Assistant has a current GPS fix.

##### Linking a phone with the OwnTracks QR code

If you don't already have a phone reporting location to Home Assistant, this integration can generate a QR code that configures the free [**OwnTracks**](https://owntracks.org) app to send GPS to your HA instance. Scanning it sets everything up — you do **not** type any server address, username, or key into the app by hand.

**Before you scan — one prerequisite:** add the built‑in **OwnTracks integration** first (**Settings → Devices & Services → Add Integration → OwnTracks**). That integration owns the webhook the phone posts to. If it isn't present, the QR step stops with *"The OwnTracks integration is not set up."* (Over [Nabu Casa](https://www.nabucasa.com) the phone reaches HA through a cloudhook, so it works away from home; without a cloud subscription the QR encodes the local webhook URL, which only works on your LAN.)

**Generate and scan:**

1. **Settings → Devices & Services → Heat Stress Guidance → Configure → Show connection QR code.**
2. Optionally edit the identity fields shown above the QR before scanning (see table below). Submitting with the fields unchanged just closes the dialog; changing one regenerates the QR.
3. In the OwnTracks app, tap **Scan QR code** (top of Settings) and point it at the code. The app switches itself to HTTP mode pointed at your HA instance and confirms the imported configuration.

| Field | Default | What it becomes |
|---|---|---|
| username | `worker` | first half of the entity name / OwnTracks `X-Limit-U` |
| device id | `phone` | second half of the entity name / `X-Limit-D` |
| tracker id | `w` | 1–2 character label shown on the map |

The connection is always **end-to-end encrypted** — the QR carries the OwnTracks integration's encryption key, so payloads are encrypted on the phone (libsodium) and decrypted in Home Assistant. No password or token is exchanged.

**What to insert into `tracked_entity`:** after the phone publishes its first location (move it, or tap *Publish* in the app), the OwnTracks integration creates a tracker named from the two fields above:

```
device_tracker.<username>_<device id>
```

So with the defaults you'd enter **`device_tracker.worker_phone`** here. Confirm it exists and has `latitude`/`longitude` under **Developer Tools → States** before selecting it. (If you blank out the username in the app, OwnTracks falls back to the literal `user` and an auto‑generated device id, producing an awkward name like `device_tracker.user_<uuid>` — regenerate the QR with the fields filled in to get a clean name.)

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

### Advanced — Worker profile

| Field | Options | Notes |
|---|---|---|
| **Workload detection mode** | `static` / `mqtt` | See below |
| Work intensity | `light` / `moderate` / `heavy` / `very_heavy` | Used when mode is `static`, or as fallback if MQTT stream is silent |
| MQTT topic | e.g. `opensensor/sensor/accelerometer` | Required when mode is `mqtt` |
| Acclimatization | `unacclimatized` / `partial` / `acclimatized` | New and returning workers: unacclimatized. Full acclimatization takes ~7–14 days of heat exposure. |
| Shift start | HH:MM (e.g. `07:00`) | Used by standards that apply time-of-day limits |
| Shift end | HH:MM (e.g. `15:00`) | |
| Clothing / PPE | Standard work / SMS coveralls / Polyolefin / Double-layer / Vapor-barrier suit | Heavier PPE traps heat — this applies a clothing adjustment factor to WBGT |
| **Safety standard** | **La Isla Network / LIN** (default), or any of the 76 downloaded standards, or **Auto** | Which standard your guidance and alerts reflect. The list is downloaded live from the API. **Auto** keeps the jurisdiction "most-protective" composite (the prior behavior); pinning a standard makes guidance follow that one standard's work/rest + hydration schedule. See [Choosing a standard](#choosing-a-standard). |
| **Country** | ISO country code (e.g. `US`), or blank | **Scopes which standards apply** (used by **Auto**, and by the safety floor when a standard is pinned). Defaults to your Home Assistant country. Blank = global standards only. |
| **US state** | e.g. `NY`, or blank | Only used when Country is `US`. A state with its own rule (CA, CO, MD, MN, NV, OR, WA) adds it; any other state — including NY — uses US federal + global standards. |

(The **Alert device** and **Worker's phone** are essential fields shown above the Advanced section — see [Essentials](#essentials-always-shown).)

#### Choosing a standard

By default the integration uses the **La Isla Network RSH-s** protocol ("LIN") — the API's own recommended default, a rest/shade/hydration model built for outdoor workers. You can instead pin any of the 76 downloaded standards (NIOSH, ACGIH, ISO 7243, a country rule, …), or choose **Auto** to keep the most-protective composite across every standard that applies in your region.

When you pin a single standard, a **safety floor** still applies: if a *legally-binding* rule for your configured Country/US state requires stopping work (for example a regional midday work ban), that **STOP WORK** alert fires even when your chosen standard would only schedule work/rest. Your pinned standard still drives the everyday work/rest and hydration numbers; the `triggered_by` attribute names whichever rule forced a stop. This means a pinned standard can never *under-alert* relative to local law.

> Existing installs that predate this field keep **Auto** until you change it, so upgrading doesn't silently alter your alerts.

### Heat alert notifications

If you pick an **Alert device** and/or a **Worker's phone**, the integration pushes a rich notification straight to that phone/tablet the moment a heat restriction begins — no automation to write. Both targets are notified (de-duplicated if you pick the same device for each), so a worker and a supervisor can be alerted at once. A restriction is a rising edge into **stop-work** or **high / extreme / critical** risk.

The notification:
- shows the current WBGT and either the work/rest/hydration targets or a **STOP WORK** instruction, with the binding standard;
- is **time-sensitive** on iOS and posts to a high-importance "Heat alerts" channel with an amber/red accent on Android;
- carries an **Open dashboard** action;
- uses a stable `tag`, so it **updates in place** as conditions escalate and **auto-clears** when they return to normal.

The device list is populated from devices that have the Home Assistant Companion app installed (the `mobile_app` integration). For more elaborate routing (multiple recipients, escalation, TTS), use the automation examples below instead.

### Why country/state matters

The API evaluates **all 76 worldwide standards** and, left unscoped, reports the single most-protective one — which can be a rule from an unrelated jurisdiction. For example, the **UAE Midday Break Rule** bans work 12:30–15:00 in summer *regardless of temperature*, so an unscoped setup will show **STOP WORK / critical** at a mild 22 °C for a worker in New York. Setting Country = `US` (and your state) restricts the guidance to standards that actually apply to you. Genuinely hot conditions still trigger global standards like ACGIH TLV and NIOSH, so real protection is unchanged. The active driver is shown in the `triggered_by` and `jurisdiction_scope` entity attributes.

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

### Plan-ahead alert when the forecast peak will be severe

Warns a supervisor *in advance* when the next-24-hour forecast peak will reach high risk or require stop-work — time to adjust the shift before it gets dangerous. (Requires `location` or `tracked_entity` weather mode.)

```yaml
automation:
  alias: "Heat forecast — plan-ahead alert"
  trigger:
    - platform: state
      entity_id: sensor.heat_stress_forecast_peak_risk_level
      to:
        - high
        - extreme
        - critical
  action:
    - action: notify.mobile_app_supervisor_phone
      data:
        title: "Heat forecast — {{ states('sensor.heat_stress_forecast_peak_risk_level') }} ahead"
        message: >
          Forecast peak {{ states('sensor.heat_stress_forecast_peak_wbgt') }}°C WBGT at
          {{ as_timestamp(states('sensor.heat_stress_forecast_peak_time')) | timestamp_custom('%a %H:%M') }}.
          {% if is_state_attr('sensor.heat_stress_forecast_peak_risk_level', 'stop_work_at_peak', true) %}
          Work will need to STOP at the peak — adjust the schedule now.
          {% else %}
          Plan rest cycles and hydration around the peak.
          {% endif %}
```

---

## Troubleshooting

**Entities show `unavailable`**
The integration cannot reach the API. Check that your HA instance has internet access and that the API URL in the integration settings is reachable. To test: open `https://heat-guidance-calculator.pages.dev/health` in a browser from the same network as HA.

**`tracked_entity` mode shows `unavailable`**
Open **Developer Tools → States** and inspect the configured `person.*` or `device_tracker.*` entity. It must expose numeric `latitude` and `longitude` attributes. If those attributes are missing, enable location permission/background location for the Home Assistant mobile app or choose fixed `location` mode.

**WBGT reading seems too low on a sunny day**
In `ha_sensors` mode without a globe temperature sensor, the integration substitutes dry-bulb temperature for Tg. This understates radiant heat in direct sunlight. For outdoor sunny conditions, use `location` mode or a hardware globe/WBGT sensor.

**Risk level stays `safe` when it should be higher**
Check the WBGT value in `sensor.heat_stress_wbgt`. If it reads correctly, verify the work intensity setting — light-work limits are significantly higher than heavy-work limits. Also confirm acclimatization status; acclimatized workers have higher limits, so an unacclimatized worker would require a higher protection setting.

**`STOP WORK` / `critical` shown when it isn't hot**
The guidance is being driven by a standard from a jurisdiction that doesn't apply to you — most often a time-of-day work ban like the UAE Midday Break Rule. Check the `triggered_by` attribute on `sensor.heat_stress_risk_level`. Fix it by setting your **Country** (and **US state**) in the integration options so only relevant standards are considered. See [Why country/state matters](#why-countrystate-matters).

**Config flow fails at the API check step**
The integration pings the API during setup. If you see a connection error, verify the API URL (default: `https://heat-guidance-calculator.pages.dev`) is reachable from your HA host, not just your browser.

**`sensor.wbgt_noaa` shows `unknown` or `unavailable`**
The NDFD endpoint returns XML (DWML format). If `value_json` fails to parse it, inspect the raw response: go to **Developer Tools → Template** and enter `{{ states.sensor.wbgt_noaa }}` to see the current state and attributes. You may need to adjust the `value_template` to parse the XML string directly for your HA version.

**Shift times rejected during setup**
Times must be entered in 24-hour `HH:MM` format with a leading zero (e.g., `07:00`, `15:30`). Values like `7:00` or `3:30 PM` will return a form error.

---

## Development — API contract test

This integration is a thin client over the [Heat Guidance Calculator API](https://heat-guidance-calculator.pages.dev). It reads response fields defensively (with `.get()`), so if the API ever renames or drops a field, a sensor would quietly go blank instead of raising an error. To catch that, `tests/test_api_contract.py` calls the **live** API with the same payloads the integration sends and asserts every field it depends on is reachable and returns sane values — across both a normal work/rest schedule and a stop-work scenario, plus the weather and health endpoints.

The [`API contract`](.github/workflows/api-contract.yml) GitHub Action runs this test **once a day** (and on demand via the Actions tab), so upstream API drift is surfaced within a day rather than by a user noticing a missing reading.

Run it locally:

```bash
pip install -r requirements_test.txt
pytest tests/test_api_contract.py -v
```

Point it at a different API instance with `HGC_API_BASE` (e.g. `HGC_API_BASE=http://localhost:8788 pytest tests/test_api_contract.py`). When you change which response fields the integration reads in `custom_components/heat_stress_guidance/coordinator.py`, update the field lists at the top of the test to match.

---

## Why WBGT is the right metric for outdoor labor

When it's 95°F and sunny, what you actually need to know isn't the air temperature — it's how hot your body is going to get. You know the difference between a humid, shaded 95°F and a dry, sunny 95°F. The first is tolerable; the second can be very uncomfortable and dangerous. The difference is **radiant heat load** and your body's ability to dissipate heat via sweat.  

### What simpler metrics miss

The number on a weather app is a dry-bulb air temperature: a thermometer in a shaded, ventilated box. It's useful for planning but tells you nothing about whether a worker digging a trench can safely stay out there.

The NWS **heat index** (the "feels like" number) improves on this by folding in relative humidity. But it was designed for a sedentary person standing in shade, and it has a critical blind spot: it ignores direct solar radiation entirely. On a clear July afternoon, standing in full sun adds the equivalent of 7–15°C to your physiological heat load compared to standing in shade at the same temperature and humidity. Heat index sees none of that.

**Wet-bulb globe temperature (WBGT)** was developed in the 1950s by the US military specifically to predict heat casualties during training exercises. It captures four things simultaneously: air temperature, humidity, radiant heat from the sun and ground, and wind speed. Every one of those variables affects how fast your core body temperature rises. WBGT is not a "feels like" approximation — it's a physiological load measurement. That's why NIOSH, ACGIH, ISO 7243, OSHA, and militaries worldwide all use it as their standard.

### The formula and what each term means

For outdoor conditions in direct sunlight:

```
WBGT = 0.7 × Tnwb + 0.2 × Tg + 0.1 × Ta
```

- **Tnwb — natural wet-bulb temperature (70% weight):** The temperature of a water-soaked wick freely exposed to ambient air, without forced ventilation. It measures how much evaporative cooling is available given the current humidity and air movement. This term dominates because sweat evaporation is the human body's primary heat defense. High humidity collapses the evaporation gradient and your core temperature climbs fast.

- **Tg — globe temperature (20% weight):** The temperature inside a hollow black copper sphere — typically 15 cm diameter, painted flat black — sitting in the open. It equilibrates where radiation absorbed from sun, sky, and ground reflection equals heat lost by convection. A globe thermometer reading of 60°C on a summer afternoon isn't unusual. This term captures what no humidity formula can: the radiant heat load from a cloudless sky.

- **Ta — dry-bulb temperature (10% weight):** Ordinary air temperature in shade. It matters least on its own.

The 70/20/10 weighting reflects measured physiological reality. Humidity is king. Radiant load is a major secondary driver. Air temperature alone is a distant third.

For indoor or shade conditions where there's no direct solar load:

```
WBGT = 0.7 × Tnwb + 0.3 × Tg
```

The globe and wet-bulb split shifts because radiant sources inside buildings (hot machinery, furnaces, metal walls) can still be significant even without the sun.

### How the NWS calculates WBGT

The National Weather Service doesn't deploy physical globe thermometers at every weather station — there are too many grid points. Instead, they generate WBGT forecasts using a numerical model developed by Liljegren et al. (2008) at Argonne National Laboratory. The inputs are things standard weather stations and forecast models already provide: air temperature, dew point (or relative humidity), wind speed, and solar radiation.

The model solves two energy balance equations — one for the globe thermometer and one for the natural wet-bulb wick. Both require numerical iteration because temperature appears inside nonlinear terms.

**Globe temperature (Tg):** At steady state, energy absorbed equals energy lost:

```
α · S_total + ε · σ · Ta⁴  =  ε · σ · Tg⁴ + hg · (Tg − Ta)
```

- `α` = absorptivity of the globe surface (≈ 0.95 for flat black paint)  
- `S_total` = total incoming solar irradiance, W/m²  
- `ε` = thermal emissivity (≈ 0.95)  
- `σ` = Stefan-Boltzmann constant, 5.67 × 10⁻⁸ W/m²·K⁴  
- `hg` = convective heat transfer coefficient — a function of wind speed and globe diameter, derived from the Nusselt-Reynolds-Prandtl relationship

The left side is what the globe absorbs (solar radiation plus longwave radiation from the sky). The right side is what it sheds (thermal emission plus convective cooling by wind). Tg must be solved iteratively because it appears inside a fourth-power term.

**Natural wet-bulb temperature (Tnwb):** The wet wick's energy balance adds evaporative cooling:

```
αw · S_total + ε · σ · Ta⁴  =  ε · σ · Tnwb⁴ + hw · (Tnwb − Ta) + (λ · hw / cp · Le^(2/3)) · (ew − ea) / P
```

- `αw` = absorptivity of the wet wick (≈ 0.4 — cotton reflects more than black paint)  
- `hw` = convective coefficient for the cylindrical wick geometry  
- `λ` = latent heat of vaporization of water  
- `Le` = Lewis number (≈ 0.87 for air/water vapor)  
- `ew` = saturation vapor pressure at Tnwb  
- `ea` = actual vapor pressure of ambient air  
- `P` = atmospheric pressure  

The evaporative term `(ew − ea)` is what makes Tnwb sensitive to humidity. When the air is already saturated, `ew ≈ ea` and evaporation stalls — the wick can't cool itself, and Tnwb approaches Ta. When humidity is low, the gradient is large and Tnwb can be many degrees below Ta.

The NWS runs this model at each grid point of their 2.5 km resolution forecast, driven by NAM model output. The results appear in the National Digital Forecast Database (NDFD) as the `wbgt` element — which is what the `sensor.wbgt_noaa` REST sensor in this integration pulls.

### Software approximations and their tradeoffs

Physical instruments measure Tnwb and Tg directly. Software has to estimate them. Several approximations exist, each with different accuracy/input tradeoffs.

**Stull (2011) wet-bulb approximation** derives Tnwb from just air temperature and relative humidity, with no solar or wind input needed:

```
Tnwb = T · atan[0.151977 · (RH + 8.313659)^0.5]
     + atan(T + RH)
     − atan(RH − 1.676331)
     + 0.00391838 · RH^1.5 · atan(0.023101 · RH)
     − 4.686035
```

Where T is in °C, RH is relative humidity in %, and `atan` returns radians. It's accurate to within ±1°C for temperatures between 5–40°C and relative humidity above 5%. The catch: because it takes no solar radiation or wind input, it can only estimate the humidity side of WBGT. Radiant heat load doesn't exist in this equation.

**What this integration does:** In `ha_sensors` mode without a globe temperature sensor, the integration computes Tnwb (via an equivalent approximation) and substitutes Ta for Tg — effectively the indoor formula `WBGT ≈ 0.7·Tnwb + 0.3·Ta`. This systematically undercounts heat stress by 3–8°C WBGT on sunny days, because the globe thermometer on a clear day runs well above air temperature. In `location` mode, Open-Meteo provides solar radiation data that allows a better Tg estimate before feeding the NWS formula.

### Practical comparison of methods

| Method | Inputs required | Typical accuracy | Use when |
|---|---|---|---|
| Physical WBGT meter (Kestrel 5400, QUESTemp, Extech HT30) | None — direct measurement | ±0.5°C | On-site compliance, OSHA documentation, legal exposure limits |
| NWS NDFD forecast (`manual_wbgt` mode) | Lat/lon | ±1–3°C, 1–3 hr forecast lag | Planning, supervisor-level alerts, no local station |
| Liljegren model with local station data | Ta, dew point, wind speed, solar radiation | ±1–2°C | `location` mode with quality data |
| Stull approximation, globe substituted | Ta, RH only | ±3–8°C (undercounts in direct sun) | Indoor/shade work, `ha_sensors` fallback |
| Heat index (NWS standard public metric) | Ta, RH only | Poor for workers in sun — ignores solar load entirely | Sedentary outdoor public health advisories |

The practical bottom line: if your workers are in direct sun for extended periods, you need a measurement method that accounts for solar radiation. A $400 Kestrel 5400 Heat Stress Tracker is the most defensible option for a job site with potential OSHA exposure. For remote monitoring and supervisor alerts at scale, the `location` mode pulling NWS NDFD data is a reasonable operational choice — just know the reading lags real-time conditions by up to an hour on fast-changing days.

---

## Standards covered

The API evaluates 76 international occupational heat stress standards including NIOSH 2016, ACGIH TLV, ISO 7243, OSHA, and country-specific rules from 40+ countries. The composite result uses the most protective (lowest work, highest rest) schedule across all applicable standards — standards are never averaged. The integration scopes that composite to the standards relevant to your configured **Country** and **US state** (see [Configuration](#configuration)) so rules from unrelated jurisdictions don't drive your guidance.

See the [Heat Guidance Calculator](https://heat-guidance-calculator.pages.dev) for the full standards list with thresholds and citations. This page also provides further guidance on work/rest ratios, hydration, and acclimatization.
