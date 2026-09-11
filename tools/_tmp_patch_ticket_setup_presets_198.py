from pathlib import Path

SOURCE = Path('stoney_verify/startup_guards/ticket_category_setup_guard.py')
TESTS = Path('tests/test_ticket_category_setup_selection.py')

source = SOURCE.read_text(encoding='utf-8')

anchor = '''_GAME_INTAKE_TYPES = {
    "game_services",
    "game_service",
    "game_support",
    "custom_game_services",
    "service_question",
}
'''
replacement = anchor + '''
# Setup presets are shortcuts only. The multi-select remains authoritative and
# owners can choose any combination from the complete managed catalog.
_COMMUNITY_CORE_PRESET_KEYS: tuple[str, ...] = (
    "verification",
    "appeal",
    "report",
    "staff-complaint",
    "bug",
    "question",
    "support",
)
_SERVICE_GAMING_PRESET_KEYS: tuple[str, ...] = (
    "verification",
    "account-access",
    "payments-refunds",
    "appeal",
    "report",
    "staff-complaint",
    "bug",
    "cod-services",
    "game-services",
    "service-request",
    "vouch-referral",
    "partnership",
    "question",
    "support",
)
_ALL_MANAGED_PRESET_KEYS: tuple[str, ...] = tuple(
    str(row["category_key"]) for row in service.CATEGORY_CATALOG
)
'''
if anchor not in source:
    raise SystemExit('preset constant anchor missing')
source = source.replace(anchor, replacement, 1)

anchor = '''        if state is not None and not db_error:
            self.add_item(ManagedCategorySelection(state))
            custom_rows = _custom_rows_from_state(state)
            if custom_rows:
'''
replacement = '''        if state is not None and not db_error:
            self.add_item(ManagedCategorySelection(state))

            core = discord.ui.Button(
                label="Community Core",
                emoji="🛡️",
                style=discord.ButtonStyle.secondary,
                custom_id="dank_ticket_category_setup:preset_core",
                row=1,
            )
            core.callback = self._use_core_preset
            self.add_item(core)

            service_gaming = discord.ui.Button(
                label="Service + Gaming",
                emoji="🎮",
                style=discord.ButtonStyle.primary,
                custom_id="dank_ticket_category_setup:preset_service_gaming",
                row=1,
            )
            service_gaming.callback = self._use_service_gaming_preset
            self.add_item(service_gaming)

            all_builtins = discord.ui.Button(
                label=f"All {len(_ALL_MANAGED_PRESET_KEYS)} Built-ins",
                emoji="📚",
                style=discord.ButtonStyle.secondary,
                custom_id="dank_ticket_category_setup:preset_all",
                row=1,
            )
            all_builtins.callback = self._use_all_preset
            self.add_item(all_builtins)

            custom_rows = _custom_rows_from_state(state)
            if custom_rows:
'''
if anchor not in source:
    raise SystemExit('view preset insertion anchor missing')
source = source.replace(anchor, replacement, 1)

old = '''                        placeholder="✏️ Edit a custom ticket choice",
                        row=1,
'''
new = '''                        placeholder="✏️ Edit a custom ticket choice",
                        row=2,
'''
if old not in source:
    raise SystemExit('custom select row anchor missing')
source = source.replace(old, new, 1)

old = '''                    label="Use Custom Choices Only",
                    emoji="🧩",
                    style=discord.ButtonStyle.secondary,
                    custom_id="dank_ticket_category_setup:custom_only",
                    row=2,
'''
new = '''                    label="Custom Only",
                    emoji="🧩",
                    style=discord.ButtonStyle.secondary,
                    custom_id="dank_ticket_category_setup:custom_only",
                    row=1,
'''
if old not in source:
    raise SystemExit('custom only row anchor missing')
source = source.replace(old, new, 1)

anchor = '''    async def _use_custom_only(self, interaction: discord.Interaction) -> None:
'''
insert = '''    async def _apply_managed_preset(
        self,
        interaction: discord.Interaction,
        keys: Iterable[str],
        *,
        title: str,
        summary: str,
    ) -> None:
        from ..commands_ext import public_setup_solid as solid

        if not await self._allowed(interaction):
            return
        state = await _save_selection(interaction, tuple(keys))
        if state is None or interaction.guild is None:
            return
        embed, view = await _build_category_manager_payload(
            interaction.guild,
            title=title,
            state=state,
        )
        embed.add_field(name="Preset Applied", value=summary, inline=False)
        await solid._edit_or_followup(interaction, embed=embed, view=view)

    async def _use_core_preset(self, interaction: discord.Interaction) -> None:
        await self._apply_managed_preset(
            interaction,
            _COMMUNITY_CORE_PRESET_KEYS,
            title="✅ Community Core Ticket Menu Saved",
            summary=(
                "Enabled the common moderation and support choices. You can still "
                "fine-tune the exact list with the multi-select above."
            ),
        )

    async def _use_service_gaming_preset(self, interaction: discord.Interaction) -> None:
        await self._apply_managed_preset(
            interaction,
            _SERVICE_GAMING_PRESET_KEYS,
            title="✅ Service + Gaming Ticket Menu Saved",
            summary=(
                "Enabled the broader service/gaming set, including account/payment "
                "help, legacy COD modding, game services, referrals, and partnerships."
            ),
        )

    async def _use_all_preset(self, interaction: discord.Interaction) -> None:
        await self._apply_managed_preset(
            interaction,
            _ALL_MANAGED_PRESET_KEYS,
            title="✅ Full Ticket Catalog Saved",
            summary=(
                f"Enabled all {len(_ALL_MANAGED_PRESET_KEYS)} built-in choices. "
                "Use the multi-select anytime to hide categories this server does not need."
            ),
        )

'''
if anchor not in source:
    raise SystemExit('preset method anchor missing')
source = source.replace(anchor, insert + anchor, 1)

old = '''                "A small temporary menu is active so support is not completely "
                "blocked. Setup remains unfinished until an admin saves a selection."
'''
new = '''                "Your last known selection stays visible while review is required. "
                "If no trustworthy prior selection exists, the safe starter choices stay "
                "active until an admin saves the server's real selection."
'''
if old not in source:
    raise SystemExit('setup required copy anchor missing')
source = source.replace(old, new, 1)

old = '''            "Use the multi-select to choose every built-in option you want. "
            "Servers with custom choices can press **Use Custom Choices Only** "
            "to keep every built-in option off."
'''
new = '''            "Use the multi-select for an exact per-server list, or use **Community Core**, "
            "**Service + Gaming**, or **All Built-ins** as a shortcut. Servers with custom "
            "choices can use **Custom Only** to keep every built-in option off."
'''
if old not in source:
    raise SystemExit('how to save copy anchor missing')
source = source.replace(old, new, 1)

old = '''def _cod_questions(intake_mod: Any) -> List[Dict[str, Any]]:
    make = getattr(intake_mod, "_make_question")
    return [
        make(key="cod_game", label="Which COD game?", placeholder="BO2, BO3, MWIII, BO6, BO7, Warzone, Zombies, etc.", style="short", max_length=180, row=0),
        make(key="cod_service", label="What COD question or service do you need help with?", placeholder="Describe what you need. Do not include passwords or private credentials.", style="paragraph", max_length=1000, row=1),
        make(key="cod_platform", label="Platform / account type", placeholder="Xbox, PlayStation, PC, Steam, Battle.net, Activision, etc.", style="short", max_length=180, row=2),
    ]
'''
new = '''def _cod_questions(intake_mod: Any) -> List[Dict[str, Any]]:
    make = getattr(intake_mod, "_make_question")
    return [
        make(key="cod_game", label="Which legacy COD title?", placeholder="BO1, BO2, BO3, WaW, MW2, MW3, Ghosts, Zombies, etc.", style="short", max_length=180, row=0),
        make(key="cod_service", label="Which modding service do you need?", placeholder="Modded/challenge lobby, unlocks, recovery, RGH/JTAG, Zombies help, etc. Do not include passwords.", style="paragraph", max_length=1000, row=1),
        make(key="cod_platform", label="Platform / account type", placeholder="Xbox, PlayStation, or PC; include console/account type if relevant.", style="short", max_length=180, row=2),
    ]
'''
if old not in source:
    raise SystemExit('COD questions anchor missing')
source = source.replace(old, new, 1)

SOURCE.write_text(source, encoding='utf-8')

tests = TESTS.read_text(encoding='utf-8')
append = r'''


def test_owner_setup_exposes_full_catalog_and_optional_presets() -> None:
    rows = categories.catalog_category_rows()
    state = categories.CategorySetupState(
        rows=rows,
        active_rows=[row for row in rows if row["category_key"] == "support"],
        selected_keys=("support",),
        required=False,
        reason="",
        version=categories.CATEGORY_SETUP_VERSION,
    )
    view = setup_guard.CategorySetupManagerView(state=state)
    custom_ids = {str(getattr(child, "custom_id", "")) for child in view.children}

    assert "dank_ticket_category_setup:preset_core" in custom_ids
    assert "dank_ticket_category_setup:preset_service_gaming" in custom_ids
    assert "dank_ticket_category_setup:preset_all" in custom_ids

    selector = next(child for child in view.children if isinstance(child, setup_guard.ManagedCategorySelection))
    assert len(selector.options) == len(categories.CATEGORY_CATALOG) == 16
    assert {option.value for option in selector.options} == {
        row["category_key"] for row in categories.CATEGORY_CATALOG
    }
    assert {option.value for option in selector.options if option.default} == {"support"}


def test_setup_presets_are_catalog_subsets_not_global_forcing() -> None:
    all_keys = tuple(row["category_key"] for row in categories.CATEGORY_CATALOG)
    assert setup_guard._ALL_MANAGED_PRESET_KEYS == all_keys
    assert set(setup_guard._COMMUNITY_CORE_PRESET_KEYS) < set(all_keys)
    assert set(setup_guard._SERVICE_GAMING_PRESET_KEYS) < set(all_keys)
    assert "cod-services" not in setup_guard._COMMUNITY_CORE_PRESET_KEYS
    assert "cod-services" in setup_guard._SERVICE_GAMING_PRESET_KEYS
    assert "content-media" not in setup_guard._SERVICE_GAMING_PRESET_KEYS
    assert "giveaway-reward" not in setup_guard._SERVICE_GAMING_PRESET_KEYS


def test_custom_setup_keeps_custom_only_and_custom_editor_off_preset_row() -> None:
    custom = {
        "id": "custom-1",
        "slug": "clan_application",
        "name": "Clan Application",
        "is_enabled": True,
        "is_default": True,
        "managed_by_dank": False,
    }
    rows = [*categories.catalog_category_rows(), custom]
    state = categories.CategorySetupState(
        rows=rows,
        active_rows=[custom],
        selected_keys=(),
        required=False,
        reason="",
        version=categories.CATEGORY_SETUP_VERSION,
    )
    view = setup_guard.CategorySetupManagerView(state=state)
    custom_ids = {str(getattr(child, "custom_id", "")) for child in view.children}
    assert "dank_ticket_category_setup:custom_only" in custom_ids
    custom_editor = next(
        child for child in view.children
        if str(getattr(child, "placeholder", "")) == "✏️ Edit a custom ticket choice"
    )
    assert custom_editor.row == 2


def test_community_core_preset_saves_only_that_subset(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        rows = categories.catalog_category_rows()
        initial = categories.CategorySetupState(
            rows=rows,
            active_rows=[row for row in rows if row["category_key"] == "support"],
            selected_keys=("support",),
            required=True,
            reason="Review choices.",
            version=0,
        )
        captured: list[tuple[str, ...]] = []

        async def fake_save(interaction: Any, selected_keys: Any):
            keys = tuple(selected_keys)
            captured.append(keys)
            return categories.CategorySetupState(
                rows=rows,
                active_rows=[row for row in rows if row["category_key"] in set(keys)],
                selected_keys=keys,
                required=False,
                reason="",
                version=categories.CATEGORY_SETUP_VERSION,
            )

        async def fake_payload(guild: Any, **kwargs: Any):
            return discord.Embed(title="saved"), object()

        async def fake_edit(*args: Any, **kwargs: Any) -> None:
            return None

        view = setup_guard.CategorySetupManagerView(state=initial)

        async def allowed(interaction: Any) -> bool:
            return True

        monkeypatch.setattr(view, "_allowed", allowed)
        monkeypatch.setattr(setup_guard, "_save_selection", fake_save)
        monkeypatch.setattr(setup_guard, "_build_category_manager_payload", fake_payload)
        monkeypatch.setattr(solid, "_edit_or_followup", fake_edit)

        interaction = SimpleNamespace(guild=SimpleNamespace(id=1234))
        await view._use_core_preset(interaction)

        assert captured == [setup_guard._COMMUNITY_CORE_PRESET_KEYS]
        assert set(captured[0]) != set(setup_guard._ALL_MANAGED_PRESET_KEYS)

    asyncio.run(scenario())
'''
if 'test_owner_setup_exposes_full_catalog_and_optional_presets' in tests:
    raise SystemExit('preset tests already present')
# New callback test needs discord. Keep the import local to this file's public test surface.
tests = tests.replace('import pytest\n', 'import pytest\nimport discord\n', 1)
tests += append
TESTS.write_text(tests, encoding='utf-8')
