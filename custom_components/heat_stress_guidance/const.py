DOMAIN = "heat_stress_guidance"
PLATFORMS = ["sensor", "binary_sensor"]

DEFAULT_API_URL = "https://heat-guidance-calculator.pages.dev"
DEFAULT_UPDATE_INTERVAL = 15  # minutes
DEFAULT_SHIFT_START = "07:00"
DEFAULT_SHIFT_END = "15:00"
DEFAULT_WORKLOAD = "moderate"
DEFAULT_ACCLIMATIZATION = "unacclimatized"
DEFAULT_CLOTHING = "work"

WEATHER_MODE_LOCATION = "location"
WEATHER_MODE_TRACKED_ENTITY = "tracked_entity"
WEATHER_MODE_HA_SENSORS = "ha_sensors"
WEATHER_MODE_MANUAL_WBGT = "manual_wbgt"

WORKLOAD_MODE_STATIC = "static"
WORKLOAD_MODE_MQTT = "mqtt"
CONF_WORKLOAD_MODE = "workload_mode"
CONF_MQTT_TOPIC = "mqtt_topic"
DEFAULT_WORKLOAD_MODE = WORKLOAD_MODE_STATIC
DEFAULT_MQTT_TOPIC = "opensensor/sensor/accelerometer"

# Excess acceleration thresholds (m/s² above the 9.81 gravity baseline)
DEFAULT_MOTION_THRESHOLD_LIGHT = 1.0     # below → light
DEFAULT_MOTION_THRESHOLD_MODERATE = 3.0  # below → moderate
DEFAULT_MOTION_THRESHOLD_HEAVY = 7.0     # below → heavy; above → very_heavy

WORKLOAD_OPTIONS = ["light", "moderate", "heavy", "very_heavy"]
ACCLIMATIZATION_OPTIONS = ["unacclimatized", "partial", "acclimatized"]
# IDs match FORMULAS.CAF_TABLE in formulas.js
CLOTHING_OPTIONS = ["work", "sms", "poly", "double", "vapor"]
CLOTHING_LABELS = {
    "work": "Standard Work Clothing (0°C)",
    "sms": "SMS Coveralls / Tyvek-type (+0.5°C)",
    "poly": "Polyolefin Coveralls (+1°C)",
    "double": "Double-Layer Cloth Clothing (+3°C)",
    "vapor": "Vapor-Barrier Suit (+11°C)",
}

RISK_LEVELS = ["safe", "low", "moderate", "high", "extreme", "critical"]

CONF_MOTION_THRESHOLD_LIGHT = "motion_threshold_light"
CONF_MOTION_THRESHOLD_MODERATE = "motion_threshold_moderate"
CONF_MOTION_THRESHOLD_HEAVY = "motion_threshold_heavy"

CONF_API_URL = "api_url"
CONF_UPDATE_INTERVAL = "update_interval_minutes"
CONF_WEATHER_MODE = "weather_mode"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_LOCATION_ENTITY = "location_entity"
CONF_TEMP_ENTITY = "temp_entity"
CONF_HUMIDITY_ENTITY = "humidity_entity"
CONF_GLOBE_TEMP_ENTITY = "globe_temp_entity"
CONF_WBGT_ENTITY = "wbgt_entity"
CONF_WORKLOAD = "workload"
CONF_ACCLIMATIZATION = "acclimatization"
CONF_SHIFT_START = "shift_start"
CONF_SHIFT_END = "shift_end"
CONF_CLOTHING = "clothing"
