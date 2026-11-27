import base64
import hmac
import json
import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, db, describe, ftp, imaging, ingest, layout, station, tradera, videos, videohost

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("app")
WEB = Path(__file__).parent / "static"          # the built React app (see web/)
EDITABLE_ITEM = {"title", "description", "category_id", "category_path", "start_price", "buy_now_price", "duration_days"}
STATUSES = ("capturing", "processing", "review", "publishing", "sent", "published", "error")


@asynccontextmanager
async def lifespan(_app):
    db.init()
    ingest.start()
    videos.start()
    videohost.start()
    ftp.start()
    yield


app = FastAPI(title="Auction station", lifespan=lifespan)


@app.middleware("http")
async def auth(request: Request, call_next):
    if request.url.path == "/healthz" or request.url.path.startswith("/v/"):   # health check, public video pages
        return await call_next(request)
    key = request.headers.get("x-station-key", "")
    if config.STATION_KEY and hmac.compare_digest(key, config.STATION_KEY):
        return await call_next(request)
    if config.REVIEW_PASSWORD:
        try:
            scheme, token = request.headers.get("authorization", " ").split(" ", 1)
            password = base64.b64decode(token).decode().split(":", 1)[1] if scheme.lower() == "basic" else ""
        except Exception:
            password = ""
        if not hmac.compare_digest(password, config.REVIEW_PASSWORD):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Auction station"'})
    return await call_next(request)


# ---- items -------------------------------------------------------------------
def _url(rel: str) -> str | None:
    if not rel:
        return None
    path = config.ITEMS / rel
    v = int(path.stat().st_mtime) if path.exists() else 0
    return f"/media/{rel}?v={v}"


def item_json(item: dict) -> dict:
    files = db.query("SELECT * FROM files WHERE item_id = ? ORDER BY kind, seq", item["id"])
    item["photos"] = [{
        "seq": f["seq"],
        "framed": _url(f["framed"]),
        "raw": f"/media/{f['raw']}",
        "preview": f"/api/items/{item['id']}/photos/{f['seq']}/preview",
        "crop": json.loads(f["crop"] or f["crop_auto"] or "null"),
        "manual_crop": bool(f["crop"]),
        "adjust": json.loads(f["adjust"]) if f["adjust"] else {"brightness": 0, "contrast": 0},
        "name": f["name"],
    } for f in files if f["kind"] == "photo"]
    item["changed_since_sent"] = item["status"] == "sent" and _changed_since_sent(item["id"])
    host = videohost.view(item["id"])
    item["video_host"] = host
    item["videos"] = [{"seq": f["seq"], "url": f"/media/{f['raw']}", "name": f["name"],
                       # with the video site set up, the public link is streamhjalpen.tv's; otherwise this server's /v page
                       "public_url": (host or {}).get("url") if host is not None else (videos.public_url(f["token"]) if f["token"] else None),
                       "public_path": None if host is not None else (f"/v/{f['token']}" if f["token"] else None),
                       "preview": f"/v/{videos.ensure_token(f['id'])}/video.mp4" if f["web_status"] == "ready" else None,
                       "web_status": f["web_status"] or ""} for f in files if f["kind"] == "video"]
    return item


def get_item(item_id: int) -> dict:
    item = db.one("SELECT * FROM items WHERE id = ?", item_id)
    if not item:
        raise HTTPException(404, "No such item")
    return item


def get_photo(item_id: int, seq: int) -> dict:
    f = db.one("SELECT * FROM files WHERE item_id = ? AND kind = 'photo' AND seq = ?", item_id, seq)
    if not f:
        raise HTTPException(404, "No such photo")
    return f


def editable(item: dict):
    if item["status"] in ("publishing", "published", "processing", "capturing"):
        raise HTTPException(409, f"Item is {item['status']}")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/info")
def info():
    counts = {s: 0 for s in STATUSES}
    for row in db.query("SELECT status, COUNT(*) AS n FROM items GROUP BY status"):
        counts[row["status"]] = row["n"]
    loose = db.one("SELECT COUNT(*) AS n FROM files WHERE item_id = 0")["n"]
    return {"tradera_ready": config.tradera_ready(), "dry_run": config.dry_run(), "auto_commit": bool(config.get("TRADERA_AUTO_COMMIT")),
            "ai_ready": bool(config.get("ANTHROPIC_API_KEY")), "photo_aspect": imaging.photo_aspect(),
            "counts": counts, "loose": loose, "camera_offset": ingest.camera_offset(),
            "campaign_code": config.settings().get("campaign_code") or "",
            "video_host": videohost.BASE_URL if videohost.configured() else None}


@app.get("/api/items")
def list_items(status: str = ""):
    statuses = [s for s in status.split(",") if s]
    if statuses:
        marks = ",".join("?" * len(statuses))
        rows = db.query(f"SELECT * FROM items WHERE status IN ({marks}) ORDER BY id DESC LIMIT 500", *statuses)
    else:
        rows = db.query("SELECT * FROM items ORDER BY id DESC LIMIT 500")
    return [item_json(r) for r in rows]


@app.post("/api/items")
async def open_item(request: Request):
    """Called by the station controller just before it starts shooting."""
    body = await request.json() if await request.body() else {}
    return {"id": ingest.open_item("station", body.get("photos"), body.get("videos"))}


@app.post("/api/items/{item_id}/captured")
def captured(item_id: int):
    get_item(item_id)
    db.update_item(item_id, captured_at=db.now())
    return {"ok": True}


@app.get("/api/items/{item_id}")
def read_item(item_id: int):
    return item_json(get_item(item_id))


@app.patch("/api/items/{item_id}")
def patch_item(item_id: int, body: dict = Body(...)):
    editable(get_item(item_id))
    fields = {k: (v if v != "" or k in ("title", "description", "category_path") else None)
              for k, v in body.items() if k in EDITABLE_ITEM}
    db.update_item(item_id, **fields)
    if "title" in fields:
        videohost.kick()
    return item_json(get_item(item_id))


@app.post("/api/items/{item_id}/video-host/retry")
def video_host_retry(item_id: int):
    get_item(item_id)
    videohost.retry(item_id)
    return item_json(get_item(item_id))


@app.post("/api/items/{item_id}/regenerate")
def regenerate(item_id: int, body: dict = Body(default={})):
    editable(get_item(item_id))
    ingest.reprocess(item_id, photos=bool(body.get("photos", True)), text=bool(body.get("text", True)))
    return {"ok": True}


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int):
    item = get_item(item_id)
    if item["status"] in ("publishing", "published"):
        raise HTTPException(409, "Published items are kept as a record")
    videohost.forget(item_id)
    db.run("DELETE FROM files WHERE item_id = ?", item_id)
    db.run("DELETE FROM items WHERE id = ?", item_id)
    return {"ok": True}


@app.get("/api/items/{item_id}/photos/{seq}/preview")
def photo_preview(item_id: int, seq: int):
    f = get_photo(item_id, seq)
    out = config.ITEMS / str(item_id) / "preview" / f"{seq:02d}.jpg"
    if not out.exists():
        imaging.preview(config.ITEMS / f["raw"], out)
    return FileResponse(out, headers={"Cache-Control": "private, max-age=3600"})


@app.put("/api/items/{item_id}/photos/{seq}/crop")
def set_crop(item_id: int, seq: int, body: dict = Body(...)):
    editable(get_item(item_id))
    f = get_photo(item_id, seq)
    try:
        crop = {k: float(body[k]) for k in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "Crop needs x, y, w and h as fractions of the photo")
    if not (0 <= crop["x"] < 1 and 0 <= crop["y"] < 1 and 0 < crop["w"] <= 1.0001 and 0 < crop["h"] <= 1.0001):
        raise HTTPException(422, "Crop is outside the photo")
    db.run("UPDATE files SET crop = ? WHERE id = ?", json.dumps(crop), f["id"])
    ingest.frame_one(get_photo(item_id, seq))
    return item_json(get_item(item_id))


@app.delete("/api/items/{item_id}/photos/{seq}/crop")
def reset_crop(item_id: int, seq: int):
    editable(get_item(item_id))
    f = get_photo(item_id, seq)
    db.run("UPDATE files SET crop = '' WHERE id = ?", f["id"])
    ingest.frame_one(get_photo(item_id, seq))
    return item_json(get_item(item_id))


@app.put("/api/items/{item_id}/photos/{seq}/adjust")
def set_adjust(item_id: int, seq: int, body: dict = Body(...)):
    """Brightness/contrast for one photo, or for all photos of the item with "all": true."""
    item = get_item(item_id)
    editable(item)
    adj = layout.clean_adjust(body)
    value = "" if not any(adj.values()) else json.dumps(adj)
    targets = db.query("SELECT * FROM files WHERE item_id = ? AND kind = 'photo'", item_id) if body.get("all") \
        else [get_photo(item_id, seq)]
    cfg = imaging.frame_config()
    for f in targets:
        db.run("UPDATE files SET adjust = ? WHERE id = ?", value, f["id"])
        ingest.frame_one({**f, "adjust": value}, cfg)
    return item_json(get_item(item_id))


@app.post("/api/items/{item_id}/photos/{seq}/adjust-preview")
def adjust_preview(item_id: int, seq: int, body: dict = Body(...)):
    """The framed photo with trial brightness/contrast, for the Adjust dialog. Nothing is saved."""
    import io
    f = get_photo(item_id, seq)
    cfg = imaging.frame_config()
    cfg = {**cfg, "canvas": {**cfg["canvas"], "long_side": 1000}}
    photo, _ = imaging.cropped(config.ITEMS / f["raw"], cfg, json.loads(f["crop"]) if f["crop"] else None)
    buf = io.BytesIO()
    imaging.frame_image(photo, cfg, f"#{item_id:04d}", layout.clean_adjust(body)).save(buf, "JPEG", quality=85)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def _listing(item_id: int, upload_video: bool):
    """What would go to Tradera for this item: (item, image paths, settings, video link)."""
    item = get_item(item_id)
    photos = db.query("SELECT framed FROM files WHERE item_id = ? AND kind = 'photo' ORDER BY seq", item_id)
    paths = [config.ITEMS / p["framed"] for p in photos if p["framed"]]
    if videohost.configured():
        # dry runs and checks don't upload; they use the link if the background upload has already happened
        video_url = videohost.link_for_publish(item_id) if upload_video else (videohost.view(item_id) or {}).get("url")
    else:
        video_url = videos.link_for_item(item_id)
    return item, paths, config.settings(), video_url


def _went_live(item_id: int, request_id: int, tradera_item_id):
    db.update_item(item_id, status="published", message="", published_at=db.now(),
                   tradera_request_id=request_id, tradera_item_id=tradera_item_id)
    videohost.kick()          # tells the video page which Tradera item it belongs to


def _publish(item_id: int):
    """Send: creates the listing on Tradera uncommitted, and commits it too when auto-publish is on."""
    try:
        item, paths, settings, video_url = _listing(item_id, upload_video=not config.dry_run())
        if config.dry_run():
            payload = tradera.build_payload(item, settings, video_url)
            note = ""
            if payload.get("campaignCode"):
                if config.tradera_ready():
                    check = tradera.validate_campaign(payload["campaignCode"], payload["categoryId"])
                    note = f" Campaign code: {check['message']}."
                else:
                    note = " The campaign code was not checked (Tradera is not connected)."
            (config.ITEMS / str(item_id) / "tradera_payload.json").write_text(
                json.dumps({"payload": payload, "images": [p.name for p in paths]}, ensure_ascii=False, indent=2), "utf-8")
            db.update_item(item_id, status="review",
                           message="Dry run: nothing was sent to Tradera. The request is saved next to the photos." + note)
            return
        sent = tradera.send(item, paths, settings, log=lambda m: log.info("item %s: %s", item_id, m), video_url=video_url)
        if config.get("TRADERA_AUTO_COMMIT"):
            tradera.commit(sent["request_id"])
            _went_live(item_id, sent["request_id"], sent["item_id"])
            return
        db.update_item(item_id, status="sent", sent_at=db.now(), tradera_request_id=sent["request_id"],
                       tradera_pending_item_id=sent["item_id"], tradera_sent_sig=sent["signature"],
                       message="" if sent["confirmed"] else "Tradera has not confirmed the listing yet; Go live checks again.")
    except Exception as exc:
        log.exception("sending item %s failed", item_id)
        db.update_item(item_id, status="error", message=f"Sending to Tradera failed: {exc}")


def _go_live(item_id: int):
    item = get_item(item_id)
    try:
        tradera.commit(item["tradera_request_id"])
        _went_live(item_id, item["tradera_request_id"], item["tradera_pending_item_id"])
    except Exception as exc:
        log.exception("committing item %s failed", item_id)
        db.update_item(item_id, status="sent", message=f"Going live failed: {exc}")   # still sent: Go live can be tried again


@app.post("/api/items/{item_id}/publish")
def publish(item_id: int):
    """Send to Tradera (uncommitted unless auto-publish is on). Also 'Send again' after edits."""
    with db.lock():
        item = get_item(item_id)
        if item["status"] not in ("review", "error", "sent"):
            raise HTTPException(409, f"Item is {item['status']}")
        missing = [name for name, ok in (("title", item["title"]), ("description", item["description"]),
                                         ("category", item["category_id"])) if not ok]
        if missing:
            raise HTTPException(422, "Missing: " + ", ".join(missing))
        db.update_item(item_id, status="publishing", publish_attempts=item["publish_attempts"] + 1, message="")
    threading.Thread(target=_publish, args=(item_id,), daemon=True).start()
    return {"ok": True}


def _changed_since_sent(item_id: int) -> bool:
    item, paths, settings, video_url = _listing(item_id, upload_video=False)
    return tradera.signature(tradera.build_payload(item, settings, video_url), paths) != item["tradera_sent_sig"]


@app.post("/api/items/{item_id}/go-live")
def go_live(item_id: int):
    """Commits a listing that was sent uncommitted."""
    with db.lock():
        item = get_item(item_id)
        if item["status"] != "sent" or not item["tradera_request_id"]:
            raise HTTPException(409, "Only items sent to Tradera and not live yet can go live")
        if _changed_since_sent(item_id):
            raise HTTPException(409, "The item has changed since it was sent. Send it again so Tradera gets the new version.")
        db.update_item(item_id, status="publishing", message="")
    threading.Thread(target=_go_live, args=(item_id,), daemon=True).start()
    return {"ok": True}


@app.get("/api/categories")
def categories(q: str = ""):
    try:
        return tradera.search_categories(q.split(), limit=25) if q.strip() else []
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


# ---- loose shots ----------------------------------------------------------------
def _ids(body: dict) -> list:
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(422, "Nothing selected")
    return ids


@app.get("/api/loose")
def list_loose():
    rows = db.query("SELECT * FROM files WHERE item_id = 0 ORDER BY COALESCE(shot_at, received_at) DESC, id DESC LIMIT 1000")
    return [{"id": f["id"], "kind": f["kind"], "name": f["name"], "shot_at": f["shot_at"], "received_at": f["received_at"],
             "url": f"/media/{f['raw']}", "thumb": f"/api/files/{f['id']}/thumb" if f["kind"] == "photo" else None}
            for f in rows]


@app.post("/api/loose/make-item")
def loose_make_item(body: dict = Body(...)):
    try:
        return {"id": ingest.make_item(_ids(body))}
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/loose/attach")
def loose_attach(body: dict = Body(...)):
    try:
        ingest.attach(_ids(body), int(body.get("item_id") or 0))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"ok": True}


@app.post("/api/loose/delete")
def loose_delete(body: dict = Body(...)):
    try:
        return {"deleted": ingest.delete_loose(_ids(body))}
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/items/{item_id}/files/{kind}/{seq}/detach")
def detach_file(item_id: int, kind: str, seq: int):
    editable(get_item(item_id))
    if kind not in ("photo", "video"):
        raise HTTPException(404)
    try:
        ingest.detach(item_id, kind, seq)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}


@app.get("/api/files/{file_id}/thumb")
def file_thumb(file_id: int):
    f = db.one("SELECT * FROM files WHERE id = ? AND kind = 'photo'", file_id)
    if not f:
        raise HTTPException(404)
    out = config.DATA / "thumbs" / f"{file_id}.jpg"
    if not out.exists():
        imaging.preview(config.ITEMS / f["raw"], out, size=480)
    return FileResponse(out, headers={"Cache-Control": "private, max-age=86400"})


# ---- settings ------------------------------------------------------------------
def _hint(value: str) -> str:
    return "…" + value[-4:] if len(value) > 8 else "set"


@app.get("/api/settings")
def read_settings():
    values, secrets = {}, {}
    for name, (kind, _default) in config.EDITABLE.items():
        value = config.get(name)
        if kind == "secret":
            secrets[name] = {"set": bool(value), "hint": _hint(value) if value else "", "source": config.source(name)}
        else:
            values[name] = value
    return {"values": values, "secrets": secrets, "listing": config.settings()}


@app.put("/api/settings")
def write_settings(body: dict = Body(...)):
    changes = {}
    for name, value in (body.get("values") or {}).items():
        if name not in config.EDITABLE or config.EDITABLE[name][0] == "secret":
            raise HTTPException(422, f"Unknown setting {name}")
        changes[name] = value
    for name, value in (body.get("secrets") or {}).items():
        if config.EDITABLE.get(name, ("",))[0] != "secret":
            raise HTTPException(422, f"Unknown secret {name}")
        changes[name] = value if value else None          # empty / null = remove, fall back to the environment
    try:
        config.update_integrations(changes)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc))
    listing = body.get("listing") or {}
    if "shipping_options" in listing and not isinstance(listing["shipping_options"], list):
        raise HTTPException(422, "shipping_options must be a list")
    config.update_listing(listing)
    return read_settings()


@app.post("/api/settings/campaign-check")
def campaign_check(body: dict):
    code = str(body.get("code") or config.settings().get("campaign_code") or "").strip()
    if not code:
        raise HTTPException(422, "No campaign code")
    if not config.tradera_ready():
        return {"ok": False, "message": "Connect Tradera first"}
    try:
        return tradera.validate_campaign(code, int(body.get("category_id") or 0))
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


@app.get("/api/settings/charities")
def charity_list():
    return tradera.charities()


@app.post("/api/settings/campaign-codes")
def campaign_codes(body: dict):
    """Tries likely campaign codes (and any typed) against a category; Tradera can't list them."""
    if not config.tradera_ready():
        raise HTTPException(409, "Connect Tradera first")
    category = body.get("category_id") or tradera.default_check_category()
    if not category:
        raise HTTPException(422, "Pick a category to check against")
    codes = [str(c) for c in body.get("codes", []) if isinstance(c, (str, int))]
    if body.get("only"):
        return tradera.find_campaigns(int(category), codes, only=True)
    return tradera.find_campaigns(int(category), codes + [config.settings().get("campaign_code") or ""])


@app.post("/api/settings/test/{what}")
def test_settings(what: str):
    if what == "claude":
        return describe.check()
    if what == "tradera":
        return tradera.check()
    raise HTTPException(404)


# ---- photo layout -------------------------------------------------------------------
TEST_SHAPES = {"landscape": 3 / 2, "portrait": 2 / 3, "square": 1.0}


@app.get("/api/layout")
def get_layout():
    return {"layout": layout.load(), "assets": layout.list_assets()}


@app.put("/api/layout")
def put_layout(body: dict = Body(...)):
    try:
        return {"layout": layout.save(body.get("layout") or {}), "assets": layout.list_assets()}
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/layout/preview")
def layout_preview(body: dict = Body(...)):
    try:
        cfg = layout.validate(body.get("layout") or {})
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    source = None
    if body.get("file_id"):
        f = db.one("SELECT raw FROM files WHERE id = ? AND kind = 'photo'", int(body["file_id"]))
        source = config.ITEMS / f["raw"] if f else None
    data = imaging.layout_preview(cfg, source, TEST_SHAPES.get(body.get("test") or ""), int(body.get("size") or 900))
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/layout/samples")
def layout_samples():
    rows = db.query("SELECT id, item_id, seq, name FROM files WHERE kind = 'photo' ORDER BY id DESC LIMIT 24")
    return [{"id": r["id"], "label": (f"#{r['item_id']:04d} photo {r['seq']}" if r["item_id"] else f"Loose: {r['name']}"),
             "thumb": f"/api/files/{r['id']}/thumb"} for r in rows]


@app.get("/api/layout/assets/{name}")
def get_asset(name: str):
    try:
        path = layout.asset_path(name)
    except ValueError:
        raise HTTPException(404)
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.put("/api/layout/assets/{name}")
async def put_asset(name: str, request: Request):
    try:
        saved = layout.add_asset(name, await request.body())
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"name": saved, "assets": layout.list_assets()}


@app.delete("/api/layout/assets/{name}")
def delete_asset(name: str):
    try:
        layout.delete_asset(name)
    except ValueError:
        raise HTTPException(404)
    return {"assets": layout.list_assets()}


@app.post("/api/layout/apply")
def apply_layout():
    """Re-frames the photos of every item that hasn't been published."""
    items = db.query("SELECT id FROM items WHERE status IN ('review', 'error')")
    for item in items:
        ingest.reprocess(item["id"], photos=True, text=False)
    return {"items": len(items)}


# ---- Tradera token login -----------------------------------------------------------
# Tradera app settings: Accept return URL = https://<site>/tradera/accept,
#                       Reject return URL = https://<site>/tradera/reject.
# Works whether or not "Display token on return URL" is enabled.
LOGIN_STATE = config.DATA / "tradera_login.json"


@app.get("/api/tradera/connect", include_in_schema=False)
def tradera_connect():
    missing = [n for n in ("TRADERA_APP_ID", "TRADERA_APP_KEY", "TRADERA_PUBLIC_KEY") if not config.get(n)]
    if missing:
        return RedirectResponse("/settings?tradera=missing", status_code=303)
    skey = uuid.uuid4().hex + uuid.uuid4().hex          # 64 characters; Tradera wants 32-255
    LOGIN_STATE.write_text(json.dumps({"skey": skey, "at": time.time()}), "utf-8")
    os.chmod(LOGIN_STATE, 0o600)
    return RedirectResponse(tradera.login_url(skey), status_code=303)


@app.get("/tradera/accept", include_in_schema=False)
def tradera_accept(request: Request):
    q = request.query_params
    try:
        if q.get("token"):                                # "Display token on return URL" is on
            token, user_id, expires = q["token"], q.get("userId", ""), q.get("exp", "")
        else:                                             # fetch it with the secret key from the login
            state = json.loads(LOGIN_STATE.read_text("utf-8")) if LOGIN_STATE.exists() else {}
            if not state or time.time() - state["at"] > 3600:
                return RedirectResponse("/settings?tradera=expired", status_code=303)
            user_id = q.get("userId") or config.get("TRADERA_USER_ID")
            if not user_id:
                return RedirectResponse("/settings?tradera=nouser", status_code=303)
            got = tradera.fetch_token(int(user_id), state["skey"])
            token, expires = got["authToken"], str(got.get("hardExpirationTime") or "")
        config.update_integrations({"TRADERA_USER_TOKEN": token, "TRADERA_USER_ID": str(user_id) or None,
                                    "TRADERA_TOKEN_EXPIRES": expires or None})
        LOGIN_STATE.unlink(missing_ok=True)
        return RedirectResponse("/settings?tradera=connected", status_code=303)
    except Exception as exc:
        log.exception("Tradera token login failed")
        return RedirectResponse("/settings?tradera=failed&reason=" + quote(str(exc)[:200]), status_code=303)


@app.get("/tradera/reject", include_in_schema=False)
def tradera_reject():
    LOGIN_STATE.unlink(missing_ok=True)
    return RedirectResponse("/settings?tradera=rejected", status_code=303)


class _HideTokens(logging.Filter):
    """Keeps the token Tradera may put in the return URL out of the access log."""
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) >= 3 and str(record.args[2]).startswith("/tradera/accept"):
            record.args = record.args[:2] + ("/tradera/accept",) + record.args[3:]
        # the station checks in every second and the site polls its state: too noisy to log when they succeed
        if isinstance(record.args, tuple) and len(record.args) >= 5 and str(record.args[2]) in ("/api/station/poll", "/api/station") \
                and record.args[4] == 200:
            return False
        return True


logging.getLogger("uvicorn.access").addFilter(_HideTokens())


# ---- the capture station (ESP32) ------------------------------------------------------
@app.post("/api/station/poll")
def station_poll(request: Request, body: dict = Body(default={})):
    """The ESP32 checks in here about once a second (with X-Station-Key)."""
    if not (config.STATION_KEY and hmac.compare_digest(request.headers.get("x-station-key", ""), config.STATION_KEY)):
        raise HTTPException(403, "Station key required")
    keep = ("busy", "camera", "table", "camera_note", "table_note", "table_battery", "table_pan_deg", "last", "last_item",
            "wifi_rssi", "version")
    status = {k: body.get(k) for k in keep if k in body}
    return {"command": station.checked_in(status, request.client.host if request.client else "")}


@app.get("/api/station")
def station_view():
    return station.view()


@app.post("/api/station/command")
def station_command(body: dict = Body(...)):
    try:
        station.request(str(body.get("command", "")))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return station.view()


# ---- public video pages (no password) -------------------------------------------------
@app.get("/v/{token}", include_in_schema=False)
def video_page(token: str):
    status, body = videos.page(token)
    return Response(body, status_code=status, media_type="text/html", headers={"X-Robots-Tag": "noindex, nofollow"})


@app.get("/v/{token}/{what}", include_in_schema=False)
def video_media(token: str, what: str):
    path = videos.media(token, what)
    if not path or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, headers={"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "public, max-age=3600"})


@app.post("/api/items/{item_id}/videos/{seq}/web-copy")
def retry_web_copy(item_id: int, seq: int):
    f = db.one("SELECT id FROM files WHERE item_id = ? AND kind = 'video' AND seq = ?", item_id, seq)
    if not f:
        raise HTTPException(404)
    db.run("UPDATE files SET web_status = '' WHERE id = ?", f["id"])
    videos.queue_web_copy(f["id"])
    return {"ok": True}


# ---- files and the web app -------------------------------------------------------
app.mount("/media", StaticFiles(directory=config.ITEMS), name="media")
if (WEB / "assets").exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith(("api/", "media/", "tradera/", "v/")):
        raise HTTPException(404)
    target = WEB / path
    if path and target.is_file() and WEB in target.resolve().parents:
        return FileResponse(target)
    index = WEB / "index.html"
    if not index.exists():
        return Response("The web app is not built. Run npm run build in server/web.", status_code=503)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
