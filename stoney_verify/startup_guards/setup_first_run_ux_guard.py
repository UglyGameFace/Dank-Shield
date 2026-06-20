from __future__ import annotations

"""Make /dank setup easier for first-time public servers.

Keeps every existing setup feature, but makes the first screen opinionated:
new users get one obvious Core Setup path, while advanced/custom flows remain
available without overwhelming the first-run path.
"""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_BUILD_MAIN_SETUP_PAYLOAD: Any = None
_ORIGINAL_SETUP_VIEW_INIT: Any = None


def _safe_text(value: Any, limit: int = 900) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _spec_label(spec: Any) -> str:
    for attr in ("label", "name", "title", "key"):
        value = getattr(spec, attr, None)
        if value:
            return _safe_text(str(value).replace("_", " ").title(), 64)
    return "Setup item"


def _missing_summary(specs: list[Any]) -> str:
    if not specs:
        return "✅ Required basics look complete. Use **Setup Health** any time to verify or fine-tune optional features."
    labels = [_spec_label(spec) for spec in specs[:6]]
    extra = len(specs) - len(labels)
    lines = [f"• {label}" for label in labels]
    if extra > 0:
        lines.append(f"• +{extra} more item(s)")
    return "Missing basics found:\n" + "\n".join(lines)


def _retitle_setup_buttons(view: discord.ui.View, *, has_missing: bool) -> None:
    for item in getattr(view, "children", []) or []:
        cid = str(getattr(item, "custom_id", "") or "")
        try:
            if cid == "stoney_setup:auto_fix":
                item.label = "Create Missing Defaults"
                item.emoji = "✨"
                item.style = discord.ButtonStyle.success
                item.disabled = False if has_missing else bool(getattr(item, "disabled", False))
            elif cid == "stoney_setup:choose_existing":
                item.label = "Use Existing Roles/Channels"
                item.emoji = "🔗"
            elif cid == "stoney_setup:customize":
                item.label = "Customize Names First"
                item.emoji = "✏️"
            elif cid == "stoney_setup:ticket_categories":
                item.label = "Ticket Setup"
                item.emoji = "🎫"
            elif cid == "stoney_setup:use_status_channel":
                item.label = "Use This as Status Channel"
                item.emoji = "📌"
            elif cid == "stoney_setup:health":
                item.label = "Setup Health"
                item.emoji = "🩺"
        except Exception:
            continue


async def _install() -> None:
    return None


def install_guard() -> None:
    global _PATCHED, _ORIGINAL_BUILD_MAIN_SETUP_PAYLOAD, _ORIGINAL_SETUP_VIEW_INIT
    if _PATCHED:
        return

    try:
        from stoney_verify.commands_ext import public_setup_start as setup_start
        from stoney_verify.commands_ext import public_setup_assistant
    except Exception as exc:
        print(f"⚠️ setup_first_run_ux_guard import failed: {exc!r}")
        return

    if not callable(getattr(setup_start, "_build_main_setup_payload", None)):
        return

    _ORIGINAL_BUILD_MAIN_SETUP_PAYLOAD = setup_start._build_main_setup_payload
    _ORIGINAL_SETUP_VIEW_INIT = setup_start.DankSetupView.__init__

    def patched_setup_view_init(self: Any, *, has_missing: bool) -> None:
        _ORIGINAL_SETUP_VIEW_INIT(self, has_missing=has_missing)
        _retitle_setup_buttons(self, has_missing=bool(has_missing))

    async def patched_build_main_setup_payload(guild: discord.Guild, *, title: str = "🚀 Dank Shield Setup"):
        setup_start._install_cleaners(public_setup_assistant)
        missing = await setup_start._current_missing_specs(guild, public_setup_assistant)
        has_missing = bool(missing)
        view = setup_start.DankSetupView(has_missing=has_missing)

        color = discord.Color.green() if not has_missing else discord.Color.blurple()
        embed = discord.Embed(
            title=title,
            description=(
                "Use the workflow sections in **/dank setup**. This first-run screen is only for creating or mapping the required basics.\n\n"
                "Nothing here deletes existing channels, roles, ticket history, or permissions."
            ),
            color=color,
        )
        embed.add_field(
            name="Step 1 — Pick how setup should work",
            value=(
                "✨ **Create Missing Defaults** creates only missing roles/channels/categories.\n"
                "🔗 **Use Existing Roles/Channels** maps your current server items instead.\n"
                "✏️ **Customize Names First** lets you choose names before creating defaults."
            ),
            inline=False,
        )
        embed.add_field(name="Current Status", value=_missing_summary(list(missing)), inline=False)
        embed.add_field(
            name="Step 2 — Ticket setup if needed",
            value="🎫 **Ticket Setup** changes ticket-facing menu/routing choices. It does not replace Setup Health.",
            inline=False,
        )
        embed.add_field(
            name="Step 3 — Verify safety",
            value="Run **🩺 Setup Health** and use **🛠️ Preview/Fix Permissions** from the Safety & Repair section if access protections drift.",
            inline=False,
        )
        embed.set_footer(text="For the full organized workflow, run /dank setup and use Core Setup, Safety & Repair, Ticket Setup, and Advanced Tools.")
        return embed, view

    setup_start.DankSetupView.__init__ = patched_setup_view_init
    setup_start._build_main_setup_payload = patched_build_main_setup_payload
    _PATCHED = True
    print("🧭 setup_first_run_ux_guard active; /dank setup first-run labels match workflow hub")


install_guard()

__all__ = ["install_guard"]
