from __future__ import annotations

import asyncio

import pytest

import stoney_verify.guild_context as guild_context
from stoney_verify.guild_config import GuildRuntimeConfig
from stoney_verify.guild_context import build_guild_context
from stoney_verify.services import membership, membership_gate

GUILD_ID = 4242
MONTHLY_SKU = 111222333


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


def _status_stub(status, calls: list | None = None):
    async def resolve(_guild_id, *, client=None):
        if calls is not None:
            calls.append((_guild_id, client))
        return status

    return resolve


def _configure(monkeypatch, status, calls: list | None = None):
    monkeypatch.setenv(membership.PRO_MONTHLY_SKU_ENV, str(MONTHLY_SKU))
    monkeypatch.setattr(membership, "guild_pro_status", _status_stub(status, calls))

    async def fake_context(_guild_id, *, refresh=False):
        return _ready_context(_guild_id)

    monkeypatch.setattr(guild_context, "get_guild_context", fake_context)


def _decide(**overrides):
    kwargs = dict(
        guild_id=GUILD_ID,
        client=object(),
        action="create a server structure backup",
        premium_feature="server_backup_restore",
    )
    kwargs.update(overrides)
    return asyncio.run(membership_gate.premium_decision(**kwargs))


# ---------------------------------------------------------------------------
# premium_decision three-state behavior
# ---------------------------------------------------------------------------


def test_unconfigured_proceeds_without_resolving_status(monkeypatch):
    calls: list = []
    monkeypatch.setattr(membership, "guild_pro_status", _status_stub(membership.STATUS_FREE, calls))
    assert _decide() is None
    assert calls == []


def test_configured_pro_proceeds(monkeypatch):
    _configure(monkeypatch, membership.STATUS_PRO)
    assert _decide() is None


def test_configured_free_is_denied_with_the_guard_blocker(monkeypatch):
    _configure(monkeypatch, membership.STATUS_FREE)
    decision = _decide()
    assert decision is not None
    assert decision.denied is True
    assert decision.premium_feature == "server_backup_restore"
    assert any(
        "`server_backup_restore` is a Dank Shield Pro feature" in blocker
        for blocker in decision.blockers
    )


def test_configured_but_status_unconfigured_proceeds(monkeypatch):
    _configure(monkeypatch, membership.STATUS_UNCONFIGURED)
    assert _decide() is None


def test_grandfathered_proceeds_even_when_configured_and_free(monkeypatch):
    calls: list = []
    _configure(monkeypatch, membership.STATUS_FREE, calls)
    assert _decide(grandfathered=True) is None
    # The grandfather short-circuit fires before any status resolution.
    assert calls == []


def test_resolver_raising_falls_back_to_proceed(monkeypatch):
    _configure(monkeypatch, membership.STATUS_FREE)

    def raising_resolver(_status):
        def boom():
            raise RuntimeError("resolver exploded")

        return boom

    monkeypatch.setattr(membership, "pro_status_resolver", raising_resolver)
    # The guard treats a resolver failure as the historical audit-only
    # warning, so the action is allowed and the helper returns None.
    assert _decide() is None


def test_status_resolution_failure_fails_open(monkeypatch):
    monkeypatch.setenv(membership.PRO_MONTHLY_SKU_ENV, str(MONTHLY_SKU))

    async def broken_status(_guild_id, *, client=None):
        raise RuntimeError("entitlement store unavailable")

    monkeypatch.setattr(membership, "guild_pro_status", broken_status)
    assert _decide() is None


# ---------------------------------------------------------------------------
# pro_denial_embed
# ---------------------------------------------------------------------------


def test_pro_denial_embed_names_feature_and_pro_panel_route():
    embed = membership_gate.pro_denial_embed("server_backup_restore")
    assert embed.title == "⭐ Dank Shield Pro"
    description = str(embed.description or "")
    assert "`server_backup_restore` is a **Dank Shield Pro** feature." in description
    assert "`/dank home`" in description
    assert "Setup & Server Settings" in description
    assert "Dank Shield Pro" in description


def test_pro_denial_embed_accepts_caller_detail_and_decision(monkeypatch):
    _configure(monkeypatch, membership.STATUS_FREE)
    decision = _decide()
    embed = membership_gate.pro_denial_embed(
        "server_backup_restore",
        decision,
        detail="Backups stay viewable for free; creating one is Pro.",
    )
    description = str(embed.description or "")
    assert "Backups stay viewable for free; creating one is Pro." in description
    assert "`server_backup_restore`" in description
