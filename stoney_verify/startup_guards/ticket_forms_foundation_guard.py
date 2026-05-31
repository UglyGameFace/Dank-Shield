from __future__ import annotations

"""Dashboard-ready ticket form foundation.

This adds bot-side support for category-specific ticket forms without adding more
slash commands. The Vercel dashboard can later write form config onto
`ticket_categories`; the Discord bot will read it and show a modal before ticket
creation.

Supported category row shapes are intentionally flexible for dashboard evolution:
- form_questions / questions / intake_questions: list or JSON string
- form_config / form_schema / metadata: dict or JSON string containing questions

Each question may include:
- label / question / name / title
- placeholder / description / help_text
- required
- style: short/text or paragraph/long
- max_length

Discord modals support up to 5 inputs, so extra questions are ignored safely.
Answers are placed in the opening ticket embed so transcripts capture them even
before a dedicated dashboard table exists.
"""

import json
from typing import Any, Dict, List, Optional

import discord

_MAX_QUESTIONS = 5
_MAX_LABEL = 45
_MAX_PLACEHOLDER = 100
_DEFAULT_MAX_LENGTH = 700


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_forms_foundation_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_forms_foundation_guard: {message}")
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
        if not text:
            return default
        return text in {"1", "true", "yes", "y", "on", "required"}
    except Exception:
        return default


def _short(value: Any, limit: int) -> str:
    text = _safe_str(value)
    return text[:limit] if len(text) > limit else text


def _jsonish(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    text = _safe_str(value)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _as_question_list(value: Any) -> List[Dict[str, Any]]:
    parsed = _jsonish(value)
    if isinstance(parsed, list):
        return [dict(item) for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        for key in ("questions", "form_questions", "intake_questions", "fields", "items"):
            items = parsed.get(key)
            if isinstance(items, list):
                return [dict(item) for item in items if isinstance(item, dict)]
    return []


def _category_questions(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    questions: List[Dict[str, Any]] = []

    for key in ("form_questions", "questions", "intake_questions", "fields"):
        questions.extend(_as_question_list(row.get(key)))

    for key in ("form_config", "form_schema", "metadata", "config"):
        parsed = _jsonish(row.get(key))
        if isinstance(parsed, dict):
            for nested_key in ("questions", "form_questions", "intake_questions", "fields", "items"):
                questions.extend(_as_question_list(parsed.get(nested_key)))

    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for q in questions:
        label = _question_label(q)
        key = label.lower()
        if not label or key in seen:
            continue
        seen.add(key)
        out.append(q)
        if len(out) >= _MAX_QUESTIONS:
            break
    return out


def _form_enabled(row: Dict[str, Any]) -> bool:
    if _category_questions(row):
        return True
    for key in ("form_enabled", "requires_form", "require_form", "intake_form_enabled"):
        if _safe_bool(row.get(key), False):
            return True
    return False


def _question_label(q: Dict[str, Any]) -> str:
    raw = _safe_str(q.get("label") or q.get("question") or q.get("name") or q.get("title"), "Question")
    return _short(raw, _MAX_LABEL) or "Question"


def _question_placeholder(q: Dict[str, Any]) -> str:
    raw = _safe_str(q.get("placeholder") or q.get("description") or q.get("help_text") or "")
    return _short(raw, _MAX_PLACEHOLDER)


def _question_required(q: Dict[str, Any]) -> bool:
    return _safe_bool(q.get("required"), True)


def _question_style(q: Dict[str, Any]) -> discord.TextStyle:
    raw = _safe_str(q.get("style") or q.get("type") or "paragraph").lower()
    if raw in {"short", "text", "single", "single_line", "one-line"}:
        return discord.TextStyle.short
    return discord.TextStyle.paragraph


def _question_max_length(q: Dict[str, Any]) -> int:
    value = _safe_int(q.get("max_length") or q.get("maxLength"), _DEFAULT_MAX_LENGTH)
    return max(50, min(value, 4000))


def _answer_summary(row: Dict[str, Any]) -> List[Dict[str, str]]:
    answers = row.get("_form_answers")
    if not isinstance(answers, list):
        return []
    out: List[Dict[str, str]] = []
    for item in answers:
        if not isinstance(item, dict):
            continue
        label = _safe_str(item.get("label"), "Question")
        answer = _safe_str(item.get("answer"), "No answer provided")
        out.append({"label": label, "answer": answer})
    return out


def _answer_block(row: Dict[str, Any], limit: int = 1000) -> str:
    lines: List[str] = []
    for item in _answer_summary(row):
        label = _short(item.get("label"), 80)
        answer = _short(item.get("answer"), 450)
        lines.append(f"**{label}:**\n{answer}")
    text = "\n\n".join(lines)
    return _short(text, limit) if text else "No answers captured."


class DashboardTicketFormModal(discord.ui.Modal):
    def __init__(self, panel_mod: Any, row: Dict[str, Any], questions: List[Dict[str, Any]]):
        self.panel_mod = panel_mod
        self.row = dict(row)
        self.questions = list(questions[:_MAX_QUESTIONS])
        self.inputs: List[discord.ui.TextInput] = []

        title = f"{panel_mod._row_name(row)} Form"
        super().__init__(title=_short(title, 45), timeout=900)

        for q in self.questions:
            item = discord.ui.TextInput(
                label=_question_label(q),
                placeholder=_question_placeholder(q),
                required=_question_required(q),
                style=_question_style(q),
                max_length=_question_max_length(q),
            )
            self.inputs.append(item)
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        panel_mod = self.panel_mod
        row = dict(self.row)
        answers: List[Dict[str, str]] = []
        for q, item in zip(self.questions, self.inputs):
            answers.append(
                {
                    "label": _question_label(q),
                    "answer": _safe_str(getattr(item, "value", ""), "No answer provided"),
                }
            )
        row["_form_answers"] = answers
        row["_form_answer_count"] = len(answers)
        row["_form_completed"] = True
        return await panel_mod._create_ticket(interaction, row)


async def _show_ticket_form(panel_mod: Any, interaction: discord.Interaction, row: Dict[str, Any], questions: List[Dict[str, Any]]) -> None:
    try:
        await interaction.response.send_modal(DashboardTicketFormModal(panel_mod, row, questions))
    except Exception as e:
        _warn(f"failed to open ticket form: {type(e).__name__}: {e}")
        try:
            await panel_mod._ephemeral(interaction, "❌ Could not open the ticket form. Please try again.")
        except Exception:
            pass


def _patch_create_ticket(panel_mod: Any) -> bool:
    original = getattr(panel_mod, "_create_ticket", None)
    if not callable(original) or getattr(original, "_ticket_forms_wrapped", False):
        return False

    async def wrapped_create_ticket(interaction: discord.Interaction, row: Dict[str, Any]) -> None:
        if isinstance(row, dict) and not row.get("_form_completed"):
            questions = _category_questions(row)
            if _form_enabled(row) and questions:
                return await _show_ticket_form(panel_mod, interaction, row, questions)
        return await original(interaction, row)

    setattr(wrapped_create_ticket, "_ticket_forms_wrapped", True)
    setattr(wrapped_create_ticket, "_ticket_forms_original", original)
    panel_mod._create_ticket = wrapped_create_ticket
    return True


def _patch_category_embed(panel_mod: Any) -> bool:
    original = getattr(panel_mod, "_category_embed", None)
    if not callable(original) or getattr(original, "_ticket_forms_wrapped", False):
        return False

    def wrapped_category_embed(row: Dict[str, Any]) -> discord.Embed:
        embed = original(row)
        questions = _category_questions(row)
        if questions:
            required = sum(1 for q in questions if _question_required(q))
            embed.add_field(
                name="Form Required",
                value=f"This ticket type asks **{len(questions)}** question(s) before opening. Required: **{required}**.",
                inline=False,
            )
            try:
                embed.set_footer(text="No ticket is created until you confirm and complete the form.")
            except Exception:
                pass
        return embed

    setattr(wrapped_category_embed, "_ticket_forms_wrapped", True)
    panel_mod._category_embed = wrapped_category_embed
    return True


def _patch_open_message(panel_mod: Any) -> bool:
    original = getattr(panel_mod, "_open_message", None)
    if not callable(original) or getattr(original, "_ticket_forms_wrapped", False):
        return False

    async def wrapped_open_message(channel: discord.TextChannel, owner: discord.Member, row: Dict[str, Any]) -> None:
        answers = _answer_summary(row) if isinstance(row, dict) else []
        if not answers:
            return await original(channel, owner, row)

        embed = discord.Embed(
            title=f"🎫 {panel_mod._row_name(row)} Ticket",
            description=f"{owner.mention}, staff will help you here.",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Category", value=f"`{panel_mod._row_slug(row)}`", inline=True)
        embed.add_field(name="Opened by", value=owner.mention, inline=True)
        embed.add_field(name="Form Answers", value=_answer_block(row), inline=False)

        view = None
        try:
            from ..tickets_new.panel import TicketChannelActionsView
            view = TicketChannelActionsView()
        except Exception as e:
            _warn(f"ticket action view unavailable: {type(e).__name__}: {e}")

        try:
            await channel.send(
                content=owner.mention,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        except TypeError:
            await channel.send(
                content=owner.mention,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        except Exception as e:
            _warn(f"open message with form answers failed channel={channel.id}: {type(e).__name__}: {e}")
            return await original(channel, owner, row)

    setattr(wrapped_open_message, "_ticket_forms_wrapped", True)
    panel_mod._open_message = wrapped_open_message
    return True


def apply() -> bool:
    try:
        from ..commands_ext import public_ticket_panel_clean as panel_mod
    except Exception as e:
        _warn(f"could not import public_ticket_panel_clean: {e!r}")
        return False

    wrapped = 0
    for patcher in (_patch_create_ticket, _patch_category_embed, _patch_open_message):
        try:
            if patcher(panel_mod):
                wrapped += 1
        except Exception as e:
            _warn(f"patcher failed: {e!r}")

    _log(f"installed dashboard-ready ticket forms wrapped={wrapped}")
    return wrapped > 0


apply()

__all__ = ["apply"]
