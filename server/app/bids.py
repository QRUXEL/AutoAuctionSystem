"""Follows the bidding on published listings.

Tradera has no bid history in its API, so the server reads each listing (GET /v4/items/{id}) on a schedule
that tightens towards the end, and notes the time whenever the highest bid changes ("last bid").
  more than a day left   every 15 min
  more than an hour      every 5 min
  more than 10 minutes   every minute
  less                   every 20 s
  ended                  once more after the end (for the winner), then a last check 10 min later
"""
import logging
import threading
import time
from datetime import datetime

from . import config, db, tradera

log = logging.getLogger("bids")
_wake = threading.Event()


def _interval(item: dict, now: float) -> float | None:
    end = item["tr_end"] or item["listing_end_at"]
    if item["tr_ended"]:
        if item["tr_checked_at"] and end and item["tr_checked_at"] > end + 600:
            return None                                       # final result recorded
        return 600 if item["tr_checked_at"] and end and item["tr_checked_at"] > end else 20
    if not end:
        return 300
    left = end - now
    if left > 86400:
        return 900
    if left > 3600:
        return 300
    if left > 600:
        return 60
    return 20


def _epoch(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def check(item_id: int) -> dict:
    """Reads one listing from Tradera and stores its bidding state."""
    item = db.one("SELECT * FROM items WHERE id = ?", item_id)
    tid = item["tradera_item_id"] if item else None
    if not tid:
        raise ValueError("The item has no Tradera id")
    now = time.time()
    try:
        r = tradera._call(tradera._session(user=True), "GET", f"/v4/items/{tid}") or {}
    except Exception as exc:
        db.update_item(item_id, tr_checked_at=now, tr_error=str(exc)[:300])
        raise
    status = r.get("status") or {}
    bids = int(r.get("totalBids") or 0)
    bid = r.get("maxBid") if bids else None
    bidder = (r.get("maxBidder") or {}).get("alias") or ""
    winner = ""
    if status.get("gotWinner"):
        buyers = r.get("buyerList") or []
        winner = (buyers[0].get("alias") if buyers else "") or bidder
    fields = {"tr_bid": bid, "tr_bids": bids, "tr_bidder": bidder, "tr_winner": winner, "tr_next_bid": r.get("nextBid"),
              "tr_end": _epoch(r.get("endDate")) or item["tr_end"], "tr_ended": 1 if status.get("ended") else 0,
              "tr_link": r.get("itemLink") or item["tr_link"] or "", "tr_thumb": r.get("thumbnailLink") or item["tr_thumb"] or "",
              "tr_checked_at": now, "tr_error": ""}
    if bids and (bids != (item["tr_bids"] or 0) or bid != item["tr_bid"]):
        fields["tr_last_bid_at"] = now                       # first seen now; Tradera doesn't give bid times
        db.run("INSERT INTO bids (item_id, at, amount, bidder, total_bids) VALUES (?, ?, ?, ?, ?)", item_id, now, bid, bidder, bids)
        log.info("item %s: bid %s kr by %s (%s bids)", item_id, bid, bidder or "?", bids)
    db.update_item(item_id, **fields)
    return fields


def _due(now: float) -> list:
    rows = db.query("SELECT * FROM items WHERE status = 'published' AND tradera_item_id IS NOT NULL")
    due = []
    for r in rows:
        step = _interval(r, now)
        if step is not None and (not r["tr_checked_at"] or now - r["tr_checked_at"] >= step):
            due.append(r["id"])
    return due


def refresh_all() -> int:
    n = 0
    for r in db.query("SELECT id FROM items WHERE status = 'published' AND tradera_item_id IS NOT NULL"):
        try:
            check(r["id"])
            n += 1
        except Exception as exc:
            log.warning("checking item %s failed: %s", r["id"], exc)
    return n


def kick():
    _wake.set()


def _worker():
    while True:
        if config.tradera_ready():
            for item_id in _due(time.time()):
                try:
                    check(item_id)
                except Exception as exc:
                    log.warning("checking item %s failed: %s", item_id, exc)
                time.sleep(0.5)                               # gentle on Tradera's rate limits
        _wake.wait(15)
        _wake.clear()


def start():
    threading.Thread(target=_worker, daemon=True, name="bids").start()


def table() -> list:
    """Rows for the Auctions page."""
    rows = db.query("SELECT * FROM items WHERE tradera_item_id IS NOT NULL AND status = 'published' ORDER BY COALESCE(tr_end, listing_end_at)")
    out = []
    for r in rows:
        photo = db.one("SELECT framed FROM files WHERE item_id = ? AND kind = 'photo' AND framed != '' ORDER BY seq LIMIT 1", r["id"])
        out.append({
            "id": r["id"], "title": r["title"], "tradera_item_id": r["tradera_item_id"],
            "link": r["tr_link"] or f"https://www.tradera.com/item/{r['tradera_item_id']}",
            "image": f"/media/{photo['framed']}" if photo else (r["tr_thumb"] or None),
            "bid": r["tr_bid"], "bids": r["tr_bids"] or 0, "next_bid": r["tr_next_bid"], "start_price": r["start_price"],
            "last_bid_at": r["tr_last_bid_at"], "bidder": r["tr_bidder"] or "", "winner": r["tr_winner"] or "",
            "end": r["tr_end"] or r["listing_end_at"], "ended": bool(r["tr_ended"]),
            "checked_at": r["tr_checked_at"], "error": r["tr_error"] or "",
        })
    return out
