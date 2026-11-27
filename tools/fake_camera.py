"""Stand-in for the camera: uploads generated product photos (and a small MP4) over FTP.

    python tools/fake_camera.py --host 127.0.0.1 --port 2121 --user camera --password change-me

Photos carry an EXIF shot time and the video an MP4 header time, like the A7 IV's,
on a camera clock that can be skewed (--camera-skew) to test the server's matching.
"""
import argparse
import calendar
import ftplib
import io
import random
import struct
import time

from PIL import Image, ImageDraw, ImageFilter

MP4_EPOCH = calendar.timegm((1904, 1, 1, 0, 0, 0))


def photo(angle: int, seed: int, shot_wall: float | None = None) -> bytes:
    """A JPEG of a vase-like object; shot_wall = the camera's wall-clock time as epoch seconds."""
    rng = random.Random(seed)
    w, h = 3000, 2000
    img = Image.new("RGB", (w, h), (203, 204, 208))
    d = ImageDraw.Draw(img)
    for y in range(h):                                   # uneven backdrop, like a real sweep
        shade = 196 + int(14 * y / h)
        d.line((0, y, w, y), fill=(shade, shade, shade + 3))
    cx, cy = w * 0.62, h * 0.55                          # deliberately off-centre
    body = (rng.randint(120, 200), rng.randint(40, 90), rng.randint(30, 70))
    wide = 420 + 160 * abs((angle % 180) - 90) / 90      # looks narrower from the side
    d.rounded_rectangle((cx - wide / 2, cy - 420, cx + wide / 2, cy + 420), 60, fill=body)
    d.ellipse((cx - wide / 2, cy - 520, cx + wide / 2, cy - 320), fill=tuple(min(255, c + 40) for c in body))
    d.rectangle((cx - 60, cy - 640, cx + 60, cy - 500), fill=(40, 40, 46))
    d.text((cx - 80, cy), f"{angle}°", fill="white", font_size=120)
    exif = Image.Exif()
    if shot_wall is not None:
        stamp = time.strftime("%Y:%m:%d %H:%M:%S", time.gmtime(shot_wall))
        exif[306] = stamp
        sub = exif.get_ifd(0x8769)
        sub[36867] = stamp
        sub[37521] = f"{int((shot_wall % 1) * 100):02d}"
    out = io.BytesIO()
    img.filter(ImageFilter.GaussianBlur(1.2)).save(out, "JPEG", quality=88, exif=exif)
    return out.getvalue()


def _atom(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(body), kind) + body


def video(start_header: float) -> bytes:
    """A tiny MP4: ftyp + moov/mvhd (creation_time = start_header) + some mdat."""
    created = int(start_header - MP4_EPOCH)
    mvhd = bytes([0, 0, 0, 0]) + struct.pack(">IIII", created, created, 1000, 20000) + bytes(80)
    return _atom(b"ftyp", b"XAVC" + bytes(4) + b"XAVCmp42iso2") + _atom(b"moov", _atom(b"mvhd", mvhd)) + _atom(b"mdat", bytes(4096))


def upload(args, name: str, data: bytes):
    ftp = ftplib.FTP()
    ftp.connect(args.host, args.port, timeout=10)
    ftp.login(args.user, args.password)
    ftp.storbinary(f"STOR {name}", io.BytesIO(data))
    ftp.quit()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2121)
    ap.add_argument("--user", default="camera")
    ap.add_argument("--password", default="change-me")
    ap.add_argument("--photos", type=int, default=4)
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--camera-skew", type=float, default=0, help="camera clock minus real time, seconds")
    ap.add_argument("--start", type=int, default=random.randint(1000, 9000), help="first file number")
    args = ap.parse_args()

    for n in range(args.photos):
        upload(args, f"DSC0{args.start + n}.JPG", photo(n * 360 // args.photos, args.start, time.time() + args.camera_skew))
        print("uploaded photo", n + 1)
        time.sleep(0.3)
    if not args.no_video:
        upload(args, f"C{args.start}.MP4", video(time.time() + args.camera_skew))
        print("uploaded video")
