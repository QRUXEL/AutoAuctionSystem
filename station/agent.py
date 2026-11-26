"""Station agent: runs on the PC beside the photo table.

It waits for the ESP32 button (HTTP POST /trigger), then runs one capture:
  photo, turn 90 deg, photo, turn 90 deg, photo, turn 90 deg, photo, turn 90 deg (back at the front),
  start video, turn 360 deg, stop video.
The camera uploads the files to the server by itself over Wi-Fi (FTP).

Run:   python agent.py                 (real hardware)
       python agent.py --simulate      (no turntable, no camera; for testing the chain)
       python agent.py --jog 90        (turn the table 90 deg and exit; for checking direction and accuracy)
"""
import argparse
import json
import logging
import threading
import time
import tomllib
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger("station")


def http(method: str, url: str, body: dict | None = None, headers: dict | None = None, timeout: float = 10):
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw.strip().startswith(b"{") else {}


# ---- turntable --------------------------------------------------------------
class Turntable:
    """edelkrone HeadONE with the turntable kit, driven through its pan axis.

    Release 0.1 of the edelkrone SDK has no "turn by N degrees" command, so this
    drives the joystick and watches the reported pan angle until the turn is done.
    """

    def __init__(self, cfg: dict):
        import edelkrone
        self.cfg = cfg
        self.device = edelkrone.connect(name=cfg.get("name") or None, mac=cfg.get("mac") or None, timeout=30)
        log.info("turntable connected: %s, axes %s, battery %s%%", self.device.info.name,
                 [str(a) for a in self.device.info.axes], self.device.status.battery_percent)

    def _pan(self) -> float:
        pan = self.device.status.pose.pan_deg
        if pan is None:
            raise RuntimeError("The head reports no pan angle. Mount the HeadONE as a pan head and update its firmware.")
        return pan

    def rotate(self, degrees: float, speed: float):
        direction = 1 if self.cfg.get("clockwise", True) else -1
        slow_zone, min_speed = self.cfg.get("slow_zone_deg", 12), self.cfg.get("min_speed", 0.06)
        stop_early = self.cfg.get("stop_early_deg", 0.4)
        self.device.refresh()
        last, travelled, last_progress = self._pan(), 0.0, time.monotonic()
        try:
            while travelled < degrees - stop_early:
                remaining = degrees - travelled
                v = speed if remaining > slow_zone else max(min_speed, speed * remaining / slow_zone)
                self.device.move.joystick(pan=direction * v)   # returns with a fresh reading
                now = self._pan()
                step = (now - last + 180) % 360 - 180           # works whether or not the angle wraps at 360
                last = now
                if abs(step) > 0.02:
                    last_progress = time.monotonic()
                travelled += step * direction
                if time.monotonic() - last_progress > 4:
                    raise RuntimeError("Turntable is not moving (blocked, switched off or out of battery?)")
                time.sleep(0.04)
        finally:
            self.device.move.stop()
        log.info("turned %.1f of %.0f deg", travelled, degrees)

    def fire_shutter(self):
        self.device.settings.fire_shutter()

    def close(self):
        self.device.disconnect()


class SimTurntable:
    def rotate(self, degrees, speed):
        log.info("[sim] turning %s deg", degrees)
        time.sleep(0.2)

    def fire_shutter(self):
        log.info("[sim] shutter cable")

    def close(self):
        pass


# ---- camera -----------------------------------------------------------------
class Esp32Camera:
    """The ESP32 holds the Bluetooth link to the Sony camera; we ask it over Wi-Fi."""

    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def ready(self) -> bool:
        return bool(http("GET", self.url + "/status", timeout=4).get("camera"))

    def shutter(self):
        http("POST", self.url + "/shutter")

    def record(self):
        http("POST", self.url + "/record")


class SimCamera:
    def ready(self):
        return True

    def shutter(self):
        log.info("[sim] photo")

    def record(self):
        log.info("[sim] record start/stop")


# ---- one capture --------------------------------------------------------------
class Station:
    def __init__(self, cfg: dict, simulate: bool):
        self.cfg, self.busy, self.last = cfg, threading.Lock(), "idle"
        self.table = SimTurntable() if simulate else Turntable(cfg["turntable"])
        self.camera = SimCamera() if simulate else Esp32Camera(cfg["camera"]["esp32_url"])

    def _server(self, method, path, body=None):
        return http(method, self.cfg["server"]["url"].rstrip("/") + path, body,
                    {"X-Station-Key": self.cfg["server"]["station_key"]})

    def trigger(self) -> bool:
        if not self.busy.acquire(blocking=False):
            return False
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _run(self):
        seq = self.cfg["sequence"]
        try:
            self.last = "running"
            if not self.camera.ready():
                raise RuntimeError("Camera is not connected to the ESP32 over Bluetooth")
            video = seq.get("video", True)
            item = self._server("POST", "/api/items", {"photos": seq["photos"], "videos": 1 if video else 0})["id"]
            log.info("item %s: capture started", item)
            step = 360 / seq["photos"]
            for n in range(seq["photos"]):
                time.sleep(seq["settle_before_photo"])
                self.camera.shutter()
                time.sleep(seq["wait_after_photo"])
                self.table.rotate(step, seq["photo_turn_speed"])
            if video:
                time.sleep(seq["settle_before_photo"])
                self.camera.record()
                time.sleep(seq["video_lead_seconds"])
                self.table.rotate(360, seq["video_turn_speed"])
                time.sleep(seq["video_lead_seconds"])
                self.camera.record()
            self._server("POST", f"/api/items/{item}/captured")
            self.last = f"item {item} captured"
            log.info(self.last)
        except Exception as exc:
            self.last = f"error: {exc}"
            log.exception("capture failed")
        finally:
            self.busy.release()


def serve(station: Station, port: int):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path == "/trigger":
                started = station.trigger()
                self._send(202 if started else 409, {"started": started})
            else:
                self._send(404, {})

        def do_GET(self):
            self._send(200, {"busy": station.busy.locked(), "last": station.last})

        def log_message(self, *args):
            pass

    log.info("waiting for the button on port %s", port)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.toml")))
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--jog", type=float, help="turn the table this many degrees and exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cfg = tomllib.loads(Path(args.config).read_text("utf-8"))
    if args.jog:
        table = Turntable(cfg["turntable"])
        table.rotate(args.jog, cfg["sequence"]["photo_turn_speed"])
        table.close()
    else:
        serve(Station(cfg, args.simulate), cfg.get("listen_port", 8765))
