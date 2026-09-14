from __future__ import annotations

from types import SimpleNamespace

import pytest

from stoney_verify import anti_nuke_audit_compat_runtime as compat
from stoney_verify import anti_nuke_self_action_runtime as runtime
from stoney_verify import anti_nuke_zero_damage_runtime as hardening


class FakeBot:
    def __init__(self) -> None:
        self.user = SimpleNamespace(id=55)
        self.guilds = []

    def get_channel(self, _channel_id: int):
        return None


class FakeRoute:
    def __init__(self, method: str, path: str) -> None:
        self.method = method
        self.path = path
        self.url = f"https://discord.com/api/v10{path}"


@pytest.fixture
def composed_classifier():
    original_request_spec = runtime._request_spec  # noqa: SLF001
    original_actions = runtime._PROTECTED_ACTIONS  # noqa: SLF001
    original_unmatched = runtime._unmatched_self_action  # noqa: SLF001

    hard_had_flag = hasattr(runtime, hardening._SELF_FLAG)  # noqa: SLF001
    hard_old_flag = getattr(runtime, hardening._SELF_FLAG, None)  # noqa: SLF001
    compat_had_flag = hasattr(runtime, compat._ROUTE_FLAG)  # noqa: SLF001
    compat_old_flag = getattr(runtime, compat._ROUTE_FLAG, None)  # noqa: SLF001

    if hard_had_flag:
        delattr(runtime, hardening._SELF_FLAG)  # noqa: SLF001
    if compat_had_flag:
        delattr(runtime, compat._ROUTE_FLAG)  # noqa: SLF001

    try:
        assert hardening._patch_self_action() is True  # noqa: SLF001
        assert compat._patch_self_action_route() is True  # noqa: SLF001
        yield
    finally:
        runtime._request_spec = original_request_spec  # noqa: SLF001
        runtime._PROTECTED_ACTIONS = original_actions  # noqa: SLF001
        runtime._unmatched_self_action = original_unmatched  # noqa: SLF001
        runtime._PENDING.clear()  # noqa: SLF001

        if hard_had_flag:
            setattr(runtime, hardening._SELF_FLAG, hard_old_flag)  # noqa: SLF001
        elif hasattr(runtime, hardening._SELF_FLAG):  # noqa: SLF001
            delattr(runtime, hardening._SELF_FLAG)  # noqa: SLF001

        if compat_had_flag:
            setattr(runtime, compat._ROUTE_FLAG, compat_old_flag)  # noqa: SLF001
        elif hasattr(runtime, compat._ROUTE_FLAG):  # noqa: SLF001
            delattr(runtime, compat._ROUTE_FLAG)  # noqa: SLF001


def test_member_patch_roles_maps_to_member_role_update(composed_classifier) -> None:
    spec = runtime._request_spec(  # noqa: SLF001
        FakeBot(),
        FakeRoute("PATCH", "/guilds/7/members/88"),
        {"json": {"roles": [1, 2, 3]}},
    )
    assert spec is not None
    assert spec.actions == frozenset({"member_role_update"})
    assert spec.guild_id == 7
    assert spec.target_key == "id:88"


def test_basic_verify_style_authorization_consumes_role_audit(composed_classifier) -> None:
    runtime._PENDING.clear()  # noqa: SLF001
    guild = SimpleNamespace(id=7)
    spec = runtime._request_spec(  # noqa: SLF001
        FakeBot(),
        FakeRoute("PATCH", "/guilds/7/members/88"),
        {"json": {"roles": [1, 2, 3]}},
    )
    assert spec is not None
    _nonce, reason = runtime._authorize(  # noqa: SLF001
        spec,
        "Dank Shield basic button verification",
    )
    event = SimpleNamespace(
        target=SimpleNamespace(id=88),
        reason=reason,
    )
    assert runtime._consume(guild, event, "member_role_update") is True  # noqa: SLF001
    assert runtime._PENDING == {}  # noqa: SLF001


def test_member_patch_voice_move_maps_to_member_move_without_target_scope(composed_classifier) -> None:
    spec = runtime._request_spec(  # noqa: SLF001
        FakeBot(),
        FakeRoute("PATCH", "/guilds/7/members/88"),
        {"json": {"channel_id": 123}},
    )
    assert spec is not None
    assert spec.actions == frozenset({"member_move"})
    assert spec.guild_id == 7
    assert spec.target_key == ""


def test_member_patch_voice_disconnect_maps_to_member_disconnect_without_target_scope(composed_classifier) -> None:
    spec = runtime._request_spec(  # noqa: SLF001
        FakeBot(),
        FakeRoute("PATCH", "/guilds/7/members/88"),
        {"json": {"channel_id": None}},
    )
    assert spec is not None
    assert spec.actions == frozenset({"member_disconnect"})
    assert spec.guild_id == 7
    assert spec.target_key == ""


def test_voice_move_authorization_consumes_targetless_audit(composed_classifier) -> None:
    runtime._PENDING.clear()  # noqa: SLF001
    guild = SimpleNamespace(id=7)
    spec = runtime._request_spec(  # noqa: SLF001
        FakeBot(),
        FakeRoute("PATCH", "/guilds/7/members/88"),
        {"json": {"channel_id": 123}},
    )
    assert spec is not None
    _nonce, reason = runtime._authorize(spec, "VC session move")  # noqa: SLF001
    event = SimpleNamespace(target=None, reason=reason)
    assert runtime._consume(guild, event, "member_move") is True  # noqa: SLF001
    assert runtime._PENDING == {}  # noqa: SLF001


def test_member_patch_other_fields_stays_member_update(composed_classifier) -> None:
    spec = runtime._request_spec(  # noqa: SLF001
        FakeBot(),
        FakeRoute("PATCH", "/guilds/7/members/88"),
        {"json": {"nick": "test"}},
    )
    assert spec is not None
    assert spec.actions == frozenset({"member_update"})
    assert spec.guild_id == 7
    assert spec.target_key == "id:88"
