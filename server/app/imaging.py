"""Crop each photo around the product and put it in the branded frame.

The frame, logo and texts come from the photo layout (layout.py), edited on the
site's Layout page.
"""
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

from . import layout

try:                                   # HEIF photos (.HIF) from Sony cameras
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

def frame_config() -> dict:
    """The current photo layout (see layout.py)."""
    return layout.load()


def _fit_box(cx, cy, w, h, aspect, img_w, img_h):
    """Smallest box of the given aspect containing a w*h box centred on (cx, cy), kept inside the image."""
    if w / h < aspect:
        w = h * aspect
    else:
        h = w / aspect
    scale = min(1.0, img_w / w, img_h / h)
    w, h = w * scale, h * scale
    left = min(max(cx - w / 2, 0), img_w - w)
    top = min(max(cy - h / 2, 0), img_h - h)
    return tuple(round(v) for v in (left, top, left + w, top + h))


def subject_box(img: Image.Image, threshold: int):
    """Bounding box of whatever differs from the backdrop, or None if it can't tell."""
    scale = 400 / max(img.size)
    small = img.convert("RGB").resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))))
    w, h = small.size
    edge = max(2, w // 40)
    strips = Image.new("RGB", (w * 2 + h * 2, edge))
    strips.paste(small.crop((0, 0, w, edge)), (0, 0))
    strips.paste(small.crop((0, h - edge, w, h)), (w, 0))
    strips.paste(small.crop((0, 0, edge, h)).rotate(90, expand=True), (w * 2, 0))
    strips.paste(small.crop((w - edge, 0, w, h)).rotate(90, expand=True), (w * 2 + h, 0))
    backdrop = tuple(int(v) for v in ImageStat.Stat(strips).median)
    diff = ImageChops.difference(small, Image.new("RGB", small.size, backdrop)).convert("L")
    mask = diff.point(lambda p: 255 if p > threshold else 0).filter(ImageFilter.MedianFilter(5))
    box = mask.getbbox()
    if not box:
        return None
    area = (box[2] - box[0]) * (box[3] - box[1]) / (w * h)
    if area < 0.01 or area > 0.97:
        return None
    return tuple(v / scale for v in box)


def auto_box(img: Image.Image, aspect: float, crop_cfg: dict):
    """Pixel box (l, t, r, b) the automatic crop would use, or None for mode 'none'."""
    mode = crop_cfg.get("mode", "auto")
    if mode == "none":
        return None
    box = subject_box(img, crop_cfg.get("threshold", 28)) if mode == "auto" else None
    if box:
        pad = 1 + 2 * crop_cfg.get("padding", 0.12)
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        w, h = (box[2] - box[0]) * pad, (box[3] - box[1]) * pad
    else:
        cx, cy = img.width / 2, img.height / 2   # largest centred crop
        w, h = (img.width, img.width / aspect) if img.width / img.height < aspect else (img.height * aspect, img.height)
    return _fit_box(cx, cy, w, h, aspect, img.width, img.height)


def to_fractions(box, img) -> dict:
    l, t, r, b = box
    return {"x": l / img.width, "y": t / img.height, "w": (r - l) / img.width, "h": (b - t) / img.height}


def from_fractions(f: dict, img):
    l = max(0.0, min(1.0, f["x"])) * img.width
    t = max(0.0, min(1.0, f["y"])) * img.height
    r = min(img.width, l + max(0.001, f["w"]) * img.width)
    b = min(img.height, t + max(0.001, f["h"]) * img.height)
    return round(l), round(t), round(r), round(b)


def photo_aspect(cfg: dict | None = None) -> float | None:
    """Shape photos are cropped to, or None when each photo keeps its own shape."""
    return layout.crop_aspect(cfg or frame_config())


def load(path: Path) -> Image.Image:
    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")


def crop_image(img: Image.Image, cfg: dict, manual: dict | None = None):
    """Returns (cropped image, crop box as fractions, or None when the photo is used whole)."""
    if manual:
        box = from_fractions(manual, img)
    else:
        aspect = layout.crop_aspect(cfg)
        if cfg["crop"]["auto"]:
            box = auto_box(img, aspect or img.width / img.height,
                           {"mode": "auto", "padding": cfg["crop"]["padding"] / 100, "threshold": 28})
        elif aspect:
            box = auto_box(img, aspect, {"mode": "center"})       # largest centred crop of that shape
        else:
            return img, None
    return img.crop(box), to_fractions(box, img)


def cropped(path: Path, cfg: dict | None = None, manual: dict | None = None):
    return crop_image(load(path), cfg or frame_config(), manual)


def preview(path: Path, out: Path, size: int = 2000) -> Path:
    """Upright, downscaled copy for the crop tool."""
    img = load(path)
    img.thumbnail((size, size), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=85)
    return out


def frame_image(photo: Image.Image, cfg: dict, item_ref: str = "", adjust: dict | None = None) -> Image.Image:
    """Cropped photo -> adjusted (layout default, then the photo's own) -> framed."""
    return layout.render(layout.apply_adjust(photo, cfg.get("adjust"), adjust), cfg, {"ref": item_ref})


def frame_photo(path: Path, out: Path, item_ref: str = "", cfg: dict | None = None, manual: dict | None = None,
                adjust: dict | None = None):
    """Writes the framed photo. Returns the crop used as fractions (None if the photo was not cropped)."""
    cfg = cfg or frame_config()
    photo, used = cropped(path, cfg, manual)
    framed = frame_image(photo, cfg, item_ref, adjust)
    out.parent.mkdir(parents=True, exist_ok=True)
    framed.save(out, "JPEG", quality=90, optimize=True)
    return used


def layout_preview(layout_cfg: dict, source: Path | None, test_aspect: float | None, long_side: int = 900) -> bytes:
    """A framed sample for the layout editor. test_aspect first trims the sample to that
    shape, to show how the layout behaves on landscape, portrait or square photos."""
    import io
    if source is not None and source.exists():
        img = load(source)
        img.thumbnail((1800, 1800), Image.LANCZOS)
    else:
        img = _placeholder()
    if test_aspect:
        img = img.crop(auto_box(img, test_aspect, {"mode": "center"}))
    cfg = {**layout_cfg, "canvas": {**layout_cfg["canvas"], "long_side": long_side}}
    photo, _ = crop_image(img, cfg)
    buf = io.BytesIO()
    frame_image(photo, cfg, "#0123").save(buf, "JPEG", quality=82)
    return buf.getvalue()


def _placeholder() -> Image.Image:
    from PIL import ImageDraw
    img = Image.new("RGB", (1800, 1200), (205, 206, 210))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((700, 300, 1100, 1000), 60, fill=(150, 70, 55))
    d.ellipse((700, 220, 1100, 400), fill=(185, 110, 90))
    return img
