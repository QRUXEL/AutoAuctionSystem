"""All configuration comes from environment variables (see .env.example)."""
import json
import os
from pathlib import Path

DATA = Path(os.getenv("DATA_DIR", "/data"))
BRANDING = Path(os.getenv("BRANDING_DIR", str(Path(__file__).resolve().parent.parent / "branding")))
ITEMS = DATA / "items"
INCOMING = DATA / "incoming"
for _d in (DATA, ITEMS, INCOMING):
    _d.mkdir(parents=True, exist_ok=True)

# Access
REVIEW_PASSWORD = os.getenv("REVIEW_PASSWORD", "")      # protects the review page (HTTP Basic, any user name)
STATION_KEY = os.getenv("STATION_KEY", "")              # shared secret the station agent sends

# FTP (the camera uploads here)
FTP_PORT = int(os.getenv("FTP_PORT", "2121"))
FTP_USER = os.getenv("FTP_USER", "camera")
FTP_PASSWORD = os.getenv("FTP_PASSWORD", "change-me")
FTP_PASSIVE_PORTS = os.getenv("FTP_PASSIVE_PORTS", "30000-30009")
FTP_PUBLIC_HOST = os.getenv("FTP_PUBLIC_HOST", "")      # LAN IP of the Docker host, needed for passive mode

# Capture defaults
EXPECTED_PHOTOS = int(os.getenv("EXPECTED_PHOTOS", "4"))
EXPECTED_VIDEOS = int(os.getenv("EXPECTED_VIDEOS", "1"))
GRACE_SECONDS = int(os.getenv("GRACE_SECONDS", "45"))   # wait this long for missing photos before processing anyway

# AI
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-5-5")
LISTING_LANGUAGE = os.getenv("LISTING_LANGUAGE", "Swedish")
AI_EXTRA_INSTRUCTIONS = os.getenv("AI_EXTRA_INSTRUCTIONS", "")

# Tradera
TRADERA_BASE = os.getenv("TRADERA_BASE", "https://api.tradera.com")
TRADERA_APP_ID = os.getenv("TRADERA_APP_ID", "")
TRADERA_APP_KEY = os.getenv("TRADERA_APP_KEY", "")
TRADERA_USER_ID = os.getenv("TRADERA_USER_ID", "")
TRADERA_USER_TOKEN = os.getenv("TRADERA_USER_TOKEN", "")
TRADERA_DRY_RUN = os.getenv("TRADERA_DRY_RUN", "1") != "0"

# Listing defaults; editable per item on the review page, or globally in /data/settings.json
DEFAULT_SETTINGS = {
    "item_type": 1,              # 1 = auction, 3 = fixed price
    "duration_days": 7,
    "start_price": 1,
    "restarts": 0,
    "accepted_bidder_id": 1,     # see GET /v4/reference-data/accepted-bidder-types
    "vat": None,
    "shipping_condition": "",
    "shipping_options": [],      # e.g. [{"shippingProviderId": 0, "cost": 69}] from /v4/reference-data/shipping-options
    "description_footer": "",    # appended to every description (e.g. info about the auction)
}


def settings() -> dict:
    merged = dict(DEFAULT_SETTINGS)
    path = DATA / "settings.json"
    if path.exists():
        merged.update(json.loads(path.read_text("utf-8")))
    return merged


def tradera_ready() -> bool:
    return all((TRADERA_APP_ID, TRADERA_APP_KEY, TRADERA_USER_ID, TRADERA_USER_TOKEN))
