"""Crop each photo around the product and put it in the branded frame.

Everything visual is driven by branding/frame.json, branding/logo.png and an
optional branding/overlay.png (a transparent PNG the size of the canvas that is
laid on top of everything, for a designer-made frame).
"""
import json
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from .config import BRANDING

DEFAULT_FRAME = {
    "canvas": [1600, 1600],
    "border": 36,
    "border_color": "#1b1b3a",
    "background": "#ffffff",
    "footer_height": 150,
    "footer_color": "#1b1b3a",
    "footer_text": "",
    "footer_text_color": "#ffffff",
    "show_item_ref": True,
    "logo": "logo.png",
    "logo_width": 0.18,          # fraction of canvas width
    "logo_margin": 40,
    "logo_position": "top-left",  # top-left | top-right | footer-right
    "overlay": "overlay.png",
    "font": "",                   # path to a .ttf; empty = DejaVu Sans Bold
    "crop": {"mode": "auto", "padding": 0.12, "threshold": 28},  # mode: auto | center | none
}


def frame_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_FRAME))
    path = BRANDING / "frame.json"
    if path.exists():
        user = json.loads(path.read_text("utf-8"))
        crop = {**cfg["crop"], **user.pop("crop", {})}
        cfg.update(user)
        cfg["crop"] = crop
    return cfg


@lru_cache(maxsize=8)
def _font(path: str, size: int):
    for candidate in (path, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"):
        if not candidate:
            continue
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


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


def crop_photo(img: Image.Image, aspect: float, crop_cfg: dict) -> Image.Image:
    mode = crop_cfg.get("mode", "auto")
    if mode == "none":
        return img
    box = subject_box(img, crop_cfg.get("threshold", 28)) if mode == "auto" else None
    if box:
        pad = 1 + 2 * crop_cfg.get("padding", 0.12)
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        w, h = (box[2] - box[0]) * pad, (box[3] - box[1]) * pad
    else:
        cx, cy = img.width / 2, img.height / 2   # largest centred crop
        w, h =(img.width, img.width / aspect) if img.width / img.height < aspect else (img.height * aspect, img.height)
    return img.crop(_fit_box(cx, cy, w, h, aspect, img.width, img.height))


def photo_area(cfg: dict):
    cw, ch = cfg["canvas"]
    b = cfg["border"]
    return b, b, cw - b, ch - cfg["footer_height"] - (0 if cfg["footer_height"] else b)


def load(path: Path) -> Image.Image:
    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")


def cropped(path: Path, cfg: dict | None = None) -> Image.Image:
    cfg = cfg or frame_config()
    l, t, r, b = photo_area(cfg)
    return crop_photo(load(path), (r - l) / (b - t), cfg["crop"])


def frame_photo(path: Path, out: Path, item_ref: str = "", cfg: dict | None = None) -> Path:
    cfg = cfg or frame_config()
    cw, ch = cfg["canvas"]
    l, t, r, b = photo_area(cfg)
    canvas = Image.new("RGB", (cw, ch), cfg["border_color"])
    canvas.paste(Image.new("RGB", (r - l, b - t), cfg["background"]), (l, t))
    photo = cropped(path, cfg)
    if cfg["crop"].get("mode") == "none":
        photo = ImageOps.contain(photo, (r - l, b - t), Image.LANCZOS)
        canvas.paste(photo, (l + (r - l - photo.width) // 2, t + (b - t - photo.height) // 2))
    else:
        canvas.paste(photo.resize((r - l, b - t), Image.LANCZOS), (l, t))

    draw = ImageDraw.Draw(canvas)
    fh = cfg["footer_height"]
    if fh:
        draw.rectangle((0, ch - fh, cw, ch), fill=cfg["footer_color"])
        font = _font(cfg.get("font", ""), int(fh * 0.38))
        mid = ch - fh // 2
        if cfg.get("footer_text"):
            draw.text((cfg["border"] + 24, mid), cfg["footer_text"], font=font, fill=cfg["footer_text_color"], anchor="lm")
        if cfg.get("show_item_ref") and item_ref and cfg.get("logo_position") != "footer-right":
            draw.text((cw - cfg["border"] - 24, mid), item_ref, font=font, fill=cfg["footer_text_color"], anchor="rm")

    logo_path = BRANDING / cfg.get("logo", "")
    if cfg.get("logo") and logo_path.exists():
        logo = Image.open(logo_path).convert("RGBA")
        lw = int(cw * cfg["logo_width"])
        logo = logo.resize((lw, max(1, round(logo.height * lw / logo.width))), Image.LANCZOS)
        m = cfg["logo_margin"]
        pos = cfg.get("logo_position", "top-left")
        if pos == "footer-right" and fh:
            lh = int(fh * 0.7)
            logo = logo.resize((max(1, round(logo.width * lh / logo.height)), lh), Image.LANCZOS)
            xy = (cw - cfg["border"] - 24 - logo.width, ch - fh // 2 - lh // 2)
        elif pos == "top-right":
            xy = (r - m - logo.width, t + m)
        else:
            xy = (l + m, t + m)
        canvas.paste(logo, xy, logo)

    overlay_path = BRANDING / cfg.get("overlay", "")
    if cfg.get("overlay") and overlay_path.exists():
        overlay = Image.open(overlay_path).convert("RGBA").resize((cw, ch), Image.LANCZOS)
        canvas.paste(overlay, (0, 0), overlay)

    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, "JPEG", quality=90, optimize=True)
    return out
