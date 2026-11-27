"""End-to-end check of time-based matching against a running server.

    python tools/test_matching.py --url http://127.0.0.1:8080 --key <STATION_KEY> --password <REVIEW_PASSWORD>

Simulates a camera whose clock is wrong (2 min 17 s behind, local time UTC+1 with
the video header in UTC), test shots before, between and after captures, a photo
uploaded only after its capture ended, and a video uploaded a while later.
"""
import argparse
import base64
import json
import time
import urllib.request
from types import SimpleNamespace

from fake_camera import photo, upload, video

SKEW = -137 + 3600          # camera wall clock = real UTC + 1 h, minus 137 s


def call(args, method, path, body=None, station=False):
    headers = {"Content-Type": "application/json"}
    if station:
        headers["X-Station-Key"] = args.key
    else:
        headers["Authorization"] = "Basic " + base64.b64encode(f"x:{args.password}".encode()).decode()
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    with urllib.request.urlopen(urllib.request.Request(args.url + path, data=data, method=method, headers=headers)) as r:
        return json.load(r)


def shoot(ftp, name, n, seed):
    upload(ftp, name, photo(n * 90, seed, time.time() + SKEW))


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

    shoot(ftp, "TEST0001.JPG", 0, 1)                    # test shot, no capture running
    time.sleep(1.5)

    a = call(args, "POST", "/api/items", {"photos": 4, "videos": 1}, station=True)["id"]
    for n in range(3):
        shoot(ftp, f"A{n}.JPG", n, 10)
        time.sleep(0.4)
    late = photo(270, 10, time.time() + SKEW)           # 4th photo shot now, uploaded after the capture ends
    time.sleep(0.4)
    video_a = video(time.time() + SKEW - 3600)          # video header in UTC, like some cameras write it
    time.sleep(1.0)
    call(args, "POST", f"/api/items/{a}/captured", station=True)
    time.sleep(4)                                        # just outside the 3 s margin
    shoot(ftp, "TEST0002.JPG", 0, 2)                    # test shot between captures

    b = call(args, "POST", "/api/items", {"photos": 4, "videos": 1}, station=True)["id"]
    for n in range(4):
        shoot(ftp, f"B{n}.JPG", n, 20)
        time.sleep(0.4)
    video_b = video(time.time() + SKEW)                 # this one on local camera time
    time.sleep(1.0)
    call(args, "POST", f"/api/items/{b}/captured", station=True)

    upload(ftp, "A3.JPG", late)                         # slow uploads arrive after both captures
    upload(ftp, "CA.MP4", video_a)
    upload(ftp, "CB.MP4", video_b)
    time.sleep(6)

    ia, ib = call(args, "GET", f"/api/items/{a}"), call(args, "GET", f"/api/items/{b}")
    loose = [f for f in call(args, "GET", "/api/loose") if f["id"] not in loose_before]
    result = {
        "A photos": [p["name"] for p in ia["photos"]], "A videos": [v["name"] for v in ia["videos"]],
        "B photos": [p["name"] for p in ib["photos"]], "B videos": [v["name"] for v in ib["videos"]],
        "loose": sorted(f["name"] for f in loose),
        "A status": ia["status"], "B status": ib["status"],
        "camera offset": call(args, "GET", "/api/info")["camera_offset"],
    }
    want = {
        "A photos": ["A0.JPG", "A1.JPG", "A2.JPG", "A3.JPG"], "A videos": ["CA.MP4"],
        "B photos": ["B0.JPG", "B1.JPG", "B2.JPG", "B3.JPG"], "B videos": ["CB.MP4"],
        "loose": ["TEST0001.JPG", "TEST0002.JPG"],
    }
    for k, v in result.items():
        ok = "" if k not in want else ("OK  " if v == want[k] else "FAIL")
        print(f"{ok:4} {k}: {v}" + ("" if ok != "FAIL" else f"   expected {want[k]}"))
    return all(result[k] == v for k, v in want.items())


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
