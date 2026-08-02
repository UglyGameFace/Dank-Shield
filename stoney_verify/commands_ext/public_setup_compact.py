from __future__ import annotations

"""Compact presentation layer for the canonical public ``/dank setup`` flow.

This module registers no command. It replaces oversized presentation callbacks
only after the canonical setup owner has loaded, preserving every existing
feature service and callback owner underneath it.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Optional

import discord

from ..guild_config import get_guild_config
from ..setup_service_state import mark_setup_completed, service_state_from_config
from . import public_setup_recommend as setup

_PATCHED = False
_ORIGINAL_HEALTH = setup._build_plain_setup_health_embed
_ORIGINAL_PROGRESS = setup._setup_progress
_ORIGINAL_CATEGORY_PAYLOAD = setup.solid._build_category_manager_payload

FEATURE_AREAS = (
    ("core", "Setup Plan & Server Items", "🧩", "Features, roles, channels, timers, and rules."),
    ("tickets", "Tickets", "🎫", "Panels, staff routing, folders, and choices."),
    ("verification", "Verification", "✅", "Simple, Voice, and approved ID/Web flows."),
    ("security", "Security & SpamGuard", "🛡️", "SpamGuard, raids, AntiNuke, access, and repairs."),
    ("logs", "Logs & Activity", "🧾", "Logging choices, channels, and activity coverage."),
    ("design", "Server Design", "🎨", "Auto-detect, previews, styling, and undo."),
    ("welcome", "Welcome & Join", "👋", "Welcome messages, join cards, and announcements."),
    ("profiles", "Profile Signatures", "🪪", "Signatures, appearance, privacy, and platforms."),
    ("history", "Backups & History", "💾", "Back up and restore selected setup areas."),
)

TEST_SPECS = {
    "tickets": (
        "Tickets", "🎫",
        "Create a test ticket. Confirm staff can claim, close, reopen, save a transcript, and delete it.",
    ),
    "simple_verify": (
        "Simple Verify", "✅",
        "Post or refresh the Verify panel, then use a second account. Confirm its role and channel access.",
    ),
    "verification": (
        "Verification", "✅",
        "Use a second account to complete the configured flow and confirm its final role and access.",
    ),
    "voice_verify": (
        "Voice Verify", "🎙️",
        "Request Voice Verify from a second account. Confirm staff can receive, claim, and complete it privately.",
    ),
    "id_verify": (
        "ID / Web Verify", "🪪",
        "Use an approved test account. Confirm review stays private and the final decision updates access.",
    ),
    "spam_guard": (
        "SpamGuard", "🛡️",
        "Use a private test channel. Confirm the intended protection and log appear without blocking normal messages.",
    ),
    "logs": (
        "Logs", "🧾",
        "Confirm the other test actions appear once in the correct configured log channels.",
    ),
}

ButtonCallback = Callable[[discord.Interaction], Awaitable[None]]


def _add(
    view: discord.ui.View,
    label: str,
    emoji: str,
    style: discord.ButtonStyle,
    custom_id: str,
    callback: ButtonCallback,
    row: int,
    *,
    disabled: bool = False,
) -> discord.ui.Button:
    item = discord.ui.Button(
        label=label,
        emoji=emoji,
        style=style,
        custom_id=custom_id,
        row=row,
        disabled=disabled,
    )
    item.callback = callback
    view.add_item(item)
    return item


def _enabled_text(state: Any) -> str:
    try:
        labels = [str(value) for value in state.enabled_labels() if str(value).strip()]
    except Exception:
        labels = []
    return " · ".join(labels) if labels else "None selected yet"


def _trim_lines(value: Any, limit: int, fallback: str = "") -> str:
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    kept = lines[:limit]
    if len(lines) > limit:
        kept.append(f"…and {len(lines) - limit} more")
    return "\n".join(kept) or fallback


async def _require_guild(interaction: discord.Interaction) -> Optional[discord.Guild]:
    if not await setup.solid._require_setup_permission(interaction):
        return None
    if interaction.guild is not None:
        return interaction.guild
    await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    return None


async def _action_error(interaction: discord.Interaction, label: str, exc: Exception) -> None:
    message = f"❌ {label}: `{type(exc).__name__}: {str(exc)[:220]}`"
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True)
        else:
            await interaction.followup.send(message, ephemeral=True)
    except Exception:
        pass


async def _route_area(interaction: discord.Interaction, area: str) -> None:
    if not await setup.solid._require_setup_permission(interaction):
        return
    routes = {
        "core": setup._open_advanced_core_setup,
        "tickets": setup._open_advanced_member_experience,
        "verification": setup._open_advanced_verification,
        "security": setup._open_advanced_security,
        "logs": setup._open_advanced_logs_activity,
        "design": setup._open_advanced_appearance,
        "history": setup._open_config_history,
    }
    if area in routes:
        await routes[area](interaction)
    elif area == "welcome":
        from stoney_verify import welcome_setup_ui
        await welcome_setup_ui.open_welcome_setup(interaction)
    elif area == "profiles":
        from stoney_verify import profile_card_setup_ui
        await profile_card_setup_ui.open_profile_card_setup(interaction)
    else:
        await _open_manager(interaction)


class FeatureAreaSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Choose a feature area…",
            min_values=1,
            max_values=1,
            custom_id="dank_setup_compact:area",
            row=0,
            options=[
                discord.SelectOption(
                    label=label,
                    value=value,
                    emoji=emoji,
                    description=description[:100],
                )
                for value, label, emoji, description in FEATURE_AREAS
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _route_area(interaction, str(self.values[0]) if self.values else "")


class CompactSetupHomeView(discord.ui.View):
    def __init__(self, *, ready: bool = False, started: bool = False, completed: bool = False) -> None:
        super().__init__(timeout=900)
        self.ready = bool(ready)
        self.started = bool(started)
        self.completed = bool(completed)
        if self.started:
            self.add_item(FeatureAreaSelect())

        if self.completed:
            primary = ("View Setup Summary", "✅")
        elif self.ready:
            primary = ("Test Features", "🧪")
        elif self.started:
            primary = ("Continue Setup", "➡️")
        else:
            primary = ("Start Setup", "⚡")
        _add(self, *primary, discord.ButtonStyle.success, "dank_setup_home:continue", self._primary, 1)

        if self.started:
            _add(self, "Change Plan", "🧭", discord.ButtonStyle.secondary, "dank_setup_home:plan", self._plan, 1)
            _add(self, "Check Configuration", "🩺", discord.ButtonStyle.secondary, "dank_setup_home:check", self._check, 2)
            _add(self, "Advanced", "⚙️", discord.ButtonStyle.secondary, "dank_setup_home:advanced", self._advanced, 2)
        _add(self, "Close", "✖️", discord.ButtonStyle.danger, "dank_setup_home:close", self._close, 2)

    async def _primary(self, interaction: discord.Interaction) -> None:
        if self.completed:
            await setup._open_completed_summary(interaction)
        elif self.ready:
            await _open_tests(interaction)
        elif self.started:
            await _open_guided(interaction)
        else:
            await _open_plan(interaction)

    async def _plan(self, interaction: discord.Interaction) -> None:
        await _open_plan(interaction)

    async def _check(self, interaction: discord.Interaction) -> None:
        await setup._open_health_check(interaction)

    async def _advanced(self, interaction: discord.Interaction) -> None:
        await _open_advanced(interaction)

    async def _close(self, interaction: discord.Interaction) -> None:
        await setup._close_setup(interaction)


class CompactManagerView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)
        self.add_item(FeatureAreaSelect())
        _add(self, "Change Plan", "🧭", discord.ButtonStyle.primary, "dank_setup_compact:plan", self._plan, 1)
        _add(self, "Check Configuration", "🩺", discord.ButtonStyle.secondary, "dank_setup_compact:check", self._check, 1)
        _add(self, "Advanced", "⚙️", discord.ButtonStyle.secondary, "dank_setup_compact:advanced", self._advanced, 2)
        _add(self, "Setup Home", "🏠", discord.ButtonStyle.secondary, "dank_setup_compact:home", self._home, 2)
        _add(self, "Close", "✖️", discord.ButtonStyle.danger, "dank_setup_compact:close", self._close, 2)

    async def _plan(self, interaction: discord.Interaction) -> None:
        await _open_plan(interaction)

    async def _check(self, interaction: discord.Interaction) -> None:
        await setup._open_health_check(interaction)

    async def _advanced(self, interaction: discord.Interaction) -> None:
        await _open_advanced(interaction)

    async def _home(self, interaction: discord.Interaction) -> None:
        await setup._home_edit(interaction)

    async def _close(self, interaction: discord.Interaction) -> None:
        await setup._close_setup(interaction)


class CompactAdvancedView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)
        _add(self, "Repair / Restart", "🧯", discord.ButtonStyle.danger, "dank_setup_advanced:repair", self._repair, 0)
        _add(self, "Help", "❓", discord.ButtonStyle.secondary, "dank_setup_advanced:help", self._help, 0)
        _add(self, "Manage Features", "🧰", discord.ButtonStyle.primary, "dank_setup_advanced:features", self._features, 1)
        _add(self, "Setup Home", "🏠", discord.ButtonStyle.secondary, "dank_setup_advanced:home", self._home, 1)
        _add(self, "Close", "✖️", discord.ButtonStyle.danger, "dank_setup_advanced:close", self._close, 1)

    async def _repair(self, interaction: discord.Interaction) -> None:
        await setup._open_advanced_danger_zone(interaction)

    async def _help(self, interaction: discord.Interaction) -> None:
        if await setup.solid._require_setup_permission(interaction):
            await setup.solid._edit_or_followup(interaction, embed=_help_embed(), view=CompactAdvancedView())

    async def _features(self, interaction: discord.Interaction) -> None:
        await _open_manager(interaction)

    async def _home(self, interaction: discord.Interaction) -> None:
        await setup._home_edit(interaction)

    async def _close(self, interaction: discord.Interaction) -> None:
        await setup._close_setup(interaction)


class CompactReviewView(discord.ui.View):
    def __init__(self, *, ready: bool) -> None:
        super().__init__(timeout=900)
        self.ready = bool(ready)
        _add(
            self,
            "Test Features" if ready else "Fix Next Required Item",
            "🧪" if ready else "➡️",
            discord.ButtonStyle.success,
            "dank_setup_review:next",
            self._next,
            0,
        )
        _add(self, "Setup Home", "🏠", discord.ButtonStyle.secondary, "dank_setup_review:home", self._home, 1)
        _add(self, "Close", "✖️", discord.ButtonStyle.danger, "dank_setup_review:close", self._close, 1)

    async def _next(self, interaction: discord.Interaction) -> None:
        if not await setup.solid._require_setup_permission(interaction):
            return
        if self.ready:
            await _open_tests(interaction)
            return
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
            return
        target, _title, _explanation, key = await setup._guided_setup_target(guild)
        await setup._open_guided_target(interaction, target, key)

    async def _home(self, interaction: discord.Interaction) -> None:
        await setup._home_edit(interaction)

    async def _close(self, interaction: discord.Interaction) -> None:
        await setup._close_setup(interaction)


def _help_embed() -> discord.Embed:
    return discord.Embed(
        title="❓ Setup Help",
        description=(
            "**Start / Continue Setup** handles one required item at a time.\n"
            "**Choose a feature area** changes one part of Dank Shield.\n"
            "**Check Configuration** automatically validates saved roles, channels, and permissions.\n"
            "**Test Features** walks through real member and staff behavior."
        ),
        color=discord.Color.blurple(),
    )


async def _main_payload(guild: discord.Guild) -> tuple[discord.Embed, discord.ui.View]:
    progress, done, total, next_step = await _ORIGINAL_PROGRESS(guild)
    try:
        state = service_state_from_config(await get_guild_config(guild.id, refresh=True))
    except Exception:
        state = service_state_from_config(None)

    started = bool(state.setup_choice)
    ready = bool(total and done >= total)
    completed = bool(ready and state.completed)
    remaining = max(0, total - done)
    issues = [line for line in str(progress).splitlines() if line.startswith(("⚠️", "🚫", "❌"))]

    if not started:
        status = "Not started"
        next_text = "Choose a plan. Setup will then show one required item at a time."
    elif completed:
        status = "Setup complete"
        next_text = "Choose a feature area below whenever you need to change something."
    elif ready:
        status = "Ready for real testing"
        next_text = "Test each enabled feature in Discord, then finish setup."
    else:
        status = f"{remaining} required {'item' if remaining == 1 else 'items'} left"
        next_text = str(next_step or "Continue Setup.")[:350]

    embed = discord.Embed(
        title="🚀 Dank Shield Setup",
        description=(
            f"**{status}** · `{done}/{total}` required\n"
            f"Plan: **{state.setup_label}**\nEnabled: {_enabled_text(state)}"
        ),
        color=discord.Color.green() if ready else discord.Color.blurple(),
    )
    embed.add_field(name="Next", value=next_text, inline=False)
    if issues:
        embed.add_field(name="Needs attention", value=_trim_lines(issues, 2), inline=False)
    return embed, CompactSetupHomeView(ready=ready, started=started, completed=completed)


async def _health_embed(guild: discord.Guild) -> discord.Embed:
    original = await _ORIGINAL_HEALTH(guild)
    fields = {str(field.name): str(field.value) for field in original.fields}
    target, _title, _explanation, _key = await setup._guided_setup_target(guild)

    if target == "ready":
        embed = discord.Embed(
            title="✅ Configuration Check Passed",
            description=(
                "Saved roles, channels, choices, and permissions look ready. "
                "This check does **not** claim the real feature flows were tested."
            ),
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Next",
            value="Press **Test Features** and confirm each enabled flow in the server.",
            inline=False,
        )
    else:
        blockers = fields.get("Fix These First") or fields.get("Try this") or original.description
        embed = discord.Embed(
            title="🚫 Configuration Needs Attention",
            description="Fix this before testing features.",
            color=discord.Color.red(),
        )
        embed.add_field(name="Fix next", value=_trim_lines(blockers, 3, "Return to guided setup."), inline=False)

    warnings = fields.get("Optional Later", "")
    if warnings and not warnings.lstrip().startswith("✅"):
        embed.add_field(name="Optional later", value=_trim_lines(warnings, 2), inline=False)
    return embed


async def _open_plan(interaction: discord.Interaction) -> None:
    guild = await _require_guild(interaction)
    if guild is None:
        return
    await setup.solid._safe_defer_update(interaction)
    from . import public_setup_fresh_choice as fresh

    hidden = (
        "\n\n🔒 ID/Web plans are hidden because this server is not approved for them."
        if not fresh.id_verify_allowed_for_guild(guild)
        else ""
    )
    embed = discord.Embed(
        title="⚡ Choose a Setup Plan",
        description="Pick the closest goal. You can change individual features later." + hidden,
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Changing plans does not delete server items.")
    await setup.solid._edit_or_followup(interaction, embed=embed, view=fresh.SetupTypeChoiceView(guild=guild))


async def _open_guided(interaction: discord.Interaction, *, saved_message: str = "") -> None:
    guild = await _require_guild(interaction)
    if guild is None:
        return
    await setup.solid._safe_defer_update(interaction)
    target, title, explanation, key = await setup._guided_setup_target(guild)
    if target == "ready":
        await setup._open_health_check(interaction, saved_message=saved_message, already_deferred=True)
        return

    _progress, done, total, _next = await _ORIGINAL_PROGRESS(guild)
    saved = f"✅ {saved_message}\n\n" if saved_message else ""
    embed = discord.Embed(
        title=f"⚡ Quick Setup · {done}/{total}",
        description=f"{saved}**Next: {title}**\n{explanation}",
        color=discord.Color.blurple(),
    )
    view = setup.ContinueSetupView(target=target, requirement_key=key, ready=False)
    await setup.solid._edit_or_followup(interaction, embed=embed, view=view)


async def _open_manager(interaction: discord.Interaction) -> None:
    guild = await _require_guild(interaction)
    if guild is None:
        return
    try:
        state = service_state_from_config(await get_guild_config(guild.id, refresh=True))
        enabled = _enabled_text(state)
    except Exception:
        enabled = "Could not load right now"
    embed = discord.Embed(
        title="🧰 Manage Dank Shield",
        description=f"Choose one feature area below.\nEnabled: **{enabled}**",
        color=discord.Color.blurple(),
    )
    await setup.solid._edit_or_followup(interaction, embed=embed, view=CompactManagerView())


async def _open_advanced(interaction: discord.Interaction) -> None:
    if not await setup.solid._require_setup_permission(interaction):
        return
    embed = discord.Embed(
        title="⚙️ Advanced Setup",
        description="Troubleshooting and recovery stay here so they do not crowd normal setup.",
        color=discord.Color.blurple(),
    )
    await setup.solid._edit_or_followup(interaction, embed=embed, view=CompactAdvancedView())


async def _category_payload(
    guild: discord.Guild,
    *,
    title: str = "🎫 Ticket Menu",
) -> tuple[discord.Embed, Any]:
    _old, view = await _ORIGINAL_CATEGORY_PAYLOAD(guild, title=title)
    error = str(getattr(view, "db_error", "") or "")
    loaded_rows = list(getattr(view, "rows", []) or [])
    if error:
        embed = discord.Embed(title=title, description="Ticket choices could not be loaded.", color=discord.Color.red())
        embed.add_field(name="Error", value=error[:1024], inline=False)
        return embed, view

    rows = [
        row
        for row in loaded_rows
        if isinstance(row, dict)
        and setup._plain_bool(row.get("is_enabled", row.get("enabled", True)), default=True)
    ]
    default = next((row for row in rows if bool(row.get("is_default"))), {})
    embed = discord.Embed(
        title=title,
        description=(
            f"Choices enabled: **{len(rows)}**\n"
            f"Default fallback: **{default.get('name') or 'Not chosen'}**\n"
            "Use the controls below to edit, add, reorder, or review choices."
        ),
        color=discord.Color.blurple(),
    )
    warning = setup.solid._category_governance_text(rows)
    if not str(warning).lstrip().startswith("✅"):
        embed.add_field(name="Needs attention", value=str(warning)[:1024], inline=False)
    return embed, view


async def _launch_state(guild: discord.Guild) -> dict[str, Any]:
    state = await setup.load_setup_service_state(guild.id)
    return {
        "tickets": bool(state.tickets),
        "verification": bool(state.verification_enabled),
        "basic_verify": bool(state.simple_verify),
        "voice_verify": bool(state.voice_verify),
        "id_verify": bool(state.id_verify),
        "spam_guard": bool(state.spam_guard),
        "logs": bool(state.logs),
        "completed": bool(state.completed),
    }


def required_test_keys(state: dict[str, Any]) -> tuple[str, ...]:
    keys: list[str] = []
    if state.get("tickets"):
        keys.append("tickets")

    specific = False
    for source, key in (
        ("basic_verify", "simple_verify"),
        ("voice_verify", "voice_verify"),
        ("id_verify", "id_verify"),
    ):
        if state.get(source):
            keys.append(key)
            specific = True
    if state.get("verification") and not specific:
        keys.append("verification")
    if state.get("spam_guard"):
        keys.append("spam_guard")
    if state.get("logs"):
        keys.append("logs")
    return tuple(keys)


def _confirmed(
    state: dict[str, Any],
    values: Optional[set[str] | frozenset[str]],
) -> frozenset[str]:
    return frozenset(set(required_test_keys(state)).intersection(set(values or set())))


class TestAreaSelect(discord.ui.Select):
    def __init__(self, state: dict[str, Any], confirmed: frozenset[str]) -> None:
        self.state = dict(state)
        self.confirmed = frozenset(confirmed)
        super().__init__(
            placeholder="Choose a feature to test…",
            min_values=1,
            max_values=1,
            custom_id="dank_setup_test:area",
            row=0,
            options=[
                discord.SelectOption(
                    label=TEST_SPECS[key][0],
                    value=key,
                    emoji="✅" if key in confirmed else TEST_SPECS[key][1],
                    description=("Tested" if key in confirmed else "Not tested") + " · open instructions",
                )
                for key in required_test_keys(state)
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _open_feature_test(
            interaction,
            self.state,
            self.confirmed,
            str(self.values[0]) if self.values else "",
        )


class CompactTestView(discord.ui.View):
    def __init__(
        self,
        state: Optional[dict[str, Any]] = None,
        *,
        confirmed: Optional[set[str] | frozenset[str]] = None,
    ) -> None:
        super().__init__(timeout=900)
        self.state = dict(state or {})
        self.confirmed = _confirmed(self.state, confirmed)
        required = set(required_test_keys(self.state))
        if required:
            self.add_item(TestAreaSelect(self.state, self.confirmed))
        if not self.state.get("completed"):
            _add(
                self,
                "Finish Setup",
                "🏁",
                discord.ButtonStyle.success,
                "dank_setup_test:finish",
                self._finish,
                1,
                disabled=not required.issubset(self.confirmed),
            )
        _add(self, "Recheck Configuration", "🩺", discord.ButtonStyle.secondary, "dank_setup_test:check", self._check, 1)
        _add(self, "Setup Home", "🏠", discord.ButtonStyle.secondary, "dank_setup_test:home", self._home, 2)
        _add(self, "Close", "✖️", discord.ButtonStyle.danger, "dank_setup_test:close", self._close, 2)

    async def _finish(self, interaction: discord.Interaction) -> None:
        await _finish(interaction, self.confirmed)

    async def _check(self, interaction: discord.Interaction) -> None:
        await setup._open_health_check(interaction)

    async def _home(self, interaction: discord.Interaction) -> None:
        await setup._home_edit(interaction)

    async def _close(self, interaction: discord.Interaction) -> None:
        await setup._close_setup(interaction)


class FeatureTestView(discord.ui.View):
    def __init__(self, state: dict[str, Any], confirmed: frozenset[str], key: str) -> None:
        super().__init__(timeout=900)
        self.state = dict(state)
        self.confirmed = frozenset(confirmed)
        self.key = key

        if key == "tickets":
            _add(self, "Post / Refresh Ticket Panel", "🎫", discord.ButtonStyle.primary, "dank_setup_test:ticket_panel", self._ticket_panel, 0)
            _add(self, "Create Test Ticket", "🧪", discord.ButtonStyle.primary, "dank_setup_test:create_ticket", self._ticket, 0)
        elif key == "simple_verify":
            _add(self, "Post / Refresh Verify Panel", "✅", discord.ButtonStyle.primary, "dank_setup_test:verify_panel", self._verify_panel, 0)

        _add(
            self,
            "Tested" if key in confirmed else "Mark Tested",
            "✅",
            discord.ButtonStyle.success,
            "dank_setup_test:mark",
            self._mark,
            1,
        )
        _add(self, "Back to Checklist", "↩️", discord.ButtonStyle.secondary, "dank_setup_test:back", self._back, 1)
        _add(self, "Close", "✖️", discord.ButtonStyle.danger, "dank_setup_test:close_feature", self._close, 1)

    async def _ticket_panel(self, interaction: discord.Interaction) -> None:
        try:
            from .public_ticket_panel_commands import post_ticket_panel_callback
            await post_ticket_panel_callback(interaction)
        except Exception as exc:
            await _action_error(interaction, "Could not post the ticket panel", exc)

    async def _ticket(self, interaction: discord.Interaction) -> None:
        await setup._create_setup_test_ticket(interaction)

    async def _verify_panel(self, interaction: discord.Interaction) -> None:
        try:
            from .public_verify_basic_panel import verify_panel
            await verify_panel(interaction)
        except Exception as exc:
            await _action_error(interaction, "Could not post the Verify panel", exc)

    async def _mark(self, interaction: discord.Interaction) -> None:
        if not await setup.solid._require_setup_permission(interaction):
            return
        await setup.solid._safe_defer_update(interaction)
        await _render_tests(interaction, self.state, frozenset(set(self.confirmed) | {self.key}))

    async def _back(self, interaction: discord.Interaction) -> None:
        await setup.solid._safe_defer_update(interaction)
        await _render_tests(interaction, self.state, self.confirmed)

    async def _close(self, interaction: discord.Interaction) -> None:
        await setup._close_setup(interaction)


async def _render_tests(
    interaction: discord.Interaction,
    state: dict[str, Any],
    confirmed: Optional[set[str] | frozenset[str]] = None,
) -> None:
    checked = _confirmed(state, confirmed)
    required = set(required_test_keys(state))
    lines = [
        f"{'✅' if key in checked else '⬜'} **{TEST_SPECS[key][0]}**"
        for key in required_test_keys(state)
    ]
    if state.get("completed"):
        finish = "Setup is already finished. Select any area to test it again."
    elif required.issubset(checked):
        finish = "Every enabled feature is marked tested. **Finish Setup** is unlocked."
    else:
        finish = "Finish Setup unlocks after every enabled feature is marked tested."

    embed = discord.Embed(
        title="🧪 Test Features",
        description=(
            "The automatic configuration check passed. Now verify real Discord "
            "behavior one feature at a time."
        ),
        color=discord.Color.green() if required.issubset(checked) else discord.Color.blurple(),
    )
    embed.add_field(
        name=f"Progress · {len(checked)}/{len(required)}",
        value="\n".join(lines) or "No enabled features require testing.",
        inline=False,
    )
    embed.add_field(name="Finish", value=finish, inline=False)
    await setup.solid._edit_or_followup(
        interaction,
        embed=embed,
        view=CompactTestView(state, confirmed=checked),
    )


async def _open_tests(
    interaction: discord.Interaction,
    *,
    confirmed: Optional[set[str] | frozenset[str]] = None,
) -> None:
    guild = await _require_guild(interaction)
    if guild is None:
        return
    await setup.solid._safe_defer_update(interaction)
    target, _title, _explanation, _key = await setup._guided_setup_target(guild)
    if target != "ready":
        await setup._open_health_check(interaction, already_deferred=True)
        return
    await _render_tests(interaction, await _launch_state(guild), confirmed)


async def _open_feature_test(
    interaction: discord.Interaction,
    state: dict[str, Any],
    confirmed: frozenset[str],
    key: str,
) -> None:
    if not await setup.solid._require_setup_permission(interaction):
        return
    if key not in required_test_keys(state):
        await _render_tests(interaction, state, confirmed)
        return

    label, emoji, instructions = TEST_SPECS[key]
    tested = key in confirmed
    embed = discord.Embed(
        title=f"{emoji} Test {label}",
        description=instructions,
        color=discord.Color.green() if tested else discord.Color.blurple(),
    )
    embed.add_field(
        name="Status",
        value=(
            "✅ Marked tested. Run it again whenever needed."
            if tested
            else "⬜ Check the real result first, then press **Mark Tested**."
        ),
        inline=False,
    )
    await setup.solid._edit_or_followup(
        interaction,
        embed=embed,
        view=FeatureTestView(state, confirmed, key),
    )


async def _finish(
    interaction: discord.Interaction,
    confirmed: Optional[set[str] | frozenset[str]] = None,
) -> None:
    guild = await _require_guild(interaction)
    if guild is None:
        return
    await setup.solid._safe_defer_update(interaction)
    target, _title, _explanation, _key = await setup._guided_setup_target(guild)
    if target != "ready":
        await setup._open_health_check(interaction, already_deferred=True)
        return

    state = await _launch_state(guild)
    required = set(required_test_keys(state))
    checked = _confirmed(state, confirmed)
    if not state.get("completed") and not required.issubset(checked):
        await _render_tests(interaction, state, checked)
        return

    completed = await mark_setup_completed(guild.id, actor=interaction.user)
    embed = discord.Embed(
        title="✅ Setup Finished",
        description=(
            "The enabled features were confirmed. Future setup changes "
            "automatically return this server to **Needs review**."
        ),
        color=discord.Color.green(),
    )
    embed.add_field(name="Enabled", value=_enabled_text(completed), inline=False)
    await setup.solid._edit_or_followup(interaction, embed=embed, view=setup.FinishedSetupView())


def apply_compact_setup_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return
    setup._build_plain_setup_health_embed = _health_embed
    setup._build_setup_help_embed = _help_embed
    setup._product_main_setup_payload = _main_payload
    setup._open_choose_setup_type = _open_plan
    setup._open_guided_setup = _open_guided
    setup._open_manage_setup = _open_manager
    setup._open_advanced_settings = _open_manager
    setup._open_test_launch = _open_tests
    setup._finish_setup = _finish
    setup.ProductSetupHomeView = CompactSetupHomeView
    setup.ManageSetupView = CompactManagerView
    setup.AdvancedSettingsHubView = CompactManagerView
    setup.SetupReviewView = CompactReviewView
    setup.LaunchTestView = CompactTestView
    setup.solid._build_category_manager_payload = _category_payload
    _PATCHED = True


def register_public_setup_compact_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    apply_compact_setup_patch()
    print("✅ public_setup_compact: compact navigation and explicit feature testing active")


__all__ = [
    "CompactAdvancedView",
    "CompactManagerView",
    "CompactReviewView",
    "CompactSetupHomeView",
    "CompactTestView",
    "FeatureAreaSelect",
    "FeatureTestView",
    "TestAreaSelect",
    "apply_compact_setup_patch",
    "register_public_setup_compact_commands",
    "required_test_keys",
]
