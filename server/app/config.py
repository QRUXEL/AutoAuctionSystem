"""Configuration.

Fixed settings (paths, ports, passwords for the site and the camera) come from
environment variables. Integration settings (API keys, AI options, capture
counts) and listing defaults can be edited on the Settings page; those are
stored in /data/settings.json and take priority over the environment.
"""
import json
import os
import threading
from pathlib import Path

DATA = Path(os.getenv("DATA_DIR", "/data"))
BRANDING = Path(os.getenv("BRANDING_DIR", str(Path(__file__).resolve().parent.parent / "branding")))
ITEMS = DATA / "items"
INCOMING = DATA / "incoming"
for _d in (DATA, ITEMS, INCOMING):
    _d.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA / "settings.json"

# Access
REVIEW_PASSWORD = os.getenv("REVIEW_PASSWORD", "")      # protects the site (HTTP Basic, any user name)
STATION_KEY = os.getenv("STATION_KEY", "")              # shared secret the ESP32 sends

# FTP (the camera uploads here)
FTP_PORT = int(os.getenv("FTP_PORT", "2121"))
FTP_USER = os.getenv("FTP_USER", "camera")
FTP_PASSWORD = os.getenv("FTP_PASSWORD", "change-me")
FTP_PASSIVE_PORTS = os.getenv("FTP_PASSIVE_PORTS", "30000-30009")
FTP_PUBLIC_HOST = os.getenv("FTP_PUBLIC_HOST", "")      # LAN IP of the Docker host, needed for passive mode

TRADERA_BASE = os.getenv("TRADERA_BASE", "https://api.tradera.com")

# Editable on the Settings page. name -> (type, default from the environment)
EDITABLE = {
    "ANTHROPIC_API_KEY": ("secret", os.getenv("ANTHROPIC_API_KEY", "")),
    "AI_MODEL": ("str", os.getenv("AI_MODEL", "claude-sonnet-5-5")),
    "LISTING_LANGUAGE": ("str", os.getenv("LISTING_LANGUAGE", "Swedish")),
    "AI_EXTRA_INSTRUCTIONS": ("str", os.getenv("AI_EXTRA_INSTRUCTIONS", "")),
    "TRADERA_APP_ID": ("str", os.getenv("TRADERA_APP_ID", "")),
    "TRADERA_APP_KEY": ("secret", os.getenv("TRADERA_APP_KEY", "")),
    "TRADERA_USER_ID": ("str", os.getenv("TRADERA_USER_ID", "")),
    "TRADERA_USER_TOKEN": ("secret", os.getenv("TRADERA_USER_TOKEN", "")),
    "TRADERA_PUBLIC_KEY": ("str", os.getenv("TRADERA_PUBLIC_KEY", "")),     # the app's public key, for the token login
    "TRADERA_TOKEN_EXPIRES": ("str", ""),                                   # set by the token login
    "TRADERA_DRY_RUN": ("bool", os.getenv("TRADERA_DRY_RUN", "1") != "0"),
    "PUBLIC_BASE_URL": ("str", os.getenv("PUBLIC_BASE_URL", "")),           # e.g. https://aas.qruxel.productions
    "EXPECTED_PHOTOS": ("int", int(os.getenv("EXPECTED_PHOTOS", "4"))),
    "EXPECTED_VIDEOS": ("int", int(os.getenv("EXPECTED_VIDEOS", "1"))),
    "GRACE_SECONDS": ("int", int(os.getenv("GRACE_SECONDS", "120"))),
}

# Listing defaults; also editable per item on the item page
DEFAULT_LISTING = {
    "item_type": 1,              # 1 = auction, 3 = fixed price
    "duration_days": 7,
    "start_price": 1,
    "restarts": 0,
    "accepted_bidder_id": 1,     # see GET /v4/reference-data/accepted-bidder-types
    "vat": None,
    "shipping_condition": "",
    "shipping_options": [],      # e.g. [{"shippingProviderId": 0, "cost": 69}] from /v4/reference-data/shipping-options
    "description_footer": "",    # appended to every description
}

_lock = threading.Lock()


def _load() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text("utf-8"))
    return {}


def _save(data: dict):
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(SETTINGS_FILE)


def get(name: str):
    kind, default = EDITABLE[name]
    stored = _load().get("integrations", {}).get(name)
    return default if stored is None else stored


def source(name: str) -> str:
    """Where the current value comes from: 'settings', 'env' or 'none'."""
    if _load().get("integrations", {}).get(name) is not None:
        return "settings"
    return "env" if EDITABLE[name][1] not in ("", None) else "none"


def _coerce(name: str, value):
    kind = EDITABLE[name][0]
    if value is None:
        return None
    if kind == "bool":
        return bool(value)
    if kind == "int":
        return int(value)
    return str(value).strip()


def update_integrations(values: dict):
    """values: name -> new value; None removes the stored value (falls back to the environment)."""
    with _lock:
        data = _load()
        integ = data.setdefault("integrations", {})
        for name, value in values.items():
            if name not in EDITABLE:
                raise KeyError(name)
            v = _coerce(name, value)
            if v is None:
                integ.pop(name, None)
            else:
                integ[name] = v
        _save(data)


def settings() -> dict:
    """Listing defaults."""
    merged = dict(DEFAULT_LISTING)
    data = _load()
    merged.update({k: v for k, v in data.items() if k in DEFAULT_LISTING})
    return merged


def update_listing(values: dict):
    with _lock:
        data = _load()
        for k, v in values.items():
            if k in DEFAULT_LISTING:
                data[k] = v
        _save(data)


def tradera_ready() -> bool:
    return all(get(k) for k in ("TRADERA_APP_ID", "TRADERA_APP_KEY", "TRADERA_USER_ID", "TRADERA_USER_TOKEN"))


def dry_run() -> bool:
    return bool(get("TRADERA_DRY_RUN")) or not tradera_ready()
