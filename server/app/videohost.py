"""Uploads each item's video to the public site (streamhjalpen.tv) and keeps it in step.

Tradera's API takes photos only, so the video lives on the donation site and the
listing links to it. Per item, in this order:

  1. PUT  /api/auction-videos/<token>?title=&ref=    the web-ready MP4 -> returns the public url
  2. PUT  /api/auction-videos/<token>/poster         a JPEG (404 until step 1 is done)
  3. PATCH /api/auction-videos/<token>               {"title"} when edited, {"traderaItemId"} once published
  DELETE /api/auction-videos/<token>                 when the item is deleted or loses its video

Every call is idempotent. What has been sent, and whether it worked, is kept in
items.video_host as JSON, so failed steps are retried and a restart doesn't upload
everything again. Network errors and 5xx are retried with backoff (at most 5 attempts);
400/401/413 are not: they are recorded for the operator and not tried again until the
input changes, the operator presses Retry, or the server restarts.

The secret comes from the environment only and never reaches a log, the database or an error message.
"""
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path

import requests

from . import config, db

log = logging.getLogger("videohost")

BASE_URL = (os.getenv("AUCTION_VIDEO_BASE_URL") or "https://streamhjalpen.tv").strip().rstrip("/")
if "://" not in BASE_URL:
    BASE_URL = "https://" + BASE_URL
# The site answers only on its main host; other names (e.g. with "ä") redirect, which breaks uploads.
BASE_WARNING = ""
try:
    from urllib.parse import urlparse as _u
    _host = _u(BASE_URL).hostname or ""
    if not _host.isascii() or _host.startswith("xn--") or _host.startswith("www."):
        BASE_WARNING = f"AUCTION_VIDEO_BASE_URL is {BASE_URL}; it must be exactly https://streamhjalpen.tv (other names redirect)"
except Exception:
    pass
MAX_VIDEO = 1024 ** 3          # 1 GB
MAX_POSTER = 10 * 1024 ** 2    # 10 MB
ATTEMPTS = 5

_sleep = time.sleep            # replaced in tests
_wake = threading.Event()
_item_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _secret() -> str:
    return os.getenv("AUCTION_VIDEO_SECRET", "")


def configured() -> bool:
    return bool(_secret())


class HostError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, fatal: bool = False):
        super().__init__(message)
        self.status, self.fatal = status, fatal


def _scrub(text: str) -> str:
    s = _secret()
    return text.replace(s, "***") if s else text


def _request(method: str, path: str, *, body_path: Path | None = None, content_type: str | None = None,
             params: dict | None = None, json_body: dict | None = None) -> dict:
    """One API call with retries. The token is part of the path; the secret only goes in the header."""
    url = BASE_URL + path
    last = None
    for attempt in range(1, ATTEMPTS + 1):
        headers = {"Authorization": f"Bearer {_secret()}"}
        fh = None
        try:
            kw = {"params": params, "timeout": (10, 600), "allow_redirects": False}
            if body_path is not None:
                fh = open(body_path, "rb")
                headers.update({"Content-Type": content_type, "Content-Length": str(body_path.stat().st_size)})
                kw["data"] = fh
            elif json_body is not None:
                kw["json"] = json_body
            r = requests.request(method, url, headers=headers, **kw)
        except requests.RequestException as exc:
            last = HostError(f"{method} {path}: network error ({type(exc).__name__})")
        else:
            if r.status_code < 300:
                return r.json() if r.content else {}
            detail = _scrub(r.text[:200]).strip()
            if 300 <= r.status_code < 400:
                raise HostError(f"{method} {path}: redirected to {r.headers.get('Location')}; "
                                f"AUCTION_VIDEO_BASE_URL must be the site's main address", r.status_code, fatal=True)
            if 400 <= r.status_code < 500:     # 400/401/413 per the contract, and any other client error: not retried
                raise HostError(f"{method} {path}: HTTP {r.status_code} {_MEANING.get(r.status_code, '')} {detail}".strip(),
                                r.status_code, fatal=True)
            last = HostError(f"{method} {path}: HTTP {r.status_code} {detail}".strip(), r.status_code)
        finally:
            if fh:
                fh.close()
        if attempt < ATTEMPTS:
            wait = min(60, 2 ** attempt)
            log.warning("%s; retrying in %s s (attempt %s of %s)", last, wait, attempt, ATTEMPTS)
            _sleep(wait)
    raise last


_MEANING = {400: "(bad token or body)", 401: "(wrong or missing AUCTION_VIDEO_SECRET)",
            404: "(unknown token, or the upload API is off on the site)", 413: "(too large)"}


# ---- per-item state ------------------------------------------------------------------------------
def _state(item_id: int) -> dict:
    row = db.one("SELECT video_host FROM items WHERE id = ?", item_id)
    return json.loads(row["video_host"]) if row and row["video_host"] else {}


def _save(item_id: int, st: dict):
    db.run("UPDATE items SET video_host = ? WHERE id = ?", json.dumps(st), item_id)


def _lock(item_id: int) -> threading.Lock:
    with _locks_guard:
        return _item_locks.setdefault(item_id, threading.Lock())


def new_token() -> str:
    return secrets.token_urlsafe(24)    # 32 characters of [A-Za-z0-9_-]


def _video_file(item_id: int) -> dict | None:
    return db.one("SELECT * FROM files WHERE item_id = ? AND kind = 'video' AND web_status = 'ready' AND web != '' "
                  "ORDER BY seq LIMIT 1", item_id)


def _has_video(item_id: int) -> bool:
    return bool(db.one("SELECT id FROM files WHERE item_id = ? AND kind = 'video' LIMIT 1", item_id))


def _poster_source(item_id: int) -> Path | None:
    f = db.one("SELECT framed FROM files WHERE item_id = ? AND kind = 'photo' AND framed != '' ORDER BY seq LIMIT 1", item_id)
    p = config.ITEMS / f["framed"] if f else None
    return p if p and p.exists() else None


def _poster_file(item_id: int, src: Path) -> Path:
    """The hero photo, scaled so it is always well under the 10 MB limit."""
    from PIL import Image
    out = config.DATA / "web" / f"poster-{item_id}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = im.convert("RGB")
        im.thumbnail((1920, 1920))
        im.save(out, "JPEG", quality=85)
    return out


def _sig(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def _step_done(st: dict, step: str, sig) -> bool:
    s = st.get(step) or {}
    return s.get("ok") and s.get("sig") == sig


def _step_blocked(st: dict, step: str, sig) -> bool:
    s = st.get(step) or {}
    return bool(s.get("fatal")) and s.get("sig") == sig


def _record(st: dict, step: str, sig, exc: Exception | None = None):
    if exc is None:
        st[step] = {"ok": True, "sig": sig, "at": time.time()}
    else:
        st[step] = {"ok": False, "sig": sig, "at": time.time(), "error": _scrub(str(exc)),
                    "status": getattr(exc, "status", None), "fatal": bool(getattr(exc, "fatal", False))}


def _ref(item_id: int) -> str:
    return f"#{item_id:04d}"


def sync(item_id: int) -> dict:
    """Brings the site in line with the item; returns the state. Safe to call any number of times."""
    if not configured():
        return {}
    with _lock(item_id):
        item = db.one("SELECT * FROM items WHERE id = ?", item_id)
        if not item:
            return {}
        st = _state(item_id)
        f = _video_file(item_id)
        if not f:
            if st.get("video", {}).get("ok") and not _has_video(item_id):      # the video was moved out of the item
                _delete(st["token"])
                st = {"token": st["token"]}
                _save(item_id, st)
            return st
        st.setdefault("token", new_token())
        token = st["token"]
        _save(item_id, st)
        title = item["title"] or "Auktionsvara"

        # 1. video
        web = config.DATA / f["web"]
        vsig = f"{f['id']}:{_sig(web)}"
        if not _step_done(st, "video", vsig) and not _step_blocked(st, "video", vsig):
            try:
                if web.stat().st_size > MAX_VIDEO:
                    raise HostError(f"The web copy is {web.stat().st_size / 1e6:.0f} MB; the site takes at most 1 GB", 413, fatal=True)
                with open(web, "rb") as fh:
                    head = fh.read(12)
                if head[4:8] != b"ftyp":
                    raise HostError("The web copy is not an MP4 (no ftyp box)", 400, fatal=True)
                res = _request("PUT", f"/api/auction-videos/{token}", body_path=web, content_type="video/mp4",
                               params={"title": title, "ref": _ref(item_id)})
                st["url"] = res.get("url") or f"{BASE_URL}/video/{token}"
                st["title_sent"], st["tradera_sent"] = title, None      # the PUT carries the title; details follow
                st.pop("poster", None)                                    # a new video entry needs its poster again
                _record(st, "video", vsig)
                log.info("item %s: video uploaded (%.1f MB)", item_id, web.stat().st_size / 1e6)
            except HostError as exc:
                _record(st, "video", vsig, exc)
                log.error("item %s: video upload failed: %s", item_id, exc)
            _save(item_id, st)
        if not _step_done(st, "video", vsig):
            return st

        # 2. poster
        src = _poster_source(item_id)
        if src:
            psig = _sig(src)
            if not _step_done(st, "poster", psig) and not _step_blocked(st, "poster", psig):
                try:
                    poster = _poster_file(item_id, src)
                    if poster.stat().st_size > MAX_POSTER:
                        raise HostError("The poster is over 10 MB", 413, fatal=True)
                    _request("PUT", f"/api/auction-videos/{token}/poster", body_path=poster, content_type="image/jpeg")
                    _record(st, "poster", psig)
                except HostError as exc:
                    if exc.status == 404:              # the site no longer has the video: upload it again next round
                        st.pop("video", None)
                    _record(st, "poster", psig, exc)
                    log.error("item %s: poster upload failed: %s", item_id, exc)
                _save(item_id, st)

        # 3. details
        patch, sig = {}, {}
        if st.get("title_sent") != title:
            patch["title"], sig["title"] = title, title
        if item["tradera_item_id"] and st.get("tradera_sent") != item["tradera_item_id"]:
            patch["traderaItemId"], sig["tradera"] = item["tradera_item_id"], item["tradera_item_id"]
        if patch and not _step_blocked(st, "details", patch):
            try:
                _request("PATCH", f"/api/auction-videos/{token}", json_body=patch)
                if "title" in patch:
                    st["title_sent"] = title
                if "traderaItemId" in patch:
                    st["tradera_sent"] = item["tradera_item_id"]
                _record(st, "details", patch)
            except HostError as exc:
                if exc.status == 404:
                    st.pop("video", None)
                _record(st, "details", patch, exc)
                log.error("item %s: updating the video page failed: %s", item_id, exc)
            _save(item_id, st)
        return st


def _delete(token: str):
    try:
        _request("DELETE", f"/api/auction-videos/{token}")
        log.info("video %s… removed from the site", token[:6])
    except HostError as exc:
        if exc.status != 404:
            log.error("removing a video from the site failed: %s", exc)


def forget(item_id: int):
    """Call before an item is deleted: removes its video from the site (in the background)."""
    st = _state(item_id)
    if configured() and st.get("token") and st.get("video", {}).get("ok"):
        threading.Thread(target=_delete, args=(st["token"],), daemon=True).start()


def link_for_publish(item_id: int) -> str | None:
    """Uploads now if needed and returns the public url, or None if the item has no video.
    Raises if the item has a video that isn't on the site yet, so no listing goes out with a dead link."""
    if not _has_video(item_id):
        return None
    f = db.one("SELECT web_status FROM files WHERE item_id = ? AND kind = 'video' ORDER BY seq LIMIT 1", item_id)
    if f["web_status"] != "ready":
        raise HostError("The video is still being prepared for the web; publish again in a minute"
                        if f["web_status"] != "failed" else "The video could not be converted for the web; move it out of the item to publish without it")
    st = sync(item_id)
    if not st.get("video", {}).get("ok"):
        raise HostError("The video is not on streamhjalpen.tv yet: " + (st.get("video", {}).get("error") or "unknown error"))
    return st["url"]


def retry(item_id: int):
    """Operator's Retry: forgets recorded errors so every step is tried again."""
    with _lock(item_id):
        st = _state(item_id)
        for step in ("video", "poster", "details"):
            if st.get(step) and not st[step].get("ok"):
                st.pop(step)
        _save(item_id, st)
    kick()


def view(item_id: int) -> dict | None:
    if not configured():
        return None
    st = _state(item_id)
    errors = [st[s]["error"] for s in ("video", "poster", "details") if st.get(s) and not st[s].get("ok")]
    return {"url": st.get("url") if st.get("video", {}).get("ok") else None,
            "video": bool(st.get("video", {}).get("ok")), "poster": bool(st.get("poster", {}).get("ok")),
            "tradera_linked": bool(st.get("tradera_sent")), "error": errors[0] if errors else None,
            "blocked": any(st[s].get("fatal") for s in ("video", "poster", "details") if st.get(s))}


def test() -> dict:
    """Settings page check: reaches the site and proves the secret with a call that changes nothing."""
    if not configured():
        return {"ok": False, "message": "AUCTION_VIDEO_SECRET is not set, so videos use this server's own /v pages"}
    if BASE_WARNING:
        return {"ok": False, "message": BASE_WARNING}
    try:
        # deleting a token that cannot exist: 404 means reachable and the secret is accepted, 401 means a wrong secret
        _request("DELETE", f"/api/auction-videos/connection-test-{secrets.token_hex(8)}")
        return {"ok": True, "message": f"{BASE_URL} reached and the secret is accepted"}
    except HostError as exc:
        if exc.status == 404:
            return {"ok": True, "message": f"{BASE_URL} reached and the secret is accepted"}
        return {"ok": False, "message": str(exc)}


def summary() -> dict:
    rows = db.query("SELECT video_host FROM items WHERE video_host != ''")
    states = [json.loads(r["video_host"]) for r in rows]
    up = sum(1 for st in states if st.get("video", {}).get("ok"))
    failed = [st[s]["error"] for st in states for s in ("video", "poster", "details") if st.get(s) and not st[s].get("ok")]
    return {"configured": configured(), "base_url": BASE_URL, "warning": BASE_WARNING, "uploaded": up,
            "failed": len(failed), "last_error": failed[-1] if failed else None}


# ---- background worker ---------------------------------------------------------------------------
def kick():
    _wake.set()


def _round():
    rows = db.query("SELECT DISTINCT item_id FROM files WHERE kind = 'video' AND item_id != 0 AND web_status = 'ready'")
    rows += db.query("SELECT id AS item_id FROM items WHERE video_host LIKE '%\"video\": {\"ok\": true%'")
    for item_id in sorted({r["item_id"] for r in rows}):
        try:
            sync(item_id)
        except Exception:
            log.exception("syncing the video of item %s", item_id)


def _worker():
    while True:
        _round()
        _wake.wait(60)
        _wake.clear()


def start():
    if not configured():
        log.info("AUCTION_VIDEO_SECRET is not set: videos are linked to this server's own /v pages")
        return
    log.info("videos are uploaded to %s", BASE_URL)
    if BASE_WARNING:
        log.error(BASE_WARNING)
    # errors that a new secret or a switched-on API could fix are tried again after a restart
    for row in db.query("SELECT id, video_host FROM items WHERE video_host LIKE '%\"fatal\": true%'"):
        st = json.loads(row["video_host"])
        for s in ("video", "poster", "details"):
            step = st.get(s) or {}
            if step.get("fatal") and (step.get("status") in (401, 404) or 300 <= (step.get("status") or 0) < 400):
                st.pop(s)
        _save(row["id"], st)
    threading.Thread(target=_worker, daemon=True, name="videohost").start()
