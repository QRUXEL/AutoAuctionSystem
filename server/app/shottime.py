"""When was a file shot, on the camera's own clock?

Both functions return "camera seconds": the camera's wall-clock reading as if it
were UTC (calendar.timegm of the naive time). The camera's clock is usually off
from the server's by some seconds or minutes, plus its time zone; ingest.py
measures that offset from upload times and converts.
"""
import calendar
import re
import struct
from datetime import datetime
from pathlib import Path

from PIL import Image

from . import imaging  # noqa: F401  (registers the HEIF opener)

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


_XML_TIME = re.compile(rb'<CreationDate\s+value="([^"]+)"|xmp:CreateDate[=>]"?([0-9T:+\-.Z]{19,})')


def _xml_time(blob: bytes) -> float | None:
    """Sony (XAVC S) keeps the recording start as <CreationDate value="2026-11-27T15:28:40+01:00"/> in an XML block."""
    m = _XML_TIME.search(blob)
    if not m:
        return None
    stamp = (m.group(1) or m.group(2)).decode(errors="ignore")
    # the camera's wall-clock reading, like EXIF: the time-zone part is dropped
    return float(calendar.timegm(datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S").timetuple()))


def video_time(path: Path) -> float | None:
    """Recording start: moov/mvhd creation_time, else the XML metadata Sony cameras embed."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            blobs = []
            for kind, body, end in _atoms(f, 0, size):
                if kind in (b"meta", b"uuid", b"udta", b"XMP_"):        # top-level metadata boxes
                    f.seek(body)
                    blobs.append(f.read(min(end - body, 4 << 20)))
                if kind != b"moov":
                    continue
                for sub, sbody, send in _atoms(f, body, end):
                    if sub == b"mvhd":
                        f.seek(sbody)
                        version = f.read(1)[0]
                        f.read(3)
                        created = struct.unpack(">Q" if version == 1 else ">I", f.read(8 if version == 1 else 4))[0]
                        # 0 means "not set"; anything before 2000 is a camera without a set clock
                        if created and MP4_EPOCH + created > 946684800:
                            return float(MP4_EPOCH + created)
                    elif sub in (b"meta", b"udta", b"uuid"):
                        f.seek(sbody)
                        blobs.append(f.read(min(send - sbody, 4 << 20)))
            for blob in blobs:
                t = _xml_time(blob)
                if t:
                    return t
            # last resort: the first and last 2 MB, where cameras put their metadata
            f.seek(0)
            blobs = [f.read(2 << 20)]
            f.seek(max(0, size - (2 << 20)))
            blobs.append(f.read())
            for blob in blobs:
                t = _xml_time(blob)
                if t:
                    return t
    except Exception:
        return None
    return None
