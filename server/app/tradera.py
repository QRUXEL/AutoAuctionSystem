"""Minimal client for Tradera's REST API v4 (https://api.tradera.com/openapi.json).

Tradera has no draft state: a listing request is either uncommitted (invisible)
or committed (live). This system therefore keeps its own review queue and only
talks to Tradera when someone presses Publish.
"""
import base64
import json
import re
import time

import requests

from . import config

RESULT_OK, RESULT_WAITING, RESULT_UNCOMMITTED = 1, 128, 256


class TraderaError(RuntimeError):
    pass


def _session(user: bool = True) -> requests.Session:
    s = requests.Session()
    s.headers.update({"X-App-Id": config.get("TRADERA_APP_ID"), "X-App-Key": config.get("TRADERA_APP_KEY"), "Accept": "application/json"})
    if user:
        s.headers.update({"X-User-Id": config.get("TRADERA_USER_ID"), "X-User-Token": config.get("TRADERA_USER_TOKEN")})
    return s


def _call(s, method, path, **kw):
    r = s.request(method, config.TRADERA_BASE + path, timeout=60, **kw)
    if r.status_code >= 400:
        raise TraderaError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else None


def item_shipping(item: dict, settings: dict) -> list:
    if item.get("shipping_override"):
        try:
            return json.loads(item["shipping_override"])
        except ValueError:
            pass
    return settings["shipping_options"]


def build_payload(item: dict, settings: dict, video_url: str | None = None, end_at: float | None = None) -> dict:
    from .videos import fill_tags
    description = item["description"]
    if settings.get("description_footer"):
        description += "\n\n" + settings["description_footer"]
    description = fill_tags(description, video_url)
    payload = {
        "title": item["title"][:80],
        "description": description,
        "categoryId": item["category_id"],
        "itemType": settings["item_type"],
        "duration": item.get("duration_days") or settings["duration_days"],
        "restarts": settings["restarts"],
        "startPrice": item.get("start_price") or settings["start_price"],
        "acceptedBidderId": item["accepted_bidder_override"] if item.get("accepted_bidder_override") else settings["accepted_bidder_id"],
        "shippingOptions": None, "shippingCondition": None,     # filled in below
        "ownReferences": [f"station-{item['id']}"],
        "autoCommit": False,
    }
    payload["shippingOptions"], payload["shippingCondition"] = tradera_shipping(item_shipping(item, settings), settings["shipping_condition"])
    if end_at and payload["itemType"] == 1:          # an exact end; Tradera ignores it for fixed price
        from . import schedule
        payload["customEndDate"] = schedule.to_tradera(end_at)
        payload["duration"] = schedule.duration_for(end_at)
    if item.get("buy_now_price"):
        payload["buyItNowPrice"] = item["buy_now_price"]
    if settings.get("vat") is not None:
        payload["vat"] = settings["vat"]
    if (settings.get("campaign_code") or "").strip():
        payload["campaignCode"] = settings["campaign_code"].strip()
    return payload


# ---- campaign codes (charity auctions, e.g. Musikhjälpen) ---------------------------
_REASONS = {
    "isInvalidBecauseDoesNotExist": "the code does not exist",
    "isInvalidBecauseHasNotStarted": "the campaign has not started yet",
    "isInvalidBecauseHasEnded": "the campaign has ended",
    "isInvalidBecauseCategoryIsNotAllowed": "the campaign does not allow this category",
    "isInvalidBecauseAlreadyUsed": "the code has already been used",
    "isInvalidBecauseUserIsNotAllowed": "this Tradera account may not use the code",
}


def default_check_category() -> int | None:
    """A category to check a code against when none is given: the most used one among recent items."""
    from . import db
    row = db.one("SELECT category_id, COUNT(*) AS n FROM items WHERE category_id IS NOT NULL GROUP BY category_id ORDER BY n DESC LIMIT 1")
    if row:
        return row["category_id"]
    leaves = categories()
    return leaves[0]["id"] if leaves else None


# Tradera has no endpoint that lists campaign codes. A campaign's code appears to match its page name
# (tradera.com/campaign/<code>), so these are tried; Tradera's validation says which ones exist.
# Musikhjälpen's own pattern: "MH 20" (private sellers) / "MHF 20" (companies) in 2020, so this year's would be MH 26 / MHF 26.
CAMPAIGN_CANDIDATES = ["MH26", "MHF26", "MH 26", "MHF 26", "MH2026", "MHF2026", "MH25", "MHF25", "musikhjalpen", "musikhjalpen-2026", "Musikhjälpen2026", "MUSIKHJALPEN", "musikhjälpen", "Musikhjalpen", "musikhjalpen2026", "musikhjalpen26", "mh2026", "mh26",
                       "radiohjalpen", "streamhjalpen"]


def find_campaigns(category_id: int, extra: list | None = None, only: bool = False) -> list:
    """Checks the candidate codes (and any extra) against a category; valid ones first."""
    seen, out = set(), []
    for code in [*(extra or []), *([] if only else CAMPAIGN_CANDIDATES)]:
        code = (code or "").strip()
        if not code or code.lower() in seen:
            continue
        seen.add(code.lower())
        try:
            r = validate_campaign(code, category_id)
            out.append({"code": code, "ok": r["ok"], "message": r["message"],
                        "description": (r.get("raw") or {}).get("description") or ""})
        except TraderaError as exc:
            out.append({"code": code, "ok": False, "message": str(exc)[:200], "description": ""})
    out.sort(key=lambda c: not c["ok"])
    return out


def trial_campaign(code: str, category_id: int) -> dict:
    """Tries a code the way a listing would get it, without publishing anything: creates a hidden test
    listing (autoCommit off, never committed), adds the code with POST .../{requestId}/campaign-code, and
    reports Tradera's answer and the request's state. The test listing is left uncommitted."""
    s = _session()
    queued = _call(s, "POST", "/v4/listings/items", json={
        "title": "Test av kampanjkod - publiceras inte", "description": "Test från auktionsstationen. Publiceras inte.",
        "categoryId": int(category_id), "itemType": 1, "duration": 7, "startPrice": 1000, "acceptedBidderId": 1,
        "shippingOptions": [], "autoCommit": False}, headers={"Idempotency-Key": f"campaign-trial-{int(time.time())}"})
    rid = queued["requestId"]
    try:
        _call(s, "POST", f"/v4/listings/items/{rid}/campaign-code", json={"campaignCode": code.strip()})
        added = {"ok": True, "message": "Tradera accepted the code on the test listing (HTTP 200)"}
    except TraderaError as exc:
        added = {"ok": False, "message": f"Tradera refused it: {exc}"}
    results = _call(s, "GET", "/v4/listings/request-results", params={"requestIds": rid}) or []
    state = results[0] if results else {}
    return {**added, "request_id": rid, "result_code": state.get("resultCode"), "result_message": state.get("message"),
            "note": "The test listing was never committed, so nothing was published."}


def validate_campaign(code: str, category_id: int) -> dict:
    """POST /v4/listings/validate-campaign-code. Returns {ok, message, raw}."""
    raw = _call(_session(), "POST", "/v4/listings/validate-campaign-code",
                json={"campaignCode": code.strip(), "categoryId": int(category_id)}) or {}
    if raw.get("isValid"):
        bits = [raw.get("description") or "Valid"]
        if raw.get("isUnsoldFeeFree"):
            bits.append("no fee if unsold")
        return {"ok": True, "message": ", ".join(bits), "raw": raw}
    reasons = [text for key, text in _REASONS.items() if raw.get(key)]
    return {"ok": False, "message": "Campaign code not valid: " + ("; ".join(reasons) or "no reason given"), "raw": raw}


def signature(payload: dict, image_paths: list) -> str:
    """Fingerprint of a listing: the request body and the image files."""
    import hashlib
    h = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())
    for p in image_paths:
        st = p.stat()
        h.update(f"{p.name}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()


def _wait(s, request_id: int, until: set, seconds: int) -> dict | None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        results = _call(s, "GET", "/v4/listings/request-results", params={"requestIds": request_id}) or []
        result = results[0] if results else {}
        code = result.get("resultCode")
        if code in until:
            return result
        if code not in (None, RESULT_WAITING, RESULT_UNCOMMITTED, RESULT_OK):
            raise TraderaError(f"Tradera rejected the listing (code {code}): {result.get('message')}")
        time.sleep(3)
    return None


def send(item: dict, image_paths: list, settings: dict, log=lambda m: None, video_url: str | None = None,
         end_at: float | None = None) -> dict:
    """Creates the listing with autoCommit off and uploads the images. Nothing is live until commit()."""
    s = _session()
    payload = build_payload(item, settings, video_url, end_at)
    attach_later = None
    if payload.get("campaignCode"):
        check = validate_campaign(payload["campaignCode"], payload["categoryId"])
        if check["ok"]:
            log(f"campaign code ok: {check['message']}")
        else:
            # The check said no; the code may still be accepted on the pending listing
            # (POST .../{requestId}/campaign-code). Nothing is committed unless that works.
            attach_later = payload.pop("campaignCode")
            log(f"campaign code check: {check['message']}; trying to add it to the pending listing instead")
    key = f"auction-station-{item['id']}-{item['publish_attempts']}"
    queued = _call(s, "POST", "/v4/listings/items", json=payload, headers={"Idempotency-Key": key})
    request_id, item_id = queued["requestId"], queued.get("itemId")
    log(f"request {request_id} created")
    if attach_later:
        try:
            _call(s, "POST", f"/v4/listings/items/{request_id}/campaign-code", json={"campaignCode": attach_later})
            log("campaign code added to the pending listing")
        except TraderaError as exc:
            raise TraderaError(f"Tradera rejected the campaign code {attach_later} both when checking it and when adding it "
                               f"to the listing ({exc}). Nothing was published.") from exc
        payload["campaignCode"] = attach_later
    for path in image_paths:
        body = {"imageData": base64.b64encode(path.read_bytes()).decode(), "imageFormat": 1, "hasMega": True}
        _call(s, "POST", f"/v4/listings/items/{request_id}/images", json=body)
        log(f"uploaded {path.name}")
    # Tradera checks the request in the background; wait until it reports it ready (uncommitted) or rejects it
    ready = _wait(s, request_id, {RESULT_UNCOMMITTED}, 60)
    return {"request_id": request_id, "item_id": item_id, "confirmed": ready is not None,
            "signature": signature(payload, image_paths)}


def commit(request_id: int) -> None:
    """Makes a sent listing live and waits for Tradera to confirm."""
    s = _session()
    _call(s, "POST", f"/v4/listings/items/{request_id}/commit")
    if _wait(s, request_id, {RESULT_OK}, 120) is None:
        raise TraderaError(f"Tradera did not confirm request {request_id} within two minutes; check the account before retrying")


def publish(item: dict, image_paths: list, settings: dict, log=lambda m: None, video_url: str | None = None,
            end_at: float | None = None) -> dict:
    """Send and commit in one go."""
    sent = send(item, image_paths, settings, log, video_url, end_at)
    commit(sent["request_id"])
    return sent


# ---- reference data (cached for a day) -----------------------------------------------
def _cached(name: str, fetch, user: bool = False):
    cache = config.DATA / f"reference-{name}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < 86400:
        return json.loads(cache.read_text("utf-8"))
    try:
        data = fetch(_session(user=user))
    except Exception:
        if cache.exists():                       # stale is better than nothing
            return json.loads(cache.read_text("utf-8"))
        raise
    cache.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    return data


def shipping_products(from_country: str = "SE") -> list:
    """Every shipping product Tradera offers from the country, flattened:
    {key, providerId, provider, productId, name, weight, price, toCountry}."""
    def fetch(s):
        raw = _call(s, "GET", "/v4/reference-data/shipping-options", params={"fromCountryCodes": from_country}) or {}
        out, seen = [], set()
        for span in raw.get("productsPerWeightSpan") or []:
            for p in span.get("products") or []:
                key = f"{p.get('shippingProviderId')}:{p.get('id')}"
                if key in seen:
                    continue
                seen.add(key)
                out.append({"key": key, "providerId": p.get("shippingProviderId"), "provider": p.get("shippingProvider") or "",
                            "productId": p.get("id"), "name": p.get("name") or "", "weight": p.get("weight") or span.get("weight"),
                            "price": p.get("price"), "toCountry": p.get("toCountry") or ""})
        out.sort(key=lambda p: (p["provider"].lower(), p["weight"] or 0, p["price"] or 0))
        return out
    return _cached(f"shipping-{from_country}", fetch, user=True)


PICKUP = re.compile(r"h[äa]mt|pick.?up|avh[äa]mt|upph[äa]mt|collect", re.IGNORECASE)


def field_values() -> dict:
    return _cached("item-field-values", lambda s: _call(s, "GET", "/v4/reference-data/item-field-values") or {})


def pickup_provider_id() -> int | None:
    """Tradera's id for 'buyer collects', if any of its lists has one (the API docs don't say how pickup works)."""
    try:
        for p in shipping_products():
            if PICKUP.search(f"{p['provider']} {p['name']}"):
                return p["providerId"]
    except Exception:
        pass
    try:
        for t in field_values().get("shippingTypes") or []:
            if PICKUP.search(t.get("description") or ""):
                return t.get("id")
    except Exception:
        pass
    return None


def tradera_shipping(options: list, condition: str) -> tuple[list, str]:
    """Turns our options into Tradera's: a {"pickup": true, "place": ...} entry becomes Tradera's pickup option
    (when it has one) and a line in the shipping note, so buyers always see it."""
    real, notes = [], [condition.strip()] if condition and condition.strip() else []
    for o in options:
        if o.get("pickup"):
            pid = pickup_provider_id()
            if pid is not None:
                real.append({"shippingProviderId": pid, "cost": 0})
            place = (o.get("place") or "").strip()
            notes.append(f"Kan hämtas hos oss{(' i ' + place) if place else ''}.")
        else:
            real.append({k: v for k, v in o.items() if k in ("shippingProviderId", "shippingProductId", "cost", "shippingWeight")})
    return real, " ".join(notes)


def bidder_types() -> list:
    """[{id, description}] - who may bid: Sweden, EU, international."""
    return _cached("bidder-types", lambda s: _call(s, "GET", "/v4/reference-data/accepted-bidder-types") or [])


# ---- categories -----------------------------------------------------------
_cache = {"at": 0.0, "flat": []}


def categories() -> list:
    """Flat list of leaf categories as {id, path}; cached on disk for a day."""
    if _cache["flat"] and time.time() - _cache["at"] < 86400:
        return _cache["flat"]
    cache_file = config.DATA / "categories.json"
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < 86400:
        flat = json.loads(cache_file.read_text("utf-8"))
    elif config.get("TRADERA_APP_ID") and config.get("TRADERA_APP_KEY"):
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


def check() -> dict:
    """Connection test for the Settings page: app keys, then the seller's user token."""
    out = {"app": None, "user": None}
    if not (config.get("TRADERA_APP_ID") and config.get("TRADERA_APP_KEY")):
        out["app"] = {"ok": False, "message": "App ID and app key are not set"}
        return out
    try:
        tree = _call(_session(user=False), "GET", "/v4/categories")
        out["app"] = {"ok": True, "message": f"App keys work ({len(tree or [])} top-level categories)"}
        _cache.update(at=0.0, flat=[])           # reload categories with the new keys
        (config.DATA / "categories.json").unlink(missing_ok=True)
    except Exception as exc:
        out["app"] = {"ok": False, "message": str(exc)}
        return out
    if not (config.get("TRADERA_USER_ID") and config.get("TRADERA_USER_TOKEN")):
        out["user"] = {"ok": False, "message": "User ID and user token are not set"}
        return out
    try:
        _call(_session(), "GET", "/v4/users/me/payment-options")
        out["user"] = {"ok": True, "message": "User token works"}
    except Exception as exc:
        out["user"] = {"ok": False, "message": str(exc)}
    return out


# ---- token login (https://api.tradera.com/token-login) --------------------------
LOGIN_URL = "https://api.tradera.com/token-login"


def login_url(skey: str) -> str:
    from urllib.parse import urlencode
    return LOGIN_URL + "?" + urlencode({"appId": config.get("TRADERA_APP_ID"), "pkey": config.get("TRADERA_PUBLIC_KEY"), "skey": skey})


def fetch_token(user_id: int, skey: str) -> dict:
    """POST /v4/auth/token with the same secret key used for the login. Returns {authToken, hardExpirationTime}."""
    return _call(_session(user=False), "POST", "/v4/auth/token", json={"userId": int(user_id), "secretKey": skey})
