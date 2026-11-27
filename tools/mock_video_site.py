"""Stand-in for streamhjalpen.tv's auction-video API, for rehearsals.

    python tools/mock_video_site.py 9098 SECRET
    # then run the server with AUCTION_VIDEO_BASE_URL=http://127.0.0.1:9098 AUCTION_VIDEO_SECRET=SECRET

Keeps entries in memory and prints every call. GET /state lists them.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SECRET = sys.argv[2] if len(sys.argv) > 2 else "secret"
ENTRIES: dict = {}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, obj=None):
        data = json.dumps(obj if obj is not None else {}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/state":
            return self._send(200, ENTRIES)
        self._send(404)

    def _api(self):
        u = urlparse(self.path)
        parts = u.path.strip("/").split("/")
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        if parts[:2] != ["api", "auction-videos"] or len(parts) < 3:
            return self._send(404)
        if self.headers.get("Authorization") != f"Bearer {SECRET}":
            return self._send(401, {"error": "unauthorized"})
        token = parts[2]
        print(self.command, u.path, f"{len(body)} bytes" if body else "", flush=True)
        if self.command == "PUT" and len(parts) == 3:
            if body[4:8] != b"ftyp":
                return self._send(400, {"error": "not an mp4"})
            q = parse_qs(u.query)
            ENTRIES[token] = {"title": q.get("title", [""])[0], "ref": q.get("ref", [""])[0], "video_bytes": len(body),
                              "poster_bytes": 0, "traderaItemId": None}
            return self._send(200, {"token": token, "url": f"https://streamhjalpen.tv/video/{token}"})
        if token not in ENTRIES:
            return self._send(404, {"error": "unknown token"})
        if self.command == "PUT" and parts[3:] == ["poster"]:
            ENTRIES[token]["poster_bytes"] = len(body)
            return self._send(200, {"ok": True})
        if self.command == "PATCH":
            ENTRIES[token].update({k: v for k, v in json.loads(body).items() if k in ("title", "ref", "traderaItemId")})
            return self._send(200, {"ok": True})
        if self.command == "DELETE":
            ENTRIES.pop(token)
            return self._send(200, {"ok": True})
        self._send(404)

    do_PUT = do_PATCH = do_DELETE = _api

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 9098), Handler).serve_forever()
