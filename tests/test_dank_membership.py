from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stoney_verify.commands_ext import public_design_studio_v2 as design
from stoney_verify.commands_ext import public_membership
from stoney_verify.guild_action_guard import decide_guild_action
from stoney_verify.guild_config import GuildRuntimeConfig
from stoney_verify.guild_context import build_guild_context
from stoney_verify.services import membership

CATEGORY_ID = 555
MONTHLY_SKU = 111222333
YEARLY_SKU = 444555666


@pytest.fixture(autouse=True)
def _clean_membership_state(monkeypatch):
    monkeypatch.delenv(membership.PRO_MONTHLY_SKU_ENV, raising=False)
    monkeypatch.delenv(membership.PRO_YEARLY_SKU_ENV, raising=False)
    membership._ENTITLEMENT_CACHE.clear()
    yield
    membership._ENTITLEMENT_CACHE.clear()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeHttp:
    def __init__(self, payloads=(), error: Exception | None = None):
        self.payloads = list(payloads)
        self.error = error
        self.calls = 0

    async def get_entitlements(self, application_id, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.payloads)


class _FakeClient:
    def __init__(self, payloads=(), error: Exception | None = None):
        self.application_id = 999000111
        self.http = _FakeHttp(payloads, error)
        self._guilds: dict[int, object] = {}

    def get_guild(self, guild_id):
        return self._guilds.get(int(guild_id))


def _entitlement(sku=MONTHLY_SKU, *, ends_at="2999-01-01T00:00:00+00:00", etype=8, deleted=False):
    return {
        "id": 1,
        "type": etype,
        "sku_id": sku,
        "user_id": 7,
        "ends_at": ends_at,
        "deleted": deleted,
    }


class _FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._rows: list[tuple] = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split()).lower()
        rows = self.connection.rows
        if "limit 0" in normalized:
            self._rows = []
        elif "has_table_privilege" in normalized:
            self._rows = [(True,)]
        elif normalized.startswith("select user_id from public.dank_membership_guild_links"):
            owner = rows.get(params[0])
            self._rows = [(owner,)] if owner is not None else []
        elif normalized.startswith("select guild_id from public.dank_membership_guild_links"):
            self._rows = [(gid,) for gid, uid in self.connection.ordered if uid == params[0]]
        elif normalized.startswith("select count(*)"):
            self._rows = [(sum(1 for uid in rows.values() if uid == params[0]),)]
        elif normalized.startswith("insert into public.dank_membership_guild_links"):
            uid, gid = params
            rows[gid] = uid
            self.connection.ordered.append((gid, uid))
            self.rowcount = 1
        elif normalized.startswith("delete from public.dank_membership_guild_links"):
            uid, gid = params
            if rows.get(gid) == uid:
                del rows[gid]
                self.connection.ordered = [(g, u) for g, u in self.connection.ordered if g != gid]
                self.rowcount = 1
            else:
                self.rowcount = 0
        else:  # pragma: no cover - guards against silent SQL drift
            raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeConnection:
    def __init__(self):
        self.rows: dict[int, int] = {}
        self.ordered: list[tuple[int, int]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return _FakeCursor(self)


def _link_db(monkeypatch) -> _FakeConnection:
    connection = _FakeConnection()
    monkeypatch.setattr(membership, "_connect", lambda: connection)
    return connection


def _ready_context(guild_id: int = 4242):
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


# ---------------------------------------------------------------------------
# SKU config
# ---------------------------------------------------------------------------


def test_sku_config_unconfigured_by_default():
    assert membership.pro_sku_ids() == ()
    assert membership.membership_configured() is False


def test_sku_config_parses_monthly_and_yearly(monkeypatch):
    monkeypatch.setenv(membership.PRO_MONTHLY_SKU_ENV, str(MONTHLY_SKU))
    monkeypatch.setenv(membership.PRO_YEARLY_SKU_ENV, str(YEARLY_SKU))
    assert membership.pro_sku_ids() == (MONTHLY_SKU, YEARLY_SKU)
    assert membership.membership_configured() is True


def test_sku_config_drops_junk_and_duplicates(monkeypatch):
    monkeypatch.setenv(membership.PRO_MONTHLY_SKU_ENV, "not-a-number")
    monkeypatch.setenv(membership.PRO_YEARLY_SKU_ENV, str(YEARLY_SKU))
    assert membership.pro_sku_ids() == (YEARLY_SKU,)
    monkeypatch.setenv(membership.PRO_MONTHLY_SKU_ENV, str(YEARLY_SKU))
    assert membership.pro_sku_ids() == (YEARLY_SKU,)


# ---------------------------------------------------------------------------
# Entitlement checks
# ---------------------------------------------------------------------------


def test_entitlement_active_for_configured_sku():
    client = _FakeClient([_entitlement()])
    assert asyncio.run(membership.user_has_active_pro(client, 7, sku_ids=(MONTHLY_SKU,))) is True
    assert client.http.calls == 1


def test_entitlement_absent_wrong_sku_expired_and_deleted():
    assert asyncio.run(membership.user_has_active_pro(_FakeClient([]), 7, sku_ids=(MONTHLY_SKU,))) is False
    wrong = _FakeClient([_entitlement(sku=999)])
    assert asyncio.run(membership.user_has_active_pro(wrong, 7, sku_ids=(MONTHLY_SKU,))) is False
    expired = _FakeClient([_entitlement(ends_at="2000-01-01T00:00:00+00:00")])
    assert asyncio.run(membership.user_has_active_pro(expired, 7, sku_ids=(MONTHLY_SKU,))) is False
    deleted = _FakeClient([_entitlement(deleted=True)])
    assert asyncio.run(membership.user_has_active_pro(deleted, 7, sku_ids=(MONTHLY_SKU,))) is False
    gift = _FakeClient([_entitlement(etype=3)])
    assert asyncio.run(membership.user_has_active_pro(gift, 7, sku_ids=(MONTHLY_SKU,))) is False


def test_entitlement_result_is_cached_for_the_ttl():
    client = _FakeClient([_entitlement()])
    assert asyncio.run(membership.user_has_active_pro(client, 7, sku_ids=(MONTHLY_SKU,))) is True
    assert asyncio.run(membership.user_has_active_pro(client, 7, sku_ids=(MONTHLY_SKU,))) is True
    assert client.http.calls == 1


def test_entitlement_api_error_falls_back_to_cached_positive():
    client = _FakeClient([_entitlement()])
    assert asyncio.run(membership.user_has_active_pro(client, 7, sku_ids=(MONTHLY_SKU,))) is True
    # Expire the cache entry, then break the API: the stale positive still wins.
    membership._ENTITLEMENT_CACHE[7] = (0.0, True)
    client.http.error = RuntimeError("discord down")
    assert asyncio.run(membership.user_has_active_pro(client, 7, sku_ids=(MONTHLY_SKU,))) is True


def test_entitlement_api_error_without_cache_is_not_entitled():
    client = _FakeClient(error=RuntimeError("discord down"))
    assert asyncio.run(membership.user_has_active_pro(client, 7, sku_ids=(MONTHLY_SKU,))) is False


# ---------------------------------------------------------------------------
# Covered-guild links
# ---------------------------------------------------------------------------


def test_link_rules_cap_owner_unlink(monkeypatch):
    _link_db(monkeypatch)
    assert membership.links_ready_sync() is True
    assert membership.link_guild_sync(7, 100) == membership.LINK_LINKED
    assert membership.link_guild_sync(7, 100) == membership.LINK_ALREADY_LINKED
    assert membership.link_guild_sync(8, 100) == membership.LINK_GUILD_OWNED
    assert membership.link_guild_sync(7, 101) == membership.LINK_LINKED
    assert membership.link_guild_sync(7, 102) == membership.LINK_LINKED
    assert membership.link_guild_sync(7, 103) == membership.LINK_CAP_REACHED
    assert membership.links_for_user_sync(7) == (100, 101, 102)
    assert membership.owner_of_guild_sync(101) == 7
    assert membership.owner_of_guild_sync(999) is None
    assert membership.unlink_guild_sync(8, 100) is False
    assert membership.unlink_guild_sync(7, 100) is True
    assert membership.links_for_user_sync(7) == (101, 102)
    assert membership.link_guild_sync(0, 100) == membership.LINK_UNAVAILABLE


def test_guild_pro_status_resolution(monkeypatch):
    _link_db(monkeypatch)
    guild = 4242
    # Unconfigured wins over everything.
    assert asyncio.run(membership.guild_pro_status(guild, client=_FakeClient())) == membership.STATUS_UNCONFIGURED
    monkeypatch.setenv(membership.PRO_MONTHLY_SKU_ENV, str(MONTHLY_SKU))
    # No link -> free.
    assert asyncio.run(membership.guild_pro_status(guild, client=_FakeClient())) == membership.STATUS_FREE
    membership.link_guild_sync(7, guild)
    # Linked owner without entitlement -> free; with -> pro. The cache is
    # cleared between the two because the service intentionally serves the
    # 5-minute TTL cache regardless of which client object asks.
    assert asyncio.run(membership.guild_pro_status(guild, client=_FakeClient([]))) == membership.STATUS_FREE
    membership._ENTITLEMENT_CACHE.clear()
    pro_client = _FakeClient([_entitlement()])
    assert asyncio.run(membership.guild_pro_status(guild, client=pro_client)) == membership.STATUS_PRO


# ---------------------------------------------------------------------------
# Guard three-state behavior
# ---------------------------------------------------------------------------


def _premium_decision(resolver=None):
    return decide_guild_action(
        _ready_context(),
        action="save a category style",
        feature="setup",
        premium_feature="design_category_styles",
        pro_status_resolver=resolver,
    )


def test_guard_without_resolver_keeps_audit_only_behavior():
    decision = _premium_decision()
    assert decision.allowed is True
    assert decision.premium_feature == "design_category_styles"
    assert decision.warnings == (
        "Premium gate pending for `design_category_styles`. Current build treats this as an audit-only warning.",
    )


def test_guard_unconfigured_status_keeps_audit_only_behavior():
    decision = _premium_decision(membership.pro_status_resolver(membership.STATUS_UNCONFIGURED))
    assert decision.allowed is True
    assert any("audit-only" in warning for warning in decision.warnings)


def test_guard_pro_status_allows_without_warning():
    decision = _premium_decision(membership.pro_status_resolver(membership.STATUS_PRO))
    assert decision.allowed is True
    assert decision.warnings == ()
    assert decision.premium_feature == "design_category_styles"


def test_guard_free_status_blocks_with_pro_blocker():
    decision = _premium_decision(membership.pro_status_resolver(membership.STATUS_FREE))
    assert decision.allowed is False
    assert decision.premium_feature == "design_category_styles"
    assert any("Dank Shield Pro" in blocker for blocker in decision.blockers)


def test_guard_resolver_failure_falls_back_to_audit_only():
    def boom():
        raise RuntimeError("resolver exploded")

    decision = _premium_decision(boom)
    assert decision.allowed is True
    assert any("audit-only" in warning for warning in decision.warnings)


# ---------------------------------------------------------------------------
# Pilot gate: Dank Design category-styles save
# ---------------------------------------------------------------------------


def _fake_guild() -> SimpleNamespace:
    garden = SimpleNamespace(
        id=CATEGORY_ID,
        name="The Garden",
        position=0,
        channels=[SimpleNamespace(id=777, name="general-chat", position=0)],
    )
    return SimpleNamespace(id=4242, categories=[garden], get_channel=lambda channel_id: None)


def _fake_interaction(guild: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=92),
        client=_FakeClient(),
        response=SimpleNamespace(defer=AsyncMock(), edit_message=AsyncMock(), is_done=lambda: False),
        edit_original_response=AsyncMock(),
    )


def _button(view: object, label: str) -> object:
    return next(item for item in getattr(view, "children", []) if getattr(item, "label", "") == label)


def _run_save(monkeypatch, *, existing_options: dict, status: str | None) -> list[dict]:
    """Drive the real Save button with membership patched to ``status``.

    status=None means membership unconfigured. Returns the saved options
    payloads (empty when the save was blocked).
    """

    monkeypatch.setattr(design, "_require_design_permission", AsyncMock(return_value=True))
    saved: list[dict] = []

    async def fake_save(interaction: object, options: dict) -> dict:
        saved.append(dict(options))
        return options

    monkeypatch.setattr(design, "_load_design_options", AsyncMock(return_value=dict(existing_options)))
    monkeypatch.setattr(design.legacy, "_save_options", fake_save)
    if status is None:
        monkeypatch.setattr(membership, "membership_configured", lambda: False)
    else:
        monkeypatch.setattr(membership, "membership_configured", lambda: True)
        monkeypatch.setattr(membership, "guild_pro_status", AsyncMock(return_value=status))
    import stoney_verify.guild_context as guild_context_module

    monkeypatch.setattr(
        guild_context_module,
        "get_guild_context",
        AsyncMock(return_value=_ready_context()),
    )
    guild = _fake_guild()
    interaction = _fake_interaction(guild)

    async def check() -> None:
        view = design.DesignCategoryStylesView(
            guild,
            {},
            category_id=CATEGORY_ID,
            draft={"font": "sans", "category_font": None, "separator_id": None, "category_frame_id": None},
        )
        await _button(view, "Save Category Style").callback(interaction)  # type: ignore[attr-defined]

    asyncio.run(check())
    return saved


def test_pilot_unconfigured_save_proceeds(monkeypatch):
    saved = _run_save(monkeypatch, existing_options={"theme_id": "gothic_clean"}, status=None)
    assert len(saved) == 1
    assert str(CATEGORY_ID) in saved[0]["category_styles"]


def test_pilot_free_guild_first_style_is_blocked(monkeypatch):
    saved = _run_save(monkeypatch, existing_options={"theme_id": "gothic_clean"}, status=membership.STATUS_FREE)
    assert saved == []


def test_pilot_pro_guild_first_style_is_saved(monkeypatch):
    saved = _run_save(monkeypatch, existing_options={"theme_id": "gothic_clean"}, status=membership.STATUS_PRO)
    assert len(saved) == 1
    assert str(CATEGORY_ID) in saved[0]["category_styles"]


def test_pilot_grandfathered_guild_keeps_saving_when_free(monkeypatch):
    existing = {
        "theme_id": "gothic_clean",
        "category_styles": {
            "556": {"font": "sans", "category_font": None, "separator_id": None, "category_frame_id": None}
        },
    }
    saved = _run_save(monkeypatch, existing_options=existing, status=membership.STATUS_FREE)
    assert len(saved) == 1
    assert str(CATEGORY_ID) in saved[0]["category_styles"]
    assert "556" in saved[0]["category_styles"]


# ---------------------------------------------------------------------------
# Pro panel smoke
# ---------------------------------------------------------------------------


def _state(**overrides) -> public_membership._ProPanelState:
    base = dict(
        configured=False,
        guild_id=4242,
        guild_status=membership.STATUS_UNCONFIGURED,
        current_owner=None,
        viewer_id=92,
        viewer_pro=False,
        viewer_links=(),
        sku_ids=(),
        notice="",
    )
    base.update(overrides)
    return public_membership._ProPanelState(**base)


def test_pro_view_unconfigured_is_inert():
    view = public_membership.DankProView(_state())
    assert view.children == []
    embed = public_membership._pro_embed(_state())
    assert "isn't switched on" in str(embed.fields[0].value)


def test_pro_view_free_viewer_gets_only_premium_buttons():
    state = _state(
        configured=True,
        guild_status=membership.STATUS_FREE,
        sku_ids=(MONTHLY_SKU, YEARLY_SKU),
    )
    view = public_membership.DankProView(state)
    assert len(view.children) == 2
    assert all(getattr(item, "style", None) is not None and int(item.style.value) == 6 for item in view.children)
    assert all(getattr(item, "sku_id", None) in (MONTHLY_SKU, YEARLY_SKU) for item in view.children)


def test_pro_view_pro_viewer_gets_link_and_unlink_buttons():
    state = _state(
        configured=True,
        guild_status=membership.STATUS_FREE,
        viewer_pro=True,
        viewer_links=((777, "Other Server"),),
        sku_ids=(MONTHLY_SKU,),
    )
    view = public_membership.DankProView(state)
    labels = [str(getattr(item, "label", "")) for item in view.children]
    assert "Link this server" in labels
    assert any(label.startswith("Unlink") for label in labels)
    assert all(getattr(item, "sku_id", None) is None for item in view.children)


def test_pro_view_pro_viewer_at_cap_gets_no_link_button():
    state = _state(
        configured=True,
        guild_status=membership.STATUS_FREE,
        viewer_pro=True,
        viewer_links=((1, "A"), (2, "B"), (3, "C")),
        sku_ids=(MONTHLY_SKU,),
    )
    view = public_membership.DankProView(state)
    labels = [str(getattr(item, "label", "")) for item in view.children]
    assert "Link this server" not in labels
    assert sum(1 for label in labels if label.startswith("Unlink")) == 3
