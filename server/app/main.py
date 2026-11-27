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

from . import config, db, describe, ftp, imaging, ingest, tradera

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("app")
WEB = Path(__file__).parent / "static"          # the built React app (see web/)
EDITABLE_ITEM = {"title", "description", "category_id", "category_path", "start_price", "buy_now_price", "duration_days"}
STATUSES = ("capturing", "processing", "review", "publishing", "published", "error")


@asynccontextmanager
async def lifespan(_app):
    db.init()
    ingest.start()
    ftp.start()
    yield


app = FastAPI(title="Auction station", lifespan=lifespan)


@app.middleware("http")
async def auth(request: Request, call_next):
    if request.url.path == "/healthz":
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
    } for f in files if f["kind"] == "photo"]
    item["videos"] = [f"/media/{f['raw']}" for f in files if f["kind"] == "video"]
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
    return {"tradera_ready": config.tradera_ready(), "dry_run": config.dry_run(),
            "ai_ready": bool(config.get("ANTHROPIC_API_KEY")), "photo_aspect": imaging.photo_aspect(),
            "counts": counts}


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


def _publish(item_id: int):
    item = get_item(item_id)
    photos = db.query("SELECT framed FROM files WHERE item_id = ? AND kind = 'photo' ORDER BY seq", item_id)
    paths = [config.ITEMS / p["framed"] for p in photos if p["framed"]]
    settings = config.settings()
    try:
        if config.dry_run():
            payload = tradera.build_payload(item, settings)
            (config.ITEMS / str(item_id) / "tradera_payload.json").write_text(
                json.dumps({"payload": payload, "images": [p.name for p in paths]}, ensure_ascii=False, indent=2), "utf-8")
            db.update_item(item_id, status="review",
                           message="Dry run: nothing was sent to Tradera. The request is saved next to the photos.")
            return
        result = tradera.publish(item, paths, settings, log=lambda m: log.info("item %s: %s", item_id, m))
        db.update_item(item_id, status="published", message="", published_at=db.now(),
                       tradera_request_id=result["request_id"], tradera_item_id=result["item_id"])
    except Exception as exc:
        log.exception("publishing item %s failed", item_id)
        db.update_item(item_id, status="error", message=f"Publishing failed: {exc}")


@app.post("/api/items/{item_id}/publish")
def publish(item_id: int):
    with db.lock():
        item = get_item(item_id)
        if item["status"] not in ("review", "error"):
            raise HTTPException(409, f"Item is {item['status']}")
        missing = [name for name, ok in (("title", item["title"]), ("description", item["description"]),
                                         ("category", item["category_id"])) if not ok]
        if missing:
            raise HTTPException(422, "Missing: " + ", ".join(missing))
        db.update_item(item_id, status="publishing", publish_attempts=item["publish_attempts"] + 1, message="")
    threading.Thread(target=_publish, args=(item_id,), daemon=True).start()
    return {"ok": True}


@app.get("/api/categories")
def categories(q: str = ""):
    try:
        return tradera.search_categories(q.split(), limit=25) if q.strip() else []
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


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


@app.post("/api/settings/test/{what}")
def test_settings(what: str):
    if what == "claude":
        return describe.check()
    if what == "tradera":
        return tradera.check()
    raise HTTPException(404)


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
        return True


logging.getLogger("uvicorn.access").addFilter(_HideTokens())


# ---- files and the web app -------------------------------------------------------
app.mount("/media", StaticFiles(directory=config.ITEMS), name="media")
if (WEB / "assets").exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith(("api/", "media/", "tradera/")):
        raise HTTPException(404)
    target = WEB / path
    if path and target.is_file() and WEB in target.resolve().parents:
        return FileResponse(target)
    index = WEB / "index.html"
    if not index.exists():
        return Response("The web app is not built. Run npm run build in server/web.", status_code=503)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
