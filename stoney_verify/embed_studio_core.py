"""Validated, bounded Discord embed projects, shared by all Embed Studio routes.

Pure model operations: no DB writes, Discord messages, startup hooks or globals.
"""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Mapping
from urllib.parse import urlsplit

import discord

STUDIO_KEY = "embed_studio_v1"
MAX_TEMPLATES = 12
MAX_THEMES = 8
MAX_DRAFTS = 8
MAX_DRAFT_AGE_SECONDS = 30 * 86400
MAX_EMBEDS = 10
MAX_FIELDS = 25
MAX_LINK_BUTTONS = 5
MAX_ROLE_ACTIONS = 10
MAX_ROLE_BUTTONS = 5
MAX_PROJECT_BYTES = 40000
MAX_TEMPLATES_BYTES = 100000
PORTABLE_FORMAT = "dank-shield-embed-studio"
PORTABLE_VERSION = 1
MAX_PORTABLE_BYTES = 65536


class StudioValidationError(ValueError):
    pass


def default_embed() -> dict[str, Any]:
    return {
        "title": "Your headline", "description": "Write your message here.",
        "color": "5865F2", "author_name": "", "author_url": "",
        "author_icon": "", "title_url": "", "footer": "",
        "footer_icon": "", "image": "", "thumbnail": "",
        "timestamp": False, "fields": [],
    }


def new_project() -> dict[str, Any]:
    return {"version": 1, "content": "", "embeds": [default_embed()], "links": [], "role_mode": "buttons", "role_actions": []}


def _text(value: Any, limit: int, label: str) -> str:
    value = str(value or "").strip()
    if len(value) > limit:
        raise StudioValidationError(f"{label} must be {limit} characters or fewer.")
    return value


def https_url(value: Any, label: str) -> str:
    text = _text(value, 2048, label)
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        if parts.scheme.lower() != "https" or not parts.hostname or parts.username or parts.password:
            raise ValueError("not a public HTTPS URL")
        if parts.hostname.lower() in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("local addresses are not allowed")
        if any(c.isspace() for c in text) or any(ord(c) < 32 for c in text):
            raise ValueError("invalid whitespace")
    except ValueError as exc:
        raise StudioValidationError(f"{label} needs a valid HTTPS URL.") from exc
    return text


def color_hex(value: Any) -> str:
    color = str(value or "5865F2").strip().lstrip("#").upper()
    if len(color) != 6 or any(c not in "0123456789ABCDEF" for c in color):
        raise StudioValidationError("Color must be six hex digits, like #5865F2.")
    return color


def validate_project(project: Mapping[str, Any]) -> dict[str, Any]:
    """Copy, fully validate, and enforce Discord's *combined* 6000-char limit."""
    if not isinstance(project, Mapping):
        raise StudioValidationError("Invalid draft.")
    content = _text(project.get("content"), 2000, "Plain message")
    embeds = project.get("embeds")
    if not isinstance(embeds, list) or not (1 <= len(embeds) <= MAX_EMBEDS):
        raise StudioValidationError("Use between 1 and 10 embeds per message.")
    result: list[dict[str, Any]] = []
    characters = 0
    for index, raw in enumerate(embeds, 1):
        if not isinstance(raw, Mapping):
            raise StudioValidationError(f"Embed {index} is invalid.")
        e = {
            "title": _text(raw.get("title"), 256, "Title"),
            "description": _text(raw.get("description"), 4096, "Description"),
            "color": color_hex(raw.get("color")),
            "title_url": https_url(raw.get("title_url"), "Title link"),
            "author_name": _text(raw.get("author_name"), 256, "Author"),
            "author_url": https_url(raw.get("author_url"), "Author link"),
            "author_icon": https_url(raw.get("author_icon"), "Author icon"),
            "footer": _text(raw.get("footer"), 2048, "Footer"),
            "footer_icon": https_url(raw.get("footer_icon"), "Footer icon"),
            "image": https_url(raw.get("image"), "Image"),
            "thumbnail": https_url(raw.get("thumbnail"), "Thumbnail"),
            "timestamp": bool(raw.get("timestamp", False)),
            "fields": [],
        }
        fields = raw.get("fields") or []
        if not isinstance(fields, list) or len(fields) > MAX_FIELDS:
            raise StudioValidationError("Each embed may have up to 25 fields.")
        for field in fields:
            if not isinstance(field, Mapping):
                raise StudioValidationError("Invalid field.")
            name = _text(field.get("name"), 256, "Field name")
            value = _text(field.get("value"), 1024, "Field value")
            if not name or not value:
                raise StudioValidationError("Every field needs a name and text.")
            e["fields"].append({"name": name, "value": value, "inline": bool(field.get("inline", False))})
            characters += len(name) + len(value)
        if not any((e["title"], e["description"], e["image"], e["thumbnail"], e["fields"], e["author_name"])):
            raise StudioValidationError(f"Embed {index} needs a title, text, image or field.")
        characters += sum(len(e[key]) for key in ("title", "description", "author_name", "footer"))
        result.append(e)
    if characters > 6000:
        raise StudioValidationError(f"Discord permits 6000 embed characters combined; draft uses {characters}.")
    links = project.get("links") or []
    if not isinstance(links, list) or len(links) > MAX_LINK_BUTTONS:
        raise StudioValidationError("A message may have up to five URL buttons.")
    buttons: list[dict[str, str]] = []
    for raw in links:
        if not isinstance(raw, Mapping):
            raise StudioValidationError("Invalid URL button.")
        name = _text(raw.get("label"), 80, "Button label")
        url = https_url(raw.get("url"), "Button destination")
        if not name or not url:
            raise StudioValidationError("URL buttons need a label and HTTPS destination.")
        buttons.append({"label": name, "url": url})
    mode = str(project.get("role_mode") or "buttons").strip().lower()
    if mode not in {"buttons", "dropdown"}:
        raise StudioValidationError("Role controls must use buttons or a dropdown.")
    actions = project.get("role_actions") or []
    if not isinstance(actions, list) or len(actions) > MAX_ROLE_ACTIONS:
        raise StudioValidationError("A message may offer up to ten member-selectable roles.")
    roles: list[dict[str, str]] = []
    used: set[str] = set()
    for raw in actions:
        if not isinstance(raw, Mapping):
            raise StudioValidationError("Invalid role control.")
        role_id = str(raw.get("role_id") or "").strip()
        if not role_id.isascii() or not role_id.isdecimal() or int(role_id) <= 0:
            raise StudioValidationError("Role controls must refer to a real role ID.")
        if role_id in used:
            raise StudioValidationError("A role can only appear once in an embed.")
        used.add(role_id)
        label = _text(raw.get("label"), 80, "Role control label")
        if not label:
            raise StudioValidationError("Every role control needs a label.")
        roles.append({"role_id": role_id, "label": label})
    if mode == "buttons" and len(roles) > MAX_ROLE_BUTTONS:
        raise StudioValidationError("Use the dropdown layout for more than five role controls.")
    if mode == "buttons" and len(roles) + len(buttons) > 5:
        raise StudioValidationError("Buttons layout supports at most five total link and role buttons. Switch roles to dropdown.")
    cleaned = {"version": 1, "content": content, "embeds": result, "links": buttons,
               "role_mode": mode, "role_actions": roles}
    if len(repr(cleaned).encode("utf-8")) > MAX_PROJECT_BYTES:
        raise StudioValidationError("Draft exceeds the storage limit. Reduce media links or text.")
    return cleaned


# Reusable branding is a compact style override, not a second embed editor.
# Built-in choices change color only; saved guild themes capture the whole style.
THEME_FIELDS = ("color", "author_name", "author_url", "author_icon", "footer", "footer_icon")
THEME_PRESETS: tuple[tuple[str, str, dict[str, str]], ...] = (
    ("evergreen", "Evergreen", {"color": "1F8B4C"}),
    ("midnight", "Midnight", {"color": "283246"}),
    ("sunset", "Sunset", {"color": "E67E46"}),
    ("royal", "Royal Blue", {"color": "5865F2"}),
    ("amethyst", "Amethyst", {"color": "9B59B6"}),
    ("warm", "Warm Gold", {"color": "D4A43A"}),
)


def validate_theme(style: Mapping[str, Any]) -> dict[str, str]:
    """Validate a *partial* style using the same editor limits and URL rules."""
    if not isinstance(style, Mapping) or not style or (set(style) - set(THEME_FIELDS)):
        raise StudioValidationError("Invalid brand theme.")
    sample = new_project()
    sample["embeds"][0].update(style)
    cleaned = validate_project(sample)["embeds"][0]
    return {key: cleaned[key] for key in THEME_FIELDS if key in style}


def theme_from_project(project: Mapping[str, Any], *, embed_index: int = 0) -> dict[str, str]:
    cleaned = validate_project(project)
    if not 0 <= embed_index < len(cleaned["embeds"]):
        raise StudioValidationError("Choose an existing embed before saving its style.")
    return validate_theme({key: cleaned["embeds"][embed_index][key] for key in THEME_FIELDS})


def apply_theme(project: Mapping[str, Any], style: Mapping[str, Any]) -> dict[str, Any]:
    """Return a valid draft styled across *all* its embeds, without posting."""
    changes = validate_theme(style)
    draft = validate_project(project)
    for embed in draft["embeds"]:
        embed.update(changes)
    return validate_project(draft)


def theme_name(value: Any) -> str:
    if not isinstance(value, str):
        raise StudioValidationError("Theme name must be text.")
    name = value.strip()
    if not name or len(name) > 60 or not name.isprintable():
        raise StudioValidationError("Theme name must be 1–60 visible characters.")
    return name


def export_portable_project(project: Mapping[str, Any], *, guild_id: int) -> bytes:
    """Export a strictly validated editor project, never a published post or config.

    The envelope includes the server of origin only to prevent accidentally
    reusing interactive self-role IDs in another server.
    """
    if int(guild_id) <= 0:
        raise StudioValidationError("Open the Studio inside a server to export.")
    envelope = {
        "format": PORTABLE_FORMAT,
        "format_version": PORTABLE_VERSION,
        "origin_guild_id": str(guild_id),
        "project": validate_project(project),
    }
    data = json.dumps(envelope, ensure_ascii=False, indent=2).encode("utf-8")
    if len(data) > MAX_PORTABLE_BYTES:
        raise StudioValidationError("This draft is too large for a portable JSON file.")
    return data


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StudioValidationError("JSON has a repeated field name.")
        result[key] = value
    return result


def _nonfinite_json(_value: str) -> Any:
    raise StudioValidationError("JSON cannot contain non-finite numbers.")


def import_portable_project(data: bytes, *, guild_id: int) -> dict[str, Any]:
    """Read a bounded Studio export without trusting its filename or media type."""
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_PORTABLE_BYTES:
        raise StudioValidationError("Studio JSON must be a nonempty file under 64 KiB.")
    if int(guild_id) <= 0:
        raise StudioValidationError("Open the Studio inside a server to import.")
    try:
        envelope = json.loads(
            data.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_nonfinite_json,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise StudioValidationError("File must contain valid UTF-8 Studio JSON.") from exc
    if not isinstance(envelope, dict) or set(envelope) != {
        "format", "format_version", "origin_guild_id", "project",
    }:
        raise StudioValidationError("File is not a supported Embed Studio export.")
    if envelope["format"] != PORTABLE_FORMAT or type(envelope["format_version"]) is not int or envelope["format_version"] != PORTABLE_VERSION:
        raise StudioValidationError("This Embed Studio export format is not supported.")
    origin = envelope["origin_guild_id"]
    if not isinstance(origin, str) or not origin.isascii() or not origin.isdecimal() or int(origin) <= 0:
        raise StudioValidationError("Export has an invalid server of origin.")
    model = validate_project(envelope["project"])
    if int(origin) != int(guild_id) and model["role_actions"]:
        raise StudioValidationError(
            "This file has self-role controls from a different server. "
            "Remove those controls in the original server before exporting for another."
        )
    return model


def render_embeds(project: Mapping[str, Any]) -> list[discord.Embed]:
    valid = validate_project(project)
    results: list[discord.Embed] = []
    for e in valid["embeds"]:
        embed = discord.Embed(
            title=e["title"] or None,
            description=e["description"] or None,
            url=e["title_url"] or None,
            color=discord.Color(int(e["color"], 16)),
            timestamp=discord.utils.utcnow() if e["timestamp"] else None,
        )
        if e["author_name"]:
            embed.set_author(name=e["author_name"], url=e["author_url"] or None, icon_url=e["author_icon"] or None)
        if e["footer"]:
            embed.set_footer(text=e["footer"], icon_url=e["footer_icon"] or None)
        if e["image"]:
            embed.set_image(url=e["image"])
        if e["thumbnail"]:
            embed.set_thumbnail(url=e["thumbnail"])
        for field in e["fields"]:
            embed.add_field(name=field["name"], value=field["value"], inline=field["inline"])
        results.append(embed)
    return results


def link_view(project: Mapping[str, Any]) -> discord.ui.View | None:
    links = validate_project(project)["links"]
    if not links:
        return None
    view = discord.ui.View(timeout=None)
    for link in links:
        view.add_item(discord.ui.Button(label=link["label"], url=link["url"], style=discord.ButtonStyle.link))
    return view


def cloned(project: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy(validate_project(project))


def parse_library(config: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = (config or {}).get(STUDIO_KEY)
    if not isinstance(raw, Mapping):
        raw = {}
    templates = raw.get("templates")
    themes = raw.get("themes")
    published = raw.get("published")
    drafts = raw.get("drafts")
    if not isinstance(templates, dict):
        templates = {}
    if not isinstance(themes, dict):
        themes = {}
    if not isinstance(published, dict):
        published = {}
    if not isinstance(drafts, dict):
        drafts = {}
    return {
        "revision": int(raw.get("revision") or 0),
        "templates": deepcopy(dict(list(templates.items())[:MAX_TEMPLATES])),
        "themes": deepcopy(dict(list(themes.items())[:MAX_THEMES])),
        "published": deepcopy(dict(list(published.items())[-20:])),
        "drafts": deepcopy(dict(list(drafts.items())[:MAX_DRAFTS])),
    }



def draft_snapshot(
    library: dict[str, Any], *, owner_id: int, expected_version: int,
    project: Mapping[str, Any], channel_id: int = 0, active: int = 0,
    post_key: str = "", now: int,
) -> int:
    """Stage one staff member's draft without overwriting another editor session.

    The entire library is still saved through the existing guild-config CAS.
    Caller retries that CAS only after a fresh read, never bypassing this
    per-draft revision check.
    """
    owner = str(int(owner_id))
    if int(owner_id) <= 0 or int(expected_version) < 0:
        raise StudioValidationError("Invalid draft owner or revision.")
    drafts = library.setdefault("drafts", {})
    if not isinstance(drafts, dict):
        raise StudioValidationError("Invalid saved drafts.")
    stored = drafts.get(owner)
    if stored is not None and not isinstance(stored, Mapping):
        raise StudioValidationError("Saved draft needs repair.")
    current_version = int(stored.get("version") or 0) if stored else 0
    if current_version != expected_version:
        raise StudioValidationError(
            "Your draft changed in another Studio session. "
            "Use Reload Saved to review that version; current unsaved edits remain here."
        )
    # Only expire abandoned *other* editor drafts, never an active owner's
    # saved state. No background cleaner or concurrent cross-guild scans.
    cutoff = int(now) - MAX_DRAFT_AGE_SECONDS
    for key, row in list(drafts.items()):
        if key != owner and isinstance(row, Mapping) and int(row.get("updated_at") or 0) < cutoff:
            drafts.pop(key, None)
    if stored is None and len(drafts) >= MAX_DRAFTS:
        raise StudioValidationError("Eight editor drafts are saved in this server. Ask staff to clear an unused draft.")
    cleaned = validate_project(project)
    active_index = int(active)
    if not 0 <= active_index < len(cleaned["embeds"]):
        raise StudioValidationError("Invalid draft page.")
    if int(channel_id) < 0:
        raise StudioValidationError("Invalid saved destination.")
    drafts[owner] = {
        "version": current_version + 1, "project": cleaned,
        "channel_id": str(int(channel_id)), "active": active_index,
        "post_key": str(post_key)[:100], "updated_at": int(now),
    }
    return current_version + 1


def trim_published_history(published: Mapping[str, Any], *, max_posts: int = 20) -> dict[str, Any]:
    """Never evict live self-role controls just to fit an edit-history entry.

    Ordinary posts lose Studio edit-history visibility oldest-first when full;
    role posts require an explicit staff edit that removes their actions.
    """
    rows = deepcopy(dict(published))
    while len(rows) > max_posts:
        removable = next(
            (key for key, value in rows.items()
             if not isinstance(value, Mapping) or not value.get("role_actions")),
            None,
        )
        if removable is None:
            raise StudioValidationError(
                "All saved post slots contain active role panels. Edit an older panel to remove its roles first."
            )
        rows.pop(removable, None)
    return rows


def library_payload(library: Mapping[str, Any]) -> dict[str, Any]:
    templates = library.get("templates") or {}
    themes = library.get("themes") or {}
    published = library.get("published") or {}
    drafts = library.get("drafts") or {}
    if len(templates) > MAX_TEMPLATES or len(themes) > MAX_THEMES or len(published) > 20 or len(drafts) > MAX_DRAFTS:
        raise StudioValidationError("Studio library is full.")
    for key, item in themes.items():
        if not isinstance(key, str) or not key or len(key) > 40 or not isinstance(item, Mapping):
            raise StudioValidationError("Invalid saved brand theme.")
        theme_name(item.get("name"))
        validate_theme(item.get("style"))
    for item in templates.values():
        if not isinstance(item, Mapping):
            raise StudioValidationError("Invalid saved template.")
        validate_project(item.get("project") or {})
    for owner, item in drafts.items():
        if not str(owner).isascii() or not str(owner).isdecimal() or not isinstance(item, Mapping):
            raise StudioValidationError("Invalid saved draft owner.")
        if int(item.get("version") or 0) < 1 or int(item.get("updated_at") or 0) < 0:
            raise StudioValidationError("Invalid saved draft revision.")
        validate_project(item.get("project") or {})
    result = {
        "revision": int(library.get("revision") or 0) + 1,
        "templates": deepcopy(templates),
        "themes": deepcopy(themes),
        "published": deepcopy(published),
        "drafts": deepcopy(drafts),
    }
    import json
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_TEMPLATES_BYTES:
        raise StudioValidationError("Saved studio library is at capacity. Delete unused templates.")
    return result
