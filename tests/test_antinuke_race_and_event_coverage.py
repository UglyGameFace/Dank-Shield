from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify.globals import bot


class FakeAuditEntry:
    def __init__(self, entry_id: int, actor=None, target_id: int | None = None) -> None:
        self.id = entry_id
        self.user = actor or SimpleNamespace(id=444, roles=[], mention="<@444>")
        self.target = SimpleNamespace(id=target_id) if target_id is not None else None
        self.created_at = datetime.now(timezone.utc)


class FakeAuditGuild:
    def __init__(self, entries) -> None:
        self.id = 987
        self.entries = list(entries)

    async def audit_logs(self, *, limit, action):
        _ = limit, action
        await asyncio.sleep(0)
        for entry in self.entries:
            yield entry


def _reset_runtime_state() -> None:
    anti_nuke._ACTION_WINDOWS.clear()
    anti_nuke._TRIGGER_COOLDOWNS.clear()
    anti_nuke._SEEN_AUDIT_ENTRY_IDS.clear()
    anti_nuke._AUDIT_CLAIM_LOCKS.clear()
    anti_nuke._CONTAINMENT_LOCKS.clear()


def test_targetless_audit_claim_is_atomic_under_concurrency(monkeypatch) -> None:
    _reset_runtime_state()
    entry = FakeAuditEntry(12345)
    guild = FakeAuditGuild([entry])
    monkeypatch.setattr(anti_nuke, "_audit_action", lambda _name: object())

    async def run():
        return await asyncio.gather(
            anti_nuke._claim_recent_audit_entry(
                guild,
                "webhook_create",
                target_id=None,
                retries=1,
            ),
            anti_nuke._claim_recent_audit_entry(
                guild,
                "webhook_create",
                target_id=None,
                retries=1,
            ),
        )

    claimed = asyncio.run(run())

    assert sum(item is entry for item in claimed) == 1
    assert sum(item is None for item in claimed) == 1
    _reset_runtime_state()


def test_webhook_handler_covers_create_update_and_delete(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    processed: list[str] = []
    guild = SimpleNamespace(id=654)
    channel = SimpleNamespace(id=777, name="alerts", guild=guild)
    entry = FakeAuditEntry(333)

    async def fake_settings(_guild_id: int):
        return anti_nuke.normalize_antinuke_settings({"antinuke_enabled": True})

    async def fake_claim_any(_guild, action_names, *, retries=3):
        _ = retries
        calls.append(tuple(action_names))
        return "webhook_delete", entry

    async def fake_process(_guild, **kwargs):
        processed.append(kwargs["action_key"])
        return True

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(anti_nuke, "_claim_recent_audit_entry_any", fake_claim_any)
    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", fake_process)

    asyncio.run(anti_nuke._handle_webhook_change(channel))

    assert calls == [("webhook_create", "webhook_update", "webhook_delete")]
    assert processed == ["webhook_delete"]


def test_new_high_risk_listeners_are_registered() -> None:
    expected = {
        anti_nuke.antinuke_on_guild_role_create,
        anti_nuke.antinuke_on_member_join,
    }
    registered = set()
    for listeners in (getattr(bot, "extra_events", {}) or {}).values():
        registered.update(listeners or [])

    assert expected.issubset(registered)


def test_non_bot_member_join_does_not_query_audit_log(monkeypatch) -> None:
    calls: list[int] = []
    member = SimpleNamespace(id=111, bot=False, guild=SimpleNamespace(id=222))

    async def fake_settings(guild_id: int):
        calls.append(guild_id)
        return anti_nuke.normalize_antinuke_settings({"antinuke_enabled": True})

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)

    asyncio.run(anti_nuke._handle_bot_add(member))

    assert calls == []


def test_dangerous_role_create_is_immediate_not_thresholded(monkeypatch) -> None:
    incidents: list[dict] = []
    role = SimpleNamespace(
        id=9876,
        name="Instant Admin",
        managed=False,
        permissions=SimpleNamespace(
            administrator=True,
            manage_guild=False,
            manage_roles=False,
            manage_channels=False,
            ban_members=False,
            kick_members=False,
            manage_webhooks=False,
        ),
    )
    guild = SimpleNamespace(id=333, owner_id=999, me=None)
    role.guild = guild
    actor = SimpleNamespace(id=444, roles=[], mention="<@444>")
    entry = FakeAuditEntry(8888, actor=actor, target_id=role.id)

    async def fake_settings(_guild_id: int):
        return anti_nuke.normalize_antinuke_settings(
            {"antinuke_enabled": True, "antinuke_mode": "alert"}
        )

    async def fake_claim(*_args, **_kwargs):
        return entry

    async def fake_post(_guild, **kwargs):
        incidents.append(kwargs)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(anti_nuke, "_claim_recent_audit_entry", fake_claim)
    monkeypatch.setattr(anti_nuke, "_post_incident", fake_post)

    asyncio.run(anti_nuke._handle_dangerous_role_create(role))

    assert len(incidents) == 1
    assert incidents[0]["title"] == "🚨 AntiNuke Dangerous Role Created"


def test_untrusted_bot_add_is_immediate_not_thresholded(monkeypatch) -> None:
    incidents: list[dict] = []
    guild = SimpleNamespace(id=444, owner_id=999)
    member = SimpleNamespace(id=555, bot=True, guild=guild)
    actor = SimpleNamespace(id=666, roles=[], mention="<@666>")
    entry = FakeAuditEntry(9999, actor=actor, target_id=member.id)

    async def fake_settings(_guild_id: int):
        return anti_nuke.normalize_antinuke_settings(
            {"antinuke_enabled": True, "antinuke_mode": "alert"}
        )

    async def fake_claim(*_args, **_kwargs):
        return entry

    async def fake_post(_guild, **kwargs):
        incidents.append(kwargs)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(anti_nuke, "_claim_recent_audit_entry", fake_claim)
    monkeypatch.setattr(anti_nuke, "_post_incident", fake_post)

    asyncio.run(anti_nuke._handle_bot_add(member))

    assert len(incidents) == 1
    assert incidents[0]["title"] == "🚨 AntiNuke Untrusted Bot Added"
