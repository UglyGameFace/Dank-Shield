from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SERVICE = ROOT / "stoney_verify/member_review_feedback.py"
UI = ROOT / "stoney_verify/member_review_ui.py"
COMMAND = ROOT / "stoney_verify/commands_ext/public_member_review_feedback.py"
ROUTER = ROOT / "stoney_verify/startup_guards/member_lifecycle_router_guard.py"
REGISTRY = ROOT / "stoney_verify/commands_ext/__init__.py"
TEST = ROOT / "tools/test_staff_verdict_feedback_loop_static.py"


SERVICE_CONTENT = r'''from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .globals import get_supabase, reset_supabase


MEMBER_REVIEW_EVENT = "member_review_verdict"
SOURCE_REVIEW_EVENT = "source_review_verdict"

VERDICT_LABELS: Dict[str, str] = {
    "looks_safe": "Looks Safe",
    "watch_member": "Watch Member",
    "false_positive": "False Positive",
    "approved_bot": "Approved Bot",
    "suspicious_bot": "Suspicious Bot",
    "bad_invite_source": "Bad Invite Source",
    "clear_invite_source": "Invite Source Cleared",
    "likely_alt": "Likely Alt",
    "confirmed_alt": "Confirmed Alt",
    "reset": "Verdict Reset",
}

ALT_VERDICTS = {"likely_alt", "confirmed_alt"}
BOT_VERDICTS = {"approved_bot", "suspicious_bot"}
SOURCE_VERDICTS = {"bad_invite_source", "clear_invite_source"}


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
        return int(str(value).strip())
    except Exception:
        return int(default)


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        return str(value)
    except Exception:
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_retryable(error: Exception) -> bool:
    text = repr(error).lower()
    return any(
        marker in text
        for marker in (
            "timeout",
            "timed out",
            "connection",
            "network",
            "remoteprotocolerror",
            "temporarily unavailable",
            "too many requests",
            "broken pipe",
            "stream closed",
            "eof",
        )
    )


def _execute(
    op_name: str,
    executor: Callable[[], Any],
    *,
    attempts: int = 5,
) -> Any:
    last_error: Optional[Exception] = None

    for attempt in range(1, attempts + 1):
        try:
            return executor()
        except Exception as exc:
            last_error = exc
            if _is_retryable(exc) and attempt < attempts:
                try:
                    reset_supabase()
                except Exception:
                    pass
                time.sleep(
                    min(0.35 * (2 ** (attempt - 1)), 3.0)
                    + random.uniform(0.05, 0.20)
                )
                continue
            raise

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"{op_name} failed")


def _insert_member_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    def _write() -> Dict[str, Any]:
        sb = get_supabase()
        if sb is None:
            raise RuntimeError("Supabase is not configured.")

        sb.table("member_events").insert(payload).execute()
        return dict(payload)

    return _execute("insert member review event", _write)


def source_key_from_join_context(context: Optional[Dict[str, Any]]) -> str:
    data = dict(context or {})

    invite_code = _safe_str(data.get("invite") or data.get("invite_code"))
    if invite_code.lower() not in {"", "unknown", "none", "null"}:
        return f"invite:{invite_code.lower()}"

    join_source = _safe_str(data.get("source") or data.get("join_source"))
    if join_source.lower() not in {"", "unknown", "unknown_join", "none", "null"}:
        return f"source:{join_source.lower()}"

    entry_method = _safe_str(data.get("entry_method"))
    if entry_method.lower() not in {"", "unknown", "unknown_join", "none", "null"}:
        return f"entry:{entry_method.lower()}"

    return ""


def infer_latest_source_key(*, guild_id: Any, user_id: Any) -> str:
    guild_text = _safe_str(guild_id)
    user_text = _safe_str(user_id)

    if not guild_text or not user_text:
        return ""

    def _read() -> str:
        sb = get_supabase()
        if sb is None:
            return ""

        res = (
            sb.table("member_joins")
            .select("invite_code,join_source,entry_method,entry_confidence")
            .eq("guild_id", guild_text)
            .eq("user_id", user_text)
            .order("joined_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows or not isinstance(rows[0], dict):
            return ""
        return source_key_from_join_context(dict(rows[0]))

    try:
        return _execute("infer latest review source", _read)
    except Exception:
        return ""


def _save_identity_link(
    *,
    guild_id: str,
    user_id: str,
    related_user_id: str,
    verdict: str,
    created_by: str,
    reason: str,
    evidence: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if verdict not in ALT_VERDICTS:
        return None

    if not related_user_id:
        raise ValueError("Related member is required for alt verdicts.")
    if user_id == related_user_id:
        raise ValueError("A member cannot be linked to themselves.")

    from .identity_proof_service import (
        confirm_duplicate_users,
        mark_users_likely_same_person,
    )

    if verdict == "confirmed_alt":
        return confirm_duplicate_users(
            guild_id=guild_id,
            user_a_id=user_id,
            user_b_id=related_user_id,
            created_by=created_by,
            reason=reason,
            evidence=evidence,
        )

    return mark_users_likely_same_person(
        guild_id=guild_id,
        user_a_id=user_id,
        user_b_id=related_user_id,
        created_by=created_by,
        reason=reason,
        evidence=evidence,
    )


def record_member_review_feedback(
    *,
    guild_id: Any,
    user_id: Any,
    verdict: str,
    created_by: Any,
    created_by_name: Optional[str] = None,
    reason: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
    related_user_id: Optional[Any] = None,
    source_key: Optional[str] = None,
) -> Dict[str, Any]:
    guild_text = _safe_str(guild_id)
    user_text = _safe_str(user_id)
    actor_text = _safe_str(created_by)
    verdict_text = _safe_str(verdict).lower()
    reason_text = _safe_str(reason, "No reason supplied.")
    related_text = _safe_str(related_user_id)
    source_text = _safe_str(source_key)

    if not guild_text:
        raise ValueError("guild_id is required")
    if not user_text:
        raise ValueError("user_id is required")
    if not actor_text:
        raise ValueError("created_by is required")
    if verdict_text not in VERDICT_LABELS:
        raise ValueError(f"Unsupported verdict: {verdict_text}")
    if verdict_text in ALT_VERDICTS and not related_text:
        raise ValueError("Related member is required for alt verdicts.")
    if verdict_text in SOURCE_VERDICTS and not source_text:
        raise ValueError("A known invite/source is required for source verdicts.")

    evidence_payload = _json_safe(dict(evidence or {}))
    identity_link = _save_identity_link(
        guild_id=guild_text,
        user_id=user_text,
        related_user_id=related_text,
        verdict=verdict_text,
        created_by=actor_text,
        reason=reason_text,
        evidence=dict(evidence_payload or {}),
    )

    metadata = {
        "verdict": verdict_text,
        "verdict_label": VERDICT_LABELS[verdict_text],
        "related_user_id": related_text or None,
        "source_key": source_text or None,
        "evidence_snapshot": evidence_payload,
        "identity_link_id": (
            _safe_str((identity_link or {}).get("id")) or None
        ),
        "supersedes_previous": True,
        "automatic_enforcement": False,
    }

    created_at = _now_iso()

    member_payload = {
        "guild_id": guild_text,
        "user_id": user_text,
        "actor_id": actor_text,
        "actor_name": _safe_str(created_by_name, actor_text),
        "event_type": MEMBER_REVIEW_EVENT,
        "title": f"Staff Verdict: {VERDICT_LABELS[verdict_text]}",
        "reason": reason_text,
        "metadata": metadata,
        "created_at": created_at,
    }

    saved_member = _insert_member_event(member_payload)
    source_saved = False

    if verdict_text in SOURCE_VERDICTS:
        source_payload = {
            "guild_id": guild_text,
            "user_id": user_text,
            "actor_id": actor_text,
            "actor_name": _safe_str(created_by_name, actor_text),
            "event_type": SOURCE_REVIEW_EVENT,
            "title": f"Source Verdict: {VERDICT_LABELS[verdict_text]}",
            "reason": reason_text,
            "metadata": {
                "verdict": verdict_text,
                "verdict_label": VERDICT_LABELS[verdict_text],
                "source_key": source_text,
                "trigger_user_id": user_text,
                "evidence_snapshot": evidence_payload,
                "automatic_enforcement": False,
            },
            "created_at": created_at,
        }
        _insert_member_event(source_payload)
        source_saved = True

    return {
        "member_event": saved_member,
        "identity_link": identity_link,
        "source_event_saved": source_saved,
        "verdict": verdict_text,
        "verdict_label": VERDICT_LABELS[verdict_text],
    }


def get_latest_member_review_feedback(
    *,
    guild_id: Any,
    user_id: Any,
) -> Optional[Dict[str, Any]]:
    guild_text = _safe_str(guild_id)
    user_text = _safe_str(user_id)

    if not guild_text or not user_text:
        return None

    def _read() -> Optional[Dict[str, Any]]:
        sb = get_supabase()
        if sb is None:
            return None

        res = (
            sb.table("member_events")
            .select("*")
            .eq("guild_id", guild_text)
            .eq("user_id", user_text)
            .eq("event_type", MEMBER_REVIEW_EVENT)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows or not isinstance(rows[0], dict):
            return None

        row = dict(rows[0])
        metadata = dict(row.get("metadata") or {})
        if _safe_str(metadata.get("verdict")).lower() == "reset":
            return None
        return row

    try:
        return _execute("get latest member review", _read)
    except Exception:
        return None


def get_member_review_history(
    *,
    guild_id: Any,
    user_id: Any,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    guild_text = _safe_str(guild_id)
    user_text = _safe_str(user_id)

    if not guild_text or not user_text:
        return []

    safe_limit = max(1, min(int(limit or 10), 25))

    def _read() -> List[Dict[str, Any]]:
        sb = get_supabase()
        if sb is None:
            return []

        res = (
            sb.table("member_events")
            .select("*")
            .eq("guild_id", guild_text)
            .eq("user_id", user_text)
            .eq("event_type", MEMBER_REVIEW_EVENT)
            .order("created_at", desc=True)
            .limit(safe_limit)
            .execute()
        )
        return [
            dict(row)
            for row in (getattr(res, "data", None) or [])
            if isinstance(row, dict)
        ]

    try:
        return _execute("get member review history", _read)
    except Exception:
        return []


def get_latest_source_review_feedback(
    *,
    guild_id: Any,
    source_key: str,
) -> Optional[Dict[str, Any]]:
    guild_text = _safe_str(guild_id)
    source_text = _safe_str(source_key)

    if not guild_text or not source_text:
        return None

    def _read() -> Optional[Dict[str, Any]]:
        sb = get_supabase()
        if sb is None:
            return None

        res = (
            sb.table("member_events")
            .select("*")
            .eq("guild_id", guild_text)
            .eq("event_type", SOURCE_REVIEW_EVENT)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )

        for raw in getattr(res, "data", None) or []:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            metadata = dict(row.get("metadata") or {})
            if _safe_str(metadata.get("source_key")) == source_text:
                return row
        return None

    try:
        return _execute("get latest source review", _read)
    except Exception:
        return None


def feedback_display_value(row: Optional[Dict[str, Any]]) -> str:
    if not row:
        return ""

    metadata = dict(row.get("metadata") or {})
    verdict = _safe_str(metadata.get("verdict"))
    label = _safe_str(
        metadata.get("verdict_label"),
        VERDICT_LABELS.get(verdict, verdict.replace("_", " ").title()),
    )
    actor = _safe_str(row.get("actor_name") or row.get("actor_id"), "Unknown staff")
    reason = _safe_str(row.get("reason"), "No reason supplied.")
    created_at = _safe_str(row.get("created_at"), "unknown time")
    related = _safe_str(metadata.get("related_user_id"))

    lines = [
        f"Verdict: **{label}**",
        f"By: **{actor}**",
        f"Reason: {reason[:500]}",
        f"Recorded: `{created_at}`",
    ]

    if related:
        lines.append(f"Related member: <@{related}> (`{related}`)")

    return "\n".join(lines)[:1000]


__all__ = [
    "ALT_VERDICTS",
    "BOT_VERDICTS",
    "SOURCE_VERDICTS",
    "VERDICT_LABELS",
    "feedback_display_value",
    "get_latest_member_review_feedback",
    "get_latest_source_review_feedback",
    "get_member_review_history",
    "infer_latest_source_key",
    "record_member_review_feedback",
    "source_key_from_join_context",
]
'''


UI_CONTENT = r'''from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import discord

from .member_review_feedback import (
    feedback_display_value,
    record_member_review_feedback,
)


async def _staff_allowed(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False

        perms = interaction.user.guild_permissions
        if (
            perms.administrator
            or perms.manage_guild
            or perms.moderate_members
            or perms.kick_members
        ):
            return True

        try:
            from .guild_config import get_guild_config

            cfg = await get_guild_config(interaction.guild.id)
            staff_ids = {
                int(value)
                for value in (
                    cfg.get("staff_role_id"),
                    cfg.get("vc_staff_role_id"),
                )
                if str(value or "").isdigit()
            }
            return any(int(role.id) in staff_ids for role in interaction.user.roles)
        except Exception:
            return False
    except Exception:
        return False


def _set_field(
    embed: discord.Embed,
    *,
    name: str,
    value: str,
) -> None:
    for index, field in enumerate(embed.fields):
        if str(field.name) == name:
            embed.set_field_at(
                index,
                name=name,
                value=value[:1024],
                inline=False,
            )
            return

    if len(embed.fields) < 25:
        embed.add_field(name=name, value=value[:1024], inline=False)


class ReviewReasonModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        parent_view: "MemberReviewView",
        verdict: str,
        title: str,
    ) -> None:
        super().__init__(title=title[:45], timeout=600)
        self.parent_view = parent_view
        self.verdict = verdict

        self.reason = discord.ui.TextInput(
            label="Reason / evidence",
            placeholder="Explain why staff chose this verdict.",
            min_length=3,
            max_length=500,
            required=True,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.parent_view.submit_feedback(
            interaction,
            verdict=self.verdict,
            reason=str(self.reason.value),
        )


class AltReviewModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        parent_view: "MemberReviewView",
        verdict: str,
        title: str,
    ) -> None:
        super().__init__(title=title[:45], timeout=600)
        self.parent_view = parent_view
        self.verdict = verdict

        self.related_user_id = discord.ui.TextInput(
            label="Related member ID",
            placeholder="Paste the other Discord user ID.",
            min_length=15,
            max_length=22,
            required=True,
        )
        self.reason = discord.ui.TextInput(
            label="Reason / evidence",
            placeholder="Explain the identity connection.",
            min_length=3,
            max_length=500,
            required=True,
            style=discord.TextStyle.paragraph,
        )

        self.add_item(self.related_user_id)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_id = str(self.related_user_id.value).strip().strip("<@!>")

        if not raw_id.isdigit():
            await interaction.response.send_message(
                "❌ Related member ID must be a numeric Discord user ID.",
                ephemeral=True,
            )
            return

        await self.parent_view.submit_feedback(
            interaction,
            verdict=self.verdict,
            reason=str(self.reason.value),
            related_user_id=raw_id,
        )


class MemberReviewView(discord.ui.View):
    def __init__(
        self,
        *,
        guild_id: int,
        target_user_id: int,
        target_is_bot: bool,
        source_key: str = "",
        evidence_snapshot: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(timeout=7 * 24 * 60 * 60)
        self.guild_id = int(guild_id)
        self.target_user_id = int(target_user_id)
        self.target_is_bot = bool(target_is_bot)
        self.source_key = str(source_key or "").strip()
        self.evidence_snapshot = dict(evidence_snapshot or {})

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await _staff_allowed(interaction):
            await interaction.response.send_message(
                "❌ Staff review requires Administrator, Manage Server, "
                "Moderate Members, Kick Members, or the configured staff role.",
                ephemeral=True,
            )
            return False

        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message(
                "❌ This review panel belongs to another server.",
                ephemeral=True,
            )
            return False

        return True

    async def _open_reason(
        self,
        interaction: discord.Interaction,
        *,
        verdict: str,
        title: str,
    ) -> None:
        await interaction.response.send_modal(
            ReviewReasonModal(
                parent_view=self,
                verdict=verdict,
                title=title,
            )
        )

    async def _open_alt(
        self,
        interaction: discord.Interaction,
        *,
        verdict: str,
        title: str,
    ) -> None:
        await interaction.response.send_modal(
            AltReviewModal(
                parent_view=self,
                verdict=verdict,
                title=title,
            )
        )

    async def submit_feedback(
        self,
        interaction: discord.Interaction,
        *,
        verdict: str,
        reason: str,
        related_user_id: Optional[str] = None,
    ) -> None:
        if not await _staff_allowed(interaction):
            await interaction.response.send_message(
                "❌ Staff only.",
                ephemeral=True,
            )
            return

        if verdict in {"approved_bot", "suspicious_bot"} and not self.target_is_bot:
            await interaction.response.send_message(
                "❌ This member is not marked by Discord as an official bot. "
                "Use Watch Member or False Positive for human accounts.",
                ephemeral=True,
            )
            return

        if verdict in {"bad_invite_source", "clear_invite_source"} and not self.source_key:
            await interaction.response.send_message(
                "❌ This join has no known invite/source key to review.",
                ephemeral=True,
            )
            return

        evidence = {
            **self.evidence_snapshot,
            "source": "staff_join_audit_buttons",
            "guild_id": str(self.guild_id),
            "target_user_id": str(self.target_user_id),
            "message_id": str(getattr(interaction.message, "id", "") or ""),
            "channel_id": str(interaction.channel_id or ""),
        }

        try:
            result = await asyncio.to_thread(
                record_member_review_feedback,
                guild_id=str(self.guild_id),
                user_id=str(self.target_user_id),
                verdict=verdict,
                created_by=str(interaction.user.id),
                created_by_name=(
                    getattr(interaction.user, "display_name", None)
                    or str(interaction.user)
                ),
                reason=reason,
                evidence=evidence,
                related_user_id=related_user_id,
                source_key=self.source_key,
            )
        except Exception as exc:
            await interaction.response.send_message(
                f"❌ Could not save staff verdict: `{type(exc).__name__}: {exc}`",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ Saved **{result.get('verdict_label', verdict)}** for "
            f"<@{self.target_user_id}>. This records staff context but does "
            "not automatically punish the member.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        try:
            if interaction.message and interaction.message.embeds:
                embed = interaction.message.embeds[0]
                event_row = dict(result.get("member_event") or {})
                _set_field(
                    embed,
                    name="Staff Verdict",
                    value=feedback_display_value(event_row),
                )

                if verdict in {"bad_invite_source", "clear_invite_source"}:
                    _set_field(
                        embed,
                        name="Source Staff Verdict",
                        value=(
                            f"Source: `{self.source_key}`\n"
                            f"Verdict: **{result.get('verdict_label', verdict)}**\n"
                            f"Reason: {reason[:500]}"
                        ),
                    )

                await interaction.message.edit(embed=embed, view=self)
        except Exception:
            pass

    @discord.ui.button(
        label="Looks Safe",
        emoji="✅",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def looks_safe(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_reason(
            interaction,
            verdict="looks_safe",
            title="Mark Member Looks Safe",
        )

    @discord.ui.button(
        label="Watch",
        emoji="👁️",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def watch_member(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_reason(
            interaction,
            verdict="watch_member",
            title="Watch Member",
        )

    @discord.ui.button(
        label="False Positive",
        emoji="🧯",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def false_positive(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_reason(
            interaction,
            verdict="false_positive",
            title="Mark False Positive",
        )

    @discord.ui.button(
        label="Approved Bot",
        emoji="🤖",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def approved_bot(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_reason(
            interaction,
            verdict="approved_bot",
            title="Approve Official Bot",
        )

    @discord.ui.button(
        label="Suspicious Bot",
        emoji="⚠️",
        style=discord.ButtonStyle.danger,
        row=0,
    )
    async def suspicious_bot(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_reason(
            interaction,
            verdict="suspicious_bot",
            title="Flag Suspicious Bot",
        )

    @discord.ui.button(
        label="Bad Source",
        emoji="🚫",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def bad_source(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_reason(
            interaction,
            verdict="bad_invite_source",
            title="Flag Bad Invite Source",
        )

    @discord.ui.button(
        label="Clear Source",
        emoji="🧼",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def clear_source(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_reason(
            interaction,
            verdict="clear_invite_source",
            title="Clear Invite Source",
        )

    @discord.ui.button(
        label="Likely Alt",
        emoji="🟠",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def likely_alt(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_alt(
            interaction,
            verdict="likely_alt",
            title="Likely Alt Link",
        )

    @discord.ui.button(
        label="Confirm Alt",
        emoji="🔴",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def confirmed_alt(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_alt(
            interaction,
            verdict="confirmed_alt",
            title="Confirmed Alt Link",
        )

    @discord.ui.button(
        label="Reset",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def reset_verdict(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_reason(
            interaction,
            verdict="reset",
            title="Reset Member Verdict",
        )


def build_member_review_view(
    *,
    guild_id: int,
    target_user_id: int,
    target_is_bot: bool,
    source_key: str = "",
    evidence_snapshot: Optional[Dict[str, Any]] = None,
) -> MemberReviewView:
    return MemberReviewView(
        guild_id=guild_id,
        target_user_id=target_user_id,
        target_is_bot=target_is_bot,
        source_key=source_key,
        evidence_snapshot=evidence_snapshot,
    )


__all__ = [
    "MemberReviewView",
    "build_member_review_view",
]
'''


COMMAND_CONTENT = r'''from __future__ import annotations

import asyncio
from typing import Any, Optional

import discord
from discord import app_commands

from .public_members_group import members_group
from stoney_verify.member_review_feedback import (
    ALT_VERDICTS,
    BOT_VERDICTS,
    SOURCE_VERDICTS,
    VERDICT_LABELS,
    feedback_display_value,
    get_member_review_history,
    infer_latest_source_key,
    record_member_review_feedback,
)


_REGISTERED = False


def _can_review(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        perms = interaction.user.guild_permissions
        return bool(
            perms.administrator
            or perms.manage_guild
            or perms.moderate_members
            or perms.kick_members
        )
    except Exception:
        return False


def _history_embed(
    member: discord.Member,
    rows: list[dict[str, Any]],
) -> discord.Embed:
    embed = discord.Embed(
        title="🧠 Staff Verdict History",
        description=f"Review history for {member.mention} (`{member.id}`).",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )

    if not rows:
        embed.add_field(
            name="History",
            value="No staff verdicts have been recorded for this member.",
            inline=False,
        )
        return embed

    for index, row in enumerate(rows[:10], start=1):
        metadata = dict(row.get("metadata") or {})
        label = str(
            metadata.get("verdict_label")
            or metadata.get("verdict")
            or "Unknown"
        )
        value = feedback_display_value(row) or "No details."
        embed.add_field(
            name=f"{index}. {label}",
            value=value[:1024],
            inline=False,
        )

    return embed


def register_public_member_review_feedback_commands(
    bot: Any,
    tree: Any,
) -> None:
    global _REGISTERED
    _ = bot, tree

    if _REGISTERED:
        return

    existing = {
        getattr(command, "name", "")
        for command in getattr(members_group, "commands", []) or []
    }

    if "review" not in existing:

        @members_group.command(
            name="review",
            description="Record a reversible staff verdict for a member.",
        )
        @app_commands.describe(
            member="Member being reviewed",
            verdict="Staff verdict",
            reason="Why staff chose this verdict",
            related_member="Required for Likely Alt or Confirmed Alt",
        )
        @app_commands.choices(
            verdict=[
                app_commands.Choice(name=label, value=value)
                for value, label in VERDICT_LABELS.items()
            ]
        )
        async def review_member(
            interaction: discord.Interaction,
            member: discord.Member,
            verdict: app_commands.Choice[str],
            reason: str,
            related_member: Optional[discord.Member] = None,
        ) -> None:
            if not _can_review(interaction):
                await interaction.response.send_message(
                    "❌ Member review requires Administrator, Manage Server, "
                    "Moderate Members, or Kick Members.",
                    ephemeral=True,
                )
                return

            verdict_value = str(verdict.value)

            if verdict_value in ALT_VERDICTS and related_member is None:
                await interaction.response.send_message(
                    "❌ Choose a related member for an alt verdict.",
                    ephemeral=True,
                )
                return

            if related_member is not None and related_member.id == member.id:
                await interaction.response.send_message(
                    "❌ A member cannot be linked to themselves.",
                    ephemeral=True,
                )
                return

            if verdict_value in BOT_VERDICTS and not member.bot:
                await interaction.response.send_message(
                    "❌ Discord does not mark this member as an official bot.",
                    ephemeral=True,
                )
                return

            source_key = await asyncio.to_thread(
                infer_latest_source_key,
                guild_id=str(interaction.guild_id or 0),
                user_id=str(member.id),
            )

            if verdict_value in SOURCE_VERDICTS and not source_key:
                await interaction.response.send_message(
                    "❌ No known invite/source key exists for this member.",
                    ephemeral=True,
                )
                return

            try:
                result = await asyncio.to_thread(
                    record_member_review_feedback,
                    guild_id=str(interaction.guild_id or 0),
                    user_id=str(member.id),
                    verdict=verdict_value,
                    created_by=str(interaction.user.id),
                    created_by_name=(
                        getattr(interaction.user, "display_name", None)
                        or str(interaction.user)
                    ),
                    reason=reason,
                    evidence={
                        "source": "dank_members_review_command",
                        "member_is_bot": bool(member.bot),
                    },
                    related_user_id=(
                        str(related_member.id)
                        if related_member is not None
                        else None
                    ),
                    source_key=source_key,
                )
            except Exception as exc:
                await interaction.response.send_message(
                    f"❌ Could not save verdict: `{type(exc).__name__}: {exc}`",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="✅ Staff Verdict Saved",
                description=(
                    "This records staff context and evidence. "
                    "It does not automatically punish the member."
                ),
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(
                name="Member",
                value=f"{member.mention} (`{member.id}`)",
                inline=False,
            )
            embed.add_field(
                name="Verdict",
                value=f"**{result.get('verdict_label', verdict.name)}**",
                inline=False,
            )
            embed.add_field(name="Reason", value=reason[:1024], inline=False)

            if related_member is not None:
                embed.add_field(
                    name="Related Member",
                    value=f"{related_member.mention} (`{related_member.id}`)",
                    inline=False,
                )

            if source_key:
                embed.add_field(
                    name="Source Key",
                    value=f"`{source_key}`",
                    inline=False,
                )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    existing = {
        getattr(command, "name", "")
        for command in getattr(members_group, "commands", []) or []
    }

    if "review-history" not in existing:

        @members_group.command(
            name="review-history",
            description="View recorded staff verdict history for a member.",
        )
        @app_commands.describe(member="Member whose review history to inspect")
        async def review_history(
            interaction: discord.Interaction,
            member: discord.Member,
        ) -> None:
            if not _can_review(interaction):
                await interaction.response.send_message(
                    "❌ Staff only.",
                    ephemeral=True,
                )
                return

            rows = await asyncio.to_thread(
                get_member_review_history,
                guild_id=str(interaction.guild_id or 0),
                user_id=str(member.id),
                limit=10,
            )

            await interaction.response.send_message(
                embed=_history_embed(member, rows),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    _REGISTERED = True
    print("✅ public_member_review_feedback: staff verdict loop registered")


__all__ = ["register_public_member_review_feedback_commands"]
'''


TEST_CONTENT = r'''from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SERVICE = (ROOT / "stoney_verify/member_review_feedback.py").read_text(encoding="utf-8")
UI = (ROOT / "stoney_verify/member_review_ui.py").read_text(encoding="utf-8")
COMMAND = (
    ROOT / "stoney_verify/commands_ext/public_member_review_feedback.py"
).read_text(encoding="utf-8")
ROUTER = (
    ROOT / "stoney_verify/startup_guards/member_lifecycle_router_guard.py"
).read_text(encoding="utf-8")
REGISTRY = (
    ROOT / "stoney_verify/commands_ext/__init__.py"
).read_text(encoding="utf-8")


def test_feedback_is_guild_scoped_and_audited() -> None:
    assert 'sb.table("member_events")' in SERVICE
    assert '"guild_id": guild_text' in SERVICE
    assert '"user_id": user_text' in SERVICE
    assert '"actor_id": actor_text' in SERVICE
    assert '"evidence_snapshot"' in SERVICE
    assert '"automatic_enforcement": False' in SERVICE


def test_all_staff_verdicts_exist() -> None:
    for token in (
        "looks_safe",
        "watch_member",
        "false_positive",
        "approved_bot",
        "suspicious_bot",
        "bad_invite_source",
        "clear_invite_source",
        "likely_alt",
        "confirmed_alt",
        "reset",
    ):
        assert token in SERVICE
        assert token in UI


def test_alt_feedback_uses_existing_identity_truth_service() -> None:
    assert "confirm_duplicate_users" in SERVICE
    assert "mark_users_likely_same_person" in SERVICE
    assert "related_user_id" in SERVICE
    assert "A member cannot be linked to themselves" in SERVICE


def test_source_feedback_is_reusable_on_future_joins() -> None:
    assert "SOURCE_REVIEW_EVENT" in SERVICE
    assert "get_latest_source_review_feedback" in SERVICE
    assert "source_key_from_join_context" in SERVICE
    assert "Source Staff Verdict" in UI
    assert "Previous Source Verdict" in ROUTER


def test_join_staff_audit_has_review_controls() -> None:
    start = ROUTER.index("async def _send_staff_join_audit(")
    end = ROUTER.index("async def _send_staff_leave_audit(", start)
    block = ROUTER[start:end]

    assert "build_member_review_view" in block
    assert "view=review_view" in block
    assert "Previous Staff Verdict" in block
    assert "_build_member_context_fields" in block


def test_public_join_leave_card_does_not_get_staff_buttons() -> None:
    start = ROUTER.index("async def _send_join_leave_join(")
    end = ROUTER.index("async def _send_public_join(", start)
    block = ROUTER[start:end]
    assert "build_member_review_view" not in block


def test_review_buttons_do_not_punish_automatically() -> None:
    combined = SERVICE + UI + COMMAND
    for forbidden in (
        ".ban(",
        ".kick(",
        ".timeout(",
        ".add_roles(",
        ".remove_roles(",
    ):
        assert forbidden not in combined


def test_durable_command_fallback_is_registered() -> None:
    assert 'name="review"' in COMMAND
    assert 'name="review-history"' in COMMAND
    assert "public_member_review_feedback" in REGISTRY
    assert '"public_member_review_feedback"' in REGISTRY


if __name__ == "__main__":
    for test in (
        test_feedback_is_guild_scoped_and_audited,
        test_all_staff_verdicts_exist,
        test_alt_feedback_uses_existing_identity_truth_service,
        test_source_feedback_is_reusable_on_future_joins,
        test_join_staff_audit_has_review_controls,
        test_public_join_leave_card_does_not_get_staff_buttons,
        test_review_buttons_do_not_punish_automatically,
        test_durable_command_fallback_is_registered,
    ):
        test()
        print(f"PASS {test.__name__}")
'''


def patch_router() -> None:
    text = ROUTER.read_text(encoding="utf-8")

    if "build_member_review_view" in text and "Previous Staff Verdict" in text:
        print("✅ member lifecycle router already has staff verdict feedback")
        return

    old = '''    embed.set_footer(text="dank_shield:staff_join_audit:v3")
    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
'''

    new = '''    # Add only the most useful intelligence fields so the mobile card stays readable.
    try:
        from stoney_verify.modlog import _build_member_context_fields

        context_fields = await _build_member_context_fields(member.guild, member)
        preferred_names = (
            "Join Intelligence",
            "Evidence & Source",
            "Identity Links",
            "Smart Join Intelligence",
            "Evidence Health",
            "Containment Posture",
        )
        selected = []
        for wanted in preferred_names:
            for item in context_fields:
                if item[0] == wanted and wanted not in {row[0] for row in selected}:
                    selected.append(item)
                    break
            if len(selected) >= 3:
                break

        for name, value, inline in selected:
            if len(embed.fields) >= 24:
                break
            embed.add_field(name=name, value=str(value)[:1024], inline=bool(inline))
    except Exception as exc:
        _log(
            "staff join intelligence unavailable "
            f"guild={member.guild.id} member={member.id}: "
            f"{type(exc).__name__}: {exc}"
        )

    review_view = None
    try:
        from stoney_verify.member_review_feedback import (
            feedback_display_value,
            get_latest_member_review_feedback,
            get_latest_source_review_feedback,
            source_key_from_join_context,
        )
        from stoney_verify.member_review_ui import build_member_review_view

        source_key = source_key_from_join_context(invite)

        latest_feedback = await asyncio.to_thread(
            get_latest_member_review_feedback,
            guild_id=str(member.guild.id),
            user_id=str(member.id),
        )
        previous_value = feedback_display_value(latest_feedback)
        if previous_value and len(embed.fields) < 24:
            embed.add_field(
                name="Previous Staff Verdict",
                value=previous_value[:1024],
                inline=False,
            )

        if source_key:
            latest_source = await asyncio.to_thread(
                get_latest_source_review_feedback,
                guild_id=str(member.guild.id),
                source_key=source_key,
            )
            source_value = feedback_display_value(latest_source)
            if source_value and len(embed.fields) < 24:
                embed.add_field(
                    name="Previous Source Verdict",
                    value=(
                        f"Source: `{source_key}`\\n{source_value}"
                    )[:1024],
                    inline=False,
                )

        review_view = build_member_review_view(
            guild_id=int(member.guild.id),
            target_user_id=int(member.id),
            target_is_bot=bool(member.bot),
            source_key=source_key,
            evidence_snapshot={
                "invite_context": dict(invite or {}),
                "account_age": _member_age_text(member),
                "member_is_bot": bool(member.bot),
                "member_name": str(member),
            },
        )
    except Exception as exc:
        _log(
            "staff verdict controls unavailable "
            f"guild={member.guild.id} member={member.id}: "
            f"{type(exc).__name__}: {exc}"
        )

    embed.set_footer(text="dank_shield:staff_join_audit:v4")
    await channel.send(
        embed=embed,
        view=review_view,
        allowed_mentions=discord.AllowedMentions.none(),
    )
'''

    if old not in text:
        raise SystemExit("Could not find staff join audit send block")

    ROUTER.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("✅ patched detailed staff join audit with intelligence + verdict controls")


def patch_registry() -> None:
    text = REGISTRY.read_text(encoding="utf-8")

    module_line = (
        '    ("public_member_review_feedback", '
        '"register_public_member_review_feedback_commands", '
        '"core: reversible staff verdict feedback for member intelligence"),\n'
    )

    if "register_public_member_review_feedback_commands" not in text:
        marker = (
            '    ("public_members_group", '
            '"register_public_members_group_commands", '
            '"core: /dank members activity review commands"),\n'
        )
        if marker not in text:
            raise SystemExit("Could not find public_members_group registry marker")
        text = text.replace(marker, marker + module_line, 1)

    core_marker = '''    "public_members_group",
    "public_members_cleanup_group",
'''
    core_replacement = '''    "public_members_group",
    "public_member_review_feedback",
    "public_members_cleanup_group",
'''

    if '"public_member_review_feedback"' not in text:
        if core_marker not in text:
            raise SystemExit("Could not find public core members module marker")
        text = text.replace(core_marker, core_replacement, 1)

    REGISTRY.write_text(text, encoding="utf-8")
    print("✅ registered public member review feedback commands")


def main() -> None:
    SERVICE.write_text(SERVICE_CONTENT, encoding="utf-8")
    UI.write_text(UI_CONTENT, encoding="utf-8")
    COMMAND.write_text(COMMAND_CONTENT, encoding="utf-8")
    TEST.write_text(TEST_CONTENT, encoding="utf-8")

    patch_router()
    patch_registry()

    print("✅ staff verdict feedback loop files written")


if __name__ == "__main__":
    main()
