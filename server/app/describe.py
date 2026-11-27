"""Ask Claude to look at the photos and draft the listing."""
import base64
import json
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


def _structured(client, tool: dict, messages: list, system: str = "", max_tokens: int = 1500) -> dict:
    """Asks for an answer shaped like the tool's input, on any model.

    Forcing the tool is the most reliable, but some models refuse a forced tool
    choice; then the tool is offered with "auto" and the model is told to use it.
    If it still answers in text, the JSON in that text is used.
    """
    import anthropic
    kwargs = {"model": config.get("AI_MODEL"), "max_tokens": max_tokens, "tools": [tool], "messages": messages}
    if system:
        kwargs["system"] = system
    try:
        msg = client.messages.create(**kwargs, tool_choice={"type": "tool", "name": tool["name"]})
    except anthropic.BadRequestError as exc:
        if "tool_choice" not in str(exc):
            raise
        nudge = f"\n\nAnswer only by calling the {tool['name']} tool."
        kwargs["system"] = (system + nudge).strip()
        msg = client.messages.create(**kwargs, tool_choice={"type": "auto"})
    for block in msg.content:
        if block.type == "tool_use" and block.name == tool["name"]:
            return block.input
    text = "".join(getattr(b, "text", "") for b in msg.content)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            pass
    raise RuntimeError(f"The model did not return the {tool['name']} answer")


def describe(images: list) -> dict:
    """images: PIL images (cropped, unframed). Returns title/description/category/notes."""
    if not config.get("ANTHROPIC_API_KEY"):
        return {"title": "", "description": "", "category_id": None, "category_path": "",
                "ai_notes": "AI description is off: add a Claude API key under Settings."}
    import anthropic

    client = anthropic.Anthropic(api_key=config.get("ANTHROPIC_API_KEY"))
    content = [_encode(i) for i in images] + [{"type": "text", "text": "Draft the listing for this item."}]
    draft = _structured(client, LISTING_TOOL, [{"role": "user", "content": content}],
                        system=SYSTEM.format(language=config.get("LISTING_LANGUAGE"), extra=config.get("AI_EXTRA_INSTRUCTIONS")))
    result = {"title": str(draft.get("title", ""))[:80], "description": str(draft.get("description", "")),
              "ai_notes": str(draft.get("notes_for_reviewer", "")), "category_id": None, "category_path": ""}

    try:
        cat = pick_category(client, result["title"], result["description"], draft.get("category_keywords") or [])
    except Exception as exc:                    # the text is still useful without a category
        cat, result["ai_notes"] = None, (result["ai_notes"] + f"\nCategory not chosen: {exc}").strip()
    if cat:
        result.update(category_id=cat["id"], category_path=cat["path"])
        try:
            skick = pick_condition(client, result["title"], result["description"], cat["id"])
        except Exception as exc:
            skick = None
            result["ai_notes"] = (result["ai_notes"] + f"\nSkick not chosen: {exc}").strip()
        if skick:
            result["attributes"] = json.dumps({str(skick[0]): [skick[1]]})
            result["ai_notes"] = (result["ai_notes"] + f"\nSkick set to \u201c{skick[1]}\u201d from the photos' description; check it.").strip()
    elif tradera.categories():
        result["ai_notes"] = (result["ai_notes"] + "\nPick the category by hand.").strip()
    return result


PICK_TOOL = {"name": "pick", "description": "Choose one option by its number.",
             "input_schema": {"type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]}}


def _choose(client, question: str, options: list) -> int | None:
    listing = "\n".join(f"{i}: {o}" for i, o in enumerate(options))
    n = _structured(client, PICK_TOOL, [{"role": "user", "content": f"{question}\n\n{listing}"}], max_tokens=200).get("number")
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    return n if 0 <= n < len(options) else None


def pick_condition(client, title: str, description: str, category_id: int) -> tuple | None:
    """(attribute id, value): the item's Skick from the category's own list of conditions."""
    cond = next((a for a in tradera.attribute_definitions(category_id) if a["condition"] and a["values"]), None)
    if not cond:
        return None
    n = _choose(client, f"Item for sale on Tradera: {title}\n\n{description[:1500]}\n\n"
                        f"Which condition ({cond['name']}) fits the item best? If unsure, pick the more modest one. Answer with the number.",
                cond["values"])
    return (cond["id"], cond["values"][n]) if n is not None else None


def pick_category(client, title: str, description: str, keywords: list) -> dict | None:
    """Two steps through Tradera's own category tree: first the section, then the category inside it.
    Keyword matches are added to the second step, so a good word match is never lost."""
    leaves = tradera.categories()
    if not leaves:
        return None
    item = f"Item for sale on Tradera: {title}\n\n{description[:1500]}"
    sections = sorted({" > ".join(c["path"].split(" > ")[:2]) for c in leaves})
    n = _choose(client, item + "\n\nWhich section of Tradera's category tree does it belong in? Answer with the number.", sections)
    in_section = [c for c in leaves if n is not None and (c["path"] + " > ").startswith(sections[n] + " > ")]
    if len(in_section) > 250:                    # very large sections: keep the leaves that share words with the item
        words = {w.lower() for w in (title + " " + " ".join(keywords)).split() if len(w) > 2}
        in_section = [c for c in in_section if any(w in c["path"].lower() for w in words)] or in_section[:250]
    options, seen = [], set()
    for c in in_section + tradera.search_categories(keywords, limit=25):
        if c["id"] not in seen:
            seen.add(c["id"])
            options.append(c)
    if not options:
        return None
    if len(options) == 1:
        return options[0]
    k = _choose(client, item + "\n\nWhich Tradera category fits it best? Answer with the number.", [c["path"] for c in options])
    return options[k] if k is not None else None


def check() -> dict:
    """Connection test for the Settings page."""
    if not config.get("ANTHROPIC_API_KEY"):
        return {"ok": False, "message": "No API key set"}
    import anthropic
    try:
        client = anthropic.Anthropic(api_key=config.get("ANTHROPIC_API_KEY"))
        client.messages.create(model=config.get("AI_MODEL"), max_tokens=1,
                               messages=[{"role": "user", "content": "ping"}])
        return {"ok": True, "message": f"Key works with {config.get('AI_MODEL')}"}
    except Exception as exc:
        return {"ok": False, "message": str(exc)[:400]}
