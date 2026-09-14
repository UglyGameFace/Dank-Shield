from __future__ import annotations

from types import SimpleNamespace

from stoney_verify import anti_nuke_self_action_runtime as runtime


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


def test_member_patch_roles_maps_to_member_role_update() -> None:
    spec = runtime._request_spec(  # noqa: SLF001
        FakeBot(),
        FakeRoute("PATCH", "/guilds/7/members/88"),
        {"json": {"roles": [1, 2, 3]}},
    )
    assert spec is not None
    assert spec.actions == frozenset({"member_role_update"})
    assert spec.guild_id == 7
    assert spec.target_key == "id:88"


def test_member_patch_voice_move_maps_to_member_move_without_target_scope() -> None:
    spec = runtime._request_spec(  # noqa: SLF001
        FakeBot(),
        FakeRoute("PATCH", "/guilds/7/members/88"),
        {"json": {"channel_id": 123}},
    )
    assert spec is not None
    assert spec.actions == frozenset({"member_move"})
    assert spec.guild_id == 7
    assert spec.target_key == ""


def test_member_patch_voice_disconnect_maps_to_member_disconnect_without_target_scope() -> None:
    spec = runtime._request_spec(  # noqa: SLF001
        FakeBot(),
        FakeRoute("PATCH", "/guilds/7/members/88"),
        {"json": {"channel_id": None}},
    )
    assert spec is not None
    assert spec.actions == frozenset({"member_disconnect"})
    assert spec.guild_id == 7
    assert spec.target_key == ""


def test_member_patch_other_fields_stays_member_update() -> None:
    spec = runtime._request_spec(  # noqa: SLF001
        FakeBot(),
        FakeRoute("PATCH", "/guilds/7/members/88"),
        {"json": {"nick": "test"}},
    )
    assert spec is not None
    assert spec.actions == frozenset({"member_update"})
    assert spec.guild_id == 7
    assert spec.target_key == "id:88"
