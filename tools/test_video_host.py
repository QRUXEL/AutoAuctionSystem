"""Tests for server/app/videohost.py against a fake streamhjalpen.tv.

    python tools/test_video_host.py

Covers the upload order, the link in the Tradera description, retries on 5xx and network
errors, no retries on 4xx, idempotent re-runs, and that the secret never shows up in logs,
the database or error messages.
"""
import io
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SECRET = "s3cret-Value_that-must-NEVER-leak-0123456789"
TMP = Path(tempfile.mkdtemp(prefix="videohost-test-"))
for d in ("data", "branding"):
    (TMP / d).mkdir()


class Fake(BaseHTTPRequestHandler):
    calls: list = []
    script: dict = {}          # (method, kind) -> list of responses: "ok" | int status | "reset" | "echo-auth"

    def _kind(self, path):
        parts = path.strip("/").split("/")
        return "poster" if parts[-1] == "poster" else "entry"

    def _handle(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        token = u.path.strip("/").split("/")[2] if u.path.count("/") >= 3 else ""
        call = {"method": self.command, "kind": self._kind(u.path), "token": token, "query": parse_qs(u.query),
                "auth_ok": self.headers.get("Authorization") == f"Bearer {SECRET}",
                "type": self.headers.get("Content-Type"), "length": self.headers.get("Content-Length"),
                "ftyp": body[4:8] == b"ftyp", "size": len(body),
                "json": json.loads(body) if self.headers.get("Content-Type") == "application/json" else None}
        Fake.calls.append(call)
        queue = Fake.script.get((self.command, call["kind"])) or []
        action = queue.pop(0) if queue else "ok"
        if action == "reset":
            self.connection.shutdown(socket.SHUT_RDWR)
            self.close_connection = True
            return
        if action == "echo-auth":            # a badly behaved server that repeats the header back
            return self._reply(401, {"error": f"bad header {self.headers.get('Authorization')}"})
        if not call["auth_ok"]:
            return self._reply(401, {"error": "unauthorized"})
        if action != "ok":
            return self._reply(action, {"error": "scripted"})
        if self.command == "PUT" and call["kind"] == "entry":
            return self._reply(200, {"token": token, "url": f"https://streamhjalpen.tv/video/{token}"})
        self._reply(200, {"ok": True})

    def _reply(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_PUT = do_PATCH = do_DELETE = _handle

    def log_message(self, *a):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()

os.environ.update(DATA_DIR=str(TMP / "data"), BRANDING_DIR=str(TMP / "branding"), FTP_ENABLED="0",
                  AUCTION_VIDEO_BASE_URL=f"http://127.0.0.1:{server.server_port}", AUCTION_VIDEO_SECRET=SECRET)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from PIL import Image                      # noqa: E402
from app import config, db, tradera, videohost   # noqa: E402

db.init()
log_buffer = io.StringIO()
_handler = logging.StreamHandler(log_buffer)
_handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
logging.getLogger().addHandler(_handler)
logging.getLogger().setLevel(logging.DEBUG)          # includes urllib3's own request logging
sleeps: list = []
videohost._sleep = sleeps.append                     # no real waiting


def make_item(title="Vas i blått glas", video=True, photo=True) -> int:
    db.run("INSERT INTO items (status, source, created_at, expected_photos, expected_videos, title, description, category_id) "
           "VALUES ('review', 'station', ?, 1, 1, ?, 'Fin vas.\nSe videon: <video link>', 12)", time.time(), title)
    item_id = db.one("SELECT MAX(id) AS id FROM items")["id"]
    (config.ITEMS / str(item_id)).mkdir(parents=True, exist_ok=True)
    if photo:
        Image.new("RGB", (800, 600), (30, 90, 160)).save(config.ITEMS / str(item_id) / "framed-01.jpg")
        db.run("INSERT INTO files (item_id, kind, seq, raw, framed, received_at) VALUES (?, 'photo', 1, 'x.jpg', ?, ?)",
               item_id, f"{item_id}/framed-01.jpg", time.time())
    if video:
        web = config.DATA / "web" / f"test-{item_id}.mp4"
        web.parent.mkdir(parents=True, exist_ok=True)
        web.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + os.urandom(50_000))
        db.run("INSERT INTO files (item_id, kind, seq, raw, received_at, web, web_status) VALUES (?, 'video', 1, 'v.mp4', ?, ?, 'ready')",
               item_id, time.time(), f"web/test-{item_id}.mp4")
    return item_id


def calls_for(token):
    return [c for c in Fake.calls if c["token"] == token]


class VideoHostTests(unittest.TestCase):
    def setUp(self):
        Fake.script = {}
        sleeps.clear()

    def test_upload_order_link_and_tradera_patch(self):
        item = make_item()
        st = videohost.sync(item)
        token = st["token"]
        self.assertRegex(token, r"^[A-Za-z0-9_-]{16,64}$")
        self.assertNotIn(str(item), token)
        c = calls_for(token)
        self.assertEqual([(x["method"], x["kind"]) for x in c], [("PUT", "entry"), ("PUT", "poster")])
        put = c[0]
        self.assertTrue(put["auth_ok"] and put["ftyp"])
        self.assertEqual(put["type"], "video/mp4")
        self.assertEqual(int(put["length"]), (config.DATA / "web" / f"test-{item}.mp4").stat().st_size)
        self.assertEqual(put["query"], {"title": ["Vas i blått glas"], "ref": [f"#{item:04d}"]})
        self.assertEqual(c[1]["type"], "image/jpeg")
        # the link goes into the Tradera description
        self.assertEqual(st["url"], f"https://streamhjalpen.tv/video/{token}")
        link = videohost.link_for_publish(item)
        payload = tradera.build_payload(dict(db.one("SELECT * FROM items WHERE id = ?", item)), config.settings(), link)
        self.assertIn(f"Se videon: https://streamhjalpen.tv/video/{token}", payload["description"])
        self.assertNotIn("<video link>", payload["description"])
        # once published, the Tradera id is sent
        db.update_item(item, tradera_item_id=705001, status="published")
        videohost.sync(item)
        patch = calls_for(token)[-1]
        self.assertEqual((patch["method"], patch["json"]), ("PATCH", {"traderaItemId": 705001}))
        self.assertTrue(videohost.view(item)["tradera_linked"])

    def test_rerun_is_idempotent_and_title_edit_patches_once(self):
        item = make_item()
        token = videohost.sync(item)["token"]
        n = len(Fake.calls)
        videohost.sync(item)
        videohost._item_locks.clear()                 # as after a restart: state comes from the database
        videohost.sync(item)
        self.assertEqual(len(Fake.calls), n, "re-running must not upload again")
        db.update_item(item, title="Vas i blått glas, signerad")
        videohost.sync(item)
        videohost.sync(item)
        new = calls_for(token)[-1:]
        self.assertEqual(len(Fake.calls), n + 1)
        self.assertEqual(new[0]["json"], {"title": "Vas i blått glas, signerad"})
        # a replaced video file is uploaded again, with the same token
        web = config.DATA / "web" / f"test-{item}.mp4"
        web.write_bytes(web.read_bytes() + b"more")
        os.utime(web, (time.time() + 5, time.time() + 5))
        videohost.sync(item)
        self.assertEqual([(x["method"], x["kind"]) for x in calls_for(token)[-2:]], [("PUT", "entry"), ("PUT", "poster")])

    def test_retries_5xx_with_backoff(self):
        Fake.script = {("PUT", "entry"): [503, 502]}
        item = make_item()
        st = videohost.sync(item)
        self.assertTrue(st["video"]["ok"])
        self.assertEqual(len([c for c in calls_for(st["token"]) if c["kind"] == "entry"]), 3)
        self.assertEqual(sleeps, [2, 4])

    def test_retries_network_errors(self):
        Fake.script = {("PUT", "entry"): ["reset"], ("PUT", "poster"): ["reset", "reset"]}
        item = make_item()
        st = videohost.sync(item)
        self.assertTrue(st["video"]["ok"] and st["poster"]["ok"])
        self.assertEqual([c["kind"] for c in calls_for(st["token"])], ["entry", "entry", "poster", "poster", "poster"])

    def test_gives_up_after_five_attempts_and_retries_later(self):
        Fake.script = {("PUT", "entry"): [503] * 5}
        item = make_item()
        st = videohost.sync(item)
        self.assertFalse(st["video"]["ok"])
        self.assertFalse(st["video"]["fatal"])
        self.assertEqual(len(calls_for(st["token"])), 5)
        Fake.script = {("PUT", "entry"): [503] * 5}
        with self.assertRaises(videohost.HostError):          # publishing refuses a listing with a dead link
            videohost.link_for_publish(item)
        self.assertEqual(len(calls_for(st["token"])), 10, "publishing tries the upload again first")
        self.assertTrue(videohost.link_for_publish(item).endswith(st["token"]), "a later round succeeds")

    def test_no_retry_on_400_401_413(self):
        for status in (400, 401, 413):
            with self.subTest(status=status):
                Fake.script = {("PUT", "entry"): [status]}
                item = make_item()
                st = videohost.sync(item)
                self.assertEqual(len(calls_for(st["token"])), 1)
                self.assertTrue(st["video"]["fatal"])
                self.assertIn(str(status), st["video"]["error"])
                videohost.sync(item)                           # not tried again on its own
                self.assertEqual(len(calls_for(st["token"])), 1)
                self.assertTrue(videohost.view(item)["blocked"])
                videohost.retry(item)                          # the operator's Retry button
                videohost.sync(item)
                self.assertTrue(videohost._state(item)["video"]["ok"])
        self.assertEqual(sleeps, [])

    def test_too_large_is_refused_before_sending(self):
        item = make_item()
        old = videohost.MAX_VIDEO
        videohost.MAX_VIDEO = 1000
        try:
            st = videohost.sync(item)
        finally:
            videohost.MAX_VIDEO = old
        self.assertEqual(calls_for(st["token"]), [])
        self.assertTrue(st["video"]["fatal"])

    def test_poster_404_uploads_the_video_again(self):
        Fake.script = {("PUT", "poster"): [404]}
        item = make_item()
        token = videohost.sync(item)["token"]
        videohost.retry(item)
        videohost.sync(item)
        self.assertEqual([(c["method"], c["kind"]) for c in calls_for(token)],
                         [("PUT", "entry"), ("PUT", "poster"), ("PUT", "entry"), ("PUT", "poster")])

    def test_poster_waits_for_the_framed_photo(self):
        item = make_item(photo=False)
        st = videohost.sync(item)
        self.assertEqual([c["kind"] for c in calls_for(st["token"])], ["entry"])

    def test_item_without_video_and_deletion(self):
        plain = make_item(video=False)
        self.assertIsNone(videohost.link_for_publish(plain))
        self.assertEqual(videohost.sync(plain), {})
        item = make_item()
        token = videohost.sync(item)["token"]
        videohost.forget(item)
        for _ in range(50):
            if any(c["method"] == "DELETE" for c in calls_for(token)):
                break
            time.sleep(0.05)
        self.assertEqual(calls_for(token)[-1]["method"], "DELETE")
        # a video moved out of an item is removed from the site too
        item2 = make_item()
        token2 = videohost.sync(item2)["token"]
        db.run("UPDATE files SET item_id = 0 WHERE item_id = ? AND kind = 'video'", item2)
        videohost.sync(item2)
        self.assertEqual(calls_for(token2)[-1]["method"], "DELETE")

    def test_secret_never_leaks(self):
        Fake.script = {("PUT", "entry"): ["echo-auth"]}
        item = make_item()
        st = videohost.sync(item)
        self.assertIn("401", st["video"]["error"])
        videohost.retry(item)
        videohost.sync(item)
        dump = "\n".join(json.dumps(dict(r)) for r in db.query("SELECT * FROM items"))
        self.assertNotIn(SECRET, dump)
        self.assertNotIn(SECRET, log_buffer.getvalue())
        self.assertIn("videohost", log_buffer.getvalue(), "the test must actually capture the module's logs")


def tearDownModule():
    # runs after every test, so the log check sees the whole run
    assert SECRET not in log_buffer.getvalue(), "secret found in logs"


if __name__ == "__main__":
    unittest.main(verbosity=2)
