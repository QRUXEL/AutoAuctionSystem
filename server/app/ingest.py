"""Turns files arriving from the camera into items, then frames and describes them.

Matching rule: the station agent opens an item before it starts shooting. Photos
go to the oldest item that is still short of photos, videos to the oldest recent
item short of a video. If nothing is open (someone pressed the shutter by hand),
a "manual" item is created and closes after GRACE_SECONDS without new files.
"""
import logging
import queue
import shutil
import threading
import time
from pathlib import Path

from . import config, db, describe, imaging

log = logging.getLogger("ingest")
PHOTO_EXT = {".jpg", ".jpeg"}
VIDEO_EXT = {".mp4", ".mov"}
_work: "queue.Queue[int]" = queue.Queue()


def open_item(source: str = "station", photos: int | None = None, videos: int | None = None) -> int:
    s = config.settings()
    if source == "station":
        # hand-shot photos still waiting for more must not swallow this item's photos
        for manual in db.query("SELECT id FROM items WHERE status = 'capturing' AND source = 'manual'"):
            _start_processing(manual["id"])
    return db.run(
        "INSERT INTO items (status, source, created_at, expected_photos, expected_videos, start_price, duration_days)"
        " VALUES ('capturing', ?, ?, ?, ?, ?, ?)",
        source, db.now(), config.EXPECTED_PHOTOS if photos is None else photos,
        config.EXPECTED_VIDEOS if videos is None else videos, s["start_price"], s["duration_days"])


def _count(item_id: int, kind: str) -> int:
    return db.one("SELECT COUNT(*) AS n FROM files WHERE item_id = ? AND kind = ?", item_id, kind)["n"]


def file_received(path: Path):
    ext = path.suffix.lower()
    kind = "photo" if ext in PHOTO_EXT else "video" if ext in VIDEO_EXT else None
    if not kind:
        log.info("ignoring %s", path.name)
        return
    with db.lock():
        item = None
        if kind == "photo":
            for cand in db.query("SELECT * FROM items WHERE status = 'capturing' ORDER BY id"):
                if _count(cand["id"], "photo") < cand["expected_photos"]:
                    item = cand
                    break
        else:
            for cand in db.query("SELECT * FROM items WHERE created_at > ? AND status != 'published' ORDER BY id",
                                 db.now() - 1800):
                if _count(cand["id"], "video") < cand["expected_videos"]:
                    item = cand
                    break
        if not item:
            item = db.one("SELECT * FROM items WHERE id = ?", open_item("manual", photos=99))
        seq = _count(item["id"], kind) + 1
        rel = f"{item['id']}/raw/{kind}-{seq:02d}{ext}"
        dest = config.ITEMS / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), dest)
        db.run("INSERT INTO files (item_id, kind, seq, raw, received_at) VALUES (?, ?, ?, ?, ?)",
               item["id"], kind, seq, rel, db.now())
        db.update_item(item["id"], last_file_at=db.now())
        log.info("item %s: %s %s", item["id"], kind, seq)
        if kind == "photo" and item["status"] == "capturing" and seq >= item["expected_photos"]:
            _start_processing(item["id"])


def _start_processing(item_id: int):
    db.update_item(item_id, status="processing", message="")
    _work.put(item_id)


def reprocess(item_id: int):
    _start_processing(item_id)


def process(item_id: int, redo_text: bool = True):
    photos = db.query("SELECT * FROM files WHERE item_id = ? AND kind = 'photo' ORDER BY seq", item_id)
    cfg = imaging.frame_config()
    crops = []
    for f in photos:
        raw = config.ITEMS / f["raw"]
        framed_rel = f"{item_id}/framed/{f['seq']:02d}.jpg"
        imaging.frame_photo(raw, config.ITEMS / framed_rel, item_ref=f"#{item_id:04d}", cfg=cfg)
        db.run("UPDATE files SET framed = ? WHERE id = ?", framed_rel, f["id"])
        crops.append(imaging.cropped(raw, cfg))
    fields = {"status": "review"}
    if redo_text:
        try:
            fields.update(describe.describe(crops))
        except Exception as exc:  # the photos are still usable; a person can write the text
            log.exception("describe failed for item %s", item_id)
            fields["ai_notes"] = f"AI description failed: {exc}"
    db.update_item(item_id, **fields)


def _worker():
    while True:
        item_id = _work.get()
        try:
            process(item_id)
        except Exception as exc:
            log.exception("processing item %s failed", item_id)
            db.update_item(item_id, status="error", message=f"Processing failed: {exc}")


def _watchdog():
    """Close items whose files stopped arriving, so one lost photo never blocks the queue."""
    while True:
        time.sleep(5)
        now = db.now()
        for item in db.query("SELECT * FROM items WHERE status = 'capturing'"):
            n = _count(item["id"], "photo")
            quiet_since = max(item["last_file_at"] or 0, item["captured_at"] or 0)
            if item["source"] == "station" and not item["captured_at"]:
                quiet_since = max(quiet_since, item["created_at"] + 300)   # agent never reported back
            if n and now - quiet_since > config.GRACE_SECONDS:
                short = "" if item["source"] == "manual" or n >= item["expected_photos"] else \
                    f"Only {n} of {item['expected_photos']} photos arrived."
                _start_processing(item["id"])
                db.update_item(item["id"], message=short)
            elif not n and now - item["created_at"] > 600:
                db.update_item(item["id"], status="error", message="No photos arrived from the camera.")


def start():
    for item in db.query("SELECT id FROM items WHERE status = 'processing'"):
        _work.put(item["id"])   # resume after a restart
    threading.Thread(target=_worker, daemon=True, name="process").start()
    threading.Thread(target=_watchdog, daemon=True, name="watchdog").start()
