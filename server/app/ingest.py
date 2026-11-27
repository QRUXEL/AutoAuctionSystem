"""Turns files arriving from the camera into items, then frames and describes them.

Matching is by time. The station controller reports when a capture starts
(POST /api/items) and ends (POST /api/items/{id}/captured); the server stamps
both on its own clock. Every file's shot time is read from the file (EXIF for
photos, the MP4 header for videos) and converted from the camera's clock to the
server's clock. A file belongs to the item whose start-end window contains its
shot time. Anything else, such as test shots, is kept as a loose shot
(files.item_id = 0) and can be deleted or turned into an item on the site.
"""
import io
import json
import logging
import queue
import shutil
import threading
import time
from pathlib import Path

from . import config, db, describe, imaging, shottime, videos

log = logging.getLogger("ingest")
PHOTO_EXT = {".jpg", ".jpeg", ".hif", ".heif", ".heic"}
VIDEO_EXT = {".mp4", ".mov"}
LOOSE_DIR = "loose"
MARGIN = 3.0             # seconds of slack around a capture window
STALE_WINDOW = 900       # an item whose end never arrives stops matching files after this
SESSION_MAX = 12 * 3600  # an item opened with Start item and never stopped closes after this
_work: "queue.Queue[tuple]" = queue.Queue()


def open_session() -> int:
    """Start item: everything shot until stop_session() goes into one item, any number of photos and videos,
    by hand or with turntable runs (which join this item instead of opening their own)."""
    with db.lock():
        cur = current_session()
        if cur:
            return cur["id"]
        s = config.settings()
        item_id = db.run(
            "INSERT INTO items (status, source, created_at, expected_photos, expected_videos, start_price, duration_days)"
            " VALUES ('capturing', 'session', ?, 0, 0, ?, ?)", db.now(), s["start_price"], s["duration_days"])
        log.info("item %s: started by hand", item_id)
        return item_id


def current_session() -> dict | None:
    return db.one("SELECT * FROM items WHERE source = 'session' AND status = 'capturing' AND captured_at IS NULL "
                  "AND created_at > ? ORDER BY id DESC LIMIT 1", db.now() - SESSION_MAX)


def stop_session(item_id: int | None = None) -> int | None:
    with db.lock():
        cur = current_session()
        if not cur or (item_id and cur["id"] != item_id):
            return None
        db.update_item(cur["id"], captured_at=db.now())
        log.info("item %s: stopped by hand", cur["id"])
        return cur["id"]


def _room(item: dict, kind: str) -> bool:
    """Whether the item still takes a file of this kind. Items started by hand take any number."""
    if item["source"] == "session":
        return True
    return _count(item["id"], kind) < (item["expected_photos"] if kind == "photo" else item["expected_videos"])


def open_item(source: str = "station", photos: int | None = None, videos: int | None = None) -> int:
    cur = current_session()
    if cur and source == "station":         # a turntable run while an item is open by hand: shoot into that item
        log.info("item %s: turntable run joins the item started by hand", cur["id"])
        return cur["id"]
    s = config.settings()
    return db.run(
        "INSERT INTO items (status, source, created_at, expected_photos, expected_videos, start_price, duration_days)"
        " VALUES ('capturing', ?, ?, ?, ?, ?, ?)",
        source, db.now(), config.get("EXPECTED_PHOTOS") if photos is None else photos,
        config.get("EXPECTED_VIDEOS") if videos is None else videos, s["start_price"], s["duration_days"])


def _count(item_id: int, kind: str) -> int:
    return db.one("SELECT COUNT(*) AS n FROM files WHERE item_id = ? AND kind = ?", item_id, kind)["n"]


# ---- clocks -------------------------------------------------------------------------
def camera_offset(latest: float | None = None) -> float | None:
    """Server time minus camera time, in seconds.

    A photo reaches the server a few seconds after it is shot, so (received - shot
    on the camera's clock) is the clock offset plus a small upload delay. The
    smallest value over recent photos is the best estimate: a slow upload only
    makes its own value larger, so one quick upload is enough to keep it right.
    """
    rows = db.query("SELECT received_at - cam_time AS d FROM files WHERE kind = 'photo' AND cam_time IS NOT NULL"
                    " AND received_at > ? ORDER BY id DESC LIMIT 30", db.now() - 6 * 3600)
    values = [r["d"] for r in rows] + ([latest] if latest is not None else [])
    return min(values) if values else None


def _windows() -> list:
    now = db.now()
    out = []
    for item in db.query("SELECT * FROM items WHERE source IN ('station', 'session') AND created_at > ? ORDER BY id",
                         now - 2 * 86400):
        limit = SESSION_MAX if item["source"] == "session" else STALE_WINDOW
        end = item["captured_at"] or min(now, item["created_at"] + limit)
        out.append((item["created_at"] - MARGIN, end + MARGIN, item))
    return out


def _match(t: float | None, kind: str):
    """The item whose capture window contains server time t."""
    if t is None:
        return None
    hits = [item for start, end, item in _windows() if start <= t <= end]
    if kind == "photo":
        hits = [i for i in hits if i["status"] == "capturing" or _room(i, "photo")]
    return hits[-1] if hits else None


def _video_item(cam: float | None, received: float):
    """Videos: the MP4 header time may be on UTC rather than the camera's local clock,
    so whole-hour shifts are tried, nearest first. Without a usable header, the most
    recent capture that ended shortly before the upload and still lacks a video is used."""
    offset = camera_offset()
    if cam is not None and offset is not None:
        for hours in sorted(range(-14, 15), key=abs):
            t = cam + offset + hours * 3600
            item = _match(t, "video")
            if item and _room(item, "video"):
                return item, t
    for start, end, item in reversed(_windows()):
        if item["captured_at"] and 0 <= received - item["captured_at"] < 3600 and _room(item, "video"):
            return item, None
    return None, None


# ---- intake --------------------------------------------------------------------------
def _place(file_id: int, item_id: int):
    """Moves a stored file into an item (or to the loose shots with item_id 0)."""
    f = db.one("SELECT * FROM files WHERE id = ?", file_id)
    ext = Path(f["raw"]).suffix.lower()
    if item_id:
        seq = _count(item_id, f["kind"]) + 1
        rel = f"{item_id}/raw/{f['kind']}-{seq:02d}-{file_id}{ext}"
    else:
        seq = 0
        rel = f"{LOOSE_DIR}/{file_id}{ext}"
    dest = config.ITEMS / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if f["raw"] != rel:
        shutil.move(str(config.ITEMS / f["raw"]), dest)
    db.run("UPDATE files SET item_id = ?, seq = ?, raw = ?, framed = '', crop = '', crop_auto = '', adjust = '' WHERE id = ?",
           item_id, seq, rel, file_id)
    if item_id and f["kind"] == "video":
        videos.ensure_token(file_id)
        videos.queue_web_copy(file_id)
    return seq


MAX_PHOTOS = 40          # Tradera takes up to 40 images per listing


def add_uploads(item_id: int, uploads: list) -> list:
    """Photos uploaded on the item page (receipts, close-ups, other shots): stored as JPEG, upright,
    added after the item's photos and framed like the rest. uploads: [(file name, bytes)]."""
    from PIL import Image, ImageOps
    from . import imaging  # noqa: F401  (registers the HEIF opener)
    have = _count(item_id, "photo")
    if have + len(uploads) > MAX_PHOTOS:
        raise ValueError(f"Tradera takes at most {MAX_PHOTOS} photos per listing; this item has {have}")
    added = []
    for name, data in uploads:
        try:
            with Image.open(io.BytesIO(data)) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                tmp = config.ITEMS / LOOSE_DIR / f"upload-{time.time_ns()}.jpg"
                tmp.parent.mkdir(parents=True, exist_ok=True)
                im.save(tmp, "JPEG", quality=92)
        except Exception as exc:
            raise ValueError(f"{name} is not a photo that can be read ({exc})") from exc
        file_id = db.run("INSERT INTO files (item_id, kind, seq, raw, received_at, name) VALUES (0, 'photo', 0, ?, ?, ?)",
                         str(tmp.relative_to(config.ITEMS)), db.now(), name)
        _place(file_id, item_id)
        added.append(file_id)
    _renumber(item_id)
    for file_id in added:
        frame_one(db.one("SELECT * FROM files WHERE id = ?", file_id))
    return added


def file_received(path: Path):
    ext = path.suffix.lower()
    kind = "photo" if ext in PHOTO_EXT else "video" if ext in VIDEO_EXT else None
    if not kind:
        # e.g. RAW (.ARW): not usable for listings, but never thrown away
        keep = config.DATA / "other" / path.name
        keep.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), keep)
        log.warning("not a JPEG/HEIF photo or a video, kept in /data/other: %s", path.name)
        return
    received = db.now()
    cam = shottime.photo_time(path) if kind == "photo" else shottime.video_time(path)
    with db.lock():
        # store it as loose first, so nothing is lost if matching fails
        staging = config.ITEMS / LOOSE_DIR / f"incoming-{time.time_ns()}{ext}"
        staging.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), staging)
        file_id = db.run("INSERT INTO files (item_id, kind, seq, raw, received_at, name, cam_time) VALUES (0, ?, 0, ?, ?, ?, ?)",
                         kind, str(staging.relative_to(config.ITEMS)), received, path.name, cam)
        if kind == "photo":
            offset = camera_offset(None if cam is None else received - cam)
            shot = cam + offset if cam is not None and offset is not None else received - 2
            item = _match(shot, "photo")
        else:
            item, shot = _video_item(cam, received)
        db.run("UPDATE files SET shot_at = ? WHERE id = ?", shot, file_id)
        seq = _place(file_id, item["id"] if item else 0)
        if not item:
            log.info("loose %s %s (shot %s, not inside any capture)", kind, path.name, shot)
            return
        db.update_item(item["id"], last_file_at=received)
        log.info("item %s: %s %s (%s)", item["id"], kind, seq, path.name)
        if kind == "photo" and item["status"] == "capturing" and item["source"] != "session" and seq >= item["expected_photos"]:
            _start_processing(item["id"])
        elif kind == "photo" and item["status"] in ("review", "error"):
            _start_processing(item["id"], photos=True, text=False)   # arrived late: frame it in


def make_item(file_ids: list) -> int:
    """Turns loose shots into a new item, in the order they were shot."""
    with db.lock():
        files = _loose(file_ids)
        photos = [f for f in files if f["kind"] == "photo"]
        if not photos:
            raise ValueError("Pick at least one photo")
        item_id = open_item("manual", photos=len(photos), videos=len(files) - len(photos))
        db.update_item(item_id, captured_at=db.now())
        for f in files:
            _place(f["id"], item_id)
        _start_processing(item_id)
        return item_id


def attach(file_ids: list, item_id: int):
    """Adds loose shots to an existing item; new photos are framed, the text is left alone."""
    with db.lock():
        item = db.one("SELECT * FROM items WHERE id = ?", item_id)
        if not item or item["status"] in ("publishing", "published"):
            raise ValueError("That item can't take more photos")
        files = _loose(file_ids)
        for f in files:
            _place(f["id"], item_id)
        _renumber(item_id)
        n = _count(item_id, "photo")
        db.update_item(item_id, expected_photos=max(item["expected_photos"], n))
        if item["status"] in ("review", "error"):
            _start_processing(item_id, photos=True, text=False)


def detach(item_id: int, kind: str, seq: int):
    """Moves one file out of an item back to the loose shots, renumbering the rest."""
    with db.lock():
        f = db.one("SELECT * FROM files WHERE item_id = ? AND kind = ? AND seq = ?", item_id, kind, seq)
        if not f:
            raise ValueError("No such file")
        if f["framed"]:
            (config.ITEMS / f["framed"]).unlink(missing_ok=True)
        _place(f["id"], 0)
        for i, rest in enumerate(db.query("SELECT id FROM files WHERE item_id = ? AND kind = ? ORDER BY seq", item_id, kind), 1):
            db.run("UPDATE files SET seq = ? WHERE id = ?", i, rest["id"])
        if kind == "photo":
            _start_processing(item_id, photos=True, text=False)
    from . import videohost
    videohost.kick()          # an item that lost its video has it removed from the site


def _renumber(item_id: int):
    """Puts an item's photos and videos in the order they were shot; photos put in order by hand keep
    that order, with newly added ones after them."""
    item = db.one("SELECT manual_order FROM items WHERE id = ?", item_id)
    manual = bool(item and item["manual_order"])
    for kind in ("photo", "video"):
        # an added file gets the next number, so ordering by number keeps the manual order with it last
        order = "seq, id" if manual and kind == "photo" else "COALESCE(shot_at, received_at), id"
        rows = db.query(f"SELECT id FROM files WHERE item_id = ? AND kind = ? ORDER BY {order}", item_id, kind)
        for i, r in enumerate(rows, 1):
            db.run("UPDATE files SET seq = ? WHERE id = ?", i, r["id"])


def delete_loose(file_ids: list) -> int:
    with db.lock():
        files = _loose(file_ids)
        for f in files:
            (config.ITEMS / f["raw"]).unlink(missing_ok=True)
            (config.DATA / "thumbs" / f"{f['id']}.jpg").unlink(missing_ok=True)
            db.run("DELETE FROM files WHERE id = ?", f["id"])
        return len(files)


def _loose(file_ids: list) -> list:
    ids = [int(i) for i in file_ids]
    if not ids:
        raise ValueError("Nothing selected")
    files = db.query(f"SELECT * FROM files WHERE item_id = 0 AND id IN ({','.join('?' * len(ids))})"
                     " ORDER BY COALESCE(shot_at, received_at), id", *ids)
    if len(files) != len(set(ids)):
        raise ValueError("Some of those shots are no longer loose")
    return files


def _start_processing(item_id: int, photos: bool = True, text: bool = True):
    db.update_item(item_id, status="processing", message="")
    _work.put((item_id, photos, text))


def reprocess(item_id: int, photos: bool = True, text: bool = True):
    _start_processing(item_id, photos, text)


def reorder_photos(item_id: int, order: list | None):
    """order: the current photo numbers in their new order; None puts them back in shooting order."""
    with db.lock():
        rows = db.query("SELECT id, seq FROM files WHERE item_id = ? AND kind = 'photo'", item_id)
        if order is None:
            db.update_item(item_id, manual_order=0)
            _renumber(item_id)
            return
        by_seq = {r["seq"]: r["id"] for r in rows}
        if sorted(order) != sorted(by_seq):
            raise ValueError("The order must list every photo of the item once")
        for i, seq in enumerate(order, 1):
            db.run("UPDATE files SET seq = ? WHERE id = ?", -i, by_seq[seq])     # two passes: no clash mid-way
        db.run("UPDATE files SET seq = -seq WHERE item_id = ? AND kind = 'photo'", item_id)
        db.update_item(item_id, manual_order=1)


def frame_one(f: dict, cfg: dict | None = None):
    """(Re)frames one photo, honouring its manual crop. Returns the cropped image for the AI."""
    cfg = cfg or imaging.frame_config()
    raw = config.ITEMS / f["raw"]
    # named by the file, not its position, so reordering photos never makes two of them share a file
    framed_rel = f"{f['item_id']}/framed/p{f['id']}.jpg"
    if f.get("framed") and f["framed"] != framed_rel:
        (config.ITEMS / f["framed"]).unlink(missing_ok=True)
    manual = json.loads(f["crop"]) if f.get("crop") else None
    adjust = json.loads(f["adjust"]) if f.get("adjust") else None
    used = imaging.frame_photo(raw, config.ITEMS / framed_rel, item_ref=f"#{f['item_id']:04d}", cfg=cfg, manual=manual,
                               adjust=adjust)
    db.run("UPDATE files SET framed = ?, crop_auto = ? WHERE id = ?", framed_rel,
           "" if manual or used is None else json.dumps(used), f["id"])


def process(item_id: int, photos: bool = True, text: bool = True):
    files = db.query("SELECT * FROM files WHERE item_id = ? AND kind = 'photo' ORDER BY seq", item_id)
    cfg = imaging.frame_config()
    if photos or any(not f["framed"] for f in files):
        for f in files:
            frame_one(f, cfg)
    fields = {"status": "review"}
    if text:
        try:
            crops = [imaging.cropped(config.ITEMS / f["raw"], cfg, json.loads(f["crop"]) if f["crop"] else None)[0]
                     for f in files]
            fields.update(describe.describe(crops))
        except Exception as exc:  # the photos are still usable; a person can write the text
            log.exception("describe failed for item %s", item_id)
            fields["ai_notes"] = f"AI description failed: {exc}"
    db.update_item(item_id, **fields)
    from . import videohost
    videohost.kick()          # the framed hero photo is the video's poster; titles may have changed


def _worker():
    while True:
        item_id, photos, text = _work.get()
        try:
            process(item_id, photos, text)
        except Exception as exc:
            log.exception("processing item %s failed", item_id)
            db.update_item(item_id, status="error", message=f"Processing failed: {exc}")


def _watchdog():
    """Closes captures: when the end is reported and the photos are in, or after a grace period."""
    while True:
        time.sleep(5)
        now = db.now()
        grace = config.get("GRACE_SECONDS")
        for item in db.query("SELECT * FROM items WHERE status = 'capturing'"):
            n = _count(item["id"], "photo")
            limit = SESSION_MAX if item["source"] == "session" else STALE_WINDOW
            ended = item["captured_at"] or (item["created_at"] + limit if now - item["created_at"] > limit else None)
            if not ended or now - max(ended, item["last_file_at"] or 0) < grace:
                continue
            if n:
                note = "" if n >= item["expected_photos"] else f"Only {n} of {item['expected_photos']} photos were shot during the capture."
                if not item["captured_at"]:
                    note = (note + (" Stop item was never pressed; it was closed after 12 hours." if item["source"] == "session"
                                    else " The station never reported the end of the capture.")).strip()
                _start_processing(item["id"])
                db.update_item(item["id"], message=note)
            else:
                db.update_item(item["id"], status="error",
                               message="No photos were shot during the capture. Check Loose shots, and the camera's FTP upload.")


def start():
    for item in db.query("SELECT id FROM items WHERE status = 'processing'"):
        _work.put((item["id"], True, True))   # resume after a restart
    threading.Thread(target=_worker, daemon=True, name="process").start()
    threading.Thread(target=_watchdog, daemon=True, name="watchdog").start()
