from __future__ import annotations

"""Store dashboard ticket form answers when the DB table exists.

This is intentionally soft-fail. Ticket creation must never fail just because the
new dashboard answer table has not been migrated yet.
"""

from typing import Any, Dict, List, Optional

import discord

try:
    from . import ticket_form_default_templates_guard as _default_templates
    _default_templates.apply()
except Exception:
    pass


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_form_answer_storage_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_form_answer_storage_guard: {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        return text in {"1", "true", "yes", "y", "on", "required"} if text else default
    except Exception:
        return default


def _short(value: Any, limit: int) -> str:
    text = _safe_str(value)
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _answer_rows(row: Dict[str, Any]) -> List[Dict[str, str]]:
    raw = row.get("_form_answers")
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "label": _short(item.get("label") or "Question", 300),
                "answer": _short(item.get("answer") or "", 4000),
                "key": _short(item.get("key") or item.get("question_key") or "", 120),
                "required": str(_safe_bool(item.get("required"), False)).lower(),
            }
        )
    return out


async def _find_ticket_id(panel_mod: Any, *, guild_id: int, channel_id: int) -> Optional[str]:
    try:
        sb = panel_mod._sb()
    except Exception:
        sb = None
    if sb is None:
        return None

    def sync() -> Optional[str]:
        try:
            rows = getattr(
                sb.table("tickets")
                .select("id")
                .eq("guild_id", str(guild_id))
                .eq("channel_id", str(channel_id))
                .order("created_at", desc=True)
                .limit(1)
                .execute(),
                "data",
                None,
            ) or []
            if rows and isinstance(rows[0], dict):
                return _safe_str(rows[0].get("id"), "") or None
        except Exception:
            return None
        return None

    try:
        return await panel_mod._to_thread(sync, None)
    except Exception:
        return None


async def _save_answers(panel_mod: Any, *, guild: discord.Guild, owner: discord.Member, channel: discord.TextChannel, row: Dict[str, Any]) -> None:
    answers = _answer_rows(row)
    if not answers:
        return

    try:
        sb = panel_mod._sb()
    except Exception:
        sb = None
    if sb is None:
        return

    ticket_id = await _find_ticket_id(panel_mod, guild_id=guild.id, channel_id=channel.id)
    category_slug = _safe_str(row.get("slug") or row.get("category_slug") or row.get("name"), "support")

    payload = []
    for idx, item in enumerate(answers):
        payload.append(
            {
                "ticket_id": ticket_id,
                "guild_id": str(guild.id),
                "channel_id": str(channel.id),
                "user_id": str(owner.id),
                "category_slug": category_slug,
                "question_index": idx,
                "question_label": item["label"],
                "question_key": item["key"] or None,
                "answer": item["answer"],
                "required": item["required"] == "true",
            }
        )

    def sync() -> None:
        try:
            sb.table("ticket_form_responses").insert(payload).execute()
        except Exception as e:
            _warn(f"ticket form answer save skipped/failed guild={guild.id} channel={channel.id}: {type(e).__name__}: {_short(e, 220)}")

    try:
        await panel_mod._to_thread(sync, None)
    except Exception:
        pass


def _patch_open_message(panel_mod: Any) -> bool:
    original = getattr(panel_mod, "_open_message", None)
    if not callable(original) or getattr(original, "_ticket_form_answer_storage_wrapped", False):
        return False

    async def wrapped_open_message(channel: discord.TextChannel, owner: discord.Member, row: Dict[str, Any]) -> None:
        result = await original(channel, owner, row)
        try:
            await _save_answers(panel_mod, guild=channel.guild, owner=owner, channel=channel, row=row)
        except Exception as e:
            _warn(f"answer storage wrapper failed channel={getattr(channel, 'id', 0)}: {type(e).__name__}: {e}")
        return result

    setattr(wrapped_open_message, "_ticket_form_answer_storage_wrapped", True)
    panel_mod._open_message = wrapped_open_message
    return True


def apply() -> bool:
    try:
        from ..commands_ext import public_ticket_panel_clean as panel_mod
    except Exception as e:
        _warn(f"could not import public_ticket_panel_clean: {e!r}")
        return False

    ok = _patch_open_message(panel_mod)
    if ok:
        _log("installed ticket form answer storage hook")
    return bool(ok)


apply()

__all__ = ["apply"]
