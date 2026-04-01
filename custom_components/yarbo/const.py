"""Constants for the Yarbo integration."""

DOMAIN = "yarbo"
PLATFORMS = ["sensor", "binary_sensor", "select"]

# Config flow
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
# Config entry data keys
DATA_ACCESS_TOKEN = "access_token"
DATA_REFRESH_TOKEN = "refresh_token"

# MQTT status update interval fallback (REST polling)
UPDATE_INTERVAL_MINUTES = 5
