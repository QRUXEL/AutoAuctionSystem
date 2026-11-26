"""Ask Claude to look at the photos and draft the listing."""
import base64
import io

from PIL import Image

from . import config, tradera

LISTING_TOOL = {
    "name": "listing",
    "description": "Return the draft auction listing.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Listing title, at most 80 characters, most searchable words first."},
            "description": {"type": "string", "description": "Listing body in plain text with short paragraphs."},
            "category_keywords": {"type": "array", "items": {"type": "string"},
                                  "description": "3-6 words in the listing language naming the kind of product, for a marketplace category search."},
            "notes_for_reviewer": {"type": "string",
                                   "description": "Anything the human reviewer must check or fill in: unreadable model numbers, unknown size, untested function, guesses."},
        },
        "required": ["title", "description", "category_keywords", "notes_for_reviewer"],
    },
}

SYSTEM = """You write second-hand auction listings for Tradera from product photos.
The photos show one item from four sides on a turntable.
Write in {language}. Describe only what the photos show: what the item is, brand and model if
legible, material, colour, visible condition and any visible wear or damage. Never invent
measurements, model numbers, age, provenance or function you cannot see; put such open
questions in notes_for_reviewer instead. Keep a plain, honest tone with no sales superlatives.
{extra}"""


def _encode(img: Image.Image) -> dict:
    img = img.copy()
    img.thumbnail((1400, 1400))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(buf.getvalue()).decode()}}


def describe(images: list) -> dict:
    """images: PIL images (cropped, unframed). Returns title/description/category/notes."""
    if not config.ANTHROPIC_API_KEY:
        return {"title": "", "description": "", "category_id": None, "category_path": "",
                "ai_notes": "AI description is off: set ANTHROPIC_API_KEY on the server."}
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    content = [_encode(i) for i in images] + [{"type": "text", "text": "Draft the listing for this item."}]
    msg = client.messages.create(
        model=config.AI_MODEL, max_tokens=1500,
        system=SYSTEM.format(language=config.LISTING_LANGUAGE, extra=config.AI_EXTRA_INSTRUCTIONS),
        tools=[LISTING_TOOL], tool_choice={"type": "tool", "name": "listing"},
        messages=[{"role": "user", "content": content}],
    )
    draft = next(b.input for b in msg.content if b.type == "tool_use")
    result = {"title": draft["title"][:80], "description": draft["description"],
              "ai_notes": draft.get("notes_for_reviewer", ""), "category_id": None, "category_path": ""}

    candidates = tradera.search_categories(draft.get("category_keywords", []))
    if candidates:
        listing = "\n".join(f"{c['id']}: {c['path']}" for c in candidates)
        pick = client.messages.create(
            model=config.AI_MODEL, max_tokens=200,
            tools=[{"name": "pick", "description": "Choose the category.",
                    "input_schema": {"type": "object", "properties": {"category_id": {"type": "integer"}},
                                     "required": ["category_id"]}}],
            tool_choice={"type": "tool", "name": "pick"},
            messages=[{"role": "user", "content":
                       f"Item: {result['title']}\n\n{result['description']}\n\nPick the best Tradera category:\n{listing}"}],
        )
        chosen = next(b.input for b in pick.content if b.type == "tool_use").get("category_id")
        match = next((c for c in candidates if c["id"] == chosen), None)
        if match:
            result.update(category_id=match["id"], category_path=match["path"])
    return result
