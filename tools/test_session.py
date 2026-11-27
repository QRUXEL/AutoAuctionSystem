"""End-to-end check of Start item / Stop item against a running server.

    python tools/test_session.py --url http://127.0.0.1:8080 --key <STATION_KEY> --password <REVIEW_PASSWORD>

Shoots a test shot, starts an item, shoots photos by hand, runs a turntable capture inside
the item (it must join it), shoots more by hand, stops the item and shoots one more test shot.
Expected: one item with every photo and the run's video; the two test shots in Loose shots;
the item stays open until Stop item, even though the run reported its end.
"""
import argparse
import time
from types import SimpleNamespace

from fake_camera import upload, video
from test_matching import SKEW, call, shoot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--key", default="k")
    ap.add_argument("--password", default="pw")
    ap.add_argument("--ftp-port", type=int, default=2121)
    ap.add_argument("--ftp-password", default="change-me")
    args = ap.parse_args()
    ftp = SimpleNamespace(host="127.0.0.1", port=args.ftp_port, user="camera", password=args.ftp_password)
    loose_before = {f["id"] for f in call(args, "GET", "/api/loose")}
    ok = True

    def check(label, got, want):
        nonlocal ok
        ok &= got == want
        print("OK  " if got == want else "FAIL", label, got, "" if got == want else f"(expected {want})")

    shoot(ftp, "TEST_BEFORE.JPG", 0, 1)
    time.sleep(4)

    item = call(args, "POST", "/api/session/start")["item_id"]
    check("start twice gives the same item", call(args, "POST", "/api/session/start")["item_id"], item)
    for n in range(3):
        shoot(ftp, f"HAND{n}.JPG", n, 30)
        time.sleep(0.4)

    run = call(args, "POST", "/api/items", {"photos": 4, "videos": 1}, station=True)["id"]
    check("turntable run joins the open item", run, item)
    for n in range(4):
        shoot(ftp, f"RUN{n}.JPG", n, 40)
        time.sleep(0.4)
    clip = video(time.time() + SKEW)
    time.sleep(1)
    call(args, "POST", f"/api/items/{run}/captured", station=True)
    upload(ftp, "RUN.MP4", clip)
    check("item still open after the run ended", (call(args, "GET", "/api/station")["session"] or {}).get("item_id"), item)

    for n in range(2):
        shoot(ftp, f"HAND{n + 3}.JPG", n, 50)
        time.sleep(0.4)
    time.sleep(1)
    check("status while open", call(args, "GET", f"/api/items/{item}")["status"], "capturing")
    call(args, "POST", "/api/session/stop")
    check("no item open after Stop", call(args, "GET", "/api/station")["session"], None)
    time.sleep(4)
    shoot(ftp, "TEST_AFTER.JPG", 0, 2)
    time.sleep(3)

    it = call(args, "GET", f"/api/items/{item}")
    loose = sorted(f["name"] for f in call(args, "GET", "/api/loose") if f["id"] not in loose_before)
    check("photos in the item", sorted(p["name"] for p in it["photos"]),
          sorted(["HAND0.JPG", "HAND1.JPG", "HAND2.JPG", "RUN0.JPG", "RUN1.JPG", "RUN2.JPG", "RUN3.JPG", "HAND3.JPG", "HAND4.JPG"]))
    check("videos in the item", [v["name"] for v in it["videos"]], ["RUN.MP4"])
    check("loose", loose, ["TEST_AFTER.JPG", "TEST_BEFORE.JPG"])
    print("     status:", it["status"], "(processed once GRACE_SECONDS have passed since the last upload)")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
