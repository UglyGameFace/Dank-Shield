from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

from stoney_verify import invite_policy_engine
from stoney_verify import spam_guard
from stoney_verify.commands_ext import public_spam_cleanup_hardening as cleanup


class _Partial:
    def __init__(self, message_id: int, *, fail: bool = False) -> None:
        self.id = int(message_id)
        self.fail = bool(fail)
        self.deleted = 0

    async def delete(self) -> None:
        if self.fail:
            raise RuntimeError("delete failed")
        self.deleted += 1


class _TextChannel:
    def __init__(
        self,
        channel_id: int,
        *,
        bulk_fail: bool = False,
        partial_fail: bool = False,
    ) -> None:
        self.id = int(channel_id)
        self.bulk_fail = bool(bulk_fail)
        self.partial_fail = bool(partial_fail)
        self.partials: dict[int, _Partial] = {}
        self.bulk_calls: list[tuple[list[int], str | None]] = []

    def get_partial_message(self, message_id: int) -> _Partial:
        mid = int(message_id)
        return self.partials.setdefault(
            mid,
            _Partial(mid, fail=self.partial_fail),
        )

    async def delete_messages(self, partials, *, reason=None) -> None:
        ids = [int(item.id) for item in partials]
        self.bulk_calls.append((ids, reason))
        if self.bulk_fail:
            raise RuntimeError("bulk delete failed")
        for item in partials:
            item.deleted += 1


class _Guild:
    def __init__(self, channel: _TextChannel) -> None:
        self.id = 77
        self._channel = channel

    def get_channel(self, channel_id: int):
        return self._channel if int(channel_id) == self._channel.id else None

    async def fetch_channel(self, channel_id: int):
        return self.get_channel(channel_id)


def _refs(*message_ids: int) -> list[dict[str, int]]:
    return [
        {"channel_id": 10, "message_id": int(message_id)}
        for message_id in message_ids
    ]


def test_single_message_cleanup_uses_message_delete_without_reason(
    monkeypatch,
) -> None:
    channel = _TextChannel(10)
    guild = _Guild(channel)
    monkeypatch.setattr(spam_guard.discord, "TextChannel", _TextChannel)

    deleted = asyncio.run(
        spam_guard._delete_recent_messages(  # noqa: SLF001
            guild=guild,
            refs=_refs(101),
            reason="Spam guard cleanup",
        )
    )

    assert deleted == 1
    assert channel.partials[101].deleted == 1
    assert channel.bulk_calls == []


def test_bulk_failure_falls_back_to_supported_individual_delete(
    monkeypatch,
) -> None:
    channel = _TextChannel(10, bulk_fail=True)
    guild = _Guild(channel)
    monkeypatch.setattr(spam_guard.discord, "TextChannel", _TextChannel)

    deleted = asyncio.run(
        spam_guard._delete_recent_messages(  # noqa: SLF001
            guild=guild,
            refs=_refs(201, 202),
            reason="Spam guard cleanup",
        )
    )

    assert deleted == 2
    assert channel.bulk_calls == [([201, 202], "Spam guard cleanup")]
    assert channel.partials[201].deleted == 1
    assert channel.partials[202].deleted == 1


def test_cleanup_failures_are_visible_in_debug_output(monkeypatch) -> None:
    channel = _TextChannel(10, partial_fail=True)
    guild = _Guild(channel)
    logs: list[str] = []
    monkeypatch.setattr(spam_guard.discord, "TextChannel", _TextChannel)
    monkeypatch.setattr(spam_guard, "_debug", logs.append)

    deleted = asyncio.run(
        spam_guard._delete_recent_messages(  # noqa: SLF001
            guild=guild,
            refs=_refs(301),
            reason="Spam guard cleanup",
        )
    )

    assert deleted == 0
    assert any("cleanup channel delete failed" in line for line in logs)
    assert any("cleanup fallback delete failed" in line for line in logs)


def test_public_cleanup_sweep_uses_supported_message_delete(monkeypatch) -> None:
    calls: list[str] = []

    class Message:
        id = 401
        guild = SimpleNamespace(id=77)
        channel = object()

        async def delete(self) -> None:
            calls.append("delete")

    monkeypatch.setattr(
        cleanup,
        "_channel_manageable",
        lambda _channel, _guild: True,
    )
    monkeypatch.setattr(
        invite_policy_engine,
        "extract_invite_codes_from_message",
        lambda _message: [],
    )

    assert (
        asyncio.run(cleanup._delete_message_object(Message()))  # noqa: SLF001
        is True
    )
    assert calls == ["delete"]


def test_message_delete_paths_do_not_restore_unsupported_reason_keyword() -> None:
    recent_source = inspect.getsource(
        spam_guard._delete_recent_messages  # noqa: SLF001
    )
    sweep_source = inspect.getsource(
        cleanup._delete_message_object  # noqa: SLF001
    )

    assert "partials[0].delete(reason=" not in recent_source
    assert "get_partial_message(mid).delete(reason=" not in recent_source
    assert "message.delete(reason=" not in sweep_source
