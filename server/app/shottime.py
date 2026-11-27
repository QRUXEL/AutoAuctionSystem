"""When was a file shot, on the camera's own clock?

Both functions return "camera seconds": the camera's wall-clock reading as if it
were UTC (calendar.timegm of the naive time). The camera's clock is usually off
from the server's by some seconds or minutes, plus its time zone; ingest.py
measures that offset from upload times and converts.
"""
import calendar
import struct
from datetime import datetime
from pathlib import Path

from PIL import Image

EXIF_IFD = 0x8769
DATETIME_ORIGINAL = 36867
SUBSEC_ORIGINAL = 37521
DATETIME = 306


def photo_time(path: Path) -> float | None:
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            sub = exif.get_ifd(EXIF_IFD)
            stamp = sub.get(DATETIME_ORIGINAL) or exif.get(DATETIME)
            if not stamp:
                return None
            t = calendar.timegm(datetime.strptime(str(stamp).strip("\x00 "), "%Y:%m:%d %H:%M:%S").timetuple())
            frac = str(sub.get(SUBSEC_ORIGINAL) or "").strip("\x00 ")
            return t + (float("0." + frac) if frac.isdigit() else 0.0)
    except Exception:
        return None


MP4_EPOCH = calendar.timegm((1904, 1, 1, 0, 0, 0))


def _atoms(f, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head)
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr:
            return
        yield kind, pos + hdr, pos + size
        pos += size


def video_time(path: Path) -> float | None:
    """Recording start from the MP4/MOV header (moov/mvhd creation_time)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            for kind, body, end in _atoms(f, 0, size):
                if kind != b"moov":
                    continue
                for sub, sbody, _ in _atoms(f, body, end):
                    if sub != b"mvhd":
                        continue
                    f.seek(sbody)
                    version = f.read(1)[0]
                    f.read(3)
                    created = struct.unpack(">Q" if version == 1 else ">I", f.read(8 if version == 1 else 4))[0]
                    return float(MP4_EPOCH + created) if created else None
    except Exception:
        return None
    return None
