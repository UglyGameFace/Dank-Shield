from __future__ import annotations

from typing import Any, Dict, List, Optional

import re

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from .common import reply_once, _staff_check

try:
    from ..identity_proof_service import (
        confirm_duplicate_users,
        get_identity_truth_context,
        mark_users_likely_same_person,
        mark_users_not_linked,
        record_verified_identity_for_user,
    )
except Exception:
    confirm_duplicate_users = None  # type: ignore
    get_identity_truth_context = None  # type: ignore
    mark_users_likely_same_person = None  # type: ignore
    mark_users_not_linked = None  # type: ignore
    record_verified_identity_for_user = None  # type: ignore

try:
    from ..raidguard import build_member_risk_profile
except Exception:
    build_member_risk_profile = None  # type: ignore


_IDENTITY_ADMIN_REGISTERED = False


def _safe_str(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _chunk_lines(lines: List[str], *, limit: int = 1000) -> List[str]:
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for line in lines:
        text = _safe_str(line)
        if not text:
            continue
        projected = current_len + len(text) + (1 if current else 0)
        if current and projected > limit:
            chunks.append("\n".join(current))
            current = [text]
            current_len = len(text)
        else:
            current.append(text)
            current_len = projected

    if current:
        chunks.append("\n".join(current))

    return chunks or ["—"]


def _member_label(member: discord.Member | discord.User) -> str:
    try:
        return f"{member.mention} (`{member.id}`)\n`{member}`"
    except Exception:
        return f"`{getattr(member, 'id', 'unknown')}`"


def _normalize_lookup_text(value: object) -> str:
    try:
        return str(value or "").strip().lower()
    except Exception:
        return ""


def _parse_member_id_from_target(target: str) -> int:
    text = str(target or "").strip()
    if not text:
        return 0

    mention_match = re.search(r"<@!?(\d+)>", text)
    if mention_match:
        try:
            return int(mention_match.group(1))
        except Exception:
            return 0

    if text.isdigit():
        try:
            return int(text)
        except Exception:
            return 0

    return 0


async def _resolve_member_any(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    try:
        member = guild.get_member(int(user_id))
        if member is not None:
            return member
    except Exception:
        pass

    try:
        return await guild.fetch_member(int(user_id))
    except Exception:
        return None


async def _resolve_member_from_target(
    guild: discord.Guild,
    target: str,
) -> Optional[discord.Member]:
    raw = str(target or "").strip()
    if not raw:
        return None

    user_id = _parse_member_id_from_target(raw)
    if user_id > 0:
        member = await _resolve_member_any(guild, user_id)
        if member is not None:
            return member

    lowered = _normalize_lookup_text(raw)
    if not lowered:
        return None

    try:
        member_list = list(guild.members or [])
    except Exception:
        member_list = []

    def _candidate_strings(member: discord.Member) -> List[str]:
        vals = [
            getattr(member, "name", None),
            getattr(member, "display_name", None),
            getattr(member, "global_name", None),
            str(member),
        ]
        out: List[str] = []
        for v in vals:
            norm = _normalize_lookup_text(v)
            if norm and norm not in out:
                out.append(norm)
        return out

    for member in member_list:
        try:
            if lowered in _candidate_strings(member):
                return member
        except Exception:
            continue

    for member in member_list:
        try:
            tag = f"{member.name}#{member.discriminator}" if getattr(member, "discriminator", "0") != "0" else member.name
            if _normalize_lookup_text(tag) == lowered:
                return member
        except Exception:
            continue

    startswith_hits: List[discord.Member] = []
    for member in member_list:
        try:
            values = _candidate_strings(member)
            if any(v.startswith(lowered) for v in values):
                startswith_hits.append(member)
        except Exception:
            continue

    if len(startswith_hits) == 1:
        return startswith_hits[0]

    contains_hits: List[discord.Member] = []
    for member in member_list:
        try:
            values = _candidate_strings(member)
            if any(lowered in v for v in values):
                contains_hits.append(member)
        except Exception:
            continue

    if len(contains_hits) == 1:
        return contains_hits[0]

    try:
        await guild.chunk(cache=True)
        member_list = list(guild.members or [])
    except Exception:
        member_list = member_list

    for member in member_list:
        try:
            if lowered in _candidate_strings(member):
                return member
        except Exception:
            continue

    return None


async def _require_target_member(
    interaction: discord.Interaction,
    target: str,
    *,
    label: str = "member",
) -> Optional[discord.Member]:
    guild = interaction.guild
    if guild is None:
        await reply_once(
            interaction,
            {"content": "❌ This command must be used in a server.", "ephemeral": True},
        )
        return None

    member = await _resolve_member_from_target(guild, target)
    if member is None:
        await reply_once(
            interaction,
            {
                "content": (
                    f"❌ I could not resolve that {label}.\n"
                    "Use a mention, raw user ID, exact username, or exact display name."
                ),
                "ephemeral": True,
            },
        )
        return None

    return member


def _render_truth_context_embed(
    guild: discord.Guild,
    member: discord.Member,
    truth: Dict[str, Any],
    risk_profile: Optional[Dict[str, Any]] = None,
) -> discord.Embed:
    embed = discord.Embed(
        title="🧬 Identity Truth Context",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Member", value=_member_label(member), inline=False)

    if risk_profile:
        tier = _safe_str(risk_profile.get("evidence_tier")) or "clear"
        level = _safe_str(risk_profile.get("level")) or "low"
        score = int(risk_profile.get("score") or 0)
        embed.add_field(
            name="Current Risk Engine Output",
            value=(
                f"tier=`{tier}` • level=`{level}` • score=`{score}/100`\n"
                f"identity_matches=`{int(risk_profile.get('identity_proof_match_count') or 0)}` • "
                f"manual_confirmed=`{int(risk_profile.get('manual_confirmed_match_count') or 0)}` • "
                f"manual_likely=`{int(risk_profile.get('manual_likely_match_count') or 0)}`"
            ),
            inline=False,
        )

    proof_matches = list(truth.get("proof_matches") or [])
    manual_confirmed = list(truth.get("manual_confirmed") or [])
    manual_likely = list(truth.get("manual_likely") or [])
    manual_not_linked = list(truth.get("manual_not_linked") or [])

    embed.add_field(
        name="Truth Totals",
        value=(
            f"proof_matches=`{len(proof_matches)}` • "
            f"manual_confirmed=`{len(manual_confirmed)}` • "
            f"manual_likely=`{len(manual_likely)}` • "
            f"not_linked=`{len(manual_not_linked)}`"
        ),
        inline=False,
    )

    def _other_label(user_id: Any) -> str:
        try:
            uid = int(str(user_id or "0") or 0)
        except Exception:
            uid = 0
        if uid <= 0:
            return "`unknown`"
        member_obj = guild.get_member(uid)
        if member_obj:
            return f"{member_obj.mention} (`{uid}`)"
        return f"`{uid}`"

    if proof_matches:
        lines = []
        for row in proof_matches[:10]:
            lines.append(
                f"• {_other_label(row.get('matched_user_id') or row.get('other_user_id'))} "
                f"confidence=`{row.get('match_confidence', 100)}` "
                f"fingerprint=`{_safe_str(row.get('identity_fingerprint'))[:24] or 'hidden'}`"
            )
        for idx, chunk in enumerate(_chunk_lines(lines, limit=900), start=1):
            embed.add_field(
                name="Verified Identity Matches" + (f" ({idx})" if idx > 1 else ""),
                value=chunk,
                inline=False,
            )

    if manual_confirmed:
        lines = []
        for row in manual_confirmed[:10]:
            lines.append(
                f"• {_other_label(row.get('other_user_id'))} "
                f"by=`{_safe_str(row.get('created_by')) or 'unknown'}` "
                f"reason={_safe_str(row.get('reason'))[:120] or '—'}"
            )
        for idx, chunk in enumerate(_chunk_lines(lines, limit=900), start=1):
            embed.add_field(
                name="Manual Confirmed Duplicate Links" + (f" ({idx})" if idx > 1 else ""),
                value=chunk,
                inline=False,
            )

    if manual_likely:
        lines = []
        for row in manual_likely[:10]:
            lines.append(
                f"• {_other_label(row.get('other_user_id'))} "
                f"by=`{_safe_str(row.get('created_by')) or 'unknown'}` "
                f"reason={_safe_str(row.get('reason'))[:120] or '—'}"
            )
        for idx, chunk in enumerate(_chunk_lines(lines, limit=900), start=1):
            embed.add_field(
                name="Manual Likely Same-Person Links" + (f" ({idx})" if idx > 1 else ""),
                value=chunk,
                inline=False,
            )

    if manual_not_linked:
        lines = []
        for row in manual_not_linked[:10]:
            lines.append(
                f"• {_other_label(row.get('other_user_id'))} "
                f"by=`{_safe_str(row.get('created_by')) or 'unknown'}` "
                f"reason={_safe_str(row.get('reason'))[:120] or '—'}"
            )
        for idx, chunk in enumerate(_chunk_lines(lines, limit=900), start=1):
            embed.add_field(
                name="Manual Not-Linked Suppressions" + (f" ({idx})" if idx > 1 else ""),
                value=chunk,
                inline=False,
            )

    if not any([proof_matches, manual_confirmed, manual_likely, manual_not_linked]):
        embed.description = "No hard truth records exist yet for this member."

    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass

    return embed


def register_identity_admin_commands(bot: Any, tree: Any) -> None:
    global _IDENTITY_ADMIN_REGISTERED

    if _IDENTITY_ADMIN_REGISTERED:
        try:
            print("ℹ️ commands_ext.identity_admin already registered; skipping duplicate registration.")
        except Exception:
            pass
        return

    # ------------------------------------------------------------
    # /identity_truth
    # ------------------------------------------------------------
    @tree.command(
        name="identity_truth",
        description="Inspect hard-proof and manual identity link context for a member.",
    )
    @app_commands.describe(member="Mention, ID, username, or display name to inspect")
    async def identity_truth(interaction: discord.Interaction, member: str):
        if not _staff_check(interaction):
            await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
            return

        if get_identity_truth_context is None:
            await reply_once(
                interaction,
                {"content": "❌ identity_proof_service is not available.", "ephemeral": True},
            )
            return

        resolved_member = await _require_target_member(interaction, member)
        if resolved_member is None:
            return

        try:
            truth = get_identity_truth_context(
                guild_id=str(interaction.guild_id or 0),
                user_id=str(resolved_member.id),
            )
        except Exception as e:
            await reply_once(
                interaction,
                {"content": f"❌ Failed loading truth context: {e}", "ephemeral": True},
            )
            return

        risk_profile = None
        if callable(build_member_risk_profile):
            try:
                risk_profile = build_member_risk_profile(resolved_member)
            except Exception:
                risk_profile = None

        embed = _render_truth_context_embed(interaction.guild, resolved_member, truth, risk_profile)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    # ------------------------------------------------------------
    # /identity_confirm_duplicate
    # ------------------------------------------------------------
    @tree.command(
        name="identity_confirm_duplicate",
        description="Staff-confirm that two members are the same person.",
    )
    @app_commands.describe(
        member_a="First member: mention, ID, username, or display name",
        member_b="Second member: mention, ID, username, or display name",
        reason="Why you are confirming this duplicate identity link",
    )
    async def identity_confirm_duplicate(
        interaction: discord.Interaction,
        member_a: str,
        member_b: str,
        reason: str,
    ):
        if not _staff_check(interaction):
            await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
            return

        if confirm_duplicate_users is None:
            await reply_once(
                interaction,
                {"content": "❌ identity_proof_service is not available.", "ephemeral": True},
            )
            return

        resolved_a = await _require_target_member(interaction, member_a, label="first member")
        if resolved_a is None:
            return

        resolved_b = await _require_target_member(interaction, member_b, label="second member")
        if resolved_b is None:
            return

        if int(resolved_a.id) == int(resolved_b.id):
            await reply_once(
                interaction,
                {"content": "❌ You must choose two different users.", "ephemeral": True},
            )
            return

        try:
            row = confirm_duplicate_users(
                guild_id=str(interaction.guild_id or 0),
                user_a_id=str(resolved_a.id),
                user_b_id=str(resolved_b.id),
                created_by=str(interaction.user.id),
                reason=_safe_str(reason),
                evidence={
                    "source": "identity_admin_command",
                    "staff_name": getattr(interaction.user, "display_name", None) or str(interaction.user),
                    "member_a": str(resolved_a.id),
                    "member_b": str(resolved_b.id),
                },
            )
        except Exception as e:
            await reply_once(
                interaction,
                {"content": f"❌ Failed saving confirmed duplicate link: {e}", "ephemeral": True},
            )
            return

        embed = discord.Embed(
            title="✅ Confirmed Duplicate Link Saved",
            color=discord.Color.red(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Member A", value=_member_label(resolved_a), inline=False)
        embed.add_field(name="Member B", value=_member_label(resolved_b), inline=False)
        embed.add_field(name="Reason", value=_safe_str(reason)[:1000] or "—", inline=False)
        embed.add_field(name="Link ID", value=f"`{_safe_str((row or {}).get('id')) or 'unknown'}`", inline=False)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    # ------------------------------------------------------------
    # /identity_mark_likely
    # ------------------------------------------------------------
    @tree.command(
        name="identity_mark_likely",
        description="Mark two members as likely the same person, but not fully confirmed.",
    )
    @app_commands.describe(
        member_a="First member: mention, ID, username, or display name",
        member_b="Second member: mention, ID, username, or display name",
        reason="Why this looks like the same person",
    )
    async def identity_mark_likely(
        interaction: discord.Interaction,
        member_a: str,
        member_b: str,
        reason: str,
    ):
        if not _staff_check(interaction):
            await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
            return

        if mark_users_likely_same_person is None:
            await reply_once(
                interaction,
                {"content": "❌ identity_proof_service is not available.", "ephemeral": True},
            )
            return

        resolved_a = await _require_target_member(interaction, member_a, label="first member")
        if resolved_a is None:
            return

        resolved_b = await _require_target_member(interaction, member_b, label="second member")
        if resolved_b is None:
            return

        if int(resolved_a.id) == int(resolved_b.id):
            await reply_once(
                interaction,
                {"content": "❌ You must choose two different users.", "ephemeral": True},
            )
            return

        try:
            row = mark_users_likely_same_person(
                guild_id=str(interaction.guild_id or 0),
                user_a_id=str(resolved_a.id),
                user_b_id=str(resolved_b.id),
                created_by=str(interaction.user.id),
                reason=_safe_str(reason),
                evidence={
                    "source": "identity_admin_command",
                    "staff_name": getattr(interaction.user, "display_name", None) or str(interaction.user),
                    "member_a": str(resolved_a.id),
                    "member_b": str(resolved_b.id),
                },
            )
        except Exception as e:
            await reply_once(
                interaction,
                {"content": f"❌ Failed saving likely same-person link: {e}", "ephemeral": True},
            )
            return

        embed = discord.Embed(
            title="🟠 Likely Same-Person Link Saved",
            color=discord.Color.orange(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Member A", value=_member_label(resolved_a), inline=False)
        embed.add_field(name="Member B", value=_member_label(resolved_b), inline=False)
        embed.add_field(name="Reason", value=_safe_str(reason)[:1000] or "—", inline=False)
        embed.add_field(name="Link ID", value=f"`{_safe_str((row or {}).get('id')) or 'unknown'}`", inline=False)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    # ------------------------------------------------------------
    # /identity_mark_not_linked
    # ------------------------------------------------------------
    @tree.command(
        name="identity_mark_not_linked",
        description="Suppress repeated false positives by marking two members as not linked.",
    )
    @app_commands.describe(
        member_a="First member: mention, ID, username, or display name",
        member_b="Second member: mention, ID, username, or display name",
        reason="Why these accounts should not be linked",
    )
    async def identity_mark_not_linked(
        interaction: discord.Interaction,
        member_a: str,
        member_b: str,
        reason: str,
    ):
        if not _staff_check(interaction):
            await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
            return

        if mark_users_not_linked is None:
            await reply_once(
                interaction,
                {"content": "❌ identity_proof_service is not available.", "ephemeral": True},
            )
            return

        resolved_a = await _require_target_member(interaction, member_a, label="first member")
        if resolved_a is None:
            return

        resolved_b = await _require_target_member(interaction, member_b, label="second member")
        if resolved_b is None:
            return

        if int(resolved_a.id) == int(resolved_b.id):
            await reply_once(
                interaction,
                {"content": "❌ You must choose two different users.", "ephemeral": True},
            )
            return

        try:
            row = mark_users_not_linked(
                guild_id=str(interaction.guild_id or 0),
                user_a_id=str(resolved_a.id),
                user_b_id=str(resolved_b.id),
                created_by=str(interaction.user.id),
                reason=_safe_str(reason),
                evidence={
                    "source": "identity_admin_command",
                    "staff_name": getattr(interaction.user, "display_name", None) or str(interaction.user),
                    "member_a": str(resolved_a.id),
                    "member_b": str(resolved_b.id),
                },
            )
        except Exception as e:
            await reply_once(
                interaction,
                {"content": f"❌ Failed saving not-linked suppression: {e}", "ephemeral": True},
            )
            return

        embed = discord.Embed(
            title="🟢 Not-Linked Suppression Saved",
            color=discord.Color.green(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Member A", value=_member_label(resolved_a), inline=False)
        embed.add_field(name="Member B", value=_member_label(resolved_b), inline=False)
        embed.add_field(name="Reason", value=_safe_str(reason)[:1000] or "—", inline=False)
        embed.add_field(name="Link ID", value=f"`{_safe_str((row or {}).get('id')) or 'unknown'}`", inline=False)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    # ------------------------------------------------------------
    # /identity_record_fingerprint
    # ------------------------------------------------------------
    @tree.command(
        name="identity_record_fingerprint",
        description="Record a trusted identity fingerprint for a member from verified evidence.",
    )
    @app_commands.describe(
        member="Member to attach the trusted fingerprint to: mention, ID, username, or display name",
        identity_fingerprint="Privacy-safe irreversible fingerprint",
        source="Source: manual_review, id_verification, voice_verification, etc.",
        notes="Optional notes about the verification source",
    )
    async def identity_record_fingerprint(
        interaction: discord.Interaction,
        member: str,
        identity_fingerprint: str,
        source: str,
        notes: Optional[str] = None,
    ):
        if not _staff_check(interaction):
            await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
            return

        if record_verified_identity_for_user is None:
            await reply_once(
                interaction,
                {"content": "❌ identity_proof_service is not available.", "ephemeral": True},
            )
            return

        resolved_member = await _require_target_member(interaction, member)
        if resolved_member is None:
            return

        source_text = _safe_str(source).lower()
        allowed = {
            "manual_review",
            "id_verification",
            "voice_verification",
            "document_verification",
            "selfie_match",
            "external_account_link",
            "trusted_admin_override",
        }
        if source_text not in allowed:
            await reply_once(
                interaction,
                {
                    "content": (
                        "❌ Invalid source. Use one of:\n"
                        + ", ".join(f"`{x}`" for x in sorted(allowed))
                    ),
                    "ephemeral": True,
                },
            )
            return

        try:
            row = record_verified_identity_for_user(
                guild_id=str(interaction.guild_id or 0),
                user_id=str(resolved_member.id),
                identity_fingerprint=_safe_str(identity_fingerprint),
                source=source_text,
                created_by=str(interaction.user.id),
                fingerprint_version="v1",
                confidence=100,
                notes=_safe_str(notes) or None,
                evidence={
                    "source": "identity_admin_command",
                    "staff_name": getattr(interaction.user, "display_name", None) or str(interaction.user),
                    "member_id": str(resolved_member.id),
                },
            )
        except Exception as e:
            await reply_once(
                interaction,
                {"content": f"❌ Failed saving trusted fingerprint: {e}", "ephemeral": True},
            )
            return

        embed = discord.Embed(
            title="🧬 Trusted Identity Fingerprint Recorded",
            color=discord.Color.green(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Member", value=_member_label(resolved_member), inline=False)
        embed.add_field(name="Source", value=f"`{source_text}`", inline=True)
        embed.add_field(name="Proof ID", value=f"`{_safe_str((row or {}).get('id')) or 'unknown'}`", inline=True)
        embed.add_field(
            name="Fingerprint",
            value=f"`{_safe_str(identity_fingerprint)[:64]}`",
            inline=False,
        )
        if notes:
            embed.add_field(name="Notes", value=_safe_str(notes)[:1000], inline=False)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    _IDENTITY_ADMIN_REGISTERED = True

    try:
        print("✅ commands_ext.identity_admin: registered identity truth admin commands")
    except Exception:
        pass


__all__ = [
    "register_identity_admin_commands",
]
