"""Build the OwnTracks scan-to-link QR payload.

The QR encodes a complete ``owntracks:///config?inline=<base64-json>`` URL. When
scanned by the OwnTracks iOS app it is handed straight to that app's existing
config-import pipeline (``processURIConfig:`` -> ``configFromDictionary:``), which
switches the app to HTTP mode pointing at this Home Assistant instance.

Transport is the OwnTracks core integration's HTTP webhook, exposed over Nabu Casa
as a cloudhook when a subscription is active. The cloudhook URL is itself the
secret, so no MQTT broker, password, or long-lived token is involved.

Payloads are always end-to-end encrypted (libsodium): the QR carries the OwnTracks
integration's encryption key, and we refuse to build a QR when no such key exists
rather than fall back to plaintext.
"""
from __future__ import annotations

import base64
import json

from homeassistant.components import cloud, webhook
from homeassistant.core import HomeAssistant

OWNTRACKS_DOMAIN = "owntracks"

# OwnTracks config-dictionary connection mode for HTTP (mode 0 = MQTT, 3 = HTTP).
OWNTRACKS_MODE_HTTP = 3

DEFAULT_USER = "worker"
DEFAULT_DEVICE_ID = "phone"
DEFAULT_TRACKER_ID = "w"


class OwnTracksNotConfigured(Exception):
    """The OwnTracks core integration is not set up, so there is no webhook."""


class CloudhookUnavailable(Exception):
    """A reachable webhook URL could not be resolved."""


class EncryptionSecretUnavailable(Exception):
    """No encryption secret is available, so an encrypted QR cannot be built."""


async def _resolve_webhook_url(hass: HomeAssistant, webhook_id: str) -> str:
    """Return the cloudhook URL when cloud is active, else the local webhook URL."""
    if cloud.async_active_subscription(hass):
        try:
            return await cloud.async_get_or_create_cloudhook(hass, webhook_id)
        except cloud.CloudNotAvailable:
            # Subscription reported active but cloud not ready; fall back below.
            pass
    try:
        return webhook.async_generate_url(hass, webhook_id)
    except Exception as err:  # network helper raises when no URL is configured
        raise CloudhookUnavailable(str(err)) from err


async def async_build_owntracks_qr_payload(
    hass: HomeAssistant,
    *,
    user: str = DEFAULT_USER,
    device_id: str = DEFAULT_DEVICE_ID,
    tracker_id: str = DEFAULT_TRACKER_ID,
) -> str:
    """Build the ``owntracks:///config?inline=...`` URL to encode in the QR code."""
    entries = hass.config_entries.async_entries(OWNTRACKS_DOMAIN)
    if not entries:
        raise OwnTracksNotConfigured

    entry = entries[0]
    webhook_id = entry.data.get("webhook_id")
    if not webhook_id:
        raise OwnTracksNotConfigured

    url = await _resolve_webhook_url(hass, webhook_id)

    # OwnTracks' configuration-import uses specific key names. They are NOT the
    # same as the JSON location-message field names: the device id is "deviceId"
    # (camelCase), the tracker id is "tid", the account name is "username", and
    # the encryption secret is "encryptionKey". Sending "deviceid"/"trackerid"/
    # "user"/"secret" is silently ignored by the app, leaving it on an auto-
    # generated device id with no username — so the OwnTracks integration in HA
    # never forms device_tracker.<username>_<deviceId>.
    config: dict = {
        "_type": "configuration",
        "mode": OWNTRACKS_MODE_HTTP,
        "url": url,
        "username": user,
        "deviceId": device_id,
        "tid": tracker_id,
    }
    # Encryption is mandatory: the device encrypts every payload end-to-end
    # (libsodium) with this key, and HA's OwnTracks integration decrypts with the
    # same secret it stores. We never emit a plaintext QR — if no secret is
    # available there is nothing HA could decrypt with, so refuse to build one.
    secret = entry.data.get("secret") or entry.options.get("secret")
    if not secret:
        raise EncryptionSecretUnavailable
    config["encryptionKey"] = secret

    inline = base64.b64encode(
        json.dumps(config, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    # Canonical OwnTracks deeplink: empty authority + "/config" path
    # (owntracks:///config?...). The triple slash matters — owntracks://config
    # parses as host="config" with an empty path, which the app rejects.
    return f"owntracks:///config?inline={inline}"
