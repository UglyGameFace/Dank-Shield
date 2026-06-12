from __future__ import annotations

"""Hotfix compatibility for Advanced Setup ticket menu controls.

Live failures fixed:
    AttributeError: module 'stoney_verify.commands_ext.public_setup_solid'
    has no attribute 'AddTicketCategoryModal'

Also fixes old advanced setup controls that can appear to do nothing when the
legacy selection/edit view is missing or half-refactored:
    - Add Custom Menu Option
    - Edit Menu Option
    - Set Default Option
    - Delete Menu Option

The giant production-readiness PR has cleaner setup UX, but Discloud is running
main right now. This tiny guard keeps the deployed branch stable without merging
the huge PR blindly.
"""

from typing import Any, Optional

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


def _keywords_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(_safe_str(v) for v in value if _safe_str(v))[:300]
    return _safe_str(value)[:300]


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


def _row_slug(row: dict[str, Any]) -> str:
    return _slugify(row.get("slug") or row.get("category_slug") or row.get("name") or row.get("display_name") or "custom")


def _row_name(row: dict[str, Any]) -> str:
    return _safe_str(row.get("name") or row.get("display_name") or row.get("category_name") or row.get("label") or _row_slug(row), "Option")


def _row_type(row: dict[str, Any]) -> str:
    return _valid_intake_type(row.get("intake_type") or row.get("type") or "custom")


def _row_description(row: dict[str, Any]) -> str:
    return _safe_str(row.get("description"), "")[:400]


def _row_keywords(row: dict[str, Any]) -> str:
    return _keywords_text(row.get("match_keywords") if "match_keywords" in row else row.get("keywords"))


def _row_sort(row: dict[str, Any], default: int = 0) -> int:
    return _safe_int(row.get("sort_order", row.get("position", default)), default)


def _option_label(row: dict[str, Any]) -> str:
    label = _row_name(row)
    return label[:100] or "Ticket option"


def _option_description(row: dict[str, Any]) -> str:
    desc = f"{_row_slug(row)} • {_row_type(row)} • sort {_row_sort(row, 0)}"
    return desc[:100]


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


async def _require_setup_permission_best_effort(solid: Any, interaction: discord.Interaction) -> bool:
    try:
        checker = getattr(solid, "_require_setup_permission", None)
        if callable(checker):
            return bool(await checker(interaction))
    except Exception:
        pass
    return True


def _find_row(rows: list[dict[str, Any]], slug: str) -> Optional[dict[str, Any]]:
    wanted = _slugify(slug)
    for row in rows:
        if _row_slug(row) == wanted:
            return row
    return None


class DeleteTicketCategoryConfirmView(discord.ui.View):
    def __init__(self, *, slug: str, name: str) -> None:
        super().__init__(timeout=300)
        self.slug = _slugify(slug)
        self.name = _safe_str(name, self.slug)

    @discord.ui.button(label="Delete", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "❌ This must be used inside a server.")
        try:
            from stoney_verify.commands_ext import ticket_category_admin as category_admin

            ok = await category_admin._delete_category(guild.id, self.slug)
            if not ok:
                detail = "unknown database error"
                try:
                    detail = category_admin._last_category_db_error() or detail
                except Exception:
                    pass
                return await _reply(interaction, f"❌ Could not delete `{self.slug}`. `{detail[:700]}`")
        except Exception as e:
            return await _reply(interaction, f"❌ Delete failed: `{type(e).__name__}: {str(e)[:300]}`")

        try:
            await interaction.response.edit_message(
                content=f"✅ Deleted ticket menu option **{self.name}** (`{self.slug}`). Press **Refresh** on setup to update the list.",
                embed=None,
                view=None,
            )
        except Exception:
            await _reply(interaction, f"✅ Deleted ticket menu option **{self.name}** (`{self.slug}`). Press **Refresh** on setup to update the list.")

    @discord.ui.button(label="Cancel", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            await interaction.response.edit_message(content="Cancelled. Nothing was deleted.", embed=None, view=None)
        except Exception:
            await _reply(interaction, "Cancelled. Nothing was deleted.")


def install_setup_category_modal_compat() -> bool:
    global _PATCHED

    try:
        from stoney_verify.commands_ext import public_setup_solid as solid
    except Exception as e:
        _warn(f"public_setup_solid unavailable: {repr(e)}")
        return False

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
            if not await _require_setup_permission_best_effort(solid, interaction):
                return

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
                    return await _reply(interaction, f"❌ Could not create ticket menu option `{slug}`. `{detail[:700]}`")
            except Exception as e:
                return await _reply(interaction, f"❌ Could not create ticket menu option `{slug}`: `{type(e).__name__}: {str(e)[:300]}`")

            return await _reply(interaction, f"✅ Created ticket menu option **{name}** (`{slug}`). Press **Refresh** on the setup panel to see it.")

        async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
            await _reply(interaction, f"❌ Ticket menu modal failed: `{type(error).__name__}: {str(error)[:300]}`")

    class EditTicketCategoryModal(discord.ui.Modal, title="Edit Ticket Menu Option"):
        def __init__(self, *, row: dict[str, Any]) -> None:
            super().__init__(timeout=600)
            self.row = dict(row)
            self.slug = _row_slug(row)

            self.name_input = discord.ui.TextInput(
                label="Option name",
                max_length=80,
                required=True,
                default=_row_name(row)[:80],
            )
            self.type_input = discord.ui.TextInput(
                label="Type",
                placeholder="support, verification, appeal, report, question, bug, custom",
                max_length=40,
                required=False,
                default=_row_type(row)[:40],
            )
            self.description_input = discord.ui.TextInput(
                label="Description",
                style=discord.TextStyle.paragraph,
                max_length=400,
                required=False,
                default=_row_description(row)[:400],
            )
            self.keywords_input = discord.ui.TextInput(
                label="Keywords",
                style=discord.TextStyle.paragraph,
                max_length=300,
                required=False,
                default=_row_keywords(row)[:300],
            )
            self.sort_input = discord.ui.TextInput(
                label="Sort order",
                placeholder="Lower numbers appear first, example: 10",
                max_length=8,
                required=False,
                default=str(_row_sort(row, 0)),
            )

            self.add_item(self.name_input)
            self.add_item(self.type_input)
            self.add_item(self.description_input)
            self.add_item(self.keywords_input)
            self.add_item(self.sort_input)

        async def on_submit(self, interaction: discord.Interaction) -> None:
            guild = interaction.guild
            if guild is None:
                return await _reply(interaction, "❌ This must be used inside a server.")
            if not await _require_setup_permission_best_effort(solid, interaction):
                return

            name = _safe_str(self.name_input.value)
            if not name:
                return await _reply(interaction, "❌ Option name is required.")

            sort_order: Optional[int] = None
            sort_raw = _safe_str(self.sort_input.value)
            if sort_raw:
                try:
                    sort_order = int(sort_raw)
                except Exception:
                    return await _reply(interaction, "❌ Sort order must be a number, like `10`.")

            patch = {
                "name": name,
                "category_name": name,
                "display_name": name,
                "description": _safe_str(self.description_input.value),
                "intake_type": _valid_intake_type(self.type_input.value),
                "type": _valid_intake_type(self.type_input.value),
                "match_keywords": _keywords(self.keywords_input.value),
                "keywords": _keywords(self.keywords_input.value),
            }
            if sort_order is not None:
                patch["sort_order"] = sort_order
                patch["position"] = sort_order

            try:
                from stoney_verify.commands_ext import ticket_category_admin as category_admin

                ok = await category_admin._update_category(guild.id, self.slug, patch)
                if not ok:
                    detail = "unknown database error"
                    try:
                        detail = category_admin._last_category_db_error() or detail
                    except Exception:
                        pass
                    return await _reply(interaction, f"❌ Could not update `{self.slug}`. `{detail[:700]}`")
            except Exception as e:
                return await _reply(interaction, f"❌ Update failed: `{type(e).__name__}: {str(e)[:300]}`")

            return await _reply(interaction, f"✅ Updated ticket menu option **{name}** (`{self.slug}`). Press **Refresh** on setup to see the change.")

        async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
            await _reply(interaction, f"❌ Ticket menu edit failed: `{type(error).__name__}: {str(error)[:300]}`")

    class CategoryActionSelect(discord.ui.Select):
        def __init__(self, *, rows: list[dict[str, Any]], action: str) -> None:
            self.rows = [dict(r) for r in rows]
            self.action = _safe_str(action, "edit").lower()
            options: list[discord.SelectOption] = []
            for row in self.rows[:25]:
                slug = _row_slug(row)
                if not slug:
                    continue
                options.append(
                    discord.SelectOption(
                        label=_option_label(row),
                        value=slug,
                        description=_option_description(row),
                        emoji="🧾",
                    )
                )
            if not options:
                options.append(discord.SelectOption(label="No ticket menu options found", value="__none__", description="Create recommended options first."))
            placeholder = {
                "edit": "Choose option to edit",
                "default": "Choose default option",
                "delete": "Choose option to delete",
            }.get(self.action, "Choose ticket menu option")
            super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options, row=0)

        async def callback(self, interaction: discord.Interaction) -> None:
            if not await _require_setup_permission_best_effort(solid, interaction):
                return
            guild = interaction.guild
            if guild is None:
                return await _reply(interaction, "❌ This must be used inside a server.")

            slug = _safe_str(self.values[0])
            if slug == "__none__":
                return await _reply(interaction, "No ticket menu options exist yet. Use **Create Recommended** or **Add Custom Menu Option** first.")

            row = _find_row(self.rows, slug)
            if not row:
                return await _reply(interaction, f"❌ Could not find `{slug}` in this setup view. Press **Refresh** and try again.")

            if self.action == "edit":
                return await interaction.response.send_modal(EditTicketCategoryModal(row=row))

            if self.action == "default":
                try:
                    from stoney_verify.commands_ext import ticket_category_admin as category_admin

                    ok = await category_admin._set_default(guild.id, slug)
                    if not ok:
                        detail = "unknown database error"
                        try:
                            detail = category_admin._last_category_db_error() or detail
                        except Exception:
                            pass
                        return await _reply(interaction, f"❌ Could not set `{slug}` as default. `{detail[:700]}`")
                except Exception as e:
                    return await _reply(interaction, f"❌ Set default failed: `{type(e).__name__}: {str(e)[:300]}`")
                return await _reply(interaction, f"⭐ Set **{_row_name(row)}** (`{slug}`) as the default ticket menu option. Press **Refresh** to see it.")

            if self.action == "delete":
                embed = discord.Embed(
                    title="Delete Ticket Menu Option?",
                    description=f"Delete **{_row_name(row)}** (`{slug}`)?\n\nThis only deletes the routing/menu record. It does **not** delete Discord channels, roles, or tickets.",
                    color=discord.Color.red(),
                )
                return await interaction.response.edit_message(
                    embed=embed,
                    view=DeleteTicketCategoryConfirmView(slug=slug, name=_row_name(row)),
                )

            return await _reply(interaction, "❌ Unknown ticket menu action.")

    class CategorySelectActionView(discord.ui.View):
        def __init__(self, *, rows: list[dict[str, Any]], action: str) -> None:
            super().__init__(timeout=600)
            self.rows = [dict(r) for r in rows]
            self.action = _safe_str(action, "edit").lower()
            self.add_item(CategoryActionSelect(rows=self.rows, action=self.action))

        @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
        async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            try:
                if hasattr(solid, "_build_category_manager_payload") and hasattr(solid, "_edit_or_followup"):
                    embed, view = await solid._build_category_manager_payload(interaction.guild)  # type: ignore[arg-type]
                    return await solid._edit_or_followup(interaction, embed=embed, view=view)
            except Exception:
                pass
            await _reply(interaction, "Go back to `/dank setup` and reopen Ticket Menu Options.")

    try:
        setattr(solid, "AddTicketCategoryModal", AddTicketCategoryModal)
        setattr(solid, "EditTicketCategoryModal", EditTicketCategoryModal)
        setattr(solid, "CategorySelectActionView", CategorySelectActionView)
        setattr(solid, "DeleteTicketCategoryConfirmView", DeleteTicketCategoryConfirmView)
        _PATCHED = True
        _log("patched ticket menu add/edit/default/delete compatibility controls")
        return True
    except Exception as e:
        _warn(f"failed setting setup ticket menu compatibility controls: {repr(e)}")
        return False


install_setup_category_modal_compat()


__all__ = ["install_setup_category_modal_compat"]
