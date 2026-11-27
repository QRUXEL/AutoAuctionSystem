"""When each auction ends.

Modes (listing setting end_mode):
  duration   - the listing runs for duration_days (Tradera picks the exact end)
  fixed      - every listing ends at end_at
  staggered  - one after another: the first ends at end_at, each next one end_interval_min minutes after
               the previous one. A listing keeps its slot when it is sent again.
An item's own end_at always wins. Times are local (LISTING_TIMEZONE, default Europe/Stockholm).
"""
import math
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db

TZ = ZoneInfo(os.getenv("LISTING_TIMEZONE", "Europe/Stockholm"))
MIN_AHEAD = 3600                      # Tradera needs some time before an auction can end; refuse closer ends


def parse_local(text: str | None) -> float | None:
    """'2026-12-13T20:00' (local) -> epoch seconds."""
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text).strip())
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.timestamp()


def to_local(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, TZ).strftime("%Y-%m-%dT%H:%M")


def to_tradera(epoch: float) -> str:
    """ISO 8601 with the offset, e.g. 2026-12-13T20:00:00+01:00."""
    return datetime.fromtimestamp(epoch, TZ).replace(microsecond=0).isoformat()


def human(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, TZ).strftime("%a %-d %b %H:%M")


def _base(settings: dict) -> float | None:
    return parse_local(settings.get("end_at"))


def resolve(item: dict, settings: dict) -> tuple[float | None, str]:
    """(end time or None for 'use the duration', how it was decided). Doesn't reserve anything."""
    own = parse_local(item.get("end_at"))
    if own:
        return own, "set on this item"
    mode = settings.get("end_mode") or "duration"
    base = _base(settings)
    if mode == "fixed" and base:
        return base, "the fixed end time in Settings"
    if mode == "staggered" and base:
        interval = max(0, int(settings.get("end_interval_min") or 0)) * 60
        if item.get("listing_end_at") and item["listing_end_at"] >= base:
            return item["listing_end_at"], "its place in the line-up (kept when sent again)"
        # items with their own end time are outside the line-up
        last = db.one("SELECT MAX(listing_end_at) AS t FROM items WHERE listing_end_at >= ? AND id != ? "
                      "AND COALESCE(end_at, '') = '' AND status IN ('publishing', 'sent', 'published')", base, item["id"])["t"]
        if last is None:
            return base, "first in the line-up"
        prev = db.one("SELECT id FROM items WHERE listing_end_at = ? AND id != ? AND COALESCE(end_at, '') = '' LIMIT 1",
                      last, item["id"])
        return last + interval, f"{interval // 60} min after #{prev['id']:04d}" if prev else "after the last one"
    return None, f"runs for {item.get('duration_days') or settings.get('duration_days')} days"


def reserve(item_id: int, settings: dict) -> float | None:
    """Fixes the item's end time just before it is sent (under the database lock, so two sends can't take
    the same slot). Raises ValueError if the end is too close or past."""
    with db.lock():
        item = db.one("SELECT * FROM items WHERE id = ?", item_id)
        end, how = resolve(dict(item), settings)
        if end is None:
            db.update_item(item_id, listing_end_at=None)
            return None
        if end < time.time() + MIN_AHEAD:
            raise ValueError(f"The end time {human(end)} ({how}) is in the past or less than an hour away. "
                             "Change it on the item or in Settings.")
        db.update_item(item_id, listing_end_at=end)
        return end


def duration_for(end: float) -> int:
    """Tradera still wants a duration in days alongside an exact end."""
    return max(1, math.ceil((end - time.time()) / 86400))
