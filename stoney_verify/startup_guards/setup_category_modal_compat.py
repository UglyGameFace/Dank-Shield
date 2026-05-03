from __future__ import annotations

"""Hotfix compatibility for Advanced Setup -> Add Custom Menu Option.

Live failure fixed:
    AttributeError: module 'stoney_verify.commands_ext.public_setup_solid'
    has no attribute 'AddTicketCategoryModal'

The older advanced setup view still calls public_setup_solid.AddTicketCategoryModal.
This guard restores that attribute at startup and writes through the existing
schema-tolerant ticket_category_admin helpers.
"""

from typing import Any

import discord

_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🧭 setup_category_modal_compat {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_category_modal_compat {message}")
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


def _slugify(value: Any) -> str:
    try:
        import re

        text = str(value or "").strip().lower()
        text = text.replace("&", " and ")
        text = re.sub(r"[^a-z0-9\s\-_]+", "", text)
        text = re.sub(r"[\s_]+", "-", text)
        text = re.sub(r"-{2,}", "-", text)
        return text.strip("-")[:80] or "custom"
    except Exception:
        return "custom"


def _keywords(value: Any) -> list[str]:
    try:
        raw = str(value or "").replace("\n", ",")
        out: list[str] = []
        for part in raw.split(","):
            item = part.strip().lower()
            if item and item not in out:
                out.append(item[:80])
        return out[:25]
    except Exception:
        return []


def _valid_intake_type(value: Any) -> str:
    text = _safe_str(value, "custom").lower().replace(" ", "-").replace("_", "-")
    aliases = {
        "general": "support",
        "help": "support",
        "verify": "verification",
        "verification-help": "verification",
        "bug-report": "bug",
        "other": "custom",
    }
    text = aliases.get(text, text)
    allowed = {
        "support",
        "verification",
        "appeal",
        "report",
        "question",
        "bug",
        "custom",
        "partnership",
        "ghost",
        "account",
        "purchase",
    }
    return text if text in allowed else "custom"


async def _reply(interaction: discord.Interaction, content: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except Exception:
        pass


def install_setup_category_modal_compat() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        from stoney_verify.commands_ext import public_setup_solid as solid
    except Exception as e:
        _warn(f"public_setup_solid unavailable: {repr(e)}")
        return False

    if hasattr(solid, "AddTicketCategoryModal"):
        _PATCHED = True
        return True

    class AddTicketCategoryModal(discord.ui.Modal, title="Add Ticket Menu Option"):
        def __init__(self, *, existing_count: int = 0) -> None:
            super().__init__(timeout=600)
            self.existing_count = max(0, _safe_int(existing_count, 0))

            self.name_input = discord.ui.TextInput(
                label="Option name",
                placeholder="Example: Support, Appeal, Report User",
                max_length=80,
                required=True,
            )
            self.slug_input = discord.ui.TextInput(
                label="Short key / slug",
                placeholder="Example: support, appeal, report-user",
                max_length=80,
                required=False,
            )
            self.type_input = discord.ui.TextInput(
                label="Type",
                placeholder="support, verification, appeal, report, question, bug, custom",
                max_length=40,
                required=False,
                default="custom",
            )
            self.description_input = discord.ui.TextInput(
                label="Description",
                placeholder="What should staff understand about this option?",
                style=discord.TextStyle.paragraph,
                max_length=400,
                required=False,
            )
            self.keywords_input = discord.ui.TextInput(
                label="Keywords",
                placeholder="Comma-separated words for routing: help, issue, appeal",
                style=discord.TextStyle.paragraph,
                max_length=300,
                required=False,
            )

            self.add_item(self.name_input)
            self.add_item(self.slug_input)
            self.add_item(self.type_input)
            self.add_item(self.description_input)
            self.add_item(self.keywords_input)

        async def on_submit(self, interaction: discord.Interaction) -> None:
            guild = interaction.guild
            if guild is None:
                return await _reply(interaction, "❌ This must be used inside a server.")

            try:
                checker = getattr(solid, "_require_setup_permission", None)
                if callable(checker):
                    allowed = await checker(interaction)
                    if not allowed:
                        return
            except Exception:
                pass

            name = _safe_str(self.name_input.value)
            slug = _slugify(_safe_str(self.slug_input.value) or name)
            intake_type = _valid_intake_type(self.type_input.value)
            description = _safe_str(self.description_input.value)
            keywords = _keywords(self.keywords_input.value)
            sort_order = (self.existing_count + 1) * 10

            if not name:
                return await _reply(interaction, "❌ Option name is required.")

            payload = {
                "guild_id": str(int(guild.id)),
                "slug": slug,
                "category_slug": slug,
                "name": name,
                "category_name": name,
                "display_name": name,
                "description": description,
                "intake_type": intake_type,
                "type": intake_type,
                "match_keywords": keywords,
                "keywords": keywords,
                "is_default": False,
                "default": False,
                "sort_order": sort_order,
                "position": sort_order,
            }

            try:
                from stoney_verify.commands_ext import ticket_category_admin as category_admin

                ok = await category_admin._insert_category(payload)
                if not ok:
                    detail = "unknown database error"
                    try:
                        detail = category_admin._last_category_db_error() or detail
                    except Exception:
                        pass
                    return await _reply(
                        interaction,
                        f"❌ Could not create ticket menu option `{slug}`. `{detail[:700]}`",
                    )
            except Exception as e:
                return await _reply(
                    interaction,
                    f"❌ Could not create ticket menu option `{slug}`: `{type(e).__name__}: {str(e)[:300]}`",
                )

            return await _reply(
                interaction,
                (
                    f"✅ Created ticket menu option **{name}** (`{slug}`).\n"
                    "Press **Refresh** on the setup panel to see it."
                ),
            )

        async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
            await _reply(
                interaction,
                f"❌ Ticket menu modal failed: `{type(error).__name__}: {str(error)[:300]}`",
            )

    try:
        setattr(solid, "AddTicketCategoryModal", AddTicketCategoryModal)
        _PATCHED = True
        _log("patched public_setup_solid.AddTicketCategoryModal")
        return True
    except Exception as e:
        _warn(f"failed setting AddTicketCategoryModal: {repr(e)}")
        return False


install_setup_category_modal_compat()


__all__ = ["install_setup_category_modal_compat"]
