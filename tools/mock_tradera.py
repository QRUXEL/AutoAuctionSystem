"""Tiny stand-in for the Tradera REST API, to rehearse publishing without touching a real account.

    python tools/mock_tradera.py 9099
then start the server with TRADERA_BASE=http://127.0.0.1:9099 TRADERA_DRY_RUN=0 and any non-empty credentials.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"next": 5000, "requests": {}}
CATEGORIES = [{"id": 1, "name": "Hem & Hushåll", "childCategories": [
    {"id": 101, "name": "Vaser", "childCategories": []}, {"id": 102, "name": "Flaskor & Karaffer", "childCategories": []}]},
    {"id": 2, "name": "Foto, Kameror & Optik", "childCategories": [{"id": 201, "name": "Objektiv", "childCategories": []}]}]


class Handler(BaseHTTPRequestHandler):
    def _send(self, body=None, code=200):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    def do_GET(self):
        if not self.headers.get("X-App-Id"):
            return self._send({"error": "no app id"}, 401)
        if self.path.startswith("/v4/items/"):
            # each read moves the bidding on: a new bid every second read, by alternating bidders
            iid = int(self.path.split("/")[3].split("?")[0])
            req = next((r for r in STATE["requests"].values() if r.get("item_id") == iid), None)
            if not req:
                return self._send({"error": "no such item"}, 404)
            req["reads"] = req.get("reads", 0) + 1
            n = req["reads"] // 2
            start = req["item"].get("startPrice") or 100
            end = req["item"].get("customEndDate") or "2030-01-01T12:00:00+01:00"
            from datetime import datetime
            ended = datetime.fromisoformat(end).timestamp() < __import__("time").time()
            return self._send({"id": iid, "totalBids": n, "maxBid": start + 50 * max(0, n - 1) if n else start, "nextBid": start + 50 * n,
                               "endDate": end, "status": {"ended": ended, "gotBidders": n > 0, "gotWinner": ended and n > 0},
                               "maxBidder": {"id": 9, "alias": ["anna_k", "bertil77"][n % 2]} if n else None,
                               "buyerList": [{"alias": ["anna_k", "bertil77"][n % 2]}] if ended and n else None,
                               "itemLink": f"https://www.tradera.com/item/{iid}", "thumbnailLink": None})
        if self.path.startswith("/v4/users/me/payment-options"):
            return self._send([]) if self.headers.get("X-User-Token") else self._send({"error": "no user token"}, 401)
        if self.path.startswith("/v4/categories"):
            return self._send(CATEGORIES)
        if self.path.startswith("/v4/reference-data/shipping-options"):
            if not self.headers.get("X-User-Token"):
                return self._send({"error": "no user token"}, 401)
            def prod(i, prov, pid, name, w, price):
                return {"id": i, "shippingProvider": prov, "shippingProviderId": pid, "name": name, "weight": w, "price": price,
                        "toCountry": "SE", "fromCountry": "SE"}
            return self._send({"productsPerWeightSpan": [
                {"weight": 1, "products": [prod(101, "PostNord", 7, "Varubrev 1 kg", 1, 59), prod(201, "DHL", 9, "DHL Paket 1 kg", 1, 79)]},
                {"weight": 5, "products": [prod(102, "PostNord", 7, "Postpaket 5 kg", 5, 119), prod(202, "DHL", 9, "DHL Paket 5 kg", 5, 129),
                                           prod(302, "DSV", 14, "DSV Paket 5 kg", 5, 99)]},
                {"weight": 20, "products": [prod(303, "DSV", 14, "DSV Paket 20 kg", 20, 189)]},
            ]})
        if self.path.startswith("/v4/reference-data/item-field-values"):
            return self._send({"vat": [0, 6, 12, 25], "shippingTypes": [{"id": 2, "description": "Postpaket"}, {"id": 6, "description": "Avhämtning"}]})
        if self.path.startswith("/v4/reference-data/accepted-bidder-types"):
            return self._send([{"id": 1, "description": "Sverige"}, {"id": 4, "description": "EU"}, {"id": 3, "description": "Internationellt"}])
        if self.path.startswith("/v4/listings/request-results"):
            rid = int(self.path.split("requestIds=")[1])
            req = STATE["requests"].get(rid, {})
            return self._send([{"requestId": rid, "resultCode": 1 if req.get("committed") else 256, "message": None}])
        self._send({}, 404)

    def do_POST(self):
        body, parts = self._body(), self.path.strip("/").split("/")
        if self.path == "/v4/auth/token":
            if not self.headers.get("X-App-Key") or len(body.get("secretKey", "")) < 32:
                return self._send({"error": "bad request"}, 400)
            return self._send({"authToken": f"mock-token-for-{body['userId']}", "hardExpirationTime": "2027-05-27T12:00:00+01:00"})
        if not self.headers.get("X-User-Token"):
            return self._send({"error": "no user token"}, 403)
        if self.path == "/v4/listings/validate-campaign-code":
            # Mock rules: MUSIK2026 is valid except in category 999; anything else does not exist.
            ok = body.get("campaignCode") in ("MUSIK2026", "musikhjalpen", "3164")
            return self._send({"isValid": ok and body.get("categoryId") != 999,
                               "isInvalidBecauseDoesNotExist": not ok,
                               "isInvalidBecauseCategoryIsNotAllowed": ok and body.get("categoryId") == 999,
                               "description": "Musikhjälpen 2026 (mock)" if ok else None, "isUnsoldFeeFree": ok})
        if self.path == "/v4/listings/items":
            if body.get("campaignCode") not in (None, "MUSIK2026", "musikhjalpen", "3164"):
                return self._send({"error": "unknown campaign code"}, 400)
            for field in ("title", "description", "categoryId", "itemType", "duration", "startPrice"):
                if not body.get(field):
                    return self._send({"error": f"{field} missing"}, 400)
            STATE["next"] += 1
            STATE["requests"][STATE["next"]] = {"item": body, "images": 0, "committed": False, "item_id": STATE["next"] + 700000}
            return self._send({"requestId": STATE["next"], "itemId": STATE["next"] + 700000})
        rid = int(parts[3])
        if parts[-1] == "campaign-code":
            if body.get("campaignCode") not in ("MUSIK2026", "musikhjalpen"):
                return self._send({"error": "unknown campaign code"}, 400)
            STATE["requests"][rid]["item"]["campaignCode"] = body["campaignCode"]
            return self._send()
        if parts[-1] == "images":
            assert body["imageData"] and body["imageFormat"] == 1
            STATE["requests"][rid]["images"] += 1
            return self._send()
        if parts[-1] == "commit":
            STATE["requests"][rid]["committed"] = True
            print("COMMITTED", rid, STATE["requests"][rid]["item"]["title"], "images:", STATE["requests"][rid]["images"],
                  "campaign:", STATE["requests"][rid]["item"].get("campaignCode"),
                  "ends:", STATE["requests"][rid]["item"].get("customEndDate"), "duration:", STATE["requests"][rid]["item"].get("duration"),
                  "bidders:", STATE["requests"][rid]["item"].get("acceptedBidderId"),
                  "shipping:", json.dumps(STATE["requests"][rid]["item"].get("shippingOptions")), flush=True)
            return self._send()
        self._send({}, 404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 9099), Handler).serve_forever()
