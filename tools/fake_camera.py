"""Stand-in for the camera: uploads generated product photos (and a dummy video) over FTP.

    python tools/fake_camera.py --host 127.0.0.1 --port 2121 --user camera --password change-me

Use it to test the server, framing and review page without any hardware.
"""
import argparse
import ftplib
import io
import random
import time

from PIL import Image, ImageDraw, ImageFilter


def photo(angle: int, seed: int) -> bytes:
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
    out = io.BytesIO()
    img.filter(ImageFilter.GaussianBlur(1.2)).save(out, "JPEG", quality=88)
    return out.getvalue()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2121)
    ap.add_argument("--user", default="camera")
    ap.add_argument("--password", default="change-me")
    ap.add_argument("--photos", type=int, default=4)
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--start", type=int, default=random.randint(1000, 9000), help="first file number")
    args = ap.parse_args()

    ftp = ftplib.FTP()
    ftp.connect(args.host, args.port, timeout=10)
    ftp.login(args.user, args.password)
    for n in range(args.photos):
        ftp.storbinary(f"STOR DSC0{args.start + n}.JPG", io.BytesIO(photo(n * 360 // args.photos, args.start)))
        print("uploaded photo", n + 1)
        time.sleep(0.2)
    if not args.no_video:
        ftp.storbinary(f"STOR C{args.start}.MP4", io.BytesIO(b"\x00" * 2048))
        print("uploaded video")
    ftp.quit()
