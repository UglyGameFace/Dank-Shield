from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path

import discord
import pytest

from stoney_verify import invite_policy_engine as engine
from stoney_verify import invite_trusted_advertisers as trust
from stoney_verify import trusted_advert_cleanup as cleanup
from stoney_verify.commands_ext import public_protection_invite_ui as invite_ui
from stoney_verify.commands_ext import trusted_advert_ui as ui


BOT = 888888888888888888
OTHER_BOT = 777777777777777777
GUILD = 424242424242424242
CHANNEL = 333333333333333333
OTHER_CHANNEL = 555555555555555555


def _cfg(cleanup_on=False):
    return {
        "automod_block_invites": True,
        trust.CONFIG_KEY: [
            {
                "bot_id": BOT, "channel_id": CHANNEL,
                "retention_hours": 24, "cleanup_enabled": cleanup_on,
            }
        ],
    }


def _message(*, bot_id=BOT, channel_id=CHANNEL, bot=True, webhook=None,
             application=None, created=None, content="https://discord.gg/outsidelink",
             embeds=None, interaction=None):
    message = SimpleNamespace(
        id=222222222222222222,
        guild=SimpleNamespace(id=GUILD),
        channel=SimpleNamespace(id=channel_id),
        author=SimpleNamespace(id=bot_id, bot=bot, name="OneBump"),
        content=content,
        webhook_id=webhook,
        application_id=application,
        interaction_metadata=interaction,
        interaction=None,
        embeds=embeds or [],
        components=[],
        attachments=[],
        poll=None,
        created_at=created or datetime.now(timezone.utc) - timedelta(hours=26),
    )
    message.delete = AsyncMock()
    return message


def test_exact_bot_channel_and_webhook_cannot_be_impersonated():
    cfg = _cfg(True)
    assert trust.trusted_rule(_message(), cfg)
    for bad in (
        _message(bot=False),
        _message(bot_id=OTHER_BOT),
        _message(channel_id=OTHER_CHANNEL),
        _message(webhook=BOT),
        _message(application=OTHER_BOT),
        _message(interaction=SimpleNamespace(id=1)),
    ):
        assert trust.trusted_rule(bad, cfg) is None
    assert not trust.trusted_rule(_message(), {})
    assert trust.clean_rules({trust.CONFIG_KEY: [{}]}) == []


def test_24_hour_retention_has_safety_buffer_not_early_delete():
    now = datetime.now(timezone.utc)
    msg = _message(created=now)
    rule = trust.trusted_rule(msg, _cfg(True))
    expiry = trust.expires_at(msg, rule)
    assert expiry == now + timedelta(hours=24, minutes=10)
    assert trust.expires_at(msg, {"retention_hours": 1}) is None


def test_ad_shape_does_not_count_interaction_receipts_or_text_only():
    assert trust.is_advert_like(_message())
    assert not trust.is_advert_like(_message(content="A generic status", embeds=[]))
    assert not trust.is_advert_like(_message(interaction=SimpleNamespace(id=12)))
    assert trust.is_advert_like(_message(content="", embeds=[SimpleNamespace(title="Partner advert")]))


def test_invite_shield_preserves_ads_but_blocks_everyone_else(monkeypatch):
    async def policy(_guild, *, refresh=False):
        return _cfg(True), {
            "allow_server_invites": True,
            "invite_protected_poster_rule_enabled": True,
            "invite_hard_block_target_all_bots": True,
            "invite_hard_block_target_channel_ids": [str(CHANNEL)],
        }
    async def remote(_guild, _code):
        return "external", "outside"
    monkeypatch.setattr(engine, "load_invite_policy", policy)
    monkeypatch.setattr(engine, "_invite_code_belongs_to_guild", remote)
    async def run():
        out = []
        for msg in (
            _message(), _message(bot=False), _message(bot_id=OTHER_BOT),
            _message(channel_id=OTHER_CHANNEL), _message(webhook=BOT),
        ):
            decision = await engine.decide_invite_message(msg)
            out.append(decision)
        return out
    allowed, human, other_bot, other_channel, webhook = asyncio.run(run())
    assert allowed.action == "allow"
    assert allowed.rule_id == "trusted_advertiser_exact_bot_channel"
    assert allowed.trusted_advertiser == str(BOT)
    assert all(item.action == "delete" for item in (human, other_bot, other_channel, webhook))


def test_revoked_trust_returns_to_normal_invite_shield(monkeypatch):
    async def policy(_guild, *, refresh=False):
        return {"automod_block_invites": True}, {"allow_server_invites": True}
    monkeypatch.setattr(engine, "load_invite_policy", policy)
    async def remote(_guild, code):
        return "external", code
    monkeypatch.setattr(engine, "_invite_code_belongs_to_guild", remote)
    result = asyncio.run(engine.decide_invite_message(_message()))
    assert result.action == "delete"


def test_cleanup_tracker_ignores_nonapproved_posts_and_requires_optin(monkeypatch):
    async def load(_guild, *, refresh=False):
        return _cfg(False), {}
    monkeypatch.setattr(engine, "load_invite_policy", load)
    assert not asyncio.run(cleanup.track_advert(_message()))
    assert not asyncio.run(cleanup.track_advert(_message(bot=False)))


def test_process_claim_cancels_revoked_bot_without_discord_fetch(monkeypatch):
    record = {
        "guild_id": str(GUILD), "channel_id": str(CHANNEL),
        "message_id": "222222222222222222", "bot_id": str(BOT),
        "posted_at": (datetime.now(timezone.utc) - timedelta(hours=26)).isoformat(),
        "attempts": 1,
    }
    guild = SimpleNamespace(id=GUILD)
    bot = SimpleNamespace(get_guild=lambda _id: guild)
    async def load(_gid, *, refresh=False):
        return {"source": "guild_config", trust.CONFIG_KEY: []}
    finish = AsyncMock()
    monkeypatch.setattr(cleanup, "get_guild_config", load)
    monkeypatch.setattr(cleanup, "_finish", finish)
    state = asyncio.run(cleanup.process_claim(bot, record))
    assert state == "rule-revoked"
    finish.assert_awaited_once_with(record, "cancelled", error="trusted_rule_revoked")


def test_process_claim_never_deletes_before_post_plus_grace(monkeypatch):
    posted = datetime.now(timezone.utc) - timedelta(hours=23)
    record = {
        "guild_id": str(GUILD), "channel_id": str(CHANNEL),
        "message_id": "222222222222222222", "bot_id": str(BOT),
        "posted_at": posted.isoformat(), "attempts": 1,
    }
    guild = SimpleNamespace(id=GUILD)
    bot = SimpleNamespace(get_guild=lambda _id: guild)
    async def load(_gid, *, refresh=False):
        return {"source": "guild_config", **_cfg(True)}
    finish = AsyncMock()
    monkeypatch.setattr(cleanup, "get_guild_config", load)
    monkeypatch.setattr(cleanup, "_finish", finish)
    assert asyncio.run(cleanup.process_claim(bot, record)) == "not-due"
    finish.assert_awaited_once_with(record, "pending", error="retention_not_elapsed")


def test_main_ui_and_worker_wiring_use_existing_owners():
    root = Path(__file__).resolve().parents[1]
    app = (root / "stoney_verify/app.py").read_text(encoding="utf-8")
    policy = (root / "stoney_verify/invite_policy_engine.py").read_text(encoding="utf-8")
    shield = (root / "stoney_verify/commands_ext/public_protection_invite_ui.py").read_text(encoding="utf-8")
    assert "start_trusted_advert_worker" in app
    assert "trusted_rule(message, cfg)" in policy
    assert "label=\"Trusted Advertising Bots\"" in shield
    assert "Clean Existing Invites" in shield
    assert "DankGuildResourceBrowserView" in Path(ui.__file__).read_text(encoding="utf-8")
    assert len(ui.TrustedAdsView(1, SimpleNamespace(), CHANNEL).children) <= 25
    assert len(invite_ui.InviteShieldView(
        author_id=1, guild=None, origin_channel_id=CHANNEL, scope={},
    ).children) <= 25


def test_sql_security_defaults_and_atomic_claims():
    text = (Path(__file__).resolve().parents[1] /
            "supabase/migrations/20261026170500_trusted_advert_retention.sql").read_text()
    assert "state text not null default 'pending'" in text
    assert "delete_after >= posted_at + interval '24 hours'" in text
    assert "for update skip locked" in text
    assert "enable row level security" in text
    assert "revoke all on function public.dank_claim_due_trusted_adverts" in text
    assert "to service_role" in text
