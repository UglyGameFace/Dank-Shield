from __future__ import annotations

from types import SimpleNamespace

from stoney_verify import anti_nuke_audit_compat_runtime as runtime
from stoney_verify import anti_nuke_guardian_runtime as guardian
from stoney_verify import anti_nuke_self_action_runtime as self_action


class FakeBot:
    def __init__(self) -> None:
        self.guilds = []

    def get_channel(self, _channel_id: int):
        return None


class FakeRoute:
    def __init__(self, method: str, path: str) -> None:
        self.method = method
        self.path = path
        self.url = f"https://discord.com/api/v10{path}"


def test_raw_voice_status_audit_values_are_named_and_guarded() -> None:
    old_guardian_name = guardian._action_name  # noqa: SLF001
    old_self_name = self_action._action_name  # noqa: SLF001
    old_actions = dict(guardian._ACTIONS)  # noqa: SLF001
    old_weights = dict(guardian._PANIC_WEIGHTS)  # noqa: SLF001
    old_panic_actions = guardian._PANIC_ACTIONS  # noqa: SLF001
    old_protected = self_action._PROTECTED_ACTIONS  # noqa: SLF001
    had_flag = hasattr(guardian, runtime._ACTION_FLAG)  # noqa: SLF001
    old_flag = getattr(guardian, runtime._ACTION_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(guardian, runtime._ACTION_FLAG)  # noqa: SLF001

    try:
        assert runtime._patch_action_names() is True  # noqa: SLF001
        update = SimpleNamespace(action=192)
        delete = SimpleNamespace(action=193)
        assert guardian._action_name(update) == "voice_channel_status_update"  # noqa: SLF001
        assert guardian._action_name(delete) == "voice_channel_status_delete"  # noqa: SLF001
        assert "voice_channel_status_update" in guardian._ACTIONS  # noqa: SLF001
        assert "voice_channel_status_delete" in self_action._PROTECTED_ACTIONS  # noqa: SLF001
    finally:
        guardian._action_name = old_guardian_name  # noqa: SLF001
        self_action._action_name = old_self_name  # noqa: SLF001
        guardian._ACTIONS.clear()  # noqa: SLF001
        guardian._ACTIONS.update(old_actions)  # noqa: SLF001
        guardian._PANIC_WEIGHTS.clear()  # noqa: SLF001
        guardian._PANIC_WEIGHTS.update(old_weights)  # noqa: SLF001
        guardian._PANIC_ACTIONS = old_panic_actions  # noqa: SLF001
        self_action._PROTECTED_ACTIONS = old_protected  # noqa: SLF001
        if had_flag:
            setattr(guardian, runtime._ACTION_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(guardian, runtime._ACTION_FLAG):  # noqa: SLF001
            delattr(guardian, runtime._ACTION_FLAG)  # noqa: SLF001


def test_voice_status_local_request_gets_self_action_spec() -> None:
    original = self_action._request_spec  # noqa: SLF001
    had_flag = hasattr(self_action, runtime._ROUTE_FLAG)  # noqa: SLF001
    old_flag = getattr(self_action, runtime._ROUTE_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(self_action, runtime._ROUTE_FLAG)  # noqa: SLF001

    try:
        assert runtime._patch_self_action_route() is True  # noqa: SLF001
        updated = self_action._request_spec(  # noqa: SLF001
            FakeBot(),
            FakeRoute("PUT", "/channels/123/voice-status"),
            {"json": {"status": "Raid lobby"}},
        )
        assert updated is not None
        assert updated.actions == frozenset({"voice_channel_status_update"})
        assert updated.target_key == "id:123"

        deleted = self_action._request_spec(  # noqa: SLF001
            FakeBot(),
            FakeRoute("PUT", "/channels/123/voice-status"),
            {"json": {"status": None}},
        )
        assert deleted is not None
        assert deleted.actions == frozenset({"voice_channel_status_delete"})
    finally:
        self_action._request_spec = original  # noqa: SLF001
        if had_flag:
            setattr(self_action, runtime._ROUTE_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(self_action, runtime._ROUTE_FLAG):  # noqa: SLF001
            delattr(self_action, runtime._ROUTE_FLAG)  # noqa: SLF001
