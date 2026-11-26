"""Minimal client for Tradera's REST API v4 (https://api.tradera.com/openapi.json).

Tradera has no draft state: a listing request is either uncommitted (invisible)
or committed (live). This system therefore keeps its own review queue and only
talks to Tradera when someone presses Publish.
"""
import base64
import json
import time

import requests

from . import config

RESULT_OK, RESULT_WAITING, RESULT_UNCOMMITTED = 1, 128, 256


class TraderaError(RuntimeError):
    pass


def _session(user: bool = True) -> requests.Session:
    s = requests.Session()
    s.headers.update({"X-App-Id": config.TRADERA_APP_ID, "X-App-Key": config.TRADERA_APP_KEY, "Accept": "application/json"})
    if user:
        s.headers.update({"X-User-Id": config.TRADERA_USER_ID, "X-User-Token": config.TRADERA_USER_TOKEN})
    return s


def _call(s, method, path, **kw):
    r = s.request(method, config.TRADERA_BASE + path, timeout=60, **kw)
    if r.status_code >= 400:
        raise TraderaError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else None


def build_payload(item: dict, settings: dict) -> dict:
    description = item["description"]
    if settings.get("description_footer"):
        description += "\n\n" + settings["description_footer"]
    payload = {
        "title": item["title"][:80],
        "description": description,
        "categoryId": item["category_id"],
        "itemType": settings["item_type"],
        "duration": item.get("duration_days") or settings["duration_days"],
        "restarts": settings["restarts"],
        "startPrice": item.get("start_price") or settings["start_price"],
        "acceptedBidderId": settings["accepted_bidder_id"],
        "shippingOptions": settings["shipping_options"],
        "shippingCondition": settings["shipping_condition"],
        "ownReferences": [f"station-{item['id']}"],
        "autoCommit": False,
    }
    if item.get("buy_now_price"):
        payload["buyItNowPrice"] = item["buy_now_price"]
    if settings.get("vat") is not None:
        payload["vat"] = settings["vat"]
    return payload


def publish(item: dict, image_paths: list, settings: dict, log=lambda m: None) -> dict:
    """Create the listing, upload images, commit, and wait for Tradera's verdict."""
    s = _session()
    payload = build_payload(item, settings)
    key = f"auction-station-{item['id']}-{item['publish_attempts']}"
    queued = _call(s, "POST", "/v4/listings/items", json=payload, headers={"Idempotency-Key": key})
    request_id, item_id = queued["requestId"], queued.get("itemId")
    log(f"request {request_id} created")
    for path in image_paths:
        body = {"imageData": base64.b64encode(path.read_bytes()).decode(), "imageFormat": 1, "hasMega": True}
        _call(s, "POST", f"/v4/listings/items/{request_id}/images", json=body)
        log(f"uploaded {path.name}")
    _call(s, "POST", f"/v4/listings/items/{request_id}/commit")
    deadline = time.time() + 120
    while time.time() < deadline:
        results = _call(s, "GET", "/v4/listings/request-results", params={"requestIds": request_id}) or []
        result = results[0] if results else {}
        code = result.get("resultCode")
        if code == RESULT_OK:
            return {"request_id": request_id, "item_id": item_id}
        if code not in (None, RESULT_WAITING, RESULT_UNCOMMITTED):
            raise TraderaError(f"Tradera rejected the listing (code {code}): {result.get('message')}")
        time.sleep(3)
    raise TraderaError(f"Tradera did not confirm request {request_id} within two minutes; check the account before retrying")


# ---- categories -----------------------------------------------------------
_cache = {"at": 0.0, "flat": []}


def categories() -> list:
    """Flat list of leaf categories as {id, path}; cached on disk for a day."""
    if _cache["flat"] and time.time() - _cache["at"] < 86400:
        return _cache["flat"]
    cache_file = config.DATA / "categories.json"
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < 86400:
        flat = json.loads(cache_file.read_text("utf-8"))
    elif config.TRADERA_APP_ID and config.TRADERA_APP_KEY:
        tree = _call(_session(user=False), "GET", "/v4/categories")
        flat = []

        def walk(nodes, trail):
            for n in nodes or []:
                path = trail + [n.get("name") or ""]
                if n.get("childCategories"):
                    walk(n["childCategories"], path)
                else:
                    flat.append({"id": n["id"], "path": " > ".join(path)})

        walk(tree, [])
        cache_file.write_text(json.dumps(flat, ensure_ascii=False), "utf-8")
    else:
        flat = []
    _cache.update(at=time.time(), flat=flat)
    return flat


def search_categories(terms: list, limit: int = 40) -> list:
    words = [w.lower() for t in terms for w in str(t).split() if len(w) > 2]
    scored = []
    for c in categories():
        path = c["path"].lower()
        leaf = path.rsplit(" > ", 1)[-1]
        score = sum((2 if w in leaf else 1) for w in words if w in path)
        if score:
            scored.append((score, c))
    scored.sort(key=lambda sc: (-sc[0], len(sc[1]["path"])))
    return [c for _, c in scored[:limit]]
