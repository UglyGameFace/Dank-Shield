from __future__ import annotations

"""Single owner for ticket category catalog, setup selection, and live menus.

This replaces the older COD/Game category patch stack. The database keeps every
managed template, setup owners explicitly choose which ones are enabled, and all
member-facing loaders use the same exact canonical deduplication path.
"""

import asyncio
from typing import Any, Dict, Iterable, List, Mapping, Optional

import discord

from ..tickets_new import managed_category_service as service

_APPLIED = False

_COD_INTAKE_TYPES = {
    "cod",
    "cod_services",
    "call_of_duty",
    "modern_cod",
    "warzone",
}
_GAME_INTAKE_TYPES = {
    "game_services",
    "game_service",
    "game_support",
    "custom_game_services",
    "service_question",
}


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_category_setup_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_category_setup_guard: {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _short(value: Any, limit: int = 100) -> str:
    text = _safe_str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _catalog_key(row: Mapping[str, Any]) -> str:
    key = service.canonical_category_key(row)
    return key if not key.startswith("custom:") else ""


def _catalog_rows_from_state(state: service.CategorySetupState) -> List[Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}
    for row in state.rows:
        key = _catalog_key(row)
        if key:
            by_key[key] = dict(row)

    rows: List[Dict[str, Any]] = []
    for catalog in service.catalog_category_rows():
        key = str(catalog.get("category_key") or catalog.get("managed_category_key") or "")
        row = dict(by_key.get(key) or catalog)
        row.setdefault("managed_category_key", key)
        row.setdefault("managed_by_dank", True)
        row["is_enabled"] = key in state.selected_keys
        rows.append(row)
    return rows


def _custom_rows_from_state(state: service.CategorySetupState) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in state.rows:
        if _catalog_key(row):
            continue
        if row.get("is_enabled") is False or row.get("enabled") is False:
            continue
        rows.append(dict(row))
    return rows


def _active_line(rows: Iterable[Mapping[str, Any]], *, empty: str) -> str:
    names = [
        _safe_str(row.get("name") or row.get("button_label") or row.get("slug"), "Ticket choice")
        for row in rows
    ]
    if not names:
        return empty
    return "\n".join(f"• **{_short(name, 70)}**" for name in names[:16])[:1024]


class ManagedCategorySelection(discord.ui.Select):
    def __init__(self, state: service.CategorySetupState) -> None:
        options: List[discord.SelectOption] = []
        for row in _catalog_rows_from_state(state):
            key = _catalog_key(row)
            if not key:
                continue
            options.append(
                discord.SelectOption(
                    label=_short(row.get("name") or key, 100),
                    value=key,
                    description=_short(row.get("description") or "Ticket choice", 100),
                    emoji="🎫",
                    default=key in state.selected_keys,
                )
            )

        super().__init__(
            placeholder="Choose every ticket option this server should show",
            min_values=1,
            max_values=max(1, min(25, len(options))),
            options=options[:25],
            custom_id="dank_ticket_category_setup:managed_selection",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from ..commands_ext import public_setup_solid as solid

        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )

        await solid._safe_defer_update(interaction)
        try:
            state = await service.save_category_selection(
                guild.id,
                self.values,
                actor_id=getattr(interaction.user, "id", ""),
                actor_name=str(interaction.user),
            )
            try:
                from ..guild_config import invalidate_guild_config

                invalidate_guild_config(guild.id)
            except Exception:
                pass
        except Exception as exc:
            embed = discord.Embed(
                title="🚫 Ticket Choices Were Not Saved",
                description=(
                    f"`{type(exc).__name__}: {_short(exc, 350)}`\n\n"
                    "Nothing was partially enabled. Refresh and try again."
                ),
                color=discord.Color.red(),
            )
            return await solid._edit_or_followup(
                interaction,
                embed=embed,
                view=solid.SetupNavView(),
            )

        embed, view = await _build_category_manager_payload(
            guild,
            title="✅ Ticket Choices Saved",
            state=state,
        )
        embed.add_field(
            name="Applied Everywhere",
            value=(
                "The public Create Ticket menu, guided setup, routing, and future "
                "restarts now use this exact selection. Duplicate labels are removed."
            ),
            inline=False,
        )
        await solid._edit_or_followup(interaction, embed=embed, view=view)


class CategorySetupManagerView(discord.ui.View):
    """Ticket choice manager with one explicit multi-select owner."""

    def __init__(
        self,
        *,
        state: Optional[service.CategorySetupState],
        db_error: str = "",
    ) -> None:
        from ..commands_ext import public_setup_solid as solid

        super().__init__(timeout=900)
        self.state = state
        self.db_error = db_error

        if state is not None and not db_error:
            self.add_item(ManagedCategorySelection(state))
            custom_rows = _custom_rows_from_state(state)
            if custom_rows:
                self.add_item(
                    solid.CategorySelect(
                        custom_rows,
                        action="edit",
                        placeholder="✏️ Edit a custom ticket choice",
                        row=1,
                    )
                )

        add = discord.ui.Button(
            label="Add Custom Ticket Choice",
            emoji="➕",
            style=discord.ButtonStyle.primary,
            custom_id="dank_ticket_category_setup:add_custom",
            row=3,
        )
        add.callback = self._add_custom
        self.add_item(add)

        refresh = discord.ui.Button(
            label="Refresh",
            emoji="🔄",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_ticket_category_setup:refresh",
            row=3,
        )
        refresh.callback = self._refresh
        self.add_item(refresh)

        home = discord.ui.Button(
            label="Setup Home",
            emoji="🏠",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_ticket_category_setup:home",
            row=4,
        )
        home.callback = self._home
        self.add_item(home)

        close = discord.ui.Button(
            label="Close",
            emoji="✖️",
            style=discord.ButtonStyle.danger,
            custom_id="dank_ticket_category_setup:close",
            row=4,
        )
        close.callback = self._close
        self.add_item(close)

    async def _allowed(self, interaction: discord.Interaction) -> bool:
        from ..commands_ext import public_setup_solid as solid

        return await solid._require_setup_permission(interaction)

    async def _add_custom(self, interaction: discord.Interaction) -> None:
        from ..commands_ext import public_setup_solid as solid

        if not await self._allowed(interaction):
            return
        try:
            await interaction.response.send_modal(solid.CategoryModal(existing=None))
        except Exception as exc:
            await solid.safe_interaction_error(
                interaction,
                title="Ticket Choice Editor Did Not Open",
                error=exc,
                hint="Nothing changed. Refresh and try again.",
            )

    async def _refresh(self, interaction: discord.Interaction) -> None:
        from ..commands_ext import public_setup_solid as solid

        if not await self._allowed(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return
        await solid._safe_defer_update(interaction)
        embed, view = await _build_category_manager_payload(guild)
        await solid._edit_or_followup(interaction, embed=embed, view=view)

    async def _home(self, interaction: discord.Interaction) -> None:
        from ..commands_ext import public_setup_recommend as recommend

        await recommend._home_edit(interaction)

    async def _close(self, interaction: discord.Interaction) -> None:
        from ..commands_ext import public_setup_recommend as recommend

        await recommend._close_setup(interaction)


async def _build_category_manager_payload(
    guild: discord.Guild,
    *,
    title: str = "🗂️ Choose Ticket Menu Options",
    state: Optional[service.CategorySetupState] = None,
) -> tuple[discord.Embed, CategorySetupManagerView]:
    error = ""
    if state is None:
        try:
            state = await service.ensure_category_setup_state(guild.id)
        except Exception as exc:
            error = f"{type(exc).__name__}: {_short(exc, 400)}"

    embed = discord.Embed(
        title=title,
        description=(
            "Choose **only** the ticket types this server actually uses. "
            "The full template library stays available here, but unselected "
            "choices never appear to members."
        ),
        color=discord.Color.red() if error else discord.Color.blurple(),
    )

    if error or state is None:
        embed.add_field(
            name="Database Problem",
            value=(error or "Ticket category state could not be loaded.")[:1024],
            inline=False,
        )
        return embed, CategorySetupManagerView(state=None, db_error=error)

    managed_active = [
        row for row in state.active_rows if _catalog_key(row)
    ]
    custom_active = _custom_rows_from_state(state)

    if state.required:
        embed.color = discord.Color.orange()
        embed.add_field(
            name="⚠️ Setup Required",
            value=(
                f"{state.reason or 'Confirm this server’s ticket choices.'}\n\n"
                "A small temporary menu is active so support is not completely "
                "blocked. Setup will remain unfinished until an admin saves the selection."
            )[:1024],
            inline=False,
        )

    embed.add_field(
        name="Currently Shown to Members",
        value=_active_line(managed_active, empty="No managed choices selected."),
        inline=False,
    )
    if custom_active:
        embed.add_field(
            name="Custom Choices Preserved",
            value=_active_line(custom_active, empty="None"),
            inline=False,
        )
    embed.add_field(
        name="How to Save",
        value=(
            "Open the dropdown, check every option you want, uncheck everything "
            "you do not want, then submit the selection. At least one is required."
        ),
        inline=False,
    )
    embed.add_field(
        name="Safety",
        value=(
            "Saving does not delete ticket channels, old tickets, roles, or custom "
            "choices. It only controls which managed labels appear in the menu."
        ),
        inline=False,
    )
    embed.set_footer(
        text=f"Guild {guild.id} • category setup v{service.CATEGORY_SETUP_VERSION}"
    )
    return embed, CategorySetupManagerView(state=state)


async def _setup_category_load(guild: discord.Guild) -> Any:
    from ..commands_ext import public_setup_solid as solid

    try:
        state = await service.ensure_category_setup_state(guild.id)
    except Exception as exc:
        return solid.CategoryLoad(
            [],
            f"Ticket choices could not be checked: {type(exc).__name__}: {_short(exc, 250)}",
        )

    if state.required:
        return solid.CategoryLoad(
            [],
            state.reason or "Ticket Menu Setup Required: choose the options this server uses.",
        )
    return solid.CategoryLoad(list(state.active_rows), "")


async def _seed_catalog_without_enabling_everything(
    guild: discord.Guild,
    *,
    managed_only: bool = False,
) -> tuple[List[str], List[str], str]:
    _ = managed_only
    try:
        rows = await service.sync_managed_categories(guild.id)
        summary = service.summarize_sync(rows)
        state = await service.ensure_category_setup_state(guild.id)
        created = [f"{summary['inserted']} catalog option(s)"] if summary["inserted"] else []
        skipped = list(state.selected_keys) or ["selection required"]
        return created, skipped, ""
    except Exception as exc:
        return [], [], f"{type(exc).__name__}: {_short(exc, 350)}"


async def _clean_panel_load_rows(guild: discord.Guild) -> tuple[List[Dict[str, Any]], str]:
    try:
        state = await service.ensure_category_setup_state(guild.id)
        warning = ""
        if state.required:
            warning = "This server is using a temporary safe ticket menu until an admin confirms its ticket choices."
        return list(state.active_rows), warning
    except Exception as exc:
        return service.starter_category_rows(), (
            f"Using safe starter ticket choices because category loading failed: {type(exc).__name__}"
        )


async def _legacy_public_load_rows(guild: discord.Guild) -> List[Dict[str, Any]]:
    return await service.load_visible_categories(guild.id)


def _cod_questions(intake_mod: Any) -> List[Dict[str, Any]]:
    make = getattr(intake_mod, "_make_question")
    return [
        make(key="cod_game", label="Which COD game?", placeholder="BO2, BO3, MWIII, BO6, BO7, Warzone, Zombies, etc.", style="short", max_length=180, row=0),
        make(key="cod_service", label="What COD question or service do you need help with?", placeholder="Describe what you need. Do not include passwords or private credentials.", style="paragraph", max_length=1000, row=1),
        make(key="cod_platform", label="Platform / account type", placeholder="Xbox, PlayStation, PC, Steam, Battle.net, Activision, etc.", style="short", max_length=180, row=2),
    ]


def _game_questions(intake_mod: Any) -> List[Dict[str, Any]]:
    make = getattr(intake_mod, "_make_question")
    return [
        make(key="game_title", label="Which game is this for?", placeholder="COD, Fortnite, Apex, Valorant, Minecraft, GTA, etc.", style="short", max_length=180, row=0),
        make(key="service_question", label="What game-related question do you need help with?", placeholder="Describe what you need. Do not include passwords or private credentials.", style="paragraph", max_length=1000, row=1),
        make(key="platform_or_account_type", label="Platform / account type", placeholder="Xbox, PlayStation, PC, mobile, Steam, Epic, etc.", style="short", required=False, max_length=180, row=2),
    ]


def _install_intake_and_form_support() -> None:
    try:
        from ..commands_ext import ticket_category_admin

        ticket_category_admin._ALLOWED_INTAKE_TYPES.update(
            _COD_INTAKE_TYPES | _GAME_INTAKE_TYPES
        )
    except Exception as exc:
        _warn(f"category admin intake types unavailable: {exc!r}")

    try:
        from ..tickets_new import intake_service

        intake_service._VALID_INTAKE_TYPES.update(
            _COD_INTAKE_TYPES | _GAME_INTAKE_TYPES
        )
        original = intake_service._default_questions_for_intake_type
        if not getattr(intake_service, "_CATEGORY_SETUP_V2_QUESTIONS", False):
            def default_questions_for_intake_type(intake_type: str):
                kind = _safe_str(intake_type).lower().replace("-", "_")
                if kind in _COD_INTAKE_TYPES:
                    return _cod_questions(intake_service)
                if kind in _GAME_INTAKE_TYPES:
                    return _game_questions(intake_service)
                return original(intake_type)

            intake_service._default_questions_for_intake_type = default_questions_for_intake_type
            intake_service._CATEGORY_SETUP_V2_QUESTIONS = True
    except Exception as exc:
        _warn(f"intake question support unavailable: {exc!r}")

    try:
        from . import ticket_form_default_templates_guard as forms

        templates = getattr(forms, "DEFAULT_TEMPLATES", None)
        if isinstance(templates, dict):
            templates.setdefault("cod_services", _cod_questions(forms))
            templates.setdefault("game_services", _game_questions(forms))
    except Exception:
        # The form guard uses a different internal question representation in
        # some deployments. Intake support above remains the source of truth.
        pass


def _install_live_loaders() -> None:
    from ..commands_ext import public_ticket_panel_clean as clean

    clean.DEFAULT_ROWS = tuple(service.starter_category_rows())
    clean._rows = lambda raw: service.dedupe_category_rows(
        raw,
        enabled_only=True,
        fallback=True,
    )
    clean._load_rows = _clean_panel_load_rows

    try:
        from ..commands_ext import public_tickettool_parity_polish as legacy_panel

        legacy_panel._normalize_ticket_rows = lambda raw: service.dedupe_category_rows(
            raw,
            enabled_only=True,
            fallback=True,
        )
        legacy_panel._effective_ticket_rows = lambda raw, defaults: service.dedupe_category_rows(
            raw,
            enabled_only=True,
            fallback=True,
        )
        legacy_panel._load_ticket_rows = _legacy_public_load_rows
    except Exception as exc:
        _warn(f"legacy public picker compatibility unavailable: {exc!r}")

    try:
        from ..tickets_new import panel

        panel._DEFAULT_BOOTSTRAP_CATEGORIES = tuple(service.catalog_category_rows())
        panel._bootstrap_categories_payload_for_guild = lambda guild_id: [
            {**row, "guild_id": str(int(guild_id))}
            for row in service.catalog_category_rows()
        ]
        panel._seed_dashboard_ticket_categories_sync = (
            lambda guild_id: (
                service._sync_managed_categories_sync(int(guild_id)),
                service.load_visible_categories_sync(int(guild_id)),
            )[1]
        )
        panel._fetch_dashboard_ticket_categories_sync = (
            lambda guild_id, allow_bootstrap=True: service.load_visible_categories_sync(int(guild_id))
        )
    except Exception as exc:
        _warn(f"native ticket routing compatibility unavailable: {exc!r}")


def _install_setup_owner() -> None:
    from ..commands_ext import public_setup_solid as solid

    solid.RECOMMENDED_CATEGORIES = tuple(service.catalog_category_rows())
    solid.INTAKE_TYPE_OPTIONS = tuple(
        sorted(set(solid.INTAKE_TYPE_OPTIONS) | _COD_INTAKE_TYPES | _GAME_INTAKE_TYPES)
    )
    solid._category_load = _setup_category_load
    solid._seed_recommended_categories = _seed_catalog_without_enabling_everything
    solid._build_category_manager_payload = _build_category_manager_payload
    solid.CategoryManagerView = CategorySetupManagerView

    try:
        from ..commands_ext import public_ticket_category_group as group

        managed_slugs = {
            _safe_str(row.get("slug")).replace("_", "-")
            for row in service.catalog_category_rows()
        }
        group._MANAGED_SLUGS = frozenset(managed_slugs)
    except Exception as exc:
        _warn(f"ticket category command ownership compatibility unavailable: {exc!r}")


def apply() -> bool:
    global _APPLIED
    if _APPLIED:
        return True

    try:
        _install_live_loaders()
        _install_setup_owner()
        _install_intake_and_form_support()
        _APPLIED = True
        _log(
            "single category owner active; explicit setup selection, exact dedupe, "
            "safe starter fallback, and existing-server migration enabled"
        )
        return True
    except Exception as exc:
        _warn(f"installation failed: {type(exc).__name__}: {exc!r}")
        return False


apply()

__all__ = [
    "CategorySetupManagerView",
    "ManagedCategorySelection",
    "apply",
]
