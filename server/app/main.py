import base64
import hmac
import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, db, ftp, ingest, tradera

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("app")
STATIC = Path(__file__).parent / "static"
EDITABLE = {"title", "description", "category_id", "category_path", "start_price", "buy_now_price", "duration_days"}


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


def item_json(item: dict) -> dict:
    files = db.query("SELECT * FROM files WHERE item_id = ? ORDER BY kind, seq", item["id"])
    item["photos"] = [{"seq": f["seq"], "framed": f"/media/{f['framed']}" if f["framed"] else None,
                       "raw": f"/media/{f['raw']}"} for f in files if f["kind"] == "photo"]
    item["videos"] = [f"/media/{f['raw']}" for f in files if f["kind"] == "video"]
    return item


def get_item(item_id: int) -> dict:
    item = db.one("SELECT * FROM items WHERE id = ?", item_id)
    if not item:
        raise HTTPException(404, "No such item")
    return item


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/info")
def info():
    return {"tradera_ready": config.tradera_ready(), "dry_run": config.TRADERA_DRY_RUN or not config.tradera_ready(),
            "ai_ready": bool(config.ANTHROPIC_API_KEY), "settings": config.settings()}


@app.get("/api/items")
def list_items(status: str = ""):
    if status:
        rows = db.query("SELECT * FROM items WHERE status = ? ORDER BY id DESC LIMIT 300", status)
    else:
        rows = db.query("SELECT * FROM items ORDER BY id DESC LIMIT 300")
    return [item_json(r) for r in rows]


@app.post("/api/items")
async def open_item(request: Request):
    """Called by the station agent just before it starts shooting."""
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
async def patch_item(item_id: int, request: Request):
    get_item(item_id)
    body = await request.json()
    fields = {k: (v if v != "" or k in ("title", "description", "category_path") else None)
              for k, v in body.items() if k in EDITABLE}
    db.update_item(item_id, **fields)
    return item_json(get_item(item_id))


@app.post("/api/items/{item_id}/regenerate")
def regenerate(item_id: int):
    item = get_item(item_id)
    if item["status"] in ("publishing", "published"):
        raise HTTPException(409, "Already published")
    ingest.reprocess(item_id)
    return {"ok": True}


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int):
    item = get_item(item_id)
    if item["status"] in ("publishing", "published"):
        raise HTTPException(409, "Published items are kept as a record")
    db.run("DELETE FROM files WHERE item_id = ?", item_id)
    db.run("DELETE FROM items WHERE id = ?", item_id)
    return {"ok": True}


def _publish(item_id: int):
    item = get_item(item_id)
    photos = db.query("SELECT framed FROM files WHERE item_id = ? AND kind = 'photo' ORDER BY seq", item_id)
    paths = [config.ITEMS / p["framed"] for p in photos if p["framed"]]
    settings = config.settings()
    try:
        if config.TRADERA_DRY_RUN or not config.tradera_ready():
            payload = tradera.build_payload(item, settings)
            (config.ITEMS / str(item_id) / "tradera_payload.json").write_text(
                json.dumps({"payload": payload, "images": [p.name for p in paths]}, ensure_ascii=False, indent=2), "utf-8")
            db.update_item(item_id, status="review",
                           message="Dry run: nothing was sent to Tradera. The payload is saved next to the photos.")
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


app.mount("/media", StaticFiles(directory=config.ITEMS), name="media")
