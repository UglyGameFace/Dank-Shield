from __future__ import annotations

"""Harden /mod ban-unban target resolution.

The old ban resolver depended too heavily on a clean raw ID or a warm member
cache. Staff could type/select a visible member and still get "could not
resolve" when the cache was cold, the value contained a label like `name (id)`,
or the display name had mobile/autocomplete decoration.
"""

import re
import sys
import unicodedata
from collections.abc import Iterable
from typing import Any

import discord

_PATCHED = False
_SNOWFLAKE_RE = re.compile(r"(?<!\d)(\d{15,25})(?!\d)")
_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\ufeff]")


def _text(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _text(value))
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.strip().strip("`'\" ")
    if text.startswith("@"):
        text = text[1:]
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def _parse_any_user_id(raw: Any) -> int:
    text = _text(raw)
    if not text:
        return 0
    mention = re.search(r"<@!?(\d{15,25})>", text)
    if mention:
        try:
            return int(mention.group(1))
        except Exception:
            return 0
    if text.isdigit():
        try:
            return int(text)
        except Exception:
            return 0
    match = _SNOWFLAKE_RE.search(text)
    if match:
        try:
            return int(match.group(1))
        except Exception:
            return 0
    return 0


def _member_values(member: discord.Member) -> list[str]:
    values = [
        getattr(member, "display_name", None),
        getattr(member, "global_name", None),
        getattr(member, "name", None),
        str(member),
    ]
    try:
        disc = _text(getattr(member, "discriminator", ""))
        name = _text(getattr(member, "name", ""))
        if name and disc and disc != "0":
            values.append(f"{name}#{disc}")
    except Exception:
        pass
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _norm(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


async def _member_by_id(guild: discord.Guild, user_id: int) -> discord.Member | None:
    try:
        member = guild.get_member(int(user_id))
        if isinstance(member, discord.Member):
            return member
    except Exception:
        pass
    try:
        member = await guild.fetch_member(int(user_id))
        if isinstance(member, discord.Member):
            return member
    except Exception:
        return None
    return None


def _unique_match(members: Iterable[discord.Member], query: str, *, mode: str) -> discord.Member | None:
    hits: list[discord.Member] = []
    for member in members:
        try:
            values = _member_values(member)
            if mode == "exact" and query in values:
                hits.append(member)
            elif mode == "startswith" and any(value.startswith(query) for value in values):
                hits.append(member)
            elif mode == "contains" and any(query in value for value in values):
                hits.append(member)
        except Exception:
            continue
    unique: dict[int, discord.Member] = {}
    for member in hits:
        try:
            unique[int(member.id)] = member
        except Exception:
            pass
    return next(iter(unique.values())) if len(unique) == 1 else None


async def _search_member_by_name(guild: discord.Guild, raw: Any) -> discord.Member | None:
    query = _norm(raw)
    if not query:
        return None

    try:
        direct = guild.get_member_named(_text(raw))
        if isinstance(direct, discord.Member):
            return direct
    except Exception:
        pass

    members = list(getattr(guild, "members", []) or [])
    for mode in ("exact", "startswith", "contains"):
        found = _unique_match(members, query, mode=mode)
        if found is not None:
            return found

    try:
        query_members = getattr(guild, "query_members", None)
        if callable(query_members):
            queried = await query_members(_text(raw).lstrip("@"), limit=25, cache=True)
            found = _unique_match(list(queried or []), query, mode="exact") or _unique_match(list(queried or []), query, mode="startswith")
            if found is not None:
                return found
    except Exception:
        pass

    try:
        await guild.chunk(cache=True)
        members = list(getattr(guild, "members", []) or [])
    except Exception:
        members = list(getattr(guild, "members", []) or [])

    for mode in ("exact", "startswith", "contains"):
        found = _unique_match(members, query, mode=mode)
        if found is not None:
            return found
    return None


async def _fetch_ban_entry(guild: discord.Guild, user_id: int) -> Any | None:
    try:
        return await guild.fetch_ban(discord.Object(id=int(user_id)))
    except discord.NotFound:
        return None
    except discord.Forbidden:
        raise
    except Exception:
        return None


async def _resolve_ban_target_hardened(guild: discord.Guild, raw_target: str) -> tuple[int, discord.Member | None, Any | None]:
    user_id = _parse_any_user_id(raw_target)
    member: discord.Member | None = None

    if user_id > 0:
        member = await _member_by_id(guild, user_id)
    else:
        member = await _search_member_by_name(guild, raw_target)
        if member is not None:
            try:
                user_id = int(member.id)
            except Exception:
                user_id = 0

    ban_entry = await _fetch_ban_entry(guild, int(user_id)) if user_id > 0 else None
    return int(user_id or 0), member, ban_entry


def _patch_module(module_name: str, attr_name: str) -> bool:
    module = sys.modules.get(module_name)
    if module is None:
        return False
    try:
        setattr(module, attr_name, _resolve_ban_target_hardened)
        return True
    except Exception:
        return False


def apply() -> bool:
    global _PATCHED
    patched = False
    try:
        patched = _patch_module("stoney_verify.commands_ext.moderation", "_resolve_ban_toggle_target") or patched
        patched = _patch_module("stoney_verify.commands_ext.public_mod_group", "_resolve_ban_toggle_target") or patched
        patched = _patch_module("stoney_verify.commands_ext.public_mod_ban_toggle_patch", "_resolve_ban_target") or patched
        patched = _patch_module("stoney_verify.commands_ext.public_ban_unban_patch", "_resolve_ban_target") or patched
        _PATCHED = _PATCHED or patched
        if patched:
            print("✅ ban_target_resolution_guard active; ban target lookup accepts IDs inside labels and cold-cache members")
        return patched
    except Exception as exc:
        try:
            print(f"⚠️ ban_target_resolution_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
