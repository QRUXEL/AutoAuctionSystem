"""Photo layout: the frame, logo, texts and bars put on every listing photo.

Stored in BRANDING/layout.json and edited on the site's Layout page. Built so one
layout works for photos of any shape:

* Every length is in "units": 1 unit = 1 % of the photo's short side. A logo
  12 units high is 12 % of the short side whether the photo is portrait,
  landscape or square.
* Every element is pinned to an anchor (top-left ... bottom-right) of a box:
  the whole canvas, the photo, or one of the four margins around the photo.
  Its offset is measured inward from that anchor.
* The output has a fixed long side in pixels; its shape is either the photo's
  own shape (after cropping) or a fixed ratio the photo is cropped to.
"""
import copy
import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

from .config import BRANDING

ASSETS = BRANDING / "assets"
LAYOUT_FILE = BRANDING / "layout.json"
SHAPES = {"photo": None, "1:1": 1.0, "4:3": 4 / 3, "3:4": 3 / 4, "3:2": 3 / 2, "2:3": 2 / 3, "16:9": 16 / 9}
ANCHORS = {"top-left", "top", "top-right", "left", "center", "right", "bottom-left", "bottom", "bottom-right"}
BOXES = {"canvas", "photo", "margin-top", "margin-bottom", "margin-left", "margin-right"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
FONT_EXT = {".ttf", ".otf"}
DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DEFAULT_FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

DEFAULT = {
    "version": 1,
    "canvas": {"shape": "photo", "long_side": 1600, "background": "#ffffff"},
    "margins": {"top": 2.5, "right": 2.5, "bottom": 10, "left": 2.5, "color": "#1b1b3a"},
    "crop": {"auto": True, "padding": 12},
    "adjust": {"brightness": 0, "contrast": 0},
    "elements": [
        {"id": "logo", "type": "image", "name": "Logo", "visible": True, "asset": "logo.png",
         "box": "photo", "anchor": "top-left", "x": 3, "y": 3, "height": 9, "opacity": 100},
        {"id": "name", "type": "text", "name": "Auction name", "visible": True, "text": "Your auction name",
         "box": "margin-bottom", "anchor": "left", "x": 3, "y": 0, "size": 4.2, "color": "#ffffff",
         "bold": True, "shadow": False, "opacity": 100},
        {"id": "ref", "type": "text", "name": "Item number", "visible": True, "text": "{ref}",
         "box": "margin-bottom", "anchor": "right", "x": 3, "y": 0, "size": 4.2, "color": "#ffffff",
         "bold": True, "shadow": False, "opacity": 100},
    ],
}


# ---- storage --------------------------------------------------------------------------
def _from_frame_json() -> dict:
    """First start after the update: carry over colours, footer text and logo from frame.json."""
    layout = copy.deepcopy(DEFAULT)
    old = BRANDING / "frame.json"
    if old.exists():
        try:
            f = json.loads(old.read_text("utf-8"))
            layout["margins"]["color"] = f.get("border_color", layout["margins"]["color"])
            for el in layout["elements"]:
                if el["id"] == "name" and f.get("footer_text"):
                    el["text"] = f["footer_text"]
                if el["type"] == "text":
                    el["color"] = f.get("footer_text_color", el["color"])
                if el["id"] == "ref":
                    el["visible"] = bool(f.get("show_item_ref", True))
        except (ValueError, OSError):
            pass
    ASSETS.mkdir(parents=True, exist_ok=True)
    logo = BRANDING / "logo.png"
    if logo.exists() and not (ASSETS / "logo.png").exists():
        (ASSETS / "logo.png").write_bytes(logo.read_bytes())
    return layout


def load() -> dict:
    if LAYOUT_FILE.exists():
        try:
            return validate(json.loads(LAYOUT_FILE.read_text("utf-8")))
        except (ValueError, OSError):
            pass
    return validate(_from_frame_json())


def save(layout: dict) -> dict:
    layout = validate(layout)
    BRANDING.mkdir(parents=True, exist_ok=True)
    tmp = LAYOUT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(layout, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(LAYOUT_FILE)
    return layout


def _num(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _color(v, default):
    try:
        ImageColor.getrgb(str(v))
        return str(v)
    except ValueError:
        return default


def validate(raw: dict) -> dict:
    """Normalises a layout, clamping numbers and dropping anything unknown."""
    if not isinstance(raw, dict):
        raise ValueError("Layout must be an object")
    d = DEFAULT
    c = raw.get("canvas", {}) or {}
    m = raw.get("margins", {}) or {}
    cr = raw.get("crop", {}) or {}
    out = {
        "version": 1,
        "canvas": {
            "shape": c.get("shape") if c.get("shape") in SHAPES else "photo",
            "long_side": int(_num(c.get("long_side"), 400, 4000, 1600)),
            "background": _color(c.get("background"), d["canvas"]["background"]),
        },
        "margins": {side: _num(m.get(side), 0, 60, 0) for side in ("top", "right", "bottom", "left")}
        | {"color": _color(m.get("color"), d["margins"]["color"])},
        "crop": {"auto": bool(cr.get("auto", True)), "padding": _num(cr.get("padding"), 0, 60, 12)},
        "adjust": clean_adjust(raw.get("adjust")),
        "elements": [],
    }
    for i, e in enumerate(raw.get("elements") or []):
        if not isinstance(e, dict) or e.get("type") not in ("text", "image", "bar"):
            continue
        el = {
            "id": re.sub(r"[^\w-]", "", str(e.get("id") or f"el{i}"))[:40] or f"el{i}",
            "type": e["type"],
            "name": str(e.get("name") or e["type"].title())[:60],
            "visible": bool(e.get("visible", True)),
            "box": e.get("box") if e.get("box") in BOXES else "canvas",
            "opacity": _num(e.get("opacity"), 0, 100, 100),
        }
        if e["type"] == "bar":
            el["edge"] = e.get("edge") if e.get("edge") in ("top", "bottom", "left", "right") else "bottom"
            el["thickness"] = _num(e.get("thickness"), 0.1, 100, 10)
            el["color"] = _color(e.get("color"), "#000000")
        else:
            el["anchor"] = e.get("anchor") if e.get("anchor") in ANCHORS else "top-left"
            el["x"] = _num(e.get("x"), -100, 100, 0)
            el["y"] = _num(e.get("y"), -100, 100, 0)
        if e["type"] == "text":
            el["text"] = str(e.get("text") or "")[:300]
            el["size"] = _num(e.get("size"), 0.5, 40, 4)
            el["color"] = _color(e.get("color"), "#ffffff")
            el["bold"] = bool(e.get("bold", True))
            el["shadow"] = bool(e.get("shadow", False))
            el["font"] = _asset_name(e.get("font")) if e.get("font") else ""
        if e["type"] == "image":
            el["asset"] = _asset_name(e.get("asset")) if e.get("asset") else ""
            el["height"] = _num(e.get("height"), 0.5, 100, 10)
        out["elements"].append(el)
    return out


def clean_adjust(raw) -> dict:
    """Brightness and contrast in % (-100 to +100; 0 = unchanged)."""
    raw = raw if isinstance(raw, dict) else {}
    return {k: round(_num(raw.get(k), -100, 100, 0)) for k in ("brightness", "contrast")}


def apply_adjust(img: Image.Image, *adjustments: dict) -> Image.Image:
    """Applies brightness/contrast adjustments one after another (layout default, then the photo's own)."""
    from PIL import ImageEnhance
    for adj in adjustments:
        if not adj:
            continue
        if adj.get("brightness"):
            img = ImageEnhance.Brightness(img).enhance(max(0.0, 1 + adj["brightness"] / 100))
        if adj.get("contrast"):
            img = ImageEnhance.Contrast(img).enhance(max(0.0, 1 + adj["contrast"] / 100))
    return img


# ---- assets (logos, graphics, fonts) ------------------------------------------------------
def _asset_name(name) -> str:
    name = Path(str(name)).name
    if not re.fullmatch(r"[\w.\- ]{1,80}", name) or Path(name).suffix.lower() not in IMAGE_EXT | FONT_EXT:
        raise ValueError(f"Bad file name: {name}")
    return name


def list_assets() -> list:
    ASSETS.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(ASSETS.iterdir()):
        ext = p.suffix.lower()
        if p.is_file() and ext in IMAGE_EXT | FONT_EXT:
            out.append({"name": p.name, "kind": "font" if ext in FONT_EXT else "image", "size": p.stat().st_size})
    return out


def add_asset(name: str, data: bytes) -> str:
    name = _asset_name(name)
    if len(data) > 15 * 1024 * 1024:
        raise ValueError("File is larger than 15 MB")
    ext = Path(name).suffix.lower()
    tmp = ASSETS / (".upload-" + name)
    ASSETS.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    try:
        if ext in FONT_EXT:
            ImageFont.truetype(str(tmp), 20)
        else:
            with Image.open(tmp) as im:
                im.verify()
    except Exception:
        tmp.unlink(missing_ok=True)
        raise ValueError("That file is not a readable image or font")
    tmp.replace(ASSETS / name)
    _image.cache_clear()
    _font.cache_clear()
    return name


def asset_path(name: str) -> Path:
    return ASSETS / _asset_name(name)


def delete_asset(name: str):
    asset_path(name).unlink(missing_ok=True)
    _image.cache_clear()
    _font.cache_clear()


@lru_cache(maxsize=16)
def _image(name: str, _mtime: float) -> Image.Image | None:
    try:
        return Image.open(ASSETS / name).convert("RGBA")
    except Exception:
        return None


def _load_image(name: str) -> Image.Image | None:
    p = ASSETS / name
    return _image(name, p.stat().st_mtime) if name and p.exists() else None


@lru_cache(maxsize=32)
def _font(name: str, bold: bool, px: int):
    candidates = [str(ASSETS / name)] if name else []
    candidates += [DEFAULT_FONT if bold else DEFAULT_FONT_REGULAR, "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"]
    for c in candidates:
        try:
            return ImageFont.truetype(c, max(4, px))
        except OSError:
            continue
    return ImageFont.load_default(max(4, px))


# ---- rendering ----------------------------------------------------------------------------
def crop_aspect(layout: dict) -> float | None:
    """Shape the photo is cropped to; None = keep the photo's own shape."""
    return SHAPES[layout["canvas"]["shape"]]


def geometry(photo_aspect: float, layout: dict):
    """Pixel sizes for a photo of the given shape: (canvas w, h), photo box, unit in px."""
    m = layout["margins"]
    a = photo_aspect
    short = min(a, 1.0)                      # photo short side when its height is 1
    coef_w = a + (m["left"] + m["right"]) * short / 100
    coef_h = 1 + (m["top"] + m["bottom"]) * short / 100
    k = layout["canvas"]["long_side"] / max(coef_w, coef_h)   # photo height in px
    unit = short * k / 100
    pw, ph = round(a * k), round(k)
    x0, y0 = round(m["left"] * unit), round(m["top"] * unit)
    cw = x0 + pw + round(m["right"] * unit)
    ch = y0 + ph + round(m["bottom"] * unit)
    return (cw, ch), (x0, y0, x0 + pw, y0 + ph), unit


def _boxes(cw, ch, photo):
    x0, y0, x1, y1 = photo
    return {
        "canvas": (0, 0, cw, ch), "photo": photo,
        "margin-top": (0, 0, cw, y0), "margin-bottom": (0, y1, cw, ch),
        "margin-left": (0, 0, x0, ch), "margin-right": (x1, 0, cw, ch),
    }


def _place(anchor, box, w, h, ox, oy):
    bx0, by0, bx1, by1 = box
    if anchor.endswith("left"):
        x = bx0 + ox
    elif anchor.endswith("right"):
        x = bx1 - ox - w
    else:
        x = (bx0 + bx1 - w) / 2 + ox
    if anchor.startswith("top"):
        y = by0 + oy
    elif anchor.startswith("bottom"):
        y = by1 - oy - h
    else:
        y = (by0 + by1 - h) / 2 + oy
    return round(x), round(y)


def _rgba(color: str, opacity: float):
    r, g, b = ImageColor.getrgb(color)[:3]
    return r, g, b, round(255 * opacity / 100)


def fill_text(text: str, variables: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(variables.get(m.group(1), m.group(0))), text)


def render(photo: Image.Image, layout: dict, variables: dict | None = None) -> Image.Image:
    """photo: the already cropped photo. Returns the finished RGB image."""
    variables = {"date": date.today().isoformat(), **(variables or {})}
    (cw, ch), pbox, unit = geometry(photo.width / photo.height, layout)
    canvas = Image.new("RGBA", (cw, ch), _rgba(layout["margins"]["color"], 100))
    bg = Image.new("RGBA", (pbox[2] - pbox[0], pbox[3] - pbox[1]), _rgba(layout["canvas"]["background"], 100))
    canvas.alpha_composite(bg, (pbox[0], pbox[1]))
    canvas.alpha_composite(photo.convert("RGBA").resize((pbox[2] - pbox[0], pbox[3] - pbox[1]), Image.LANCZOS), (pbox[0], pbox[1]))
    boxes = _boxes(cw, ch, pbox)

    for el in layout["elements"]:
        if not el["visible"]:
            continue
        box = boxes[el["box"]]
        bw, bh = box[2] - box[0], box[3] - box[1]
        if bw <= 0 or bh <= 0:
            continue
        layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        if el["type"] == "bar":
            t = round(el["thickness"] * unit)
            rect = {"top": (box[0], box[1], box[2], box[1] + t), "bottom": (box[0], box[3] - t, box[2], box[3]),
                    "left": (box[0], box[1], box[0] + t, box[3]), "right": (box[2] - t, box[1], box[2], box[3])}[el["edge"]]
            ImageDraw.Draw(layer).rectangle(rect, fill=_rgba(el["color"], el["opacity"]))
        elif el["type"] == "text":
            text = fill_text(el["text"], variables)
            if not text.strip():
                continue
            px = round(el["size"] * unit)
            room = bw - 2 * max(0, el["x"]) * unit        # shrink long text to fit its box
            font = _font(el.get("font", ""), el["bold"], px)
            l, t, r, b = ImageDraw.Draw(layer).textbbox((0, 0), text, font=font)
            while r - l > room and px > 6:
                px = int(px * 0.92)
                font = _font(el.get("font", ""), el["bold"], px)
                l, t, r, b = ImageDraw.Draw(layer).textbbox((0, 0), text, font=font)
            x, y = _place(el["anchor"], box, r - l, b - t, el["x"] * unit, el["y"] * unit)
            draw = ImageDraw.Draw(layer)
            if el["shadow"]:
                shadow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                off = max(1, round(px * 0.05))
                ImageDraw.Draw(shadow).text((x - l + off, y - t + off), text, font=font, fill=(0, 0, 0, round(160 * el["opacity"] / 100)))
                layer.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(max(1, px * 0.04))))
            draw.text((x - l, y - t), text, font=font, fill=_rgba(el["color"], el["opacity"]))
        elif el["type"] == "image":
            src = _load_image(el["asset"])
            if src is None:
                continue
            h = max(1, round(el["height"] * unit))
            w = max(1, round(src.width * h / src.height))
            img = src.resize((w, h), Image.LANCZOS)
            if el["opacity"] < 100:
                alpha = img.getchannel("A").point(lambda a: round(a * el["opacity"] / 100))
                img.putalpha(alpha)
            x, y = _place(el["anchor"], box, w, h, el["x"] * unit, el["y"] * unit)
            layer.alpha_composite(img, (max(0, x), max(0, y)), (max(0, -x), max(0, -y)))
        canvas.alpha_composite(layer)
    return canvas.convert("RGB")
