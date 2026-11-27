"""Public video pages, since Tradera's API takes photos only.

Each video gets a hard-to-guess public address, /v/<token>, that needs no
password. The camera's file is large and may use a format browsers can't play,
so a web copy (H.264, at most 1280 px wide) is made in the background with
ffmpeg. At publishing, a link tag in the description is replaced with the
address; without a video, the line holding the tag is removed.
"""
import html
import logging
import queue
import re
import secrets
import subprocess
import threading
from pathlib import Path

from . import config, db

log = logging.getLogger("videos")
TAG = re.compile(r"<\s*(?:youtube\s+)?video\s+link\s*>", re.IGNORECASE)
_work: "queue.Queue[int]" = queue.Queue()


def ensure_token(file_id: int) -> str:
    f = db.one("SELECT token FROM files WHERE id = ?", file_id)
    if f and f["token"]:
        return f["token"]
    token = secrets.token_urlsafe(12)
    db.run("UPDATE files SET token = ? WHERE id = ?", token, file_id)
    return token


def base_url() -> str:
    return (config.get("PUBLIC_BASE_URL") or "").rstrip("/")


def public_url(token: str) -> str:
    return f"{base_url()}/v/{token}"


def link_for_item(item_id: int) -> str | None:
    """Address of the item's first video on this server's own /v pages, or None.
    Only used when no AUCTION_VIDEO_SECRET is set; otherwise videohost.py gives the streamhjalpen.tv link."""
    f = db.one("SELECT id FROM files WHERE item_id = ? AND kind = 'video' ORDER BY seq LIMIT 1", item_id)
    return public_url(ensure_token(f["id"])) if f else None


def fill_tags(text: str, link: str | None) -> str:
    """Replaces the link tag; without a link, drops each line that holds a tag.
    With a link but no tag anywhere, a line with the link is added at the end."""
    if link:
        if not TAG.search(text):
            return text.rstrip() + f"\n\nSe videon: {link}"
        return TAG.sub(link, text)
    return "\n".join(line for line in text.split("\n") if not TAG.search(line)).strip("\n")


# ---- web copy ------------------------------------------------------------------------------
def queue_web_copy(file_id: int):
    f = db.one("SELECT web_status FROM files WHERE id = ? AND kind = 'video'", file_id)
    if f and f["web_status"] not in ("ready", "working", "queued"):
        db.run("UPDATE files SET web_status = 'queued' WHERE id = ?", file_id)
        _work.put(file_id)


def _transcode(file_id: int):
    f = db.one("SELECT * FROM files WHERE id = ?", file_id)
    if not f:
        return
    src = config.ITEMS / f["raw"]
    rel = f"web/{file_id}.mp4"
    out = config.DATA / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    db.run("UPDATE files SET web_status = 'working' WHERE id = ?", file_id)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
           "-vf", "scale='min(1280,iw)':-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=3 * 3600)
        tmp.replace(out)
        db.run("UPDATE files SET web = ?, web_status = 'ready' WHERE id = ?", rel, file_id)
        from . import videohost
        videohost.kick()
        log.info("web copy ready for video %s (%.1f MB)", file_id, out.stat().st_size / 1e6)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        detail = getattr(exc, "stderr", b"") or b""
        log.error("web copy failed for video %s: %s %s", file_id, exc, detail.decode(errors="replace")[-400:])
        db.run("UPDATE files SET web_status = 'failed' WHERE id = ?", file_id)


def _worker():
    while True:
        file_id = _work.get()
        try:
            _transcode(file_id)
        except Exception:
            log.exception("transcoding %s", file_id)


def start():
    db.run("UPDATE files SET web_status = '' WHERE web_status IN ('working', 'queued')")
    for f in db.query("SELECT id FROM files WHERE kind = 'video' AND item_id != 0 AND COALESCE(web_status, '') = ''"):
        queue_web_copy(f["id"])
    threading.Thread(target=_worker, daemon=True, name="video").start()


# ---- the public page -------------------------------------------------------------------------
def page(token: str) -> tuple[int, str]:
    f = db.one("SELECT * FROM files WHERE token = ? AND kind = 'video'", token)
    if not f:
        return 404, _html("Video not found", "<p class='note'>This video doesn't exist or has been removed.</p>")
    item = db.one("SELECT * FROM items WHERE id = ?", f["item_id"]) if f["item_id"] else None
    title = (item and item["title"]) or "Video"
    poster = db.one("SELECT framed FROM files WHERE item_id = ? AND kind = 'photo' AND framed != '' ORDER BY seq LIMIT 1",
                    f["item_id"]) if f["item_id"] else None
    if f["web_status"] == "ready":
        poster_attr = f' poster="/v/{token}/poster.jpg"' if poster else ""
        body = (f"<video controls playsinline preload='metadata'{poster_attr}>"
                f"<source src='/v/{token}/video.mp4' type='video/mp4'></video>")
    elif f["web_status"] == "failed":
        body = "<p class='note'>This video could not be prepared for the web.</p>"
    else:
        body = "<p class='note'>The video is being prepared. Please check back in a few minutes.</p>"
    ref = f"<p class='ref'>#{item['id']:04d}</p>" if item else ""
    return 200, _html(title, f"<h1>{html.escape(title)}</h1>{ref}{body}")


def _html(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>{html.escape(title)}</title>
<style>body{{margin:0;background:#141414;color:#eee;font:16px/1.5 system-ui,sans-serif;display:flex;justify-content:center}}
main{{width:min(100%,1100px);padding:24px 16px}}h1{{font-size:22px;margin:0 0 4px}}.ref{{color:#999;margin:0 0 16px;font-family:monospace}}
video{{width:100%;max-height:80vh;background:#000;border-radius:8px}}.note{{color:#bbb}}</style></head>
<body><main>{body}</main></body></html>"""


def media(token: str, what: str) -> Path | None:
    f = db.one("SELECT * FROM files WHERE token = ? AND kind = 'video'", token)
    if not f:
        return None
    if what == "video.mp4" and f["web_status"] == "ready" and f["web"]:
        return config.DATA / f["web"]
    if what == "poster.jpg" and f["item_id"]:
        p = db.one("SELECT framed FROM files WHERE item_id = ? AND kind = 'photo' AND framed != '' ORDER BY seq LIMIT 1", f["item_id"])
        return config.ITEMS / p["framed"] if p else None
    return None
