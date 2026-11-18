"""Membership-rule availability resolution for Embed Studio sub-features.

Governing rule: a legacy env flag is an operator override; while Dank
Shield Pro is unconfigured the finished features are available wherever
studio storage is ready; once configured, uncovered guilds are Pro-locked
with an explicit message — never silently hidden.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from stoney_verify import embed_studio_availability as availability
from stoney_verify import guild_context
from stoney_verify.embed_studio_campaigns import FLAG as RECURRING_FLAG
from stoney_verify.embed_studio_forms import FLAG as FORMS_FLAG
from stoney_verify.embed_studio_live_text import FLAG as LIVE_FLAG
from stoney_verify.embed_studio_schedules import FLAG as SCHEDULING_FLAG
from stoney_verify.embed_studio_storage_v2 import FLAG as V2_FLAG
from stoney_verify.services import membership

FEATURES = (
    availability.FEATURE_SCHEDULING,
    availability.FEATURE_RECURRING,
    availability.FEATURE_FORMS,
    availability.FEATURE_LIVE,
)

STORAGE_ONLY = {V2_FLAG: "1"}
LEGACY_ENV = {
    availability.FEATURE_SCHEDULING: {V2_FLAG: "1", SCHEDULING_FLAG: "1"},
    availability.FEATURE_RECURRING: {
        V2_FLAG: "1", SCHEDULING_FLAG: "1", RECURRING_FLAG: "1",
    },
    availability.FEATURE_FORMS: {V2_FLAG: "1", FORMS_FLAG: "1"},
    availability.FEATURE_LIVE: {V2_FLAG: "1", LIVE_FLAG: "1"},
}


@pytest.fixture(autouse=True)
def _unconfigured(monkeypatch):
    monkeypatch.delenv(membership.PRO_MONTHLY_SKU_ENV, raising=False)
    monkeypatch.delenv(membership.PRO_YEARLY_SKU_ENV, raising=False)


def _status_stub(status):
    async def resolve(_guild_id, *, client=None):
        return status
    return resolve


def test_legacy_flag_is_an_operator_override(monkeypatch):
    # Legacy wins even over a configured, uncovered guild.
    monkeypatch.setenv(membership.PRO_MONTHLY_SKU_ENV, "123456")
    monkeypatch.setattr(
        membership, "guild_pro_status", _status_stub(membership.STATUS_FREE))
    for feature in FEATURES:
        env = LEGACY_ENV[feature]
        assert availability.legacy_enabled(feature, env)
        assert asyncio.run(availability.resolve_state(feature, 4242, environ=env)) \
            == availability.STATE_AVAILABLE
        assert availability.service_refusal(feature, 4242, environ=env) is None


def test_unconfigured_with_storage_ready_is_available():
    for feature in FEATURES:
        assert not availability.legacy_enabled(feature, STORAGE_ONLY)
        assert asyncio.run(availability.resolve_state(
            feature, 4242, environ=STORAGE_ONLY)) == availability.STATE_AVAILABLE
        assert asyncio.run(availability.feature_denial(
            feature, 4242, environ=STORAGE_ONLY)) is None
        assert availability.service_refusal(feature, 4242, environ=STORAGE_ONLY) is None
        assert availability.service_may_serve(feature, 4242, environ=STORAGE_ONLY)


def test_storage_off_is_deployment_off_with_legacy_wording():
    for feature in FEATURES:
        assert asyncio.run(availability.resolve_state(feature, 4242, environ={})) \
            == availability.STATE_DEPLOYMENT_OFF
        assert asyncio.run(availability.feature_denial(feature, 4242, environ={})) \
            == availability.LEGACY_OFF_MESSAGES[feature]
        assert availability.service_refusal(feature, 4242, environ={}) \
            == availability.REFUSAL_OFF
        assert not availability.service_may_serve(feature, 4242, environ={})
        assert not availability.sync_button_enabled(feature, environ={})
        assert not availability.worker_may_claim(feature, environ={})


def test_deployment_level_gates_open_with_storage_alone():
    # Buttons stay clickable (the callback explains any Pro lock) and
    # workers may run whenever the deployment can serve the feature.
    for feature in FEATURES:
        assert availability.sync_button_enabled(feature, environ=STORAGE_ONLY)
        assert availability.worker_may_claim(feature, environ=STORAGE_ONLY)


def test_configured_free_guild_is_pro_locked(monkeypatch):
    monkeypatch.setenv(membership.PRO_MONTHLY_SKU_ENV, "123456")
    monkeypatch.setattr(
        membership, "guild_pro_status", _status_stub(membership.STATUS_FREE))

    async def fake_context(_guild_id, *, refresh=False):
        return SimpleNamespace(
            unsafe_to_act=False, get_id=lambda _key, default=0: default)

    monkeypatch.setattr(guild_context, "get_guild_context", fake_context)
    for feature in FEATURES:
        assert asyncio.run(availability.resolve_state(
            feature, 4242, environ=STORAGE_ONLY)) == availability.STATE_PRO_LOCKED
        assert not asyncio.run(availability.feature_available(
            feature, 4242, environ=STORAGE_ONLY))
        denial = asyncio.run(availability.feature_denial(
            feature, 4242, environ=STORAGE_ONLY))
        assert denial == availability.pro_locked_text(feature)
        assert "Dank Shield Pro" in denial
        assert availability.LABELS[feature] in denial
        # The button stays clickable so the lock is explained, not silent.
        assert availability.sync_button_enabled(feature, environ=STORAGE_ONLY)


def test_configured_pro_guild_is_available(monkeypatch):
    monkeypatch.setenv(membership.PRO_MONTHLY_SKU_ENV, "123456")
    monkeypatch.setattr(
        membership, "guild_pro_status", _status_stub(membership.STATUS_PRO))
    for feature in FEATURES:
        assert asyncio.run(availability.resolve_state(
            feature, 4242, environ=STORAGE_ONLY)) == availability.STATE_AVAILABLE
        assert asyncio.run(availability.feature_denial(
            feature, 4242, environ=STORAGE_ONLY)) is None


def test_service_layer_uses_link_store_once_configured(monkeypatch):
    monkeypatch.setenv(membership.PRO_MONTHLY_SKU_ENV, "123456")
    for feature in FEATURES:
        monkeypatch.setattr(membership, "owner_of_guild_sync", lambda _gid: 777)
        assert availability.service_refusal(feature, 4242, environ=STORAGE_ONLY) is None
        monkeypatch.setattr(membership, "owner_of_guild_sync", lambda _gid: None)
        assert availability.service_refusal(feature, 4242, environ=STORAGE_ONLY) \
            == availability.REFUSAL_PRO

        def broken(_gid):
            raise RuntimeError("link store unavailable")

        monkeypatch.setattr(membership, "owner_of_guild_sync", broken)
        # Fail closed: an unreadable link store never unlocks a Pro feature.
        assert availability.service_refusal(feature, 4242, environ=STORAGE_ONLY) \
            == availability.REFUSAL_PRO
