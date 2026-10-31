"""OneBump-style embed collapse: exact bot+channel, no content/delete mutation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from stoney_verify import invite_trusted_advertisers as trust
from stoney_verify import trusted_advert_embeds as embeds
from stoney_verify.commands_ext import trusted_advert_ui as ui


BOT = 888888888888888888
GUILD = 111111111111111111
CHANNEL = 222222222222222222


def _config(*, collapse: bool, cleanup: bool = True, hours: int = 48):
    return {
        trust.CONFIG_KEY: [{
            "bot_id": BOT, "channel_id": CHANNEL,
            "suppress_embeds": collapse,
            "cleanup_enabled": cleanup,
            "retention_hours": hours,
        }]
    }


class FakeChannel:
    id = CHANNEL
    def __init__(self, posts=None):
        self.posts = list(posts or [])
    def permissions_for(self, member):
        return SimpleNamespace(view_channel=True, read_message_history=True,
                               manage_messages=True)
    def history(self, *, limit):
        async def read():
            for msg in self.posts[:limit]:
                yield msg
        return read()


def _message(*, bot_id=BOT, channel=None, human=False, webhook=None,
             with_embed=True, suppressed=False):
    channel = channel or FakeChannel()
    guild = SimpleNamespace(
        id=GUILD,
        me=SimpleNamespace(id=123456789012345678, bot=True),
    )
    return SimpleNamespace(
        guild=guild, channel=channel,
        author=SimpleNamespace(id=bot_id, bot=not human),
        id=777777777777777777,
        webhook_id=webhook, application_id=None,
        interaction=None, interaction_metadata=None,
        embeds=[SimpleNamespace(title="Partner advert")] if with_embed else [],
        flags=SimpleNamespace(suppress_embeds=suppressed),
        content="https://discord.gg/somewhere",
        edit=AsyncMock(),
        delete=AsyncMock(),
    )


def test_suppression_defaults_off_for_existing_saved_pairs():
    old = {trust.CONFIG_KEY: [{
        "bot_id": BOT, "channel_id": CHANNEL,
        "cleanup_enabled": True, "retention_hours": 48,
    }]}
    rules = trust.clean_rules(old)
    assert rules[0]["suppress_embeds"] is False
    assert rules[0]["cleanup_enabled"] is True
    assert rules[0]["retention_hours"] == 48
    assert embeds.needs_collapse(_message(), old) is False


def test_exact_trust_blocks_members_webhooks_other_bots_and_channels():
    cfg = _config(collapse=True)
    assert embeds.needs_collapse(_message(), cfg)
    assert not embeds.needs_collapse(_message(bot_id=123456789012345679), cfg)
    assert not embeds.needs_collapse(_message(human=True), cfg)
    assert not embeds.needs_collapse(_message(webhook=BOT), cfg)
    other_channel = FakeChannel()
    other_channel.id = 333333333333333333
    assert not embeds.needs_collapse(_message(channel=other_channel), cfg)
    assert not embeds.needs_collapse(_message(with_embed=False), cfg)
    assert not embeds.needs_collapse(_message(suppressed=True), cfg)


def test_preview_edit_only_sets_suppress_flag(monkeypatch):
    monkeypatch.setattr(embeds.discord, "TextChannel", FakeChannel)
    monkeypatch.setattr(embeds, "_RECENT_WRITES", embeds.deque())
    msg = _message()
    assert asyncio.run(embeds.suppress_one(msg, _config(collapse=True))) is True
    msg.edit.assert_awaited_once_with(suppress=True)
    msg.delete.assert_not_awaited()


def test_collapse_fails_closed_without_manage_messages(monkeypatch):
    monkeypatch.setattr(embeds.discord, "TextChannel", FakeChannel)
    msg = _message()
    msg.channel.permissions_for = lambda _me: SimpleNamespace(view_channel=True,
                                                               manage_messages=False)
    assert asyncio.run(embeds.suppress_one(msg, _config(collapse=True))) is False
    msg.edit.assert_not_awaited()


def test_existing_cleanup_and_retention_preserved_when_collapsing(monkeypatch):
    from stoney_verify import guild_config
    from stoney_verify import invite_policy_engine
    async def get(gid, *, refresh=False):
        assert gid == GUILD and refresh
        return {"source": "guild_config", **_config(collapse=False)}
    cas = AsyncMock(return_value=(True, {}))
    monkeypatch.setattr(guild_config, "get_guild_config", get)
    monkeypatch.setattr(guild_config, "compare_and_swap_guild_config_key", cas)
    monkeypatch.setattr(invite_policy_engine, "invalidate_invite_policy", lambda gid: None)
    result = asyncio.run(trust.save_rule(
        GUILD, bot_id=BOT, channel_id=CHANNEL,
        cleanup_enabled=True, retention_hours=48, suppress_embeds=True,
    ))
    assert result[0]["suppress_embeds"] is True
    assert result[0]["cleanup_enabled"] is True
    assert result[0]["retention_hours"] == 48
    assert cas.await_args.kwargs["value"][0]["suppress_embeds"] is True


def test_updating_cleanup_keeps_previous_collapse_preference(monkeypatch):
    from stoney_verify import guild_config
    from stoney_verify import invite_policy_engine
    async def get(gid, *, refresh=False):
        return {"source": "guild_config", **_config(collapse=True)}
    monkeypatch.setattr(guild_config, "get_guild_config", get)
    monkeypatch.setattr(guild_config, "compare_and_swap_guild_config_key",
                        AsyncMock(return_value=(True, {})))
    monkeypatch.setattr(invite_policy_engine, "invalidate_invite_policy", lambda gid: None)
    result = asyncio.run(trust.save_rule(
        GUILD, bot_id=BOT, channel_id=CHANNEL,
        cleanup_enabled=False, retention_hours=48,
    ))
    assert result[0]["suppress_embeds"] is True
    assert result[0]["cleanup_enabled"] is False


def test_manual_recent_scans_at_most_15_and_changes_at_most_three(monkeypatch):
    monkeypatch.setattr(embeds.discord, "TextChannel", FakeChannel)
    monkeypatch.setattr(embeds, "_MANUAL_COOLDOWN", {})
    posts = [_message() for _ in range(10)]
    channel = FakeChannel(posts=posts)
    for index, post in enumerate(posts):
        post.channel = channel
        post.id += index
    guild = SimpleNamespace(
        id=GUILD, me=SimpleNamespace(id=123456789012345678, bot=True),
    )
    for post in posts:
        post.guild = guild
    from stoney_verify import invite_policy_engine
    async def fetch(_guild, *, refresh=False):
        assert refresh
        return _config(collapse=False), {}
    monkeypatch.setattr(invite_policy_engine, "load_invite_policy", fetch)
    result = asyncio.run(embeds.collapse_recent_trusted_adverts(guild, channel, BOT))
    assert result["collapsed"] == 3
    assert result["scanned"] == 3
    assert sum(p.edit.await_count for p in posts) == 3
    assert all(p.delete.await_count == 0 for p in posts)
    import pytest
    with pytest.raises(ValueError, match="one minute"):
        asyncio.run(embeds.collapse_recent_trusted_adverts(guild, channel, BOT))


def test_owner_panel_has_single_reused_collapse_control():
    actions = [item.label for item in ui.TrustedAdsView(
        5, SimpleNamespace(), CHANNEL,
        rules=trust.clean_rules(_config(collapse=True)), section="retention",
    ).children if hasattr(item, "label")]
    assert "Collapse Previews" in actions
    assert "Enable Cleanup" in actions
    assert "Use This Channel" in actions
    assert len(actions) <= 25
    modal = ui.TrustedPreviewModal(5, channel_id=CHANNEL, bot_id=BOT)
    assert modal.bot_id.default == str(BOT)
    assert modal.action.default == "ON+RECENT"


def test_listener_handles_new_posts_and_late_embeds():
    from pathlib import Path
    worker = (Path(__file__).resolve().parents[1] /
              "stoney_verify/trusted_advert_cleanup.py").read_text()
    assert 'bot.add_listener(on_message, "on_message")' in worker
    assert 'bot.add_listener(on_message_edit, "on_message_edit")' in worker
    assert "suppress_new_trusted_advert(after)" in worker
