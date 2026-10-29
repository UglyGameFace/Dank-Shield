from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
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


def _cfg(cleanup_on=False, *, hours=24):
    return {
        "automod_block_invites": True,
        trust.CONFIG_KEY: [
            {
                "bot_id": BOT, "channel_id": CHANNEL,
                "retention_hours": hours, "cleanup_enabled": cleanup_on,
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


@pytest.mark.parametrize("hours", [24, 25, 26, 36, 48, 72, 100, 168, 10000])
def test_custom_hours_survive_saved_config_and_set_exact_expiry(hours):
    posted = datetime.now(timezone.utc)
    msg = _message(created=posted)
    rule = trust.trusted_rule(msg, _cfg(True, hours=str(hours)))
    assert rule["retention_hours"] == hours
    assert rule["cleanup_enabled"] is True
    assert trust.expires_at(msg, rule) == posted + timedelta(hours=hours, minutes=10)


@pytest.mark.parametrize("value", [23, 0, -25, True, 25.5, "25.5", "", "hours", None, 10**20])
def test_invalid_hours_never_enable_or_shorten_cleanup(value):
    with pytest.raises(ValueError, match="at least 24"):
        trust.parse_retention_hours(value)
    rules = trust.clean_rules(_cfg(True, hours=value))
    assert rules[0]["bot_id"] == BOT
    assert rules[0]["cleanup_enabled"] is False
    assert trust.expires_at(_message(), {"retention_hours": value}) is None


def test_saving_custom_hours_round_trips_without_losing_preview_preference(monkeypatch):
    from stoney_verify import guild_config
    saved = {"source": "guild_config", **_cfg(True, hours=48)}
    saved[trust.CONFIG_KEY][0]["suppress_embeds"] = True
    monkeypatch.setattr(guild_config, "get_guild_config", AsyncMock(return_value=saved))
    cas = AsyncMock(return_value=(True, {}))
    monkeypatch.setattr(guild_config, "compare_and_swap_guild_config_key", cas)
    monkeypatch.setattr(engine, "invalidate_invite_policy", lambda _gid: None)
    result = asyncio.run(trust.save_rule(
        GUILD, bot_id=BOT, channel_id=CHANNEL,
        cleanup_enabled=True, retention_hours=25,
    ))
    stored = cas.await_args.kwargs["value"]
    assert result == trust.clean_rules({trust.CONFIG_KEY: stored})
    assert result[0]["retention_hours"] == 25
    assert result[0]["suppress_embeds"] is True


@pytest.mark.parametrize("hours", [25, 100, 168])
def test_cleanup_modal_accepts_custom_hours(monkeypatch, hours):
    modal = ui.ConfirmAdvertCleanupModal(1, channel_id=CHANNEL)
    modal.hours._value = f" {hours} "
    modal.bot_id._value = str(BOT)
    modal.consent._value = "DELETE"
    assert modal.hours.max_length >= len(str(hours))
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=GUILD),
        user=SimpleNamespace(id=1, guild_permissions=SimpleNamespace(manage_guild=True)),
        response=SimpleNamespace(defer=AsyncMock()),
    )
    monkeypatch.setattr(ui, "_resolve_bot", AsyncMock(return_value=SimpleNamespace(id=BOT)))
    monkeypatch.setattr(ui, "load_rules", AsyncMock(return_value=trust.clean_rules(_cfg())))
    save = AsyncMock()
    notice = AsyncMock()
    monkeypatch.setattr(ui, "save_rule", save)
    monkeypatch.setattr(ui, "_notice", notice)
    asyncio.run(modal.on_submit(interaction))
    save.assert_awaited_once_with(
        GUILD, bot_id=BOT, channel_id=CHANNEL,
        cleanup_enabled=True, retention_hours=hours,
    )
    assert f"{hours} hours + 10 minutes" in notice.await_args.args[1]


@pytest.mark.parametrize("hours,manage_guild", [("23", True), ("25.5", True), ("25", False)])
def test_cleanup_modal_rejects_invalid_hours_or_nonstaff_before_saving(monkeypatch, hours, manage_guild):
    modal = ui.ConfirmAdvertCleanupModal(1, channel_id=CHANNEL)
    modal.hours._value = hours
    modal.consent._value = "DELETE"
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=GUILD),
        user=SimpleNamespace(id=1, guild_permissions=SimpleNamespace(manage_guild=manage_guild)),
        response=SimpleNamespace(defer=AsyncMock()),
    )
    resolve = AsyncMock()
    save = AsyncMock()
    monkeypatch.setattr(ui, "_resolve_bot", resolve)
    monkeypatch.setattr(ui, "save_rule", save)
    monkeypatch.setattr(ui, "_notice", AsyncMock())
    asyncio.run(modal.on_submit(interaction))
    save.assert_not_awaited()
    resolve.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()


def test_custom_hours_are_used_when_tracking_advert(monkeypatch):
    posted = datetime.now(timezone.utc)
    message = _message(created=posted)
    monkeypatch.setattr(engine, "load_invite_policy", AsyncMock(return_value=(_cfg(True, hours=25), {})))
    database = Mock()
    monkeypatch.setattr(cleanup, "_db", lambda: database)
    assert asyncio.run(cleanup.track_advert(message)) is True
    payload = database.table.return_value.insert.call_args.args[0]
    assert payload["delete_after"] == (posted + timedelta(hours=25, minutes=10)).isoformat()
    assert payload["posted_at"] == posted.isoformat()


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
    finish.assert_awaited_once_with(
        record, "pending", error="retention_not_elapsed",
        due_at=posted + timedelta(hours=24, minutes=10),
    )


def test_longer_retention_reschedules_without_exhausting_failure_retries(monkeypatch):
    posted = datetime.now(timezone.utc) - timedelta(hours=26)
    record = {
        "guild_id": str(GUILD), "channel_id": str(CHANNEL),
        "message_id": "222222222222222222", "bot_id": str(BOT),
        "posted_at": posted.isoformat(), "attempts": 8,
    }
    guild = SimpleNamespace(id=GUILD, get_channel=Mock())
    bot = SimpleNamespace(get_guild=lambda _id: guild)
    monkeypatch.setattr(cleanup, "get_guild_config", AsyncMock(return_value={
        "source": "guild_config", **_cfg(True, hours=100),
    }))
    database = Mock()
    monkeypatch.setattr(cleanup, "_db", lambda: database)
    assert asyncio.run(cleanup.process_claim(bot, record)) == "not-due"
    update = database.table.return_value.update.call_args.args[0]
    assert update["state"] == "pending"
    assert update["attempts"] == 0
    assert update["delete_after"] == (posted + timedelta(hours=100, minutes=10)).isoformat()
    guild.get_channel.assert_not_called()


def test_actual_deletion_failures_still_have_a_bounded_retry_budget(monkeypatch):
    database = Mock()
    monkeypatch.setattr(cleanup, "_db", lambda: database)
    record = {"guild_id": str(GUILD), "message_id": "222222222222222222", "attempts": 8}
    asyncio.run(cleanup._finish(record, "pending", error="Forbidden"))
    update = database.table.return_value.update.call_args.args[0]
    assert update["state"] == "failed"
    assert "attempts" not in update


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


@pytest.mark.parametrize("age,expected", [
    (timedelta(hours=24, minutes=59), "not-due"),
    (timedelta(hours=25, minutes=9, seconds=59), "not-due"),
    (timedelta(hours=25, minutes=10), "deleted"),
])
def test_custom_retention_deletes_only_after_selected_hours_plus_grace(monkeypatch, age, expected):
    now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)
    monkeypatch.setattr(cleanup, "datetime", FrozenDateTime)
    posted = now - age
    advert = _message(created=posted, embeds=[SimpleNamespace(title="Public Partner Ad")])
    advert.delete = AsyncMock()

    class FakeChannel:
        id = CHANNEL
        def __init__(self):
            self.fetch_message = AsyncMock(return_value=advert)
        def permissions_for(self, member):
            return SimpleNamespace(
                view_channel=True, read_message_history=True,
                manage_messages=True,
            )

    channel = FakeChannel()
    advert.channel = channel
    guild = SimpleNamespace(
        id=GUILD,
        me=SimpleNamespace(id=222222222222222221, bot=True),
        get_channel=lambda cid: channel if cid == CHANNEL else None,
    )
    bot = SimpleNamespace(get_guild=lambda gid: guild if gid == GUILD else None)
    async def load(gid, *, refresh=False):
        assert gid == GUILD and refresh
        return {"source": "guild_config", **_cfg(True, hours=25)}
    done = AsyncMock()
    monkeypatch.setattr(cleanup, "get_guild_config", load)
    monkeypatch.setattr(cleanup, "_finish", done)
    monkeypatch.setattr(cleanup.discord, "TextChannel", FakeChannel)
    row = {
        "guild_id": str(GUILD), "channel_id": str(CHANNEL),
        "message_id": str(advert.id), "bot_id": str(BOT),
        "posted_at": posted.isoformat(), "attempts": 1,
    }
    outcome = asyncio.run(cleanup.process_claim(bot, row))
    assert outcome == expected
    if expected == "not-due":
        advert.delete.assert_not_awaited()
        channel.fetch_message.assert_not_awaited()
        done.assert_awaited_once_with(
            row, "pending", error="retention_not_elapsed",
            due_at=posted + timedelta(hours=25, minutes=10),
        )
        return
    advert.delete.assert_awaited_once_with(
        reason="Trusted advertising retention expired (server opt-in)"
    )
    done.assert_awaited_once_with(row, "done")
    channel.fetch_message.assert_awaited_once_with(advert.id)


def test_no_ad_history_scans_or_delete_all_calls_in_worker():
    source = (Path(__file__).resolve().parents[1] /
              "stoney_verify/trusted_advert_cleanup.py").read_text()
    assert ".history(" not in source
    assert ".purge(" not in source
    assert ".delete_messages(" not in source
    assert "asyncio.sleep(_POLL_SECONDS)" in source
    assert "for record in batch[:8]" in source


def test_onebump_setup_does_not_trust_by_name():
    source = Path(ui.__file__).read_text(encoding="utf-8")
    assert '"onebump" in' in source  # suggestion UI only
    assert "await _resolve_bot(guild, self.bot_id.value)" in source
    assert "not member.bot" in source
    assert "Matching a name is only a suggestion" in source
