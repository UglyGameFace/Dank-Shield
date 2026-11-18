from __future__ import annotations

"""Phase 3 Slice 8 — Design Smart Repair Pro gate.

Both Smart Repair preview-build points (the v2 drift preview and the
legacy scoped category/channel repair preview) resolve through
``membership_gate.premium_decision`` with premium feature
``design_smart_repair``:

- membership unconfigured -> preview builds, exactly as before;
- configured + Pro -> preview builds;
- configured + free + a saved design footprint (non-empty
  ``category_styles`` / ``category_format_locks`` / ``channel_format_locks``)
  -> grandfathered, preview builds;
- configured + free + no footprint -> Pro denial embed, no plan is built
  and nothing is stored.

The normal saved-design scan (``build_saved_design_plan`` path) and the
non-editor preview modes are never gated.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

import stoney_verify.guild_context as guild_context
from stoney_verify.commands_ext import public_design_studio as legacy
from stoney_verify.commands_ext import public_design_studio_v2 as design
from stoney_verify.guild_config import GuildRuntimeConfig
from stoney_verify.guild_context import build_guild_context
from stoney_verify.services import membership
from stoney_verify.services import server_design_plan_service as plan_service

GUILD_ID = 4242
USER_ID = 777
MONTHLY_SKU = 111222333

_NO_FOOTPRINT = {"theme_id": "gothic_clean", "strength": 4}
_STYLES_FOOTPRINT = {
    **_NO_FOOTPRINT,
    "category_styles": {"555": {"font": "bold_sans"}},
}
_LOCKS_FOOTPRINT = {
    **_NO_FOOTPRINT,
    "category_format_locks": {"555": {"font": "bold_sans"}},
}


@pytest.fixture(autouse=True)
def _clean_membership_env(monkeypatch):
    monkeypatch.delenv(membership.PRO_MONTHLY_SKU_ENV, raising=False)
    monkeypatch.delenv(membership.PRO_YEARLY_SKU_ENV, raising=False)


def _ready_context(guild_id: int = GUILD_ID):
    config = {
        "guild_id": str(guild_id),
        "source": "supabase:guild_configs",
        "ticket_category_id": "111",
        "staff_role_id": "222",
        "verify_channel_id": "333",
        "unverified_role_id": "444",
        "verified_role_id": "555",
        "modlog_channel_id": "666",
        "use_env_fallbacks": False,
        "allow_runtime_discovery": True,
    }
    return build_guild_context(config["guild_id"], GuildRuntimeConfig(config))


def _configure(monkeypatch, status):
    monkeypatch.setenv(membership.PRO_MONTHLY_SKU_ENV, str(MONTHLY_SKU))

    async def resolve(_guild_id, *, client=None):
        return status

    monkeypatch.setattr(membership, "guild_pro_status", resolve)

    async def fake_context(_guild_id, *, refresh=False):
        return _ready_context(_guild_id)

    monkeypatch.setattr(guild_context, "get_guild_context", fake_context)


class _Interaction:
    def __init__(self, guild) -> None:
        self.guild = guild
        self.user = SimpleNamespace(id=USER_ID)
        self.client = object()
        self.response = SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock())
        self.edits: list[dict] = []

    async def edit_original_response(self, **kwargs):
        self.edits.append(kwargs)


def _guild():
    return SimpleNamespace(id=GUILD_ID, name="Gate Test Guild")


def _assert_pro_denial(interaction: _Interaction) -> None:
    assert len(interaction.edits) == 1
    embed = interaction.edits[0]["embed"]
    assert isinstance(embed, discord.Embed)
    assert embed.title == "⭐ Dank Shield Pro"
    description = embed.description or ""
    assert "`design_smart_repair` is a **Dank Shield Pro** feature" in description
    assert "/dank home" in description
    assert "Dank Shield Pro" in description


# ---------------------------------------------------------------------------
# Grandfather evidence
# ---------------------------------------------------------------------------


def test_grandfather_evidence_requires_styles_or_locks() -> None:
    assert legacy._design_repair_grandfathered({}) is False
    assert legacy._design_repair_grandfathered(_NO_FOOTPRINT) is False
    assert legacy._design_repair_grandfathered({"category_styles": {}}) is False
    assert legacy._design_repair_grandfathered({"category_format_locks": {}}) is False
    assert legacy._design_repair_grandfathered({"channel_format_locks": {}}) is False
    assert legacy._design_repair_grandfathered({"category_styles": "junk"}) is False
    assert legacy._design_repair_grandfathered(None) is False


def test_grandfather_evidence_accepts_any_saved_footprint() -> None:
    assert legacy._design_repair_grandfathered(_STYLES_FOOTPRINT) is True
    assert legacy._design_repair_grandfathered(_LOCKS_FOOTPRINT) is True
    assert legacy._design_repair_grandfathered(
        {"channel_format_locks": {"777": {"font": "bold_sans"}}}
    ) is True


# ---------------------------------------------------------------------------
# Drift preview (public_design_studio_v2._show_smart_repair_preview)
# ---------------------------------------------------------------------------


def _drive_drift(monkeypatch, *, options):
    built: list[dict] = []

    async def fake_build(guild, opts, **kwargs):
        built.append(dict(opts))
        return ([{"status": "changed", "kind": "channel", "channel_id": "1"}], dict(opts), {})

    monkeypatch.setattr(design.plans, "build_drift_repair_plan", fake_build)
    monkeypatch.setattr(design, "_load_design_options", AsyncMock(return_value=dict(options)))
    monkeypatch.setattr(
        design.search_safe_naming,
        "normalize_design_plan_for_guild",
        AsyncMock(side_effect=lambda _gid, items: (items, {})),
    )
    marker = discord.Embed(title="repair preview marker")
    monkeypatch.setattr(design, "_repair_preview_embed", lambda *args, **kwargs: marker)
    interaction = _Interaction(_guild())
    pending_before = dict(legacy._PENDING)
    asyncio.run(design._show_smart_repair_preview(interaction, keep_saved_rules=False))
    return interaction, built, marker, pending_before


def test_drift_preview_builds_when_unconfigured(monkeypatch) -> None:
    interaction, built, marker, _pending = _drive_drift(monkeypatch, options=_NO_FOOTPRINT)
    assert len(built) == 1
    assert interaction.edits[0]["embed"] is marker


def test_drift_preview_builds_when_pro(monkeypatch) -> None:
    _configure(monkeypatch, membership.STATUS_PRO)
    interaction, built, marker, _pending = _drive_drift(monkeypatch, options=_NO_FOOTPRINT)
    assert len(built) == 1
    assert interaction.edits[0]["embed"] is marker


def test_drift_preview_builds_when_free_with_saved_styles(monkeypatch) -> None:
    _configure(monkeypatch, membership.STATUS_FREE)
    interaction, built, marker, _pending = _drive_drift(monkeypatch, options=_STYLES_FOOTPRINT)
    assert len(built) == 1
    assert interaction.edits[0]["embed"] is marker


def test_drift_preview_denied_when_free_without_footprint(monkeypatch) -> None:
    _configure(monkeypatch, membership.STATUS_FREE)
    interaction, built, _marker, pending_before = _drive_drift(
        monkeypatch, options=_NO_FOOTPRINT
    )
    assert built == []
    _assert_pro_denial(interaction)
    # Nothing was stored for a later apply.
    assert legacy._PENDING == pending_before


# ---------------------------------------------------------------------------
# Scoped repair preview (public_design_studio._preview_scope, editor modes)
# ---------------------------------------------------------------------------


def _drive_scoped(monkeypatch, *, options, mode="category_editor"):
    built: list[dict] = []

    async def fake_build(guild, opts, **kwargs):
        built.append(dict(kwargs))
        return (
            [{"status": "changed", "kind": "category", "channel_id": "10", "category_id": "10"}],
            dict(opts),
            {},
        )

    monkeypatch.setattr(plan_service, "build_scoped_repair_plan", fake_build)
    monkeypatch.setattr(legacy, "_require_design_permission", AsyncMock(return_value=True))
    monkeypatch.setattr(legacy, "_load_design_options", AsyncMock(return_value=dict(options)))
    monkeypatch.setattr(
        legacy.search_safe_naming,
        "normalize_design_plan_for_guild",
        AsyncMock(side_effect=lambda _gid, items: (items, {})),
    )
    marker = discord.Embed(title="scoped preview marker")
    monkeypatch.setattr(legacy, "_preview_embed", lambda *args, **kwargs: marker)
    interaction = _Interaction(_guild())
    pending_before = dict(legacy._PENDING)
    asyncio.run(
        legacy._preview_scope(
            interaction,
            scope_title="👁️ Category Repair Preview",
            mode=mode,
            category_id=10,
        )
    )
    return interaction, built, marker, pending_before


def test_scoped_preview_builds_when_unconfigured(monkeypatch) -> None:
    interaction, built, marker, _pending = _drive_scoped(monkeypatch, options=_NO_FOOTPRINT)
    assert len(built) == 1
    assert interaction.edits[0]["embed"] is marker


def test_scoped_preview_builds_when_pro(monkeypatch) -> None:
    _configure(monkeypatch, membership.STATUS_PRO)
    interaction, built, marker, _pending = _drive_scoped(monkeypatch, options=_NO_FOOTPRINT)
    assert len(built) == 1
    assert interaction.edits[0]["embed"] is marker


def test_scoped_preview_builds_when_free_with_saved_locks(monkeypatch) -> None:
    _configure(monkeypatch, membership.STATUS_FREE)
    interaction, built, marker, _pending = _drive_scoped(
        monkeypatch, options=_LOCKS_FOOTPRINT
    )
    assert len(built) == 1
    assert interaction.edits[0]["embed"] is marker


def test_scoped_preview_denied_when_free_without_footprint(monkeypatch) -> None:
    _configure(monkeypatch, membership.STATUS_FREE)
    interaction, built, _marker, pending_before = _drive_scoped(
        monkeypatch, options=_NO_FOOTPRINT
    )
    assert built == []
    _assert_pro_denial(interaction)
    assert legacy._PENDING == pending_before


# ---------------------------------------------------------------------------
# Paths that must never be gated
# ---------------------------------------------------------------------------


def test_non_editor_preview_mode_is_never_gated(monkeypatch) -> None:
    _configure(monkeypatch, membership.STATUS_FREE)
    built: list[dict] = []

    async def fake_build(guild, opts):
        built.append(dict(opts))
        return [{"status": "changed", "kind": "channel", "channel_id": "1"}]

    monkeypatch.setattr(legacy, "build_design_plan", fake_build)
    monkeypatch.setattr(legacy, "_require_design_permission", AsyncMock(return_value=True))
    monkeypatch.setattr(legacy, "_load_design_options", AsyncMock(return_value=dict(_NO_FOOTPRINT)))
    monkeypatch.setattr(
        legacy.search_safe_naming,
        "normalize_design_plan_for_guild",
        AsyncMock(side_effect=lambda _gid, items: (items, {})),
    )
    marker = discord.Embed(title="plain preview marker")
    monkeypatch.setattr(legacy, "_preview_embed", lambda *args, **kwargs: marker)
    interaction = _Interaction(_guild())
    asyncio.run(
        legacy._preview_scope(
            interaction,
            scope_title="👁️ Design Preview",
            mode="full_preview",
        )
    )
    assert len(built) == 1
    assert interaction.edits[0]["embed"] is marker


def test_saved_design_scan_is_never_gated(monkeypatch) -> None:
    _configure(monkeypatch, membership.STATUS_FREE)
    monkeypatch.setattr(design, "_require_design_permission", AsyncMock(return_value=True))
    monkeypatch.setattr(
        design, "_load_design_options", AsyncMock(return_value=dict(_NO_FOOTPRINT))
    )
    built: list[bool] = []

    async def fake_scan(guild, opts):
        built.append(True)
        return ([], dict(opts), {})

    monkeypatch.setattr(design.plans, "build_saved_design_plan", fake_scan)
    marker = discord.Embed(title="scan marker")
    monkeypatch.setattr(design, "_scan_embed", lambda *args, **kwargs: marker)
    interaction = _Interaction(_guild())
    view = design.ReviewRepairView()
    button = next(
        item for item in view.children if getattr(item, "label", "") == "Scan Saved Design"
    )
    asyncio.run(button.callback(interaction))
    assert built == [True]
    assert interaction.edits[0]["embed"] is marker
