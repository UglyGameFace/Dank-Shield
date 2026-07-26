from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from stoney_verify import modlog


class FakeRole:
    def __init__(self, role_id: int, name: str) -> None:
        self.id = role_id
        self.name = name
        self.mention = f"<@&{role_id}>"
        self.position = role_id

    def is_default(self) -> bool:
        return False


class FakeAvatar:
    url = "https://example.test/avatar.png"


class FakeMember:
    def __init__(
        self,
        *,
        user_id: int,
        roles: list[FakeRole],
        joined_at: datetime,
        nick: str | None = None,
    ) -> None:
        self.id = user_id
        self.roles = roles
        self.joined_at = joined_at
        self.nick = nick
        self.bot = False
        self.mention = f"<@{user_id}>"
        self.timed_out_until = None
        self.display_avatar = FakeAvatar()

    def __str__(self) -> str:
        return f"member-{self.id}"


def test_expected_recent_unverified_assignment_is_suppressed(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    guild = SimpleNamespace(id=777)
    unverified = FakeRole(100, "Unverified")
    before = FakeMember(user_id=101, roles=[], joined_at=now)
    after = FakeMember(user_id=101, roles=[unverified], joined_at=now)
    sent: list[dict] = []

    async def fake_config(_guild_id):
        return {"unverified_role_id": 100}

    async def fake_post(*_args, **kwargs):
        sent.append(dict(kwargs))
        return object()

    monkeypatch.setattr(modlog, "get_guild_config", fake_config)
    monkeypatch.setattr(modlog, "_post_modlog", fake_post)

    result = asyncio.run(
        modlog.maybe_log_member_update_diff(guild, before, after)
    )

    assert result is False
    assert sent == []


def test_meaningful_member_update_is_compact_and_semantically_deduped(
    monkeypatch,
) -> None:
    now = datetime.now(timezone.utc)
    guild = SimpleNamespace(id=778)
    before = FakeMember(
        user_id=202,
        roles=[],
        joined_at=now - timedelta(days=2),
    )
    after = FakeMember(
        user_id=202,
        roles=[FakeRole(999, "Moderator")],
        joined_at=now - timedelta(days=2),
    )
    sent: list[dict] = []

    async def fake_config(_guild_id):
        return {"unverified_role_id": 100}

    async def fake_audit(*_args, **_kwargs):
        return None

    async def fake_post(_guild, embed, **kwargs):
        sent.append({"embed": embed, **kwargs})
        return object()

    monkeypatch.setattr(modlog, "get_guild_config", fake_config)
    monkeypatch.setattr(modlog, "_audit_find_best_member_update_match", fake_audit)
    monkeypatch.setattr(modlog, "_post_modlog", fake_post)

    result = asyncio.run(
        modlog.maybe_log_member_update_diff(guild, before, after)
    )

    assert result is True
    assert len(sent) == 1
    field_names = [field.name for field in sent[0]["embed"].fields]
    assert field_names == ["User", "Role Changes", "By"]
    assert "Join Intelligence" not in field_names
    assert "Evidence & Source" not in field_names
    assert sent[0]["event_key"].startswith("member_update:202:")
    assert sent[0]["dedupe_window_seconds"] == 20
