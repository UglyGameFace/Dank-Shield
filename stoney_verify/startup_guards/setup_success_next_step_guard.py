from __future__ import annotations

"""Give first-time setup users a clear next step after auto-build.

The public setup flow already has every feature. This guard makes the happy path
feel like an installer: after Start Setup / Fix Missing runs, the user gets a
compact follow-up card with a health check and the next safest action.
"""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_AUTO_FIX: Any = None


def _safe_str(value: Any, limit: int = 900) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _field_text(items: list[str], *, empty: str = "None", limit: int = 1000) -> str:
    lines = [_safe_str(item, 180) for item in items if str(item or "").strip()]
    if not lines:
        return empty
    out: list[str] = []
    total = 0
    for line in lines:
        next_line = f"• {line}"
        if total + len(next_line) + 1 > limit:
            out.append(f"• +{len(lines) - len(out)} more")
            break
        out.append(next_line)
        total += len(next_line) + 1
    return "\n".join(out)[:limit]


def _button(view: discord.ui.View, *, label: str, emoji: str, style: discord.ButtonStyle, custom_id: str, row: int) -> None:
    class NextButton(discord.ui.Button):
        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            try:
                from stoney_verify.commands_ext import public_setup_solid as solid

                if not await solid._require_setup_permission(interaction):
                    return
                guild = interaction.guild
                if guild is None:
                    return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
                await solid._safe_defer_update(interaction)
                if self.custom_id == "stoney_setup_next:health":
                    embed = await solid._build_health_embed(guild)
                    return await solid._edit_or_followup(interaction, embed=embed, view=solid.SetupNavView())
                if self.custom_id == "stoney_setup_next:existing":
                    embed = discord.Embed(
                        title="🧩 Use Existing Roles/Channels",
                        description=(
                            "Pick any server items Dank Shield still needs. Each dropdown saves immediately.\n"
                            "Start with **Ticket Basics** if tickets are not opening yet."
                        ),
                        color=discord.Color.blurple(),
                    )
                    embed.add_field(
                        name="Sections",
                        value=(
                            "🎫 **Ticket Basics** — open/closed categories, staff role, transcripts\n"
                            "🎭 **Access Roles** — waiting/approved/member roles\n"
                            "🎙️ **Verification Channels** — text/voice verification channels\n"
                            "🧾 **Logs + Status** — modlog, join/leave log, bot status\n"
                            "⚙️ **Optional Rules** — verification style, ticket prefix, kick timer"
                        ),
                        inline=False,
                    )
                    return await solid._edit_or_followup(interaction, embed=embed, view=solid.ChooseExistingView())
                embed, setup_view = await solid._build_main_setup_payload(guild)
                return await solid._edit_or_followup(interaction, embed=embed, view=setup_view)
            except Exception as exc:
                try:
                    await interaction.response.send_message(
                        f"❌ Could not open next setup step: `{type(exc).__name__}: {str(exc)[:250]}`",
                        ephemeral=True,
                    )
                except Exception:
                    pass

    view.add_item(NextButton(label=label, emoji=emoji, style=style, custom_id=custom_id, row=row))


class SetupNextStepView(discord.ui.View):
    def __init__(self, *, has_blockers: bool) -> None:
        super().__init__(timeout=900)
        _button(
            self,
            label="Health Check",
            emoji="🩺",
            style=discord.ButtonStyle.success if not has_blockers else discord.ButtonStyle.primary,
            custom_id="stoney_setup_next:health",
            row=0,
        )
        _button(
            self,
            label="Use Existing Roles/Channels",
            emoji="🧩",
            style=discord.ButtonStyle.secondary,
            custom_id="stoney_setup_next:existing",
            row=0,
        )
        _button(
            self,
            label="Back to Setup",
            emoji="⬅️",
            style=discord.ButtonStyle.secondary,
            custom_id="stoney_setup_next:back",
            row=1,
        )


async def _send_seamless_next_step(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        return
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        health = await solid._build_health_embed(guild)
        blockers: list[str] = []
        warnings: list[str] = []
        passing: list[str] = []
        for field in list(getattr(health, "fields", []) or []):
            name = str(getattr(field, "name", "") or "")
            value = str(getattr(field, "value", "") or "")
            if name == "Blockers" and "✅ None" not in value:
                blockers.append(value)
            elif name == "Warnings" and "✅ None" not in value:
                warnings.append(value)
            elif name == "Passing Checks":
                passing.append(value)

        has_blockers = bool(blockers)
        embed = discord.Embed(
            title="✅ Setup Step Finished" if not has_blockers else "🧭 Setup Needs One More Pass",
            description=(
                "Dank Shield ran the setup action. Here is the next safest step."
                if has_blockers
                else "Dank Shield ran the setup action. You are ready to verify the setup and test the flow."
            ),
            color=discord.Color.orange() if has_blockers else discord.Color.green(),
        )
        if has_blockers:
            embed.add_field(
                name="Still Missing",
                value=_field_text(blockers, empty="No blockers reported."),
                inline=False,
            )
            embed.add_field(
                name="Next Click",
                value="Press **🧩 Use Existing Roles/Channels** if you want to pick existing items, or press **🩺 Health Check** for the full checklist.",
                inline=False,
            )
        else:
            embed.add_field(
                name="Next Click",
                value="Press **🩺 Health Check**, then test opening a ticket and verification.",
                inline=False,
            )
        if warnings:
            embed.add_field(name="Warnings", value=_field_text(warnings[:2], empty="None"), inline=False)
        embed.set_footer(text="No features were removed. Advanced setup is still available from Back to Setup.")
        await interaction.followup.send(
            embed=embed,
            view=SetupNextStepView(has_blockers=has_blockers),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as exc:
        try:
            await interaction.followup.send(
                f"✅ Setup action finished, but the next-step card could not load: `{type(exc).__name__}`. Run `/dank setup` → Health Check.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass


def apply() -> bool:
    global _PATCHED, _ORIGINAL_AUTO_FIX
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        original = getattr(solid.SolidSetupView.auto_fix, "callback", None)
        if not callable(original):
            return False
        _ORIGINAL_AUTO_FIX = original

        async def wrapped_auto_fix(self: Any, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            await _ORIGINAL_AUTO_FIX(self, interaction, button)
            await _send_seamless_next_step(interaction)

        solid.SolidSetupView.auto_fix.callback = wrapped_auto_fix
        _PATCHED = True
        print("🧭 setup_success_next_step_guard active; auto-build now ends with guided next-step card")
        return True
    except Exception as exc:
        print(f"⚠️ setup_success_next_step_guard failed: {exc!r}")
        return False


apply()

__all__ = ["apply", "SetupNextStepView"]
