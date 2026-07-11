from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REGISTRY = ROOT / "stoney_verify/commands_ext/__init__.py"
MEMBERS = ROOT / "stoney_verify/commands_ext/public_members_group.py"
COMMAND = ROOT / "stoney_verify/commands_ext/public_member_review_feedback.py"
SERVICE = ROOT / "stoney_verify/member_review_feedback.py"
UI = ROOT / "stoney_verify/member_review_ui.py"
TEST = ROOT / "tools/test_staff_verdict_feedback_loop_static.py"
OLD_APPLIER = ROOT / "tools/apply_staff_verdict_feedback_loop.py"


COMMAND_CONTENT = r'''from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional

import discord
from discord import app_commands

from .public_members_group import members_group
from stoney_verify.member_review_feedback import (
    feedback_display_value,
    get_latest_member_review_feedback,
    get_latest_source_review_feedback,
    get_member_review_history,
    infer_latest_source_key,
)
from stoney_verify.member_review_ui import build_member_review_view


_REGISTERED = False


def _cfg_role_id(cfg: Any, key: str) -> int:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return int(str(value))
    except Exception:
        pass

    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return int(str(value))
    except Exception:
        pass

    return 0


async def _can_review(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None or not isinstance(
            interaction.user,
            discord.Member,
        ):
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
            from stoney_verify.guild_config import get_guild_config

            cfg = await get_guild_config(interaction.guild.id)

            staff_ids = {
                role_id
                for role_id in (
                    _cfg_role_id(cfg, "staff_role_id"),
                    _cfg_role_id(cfg, "vc_staff_role_id"),
                )
                if role_id > 0
            }

            return any(
                int(role.id) in staff_ids
                for role in interaction.user.roles
            )
        except Exception:
            return False
    except Exception:
        return False


def _relative_timestamp(value: Any) -> str:
    try:
        raw = str(value or "").strip()
        if not raw:
            return "unknown time"

        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return f"<t:{int(parsed.timestamp())}:R>"
    except Exception:
        return "unknown time"


def _history_embed(
    user: discord.User | discord.Member,
    rows: list[dict[str, Any]],
) -> discord.Embed:
    embed = discord.Embed(
        title="🧠 Member Verdict History",
        description=(
            f"Staff decisions recorded for {user.mention} (`{user.id}`).\n"
            "Newest decision first. Reset entries preserve the audit trail."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )

    if not rows:
        embed.add_field(
            name="History",
            value="No staff verdicts have been recorded for this user.",
            inline=False,
        )
        return embed

    lines: list[str] = []

    for index, row in enumerate(rows[:10], start=1):
        metadata = dict(row.get("metadata") or {})
        label = str(
            metadata.get("verdict_label")
            or metadata.get("verdict")
            or "Unknown"
        )
        actor = str(
            row.get("actor_name")
            or row.get("actor_id")
            or "Unknown staff"
        )
        reason = str(row.get("reason") or "No reason supplied.").strip()
        reason = reason if len(reason) <= 120 else reason[:119] + "…"

        lines.append(
            f"`{index}.` **{label}** • {_relative_timestamp(row.get('created_at'))}\n"
            f"By **{discord.utils.escape_markdown(actor, as_needed=True)}** • {reason}"
        )

    text = "\n\n".join(lines)
    embed.add_field(
        name="Recent Decisions",
        value=text[:1024],
        inline=False,
    )
    embed.set_footer(
        text="Review verdicts are evidence context, not automatic punishment."
    )
    return embed


def _add_context_fields(
    embed: discord.Embed,
    context_fields: list[tuple[str, str, bool]],
) -> None:
    preferred = (
        "Join Intelligence",
        "Evidence & Source",
        "Identity Links",
        "Smart Join Intelligence",
        "Evidence Health",
        "Containment Posture",
    )

    added: set[str] = set()

    for wanted in preferred:
        for name, value, inline in context_fields:
            if name != wanted or name in added:
                continue

            embed.add_field(
                name=name,
                value=str(value)[:1024],
                inline=bool(inline),
            )
            added.add(name)
            break

        if len(added) >= 3:
            break


def _review_embed(
    user: discord.User | discord.Member,
    *,
    context_fields: list[tuple[str, str, bool]],
    previous_feedback: Optional[dict[str, Any]],
    previous_source_feedback: Optional[dict[str, Any]],
    source_key: str,
) -> discord.Embed:
    embed = discord.Embed(
        title="🛡️ Member Intelligence Review",
        description=(
            f"Review {user.mention} (`{user.id}`) before recording a staff verdict.\n\n"
            "**No button on this panel automatically bans, kicks, times out, "
            "or changes roles.**"
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )

    try:
        embed.set_thumbnail(url=user.display_avatar.url)
    except Exception:
        pass

    _add_context_fields(embed, context_fields)

    previous_value = feedback_display_value(previous_feedback)
    if previous_value:
        embed.add_field(
            name="Current Staff Verdict",
            value=previous_value[:1024],
            inline=False,
        )

    source_value = feedback_display_value(previous_source_feedback)
    if source_key and source_value:
        embed.add_field(
            name="Current Source Verdict",
            value=f"Source: `{source_key}`\n{source_value}"[:1024],
            inline=False,
        )

    embed.add_field(
        name="How to Review",
        value=(
            "Use **Looks Safe**, **Watch**, or **False Positive** for common decisions.\n"
            "Use **More Staff Verdicts** for bots, invite sources, alt links, "
            "or resetting only the review verdict."
        ),
        inline=False,
    )

    embed.set_footer(
        text="Reset Review Verdict does not revoke an existing identity/alt link."
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

    # Remove the old long command name if an earlier module version added it.
    try:
        members_group.remove_command("review-history")
    except Exception:
        pass

    existing = {
        getattr(command, "name", "")
        for command in getattr(members_group, "commands", []) or []
    }

    if "review" not in existing:

        @members_group.command(
            name="review",
            description="Open a member intelligence panel and record a staff verdict.",
        )
        @app_commands.describe(
            member="Member or user to review",
        )
        async def review_member(
            interaction: discord.Interaction,
            member: discord.User,
        ) -> None:
            if not await _can_review(interaction):
                await interaction.response.send_message(
                    "❌ Member review requires a configured staff role or "
                    "Administrator, Manage Server, Moderate Members, or Kick Members.",
                    ephemeral=True,
                )
                return

            if interaction.guild is None:
                await interaction.response.send_message(
                    "❌ This command must be used inside a server.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(
                ephemeral=True,
                thinking=True,
            )

            source_key = await asyncio.to_thread(
                infer_latest_source_key,
                guild_id=str(interaction.guild.id),
                user_id=str(member.id),
            )

            previous_feedback_task = asyncio.to_thread(
                get_latest_member_review_feedback,
                guild_id=str(interaction.guild.id),
                user_id=str(member.id),
            )

            if source_key:
                previous_source_task = asyncio.to_thread(
                    get_latest_source_review_feedback,
                    guild_id=str(interaction.guild.id),
                    source_key=source_key,
                )
            else:
                previous_source_task = asyncio.sleep(
                    0,
                    result=None,
                )

            previous_feedback, previous_source_feedback = await asyncio.gather(
                previous_feedback_task,
                previous_source_task,
            )

            context_fields: list[tuple[str, str, bool]] = []

            try:
                from stoney_verify.modlog import _build_member_context_fields

                context_fields = await _build_member_context_fields(
                    interaction.guild,
                    member,
                )
            except Exception:
                context_fields = []

            view = build_member_review_view(
                guild_id=int(interaction.guild.id),
                target_user_id=int(member.id),
                target_is_bot=bool(member.bot),
                source_key=source_key,
                evidence_snapshot={
                    "source": "dank_members_review_panel",
                    "target_user_id": str(member.id),
                    "target_is_bot": bool(member.bot),
                    "source_key": source_key,
                },
            )

            await interaction.followup.send(
                embed=_review_embed(
                    member,
                    context_fields=context_fields,
                    previous_feedback=previous_feedback,
                    previous_source_feedback=previous_source_feedback,
                    source_key=source_key,
                ),
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    existing = {
        getattr(command, "name", "")
        for command in getattr(members_group, "commands", []) or []
    }

    if "history" not in existing:

        @members_group.command(
            name="history",
            description="View staff verdict history for a member or departed user.",
        )
        @app_commands.describe(
            member="Member or user whose verdict history to inspect",
        )
        async def review_history(
            interaction: discord.Interaction,
            member: discord.User,
        ) -> None:
            if not await _can_review(interaction):
                await interaction.response.send_message(
                    "❌ Staff only.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(
                ephemeral=True,
                thinking=True,
            )

            rows = await asyncio.to_thread(
                get_member_review_history,
                guild_id=str(interaction.guild_id or 0),
                user_id=str(member.id),
                limit=10,
            )

            await interaction.followup.send(
                embed=_history_embed(member, rows),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    _REGISTERED = True
    print(
        "✅ public_member_review_feedback: mobile member review panel registered"
    )


__all__ = ["register_public_member_review_feedback_commands"]
'''


MORE_ACTIONS_CLASS = r'''
class MoreReviewActionsSelect(discord.ui.Select):
    def __init__(self, parent_view: "MemberReviewView") -> None:
        self.parent_view = parent_view

        options: list[discord.SelectOption] = []

        if parent_view.target_is_bot:
            options.extend(
                [
                    discord.SelectOption(
                        label="Approved Bot",
                        value="approved_bot",
                        emoji="🤖",
                        description="Official bot is expected and approved.",
                    ),
                    discord.SelectOption(
                        label="Suspicious Bot",
                        value="suspicious_bot",
                        emoji="⚠️",
                        description="Official bot needs staff investigation.",
                    ),
                ]
            )

        if parent_view.source_key:
            options.extend(
                [
                    discord.SelectOption(
                        label="Bad Invite Source",
                        value="bad_invite_source",
                        emoji="🚫",
                        description="This invite/source has concerning activity.",
                    ),
                    discord.SelectOption(
                        label="Clear Invite Source",
                        value="clear_invite_source",
                        emoji="🧼",
                        description="Clear the current source concern.",
                    ),
                ]
            )

        options.extend(
            [
                discord.SelectOption(
                    label="Likely Alt",
                    value="likely_alt",
                    emoji="🟠",
                    description="Link another account as likely the same person.",
                ),
                discord.SelectOption(
                    label="Confirmed Alt",
                    value="confirmed_alt",
                    emoji="🔴",
                    description="Create a confirmed duplicate identity link.",
                ),
                discord.SelectOption(
                    label="Reset Review Verdict",
                    value="reset",
                    emoji="↩️",
                    description="Resets verdict only; identity links stay active.",
                ),
            ]
        )

        super().__init__(
            placeholder="More staff verdicts…",
            min_values=1,
            max_values=1,
            options=options,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0]

        titles = {
            "approved_bot": "Approve Official Bot",
            "suspicious_bot": "Flag Suspicious Bot",
            "bad_invite_source": "Flag Bad Invite Source",
            "clear_invite_source": "Clear Invite Source",
            "likely_alt": "Likely Alt Link",
            "confirmed_alt": "Confirmed Alt Link",
            "reset": "Reset Review Verdict",
        }

        if value in {"likely_alt", "confirmed_alt"}:
            await self.parent_view._open_alt(
                interaction,
                verdict=value,
                title=titles[value],
            )
            return

        await self.parent_view._open_reason(
            interaction,
            verdict=value,
            title=titles[value],
        )
'''


TEST_CONTENT = r'''from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SERVICE = (
    ROOT / "stoney_verify/member_review_feedback.py"
).read_text(encoding="utf-8")
UI = (
    ROOT / "stoney_verify/member_review_ui.py"
).read_text(encoding="utf-8")
COMMAND = (
    ROOT / "stoney_verify/commands_ext/public_member_review_feedback.py"
).read_text(encoding="utf-8")
MEMBERS = (
    ROOT / "stoney_verify/commands_ext/public_members_group.py"
).read_text(encoding="utf-8")
ROUTER = (
    ROOT / "stoney_verify/startup_guards/member_lifecycle_router_guard.py"
).read_text(encoding="utf-8")
REGISTRY = (
    ROOT / "stoney_verify/commands_ext/__init__.py"
).read_text(encoding="utf-8")


def test_feedback_is_guild_scoped_and_non_enforcing() -> None:
    assert 'sb.table("member_events")' in SERVICE
    assert '"guild_id": guild_text' in SERVICE
    assert '"user_id": user_text' in SERVICE
    assert '"actor_id": actor_text' in SERVICE
    assert '"automatic_enforcement": False' in SERVICE


def test_public_profile_loads_member_review_module() -> None:
    assert "register_public_member_review_feedback_commands" in REGISTRY

    start = REGISTRY.index("_PUBLIC_CORE_MODULES:")
    end = REGISTRY.index("_PUBLIC_ADMIN_EXTRA_MODULES:", start)
    core = REGISTRY[start:end]

    assert '"public_member_review_feedback"' in core


def test_review_command_opens_panel_before_verdict() -> None:
    start = COMMAND.index('if "review" not in existing:')
    end = COMMAND.index('if "history" not in existing:', start)
    block = COMMAND[start:end]

    assert "build_member_review_view" in block
    assert "_build_member_context_fields" in block
    assert "previous_feedback" in block
    assert "source_key" in block
    assert "verdict: app_commands.Choice" not in block
    assert "reason: str" not in block


def test_mobile_review_controls_are_compact() -> None:
    assert "class MoreReviewActionsSelect" in UI
    assert 'placeholder="More staff verdicts…"' in UI
    assert "Reset Review Verdict" in UI
    assert "identity links stay active" in UI
    assert "self.add_item(MoreReviewActionsSelect(self))" in UI


def test_command_permission_accepts_configured_staff_roles() -> None:
    assert "staff_role_id" in COMMAND
    assert "vc_staff_role_id" in COMMAND
    assert "get_guild_config" in COMMAND


def test_clean_command_names_replace_old_aliases() -> None:
    assert 'name="review"' in COMMAND
    assert 'name="history"' in COMMAND
    assert 'name="review-history"' not in COMMAND

    assert 'name="scan"' in MEMBERS
    assert 'name="scan-custom"' in MEMBERS
    assert 'name="scan-last"' in MEMBERS

    assert 'name="inactive"' not in MEMBERS
    assert 'name="advanced-scan"' not in MEMBERS
    assert 'name="last-scan"' not in MEMBERS


def test_reset_wording_is_honest() -> None:
    assert '"reset": "Reset Review Verdict"' in SERVICE
    assert '"identity_links_unchanged": verdict_text == "reset"' in SERVICE


def test_staff_audit_still_has_review_controls() -> None:
    start = ROUTER.index("async def _send_staff_join_audit(")
    end = ROUTER.index("async def _send_staff_leave_audit(", start)
    block = ROUTER[start:end]

    assert "build_member_review_view" in block
    assert "view=review_view" in block
    assert "Previous Staff Verdict" in block


def test_review_system_never_punishes_automatically() -> None:
    combined = SERVICE + UI + COMMAND

    for forbidden in (
        ".ban(",
        ".kick(",
        ".timeout(",
        ".add_roles(",
        ".remove_roles(",
    ):
        assert forbidden not in combined


if __name__ == "__main__":
    for test in (
        test_feedback_is_guild_scoped_and_non_enforcing,
        test_public_profile_loads_member_review_module,
        test_review_command_opens_panel_before_verdict,
        test_mobile_review_controls_are_compact,
        test_command_permission_accepts_configured_staff_roles,
        test_clean_command_names_replace_old_aliases,
        test_reset_wording_is_honest,
        test_staff_audit_still_has_review_controls,
        test_review_system_never_punishes_automatically,
    ):
        test()
        print(f"PASS {test.__name__}")
'''


def patch_registry() -> None:
    text = REGISTRY.read_text(encoding="utf-8")

    if "register_public_member_review_feedback_commands" not in text:
        marker = (
            '    ("public_members_group", '
            '"register_public_members_group_commands", '
            '"core: /dank members activity review commands"),\n'
        )
        addition = (
            '    ("public_member_review_feedback", '
            '"register_public_member_review_feedback_commands", '
            '"core: reversible staff verdict feedback for member intelligence"),\n'
        )

        if marker not in text:
            raise SystemExit("Could not find member module registry marker")

        text = text.replace(marker, marker + addition, 1)

    start = text.index("_PUBLIC_CORE_MODULES:")
    end = text.index("_PUBLIC_ADMIN_EXTRA_MODULES:", start)
    core = text[start:end]

    if '"public_member_review_feedback"' not in core:
        marker = '''    "public_members_group",
    "public_members_cleanup_group",
'''
        replacement = '''    "public_members_group",
    "public_member_review_feedback",
    "public_members_cleanup_group",
'''

        if marker not in core:
            raise SystemExit("Could not find public core member module position")

        core = core.replace(marker, replacement, 1)
        text = text[:start] + core + text[end:]

    REGISTRY.write_text(text, encoding="utf-8")
    print("✅ public review module wired into public profile")


def patch_members_commands() -> None:
    text = MEMBERS.read_text(encoding="utf-8")

    duplicate = '''
@members_group.command(name="inactive", description="Open the verified member activity review console.")
async def members_inactive(interaction: discord.Interaction) -> None:
    await _run_activity_scan(interaction)


'''

    text = text.replace(duplicate, "")

    text = text.replace(
        '@members_group.command(name="scan", description="Run the default verified member activity review.")',
        '@members_group.command(name="scan", description="Review verified/resident activity using safe default thresholds.")',
    )

    text = text.replace(
        'name="advanced-scan"',
        'name="scan-custom"',
    )

    text = text.replace(
        'description="Run member review with custom thresholds."',
        'description="Review member activity with custom thresholds."',
    )

    text = text.replace(
        'name="last-scan"',
        'name="scan-last"',
    )

    text = text.replace(
        'description="Show the latest member server-activity review since the bot started."',
        'description="Reopen the latest member activity scan since this restart."',
    )

    for forbidden in (
        'name="inactive"',
        'name="advanced-scan"',
        'name="last-scan"',
    ):
        if forbidden in text:
            raise SystemExit(f"Old member command still exists: {forbidden}")

    MEMBERS.write_text(text, encoding="utf-8")
    print("✅ removed duplicate/wordy member activity aliases")


def patch_service() -> None:
    text = SERVICE.read_text(encoding="utf-8")

    text = text.replace(
        '"reset": "Verdict Reset"',
        '"reset": "Reset Review Verdict"',
    )

    if '"identity_links_unchanged": verdict_text == "reset"' not in text:
        marker = '''        "automatic_enforcement": False,
    }
'''
        replacement = '''        "automatic_enforcement": False,
        "identity_links_unchanged": verdict_text == "reset",
    }
'''

        if marker not in text:
            raise SystemExit("Could not add honest reset metadata")

        text = text.replace(marker, replacement, 1)

    SERVICE.write_text(text, encoding="utf-8")
    print("✅ clarified reset semantics")


def patch_ui() -> None:
    text = UI.read_text(encoding="utf-8")

    if "class MoreReviewActionsSelect" not in text:
        marker = "\n\nclass MemberReviewView(discord.ui.View):"

        if marker not in text:
            raise SystemExit("Could not find MemberReviewView insertion marker")

        text = text.replace(
            marker,
            "\n\n" + MORE_ACTIONS_CLASS.strip() + marker,
            1,
        )

    init_marker = '''        self.evidence_snapshot = dict(evidence_snapshot or {})
'''

    init_addition = '''        self.evidence_snapshot = dict(evidence_snapshot or {})

        # Keep the common actions obvious and move specialist actions into
        # one compact mobile-friendly select menu.
        specialist_labels = {
            "Approved Bot",
            "Suspicious Bot",
            "Bad Source",
            "Clear Source",
            "Likely Alt",
            "Confirm Alt",
            "Reset",
        }

        for item in list(self.children):
            if (
                isinstance(item, discord.ui.Button)
                and str(getattr(item, "label", "")) in specialist_labels
            ):
                self.remove_item(item)

        self.add_item(MoreReviewActionsSelect(self))
'''

    if "self.add_item(MoreReviewActionsSelect(self))" not in text:
        if init_marker not in text:
            raise SystemExit("Could not patch MemberReviewView mobile controls")

        text = text.replace(init_marker, init_addition, 1)

    UI.write_text(text, encoding="utf-8")
    print("✅ condensed specialist verdicts into one mobile select")


def main() -> None:
    patch_registry()
    patch_members_commands()
    patch_service()
    patch_ui()

    COMMAND.write_text(COMMAND_CONTENT, encoding="utf-8")
    TEST.write_text(TEST_CONTENT, encoding="utf-8")

    if OLD_APPLIER.exists():
        OLD_APPLIER.unlink()
        print("🗑️ removed obsolete staff verdict migration applier")

    print("✅ member command surface polish complete")


if __name__ == "__main__":
    main()
