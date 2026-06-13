from __future__ import annotations

"""Attach direct fix/test buttons to setup health results.

This turns Health Check into a small installer dashboard: progress, exact fix
routes, safe test mode, create test ticket, refresh, cleanup, and back.
"""

from typing import Any, Optional

import discord

_PATCHED = False
_ORIGINAL_EDIT_OR_FOLLOWUP: Any = None

_PROGRESS_FIELDS = ("Blockers", "Warnings", "Passing Checks")


def _is_health_embed(embed: Any) -> bool:
    try:
        title = str(getattr(embed, "title", "") or "").lower()
        desc = str(getattr(embed, "description", "") or "").lower()
        if "setup health" in title or "health check" in title or "recommended next click" in desc:
            return True
        names = {str(getattr(field, "name", "") or "") for field in list(getattr(embed, "fields", []) or [])}
        return bool(set(_PROGRESS_FIELDS).intersection(names))
    except Exception:
        return False


def _field_value(embed: Any, name: str) -> str:
    try:
        for field in list(getattr(embed, "fields", []) or []):
            if str(getattr(field, "name", "") or "") == name:
                return str(getattr(field, "value", "") or "")
    except Exception:
        pass
    return ""


def _has_items(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    return "✅ none" not in lowered and lowered not in {"none", "no passing checks reported."}


def _progress_from_embed(embed: Any) -> tuple[int, int, str]:
    blockers = _field_value(embed, "Blockers")
    warnings = _field_value(embed, "Warnings")
    passing = _field_value(embed, "Passing Checks")
    blocked = _has_items(blockers)
    warn = _has_items(warnings)
    pass_count = 0 if not _has_items(passing) else max(1, min(8, passing.count("\n") + 1))
    total = max(6, min(10, pass_count + (2 if blocked else 0) + (1 if warn else 0)))
    complete = max(0, min(total, total - (2 if blocked else 0) - (1 if warn else 0)))
    filled = int(round((complete / max(1, total)) * 10))
    bar = "█" * filled + "░" * (10 - filled)
    return complete, total, bar


def _route_from_embed(embed: Any) -> tuple[str, str, str]:
    text = "\n".join(
        str(getattr(field, "value", "") or "")
        for field in list(getattr(embed, "fields", []) or [])
    ).lower()
    title = str(getattr(embed, "title", "") or "").lower()
    haystack = f"{title}\n{text}"
    if any(key in haystack for key in ("ticket category", "open ticket", "archive", "transcript", "staff role", "ticket basics")):
        return "ticket", "Fix Ticket Basics", "🎫"
    if any(key in haystack for key in ("modlog", "mod log", "join log", "status channel", "logs")):
        return "logs", "Fix Logs + Status", "🧾"
    if any(key in haystack for key in ("unverified", "verified role", "resident", "server-control", "server control", "access role")):
        return "roles", "Fix Access Roles", "🎭"
    if any(key in haystack for key in ("verify channel", "verification channel", "vc verify", "voice channel", "queue channel")):
        return "channels", "Fix Verification Channels", "🎙️"
    if any(key in haystack for key in ("ticket menu", "ticket_categories", "routing", "category option")):
        return "routing", "Fix Ticket Routing", "🗂️"
    return "existing", "Fix Missing Items", "🧩"


def _prepend_progress(embed: discord.Embed) -> discord.Embed:
    try:
        if any(str(getattr(field, "name", "") or "") == "Setup Progress" for field in list(getattr(embed, "fields", []) or [])):
            return embed
        complete, total, bar = _progress_from_embed(embed)
        percent = int(round((complete / max(1, total)) * 100))
        old_fields = [
            (str(getattr(field, "name", "") or ""), str(getattr(field, "value", "") or ""), bool(getattr(field, "inline", False)))
            for field in list(getattr(embed, "fields", []) or [])
        ]
        embed.clear_fields()
        embed.add_field(
            name="Setup Progress",
            value=f"`{complete}/{total}` complete • `{percent}%`\n`{bar}`",
            inline=False,
        )
        for name, value, inline in old_fields[:20]:
            embed.add_field(name=(name or "Status")[:256], value=(value or "—")[:1024], inline=inline)
    except Exception:
        pass
    return embed


async def _open_existing(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    embed = discord.Embed(
        title="🧩 Use Existing Roles/Channels",
        description=(
            "Pick the exact roles/channels/categories your server already uses.\n"
            "Names do not matter — Dank Shield saves Discord IDs."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Best order",
        value=(
            "1. **Ticket Basics** — open/closed categories, staff role, transcripts\n"
            "2. **Access Roles** — waiting/approved/member roles\n"
            "3. **Verification Channels** — only if your server uses them\n"
            "4. **Logs + Status** — modlog/status channels\n"
            "5. **Optional Rules** — prefix/timers/style"
        ),
        inline=False,
    )
    await solid._edit_or_followup(interaction, embed=embed, view=solid.ChooseExistingView())


async def _open_exact_route(interaction: discord.Interaction, route: str) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)

    if route == "ticket":
        embed = discord.Embed(
            title="🎫 Fix Ticket Basics",
            description="Pick where tickets open/close, the staff role, and transcripts. Each dropdown saves immediately.",
            color=discord.Color.blurple(),
        )
        return await solid._edit_or_followup(interaction, embed=embed, view=solid.TicketBasicsPickerView())
    if route == "roles":
        embed = discord.Embed(
            title="🎭 Fix Access Roles",
            description="Pick the roles your server uses. Leave optional roles alone if your server does not use them.",
            color=discord.Color.blurple(),
        )
        return await solid._edit_or_followup(interaction, embed=embed, view=solid.AccessRolesPickerView())
    if route == "channels":
        embed = discord.Embed(
            title="🎙️ Fix Verification Channels",
            description="Pick the text/voice channels used by verification. Leave unused flows blank.",
            color=discord.Color.blurple(),
        )
        return await solid._edit_or_followup(interaction, embed=embed, view=solid.VerificationChannelsPickerView())
    if route == "logs":
        embed = discord.Embed(
            title="🧾 Fix Logs + Status",
            description="Pick modlog, join/leave log, and bot status channels. Names do not matter.",
            color=discord.Color.blurple(),
        )
        return await solid._edit_or_followup(interaction, embed=embed, view=solid.LogsStatusPickerView())
    if route == "routing":
        embed, view = await solid._build_category_manager_payload(guild)
        return await solid._edit_or_followup(interaction, embed=embed, view=view)
    return await _open_existing(interaction)


async def _open_main_setup(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    embed, view = await solid._build_main_setup_payload(guild)
    await solid._edit_or_followup(interaction, embed=embed, view=view)


async def _open_cleanup(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    if not await solid._require_setup_permission(interaction):
        return
    await solid._safe_defer_update(interaction)
    embed = discord.Embed(
        title="🧹 Undo / Cleanup",
        description=(
            "Use this when setup created something you do not want or you picked the wrong item.\n\n"
            "Safe rule: cleanup should only remove things Dank Shield created or things you explicitly choose."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Recommended order",
        value="1. View Current Setup.\n2. Remove only the wrong item.\n3. Return to setup.\n4. Refresh Health.",
        inline=False,
    )
    await solid._edit_or_followup(interaction, embed=embed, view=solid.SetupNavView())


async def _rerun_health(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    embed = await solid._build_health_embed(guild)
    await solid._edit_or_followup(interaction, embed=embed, view=HealthActionView(embed=embed))


async def _start_setup(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass
    try:
        from stoney_verify.commands_ext import public_setup_defaults

        await public_setup_defaults._setup_defaults_callback(interaction)
        try:
            created, skipped, error = await solid._seed_recommended_categories(guild)
            if error:
                await interaction.followup.send(
                    f"⚠️ Setup ran, but ticket menu options could not be checked: `{error}`",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            elif created:
                await interaction.followup.send(
                    f"✅ Ticket menu options created: {', '.join(f'`{x}`' for x in created)}",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            elif skipped:
                await interaction.followup.send(
                    "✅ Ticket menu options already exist. Nothing was overwritten.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except Exception:
            pass
        embed = await solid._build_health_embed(guild)
        await interaction.followup.send(
            embed=embed,
            view=HealthActionView(embed=embed),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as exc:
        embed = discord.Embed(
            title="❌ Setup Could Not Finish",
            description=(
                f"`{type(exc).__name__}: {str(exc)[:300]}`\n\n"
                "Next: press **Use Existing Roles/Channels** if you want to map items manually, or fix permissions and try Start Setup again."
            ),
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed, view=HealthActionView(embed=embed), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def _safe_test_setup(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    health = await solid._build_health_embed(guild)
    blockers = _field_value(health, "Blockers")
    warnings = _field_value(health, "Warnings")
    embed = discord.Embed(
        title="🧪 Safe Setup Test",
        description="This test does not kick members or delete anything. It checks whether setup looks safe enough to try live flows.",
        color=discord.Color.red() if _has_items(blockers) else (discord.Color.orange() if _has_items(warnings) else discord.Color.green()),
    )
    embed.add_field(name="Ticket creation readiness", value=("🚫 Fix blockers first." if _has_items(blockers) else "✅ Ticket basics are ready enough to test."), inline=False)
    embed.add_field(name="Verification readiness", value=("⚠️ Review warnings before inviting real members." if _has_items(warnings) else "✅ Verification checks do not show warnings."), inline=False)
    embed.add_field(name="Next", value="If green, press **🎫 Create Test Ticket**. If not, use the fix buttons below and refresh health.", inline=False)
    await solid._edit_or_followup(interaction, embed=embed, view=HealthActionView(embed=health))


async def _create_test_ticket(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await interaction.response.send_message("❌ This must be used inside a server as a server member.", ephemeral=True)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass
    try:
        from stoney_verify.tickets_new.service import create_ticket_channel

        channel = await create_ticket_channel(
            guild=guild,
            owner=member,
            category="setup_test",
            source="setup_health_test_ticket",
            opening_message=(
                f"🧪 {member.mention} this is a Dank Shield setup test ticket.\n\n"
                "Use this to test claim/close/reopen/transcript/delete controls. Safe to delete when finished."
            ),
            priority="low",
            matched_category_slug="setup_test",
            matched_category_name="Setup Test",
            matched_intake_type="test",
            matched_category_reason="Created from /dank setup health test button",
            matched_category_score=100,
            category_override=True,
        )
        if isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                f"✅ Test ticket ready: {channel.mention}\nTry the staff buttons, then delete it when done.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        await interaction.followup.send(
            "🚫 Test ticket could not be created. Press **Health Check** and fix the blockers shown there.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as exc:
        await interaction.followup.send(
            f"🚫 Test ticket failed: `{type(exc).__name__}: {str(exc)[:300]}`",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class RouteButton(discord.ui.Button):
    def __init__(self, *, route: str, label: str, emoji: str, style: discord.ButtonStyle, row: int) -> None:
        super().__init__(label=label[:80], emoji=emoji, style=style, custom_id=f"stoney_setup_health:route:{route}", row=row)
        self.route = route

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await _open_exact_route(interaction, self.route)


class HealthActionView(discord.ui.View):
    def __init__(self, *, embed: Optional[discord.Embed] = None) -> None:
        super().__init__(timeout=900)
        route, label, emoji = _route_from_embed(embed) if embed is not None else ("existing", "Fix Missing Items", "🧩")
        self.add_item(RouteButton(route=route, label=label, emoji=emoji, style=discord.ButtonStyle.primary, row=0))
        self.add_item(RouteButton(route="existing", label="All Setup Pickers", emoji="🧩", style=discord.ButtonStyle.secondary, row=0))

    @discord.ui.button(label="Start Setup / Fix Missing", emoji="🚀", style=discord.ButtonStyle.success, custom_id="stoney_setup_health:auto", row=1)
    async def start_setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _start_setup(interaction)

    @discord.ui.button(label="Safe Test", emoji="🧪", style=discord.ButtonStyle.secondary, custom_id="stoney_setup_health:safe_test", row=1)
    async def safe_test(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _safe_test_setup(interaction)

    @discord.ui.button(label="Create Test Ticket", emoji="🎫", style=discord.ButtonStyle.secondary, custom_id="stoney_setup_health:test_ticket", row=2)
    async def test_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _create_test_ticket(interaction)

    @discord.ui.button(label="Refresh Health", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="stoney_setup_health:refresh", row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _rerun_health(interaction)

    @discord.ui.button(label="Undo / Cleanup", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="stoney_setup_health:cleanup", row=3)
    async def cleanup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_cleanup(interaction)

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="stoney_setup_health:back", row=3)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_main_setup(interaction)


async def _wrapped_edit_or_followup(interaction: discord.Interaction, *, embed: discord.Embed, view: Optional[discord.ui.View] = None) -> None:
    next_embed = _prepend_progress(embed) if _is_health_embed(embed) else embed
    next_view = HealthActionView(embed=next_embed) if _is_health_embed(next_embed) else view
    await _ORIGINAL_EDIT_OR_FOLLOWUP(interaction, embed=next_embed, view=next_view)


def apply() -> bool:
    global _PATCHED, _ORIGINAL_EDIT_OR_FOLLOWUP
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        original = getattr(solid, "_edit_or_followup", None)
        if not callable(original) or getattr(original, "_health_action_buttons_wrapped", False):
            return False
        _ORIGINAL_EDIT_OR_FOLLOWUP = original
        setattr(_wrapped_edit_or_followup, "_health_action_buttons_wrapped", True)
        solid._edit_or_followup = _wrapped_edit_or_followup
        _PATCHED = True
        print("🧭 setup_health_action_buttons_guard active; health results now include progress, exact fix routes, safe test, and test ticket")
        return True
    except Exception as exc:
        print(f"⚠️ setup_health_action_buttons_guard failed: {exc!r}")
        return False


apply()

__all__ = ["apply", "HealthActionView"]
