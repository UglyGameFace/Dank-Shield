from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify.commands_ext import common
from stoney_verify.commands_ext import public_access_control
from stoney_verify.commands_ext import public_command_hub
from stoney_verify.commands_ext import public_diagnostics_group
from stoney_verify.commands_ext import public_design_studio
from stoney_verify.commands_ext import public_embed_group
from stoney_verify.commands_ext import public_owner_authority
from stoney_verify.commands_ext import public_setup_group
from stoney_verify.commands_ext import public_setup_overview
from stoney_verify.commands_ext import public_staff_scope


def _interaction(*, user_id: int, owner_id: int) -> SimpleNamespace:
    guild = SimpleNamespace(id=9001, owner_id=owner_id)
    user = SimpleNamespace(id=user_id, guild=guild)
    return SimpleNamespace(guild=guild, user=user)


def test_owner_identity_is_authoritative_without_member_cache_state() -> None:
    interaction = _interaction(user_id=111, owner_id=111)

    assert public_owner_authority.interaction_is_actual_guild_owner(interaction)
    assert public_owner_authority.interaction_has_manage_guild_authority(interaction)
    assert public_owner_authority.interaction_has_channel_management_authority(interaction)
    assert public_owner_authority.is_actual_guild_owner(
        interaction.user,
        interaction.guild,
    )


def test_owner_identity_falls_back_to_guild_owner_object() -> None:
    guild = SimpleNamespace(id=9001, owner_id=0, owner=SimpleNamespace(id=111))
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=111),
        permissions=SimpleNamespace(administrator=False, manage_guild=False),
    )

    assert public_owner_authority.interaction_is_actual_guild_owner(interaction)
    assert public_owner_authority.interaction_has_administrator_authority(interaction)
    assert public_owner_authority.interaction_has_manage_guild_authority(interaction)


def test_resolved_interaction_administrator_does_not_require_member_cache() -> None:
    guild = SimpleNamespace(id=9001, owner_id=0, owner=None)
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=111),
        permissions=SimpleNamespace(administrator=True, manage_guild=True),
    )

    assert public_owner_authority.interaction_has_administrator_authority(interaction)
    assert public_owner_authority.interaction_has_manage_guild_authority(interaction)
    assert public_staff_scope.scoped_interaction_is_staff(interaction)
    assert public_access_control.scoped_interaction_is_server_control(interaction)
    assert common._staff_check(interaction)
    assert public_command_hub._admin_or_manage(interaction)


def test_resolved_manage_channels_is_valid_channel_repair_authority_without_member_cache() -> None:
    guild = SimpleNamespace(id=9001, owner_id=0, owner=None)
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=111),
        permissions=SimpleNamespace(
            administrator=False,
            manage_guild=False,
            manage_channels=True,
        ),
    )

    assert not public_owner_authority.interaction_has_manage_guild_authority(interaction)
    assert public_owner_authority.interaction_has_channel_management_authority(interaction)


def test_role_builder_doorways_accept_resolved_admin_without_member_cache(monkeypatch) -> None:
    async def scenario() -> None:
        guild = SimpleNamespace(id=9001, owner_id=0, owner=None)
        interaction = SimpleNamespace(
            guild=guild,
            user=SimpleNamespace(id=111),
            permissions=SimpleNamespace(administrator=True, manage_guild=True),
        )

        async def forbidden_reply(*args, **kwargs) -> None:
            raise AssertionError("resolved Administrator must reach Role Builder")

        monkeypatch.setattr(public_setup_group, "reply_once", forbidden_reply)

        # /dank home -> Roles & Profiles
        assert public_command_hub._admin_or_manage(interaction)

        # /dank profile builder and /dank roles ... direct entrypoints
        assert public_setup_group._admin_or_manage_guild(interaction)
        assert await public_setup_group._require_setup_permission(interaction) is True

    asyncio.run(scenario())


def test_role_builder_routes_share_central_authority_contract() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    home = (root / "stoney_verify" / "commands_ext" / "public_command_surface_v2.py").read_text(encoding="utf-8")
    roles = (root / "stoney_verify" / "commands_ext" / "public_self_roles_group.py").read_text(encoding="utf-8")

    home_start = home.index('label="Roles & Profiles"')
    home_end = home.index('label="Logs & Activity"', home_start)
    home_route = home[home_start:home_end]
    assert "_admin_or_manage(interaction)" in home_route
    assert "_post_profile_builder(interaction" in home_route

    builder_start = roles.index('@profile_group.command(name="builder"')
    builder_end = roles.index('@profile_group.command(name="view"', builder_start)
    builder_route = roles[builder_start:builder_end]
    assert "await _require_setup_permission(interaction)" in builder_route
    assert "await _post_profile_builder(interaction" in builder_route


def test_partial_non_owner_without_resolved_permissions_still_fails_closed() -> None:
    guild = SimpleNamespace(id=9001, owner_id=111, owner=SimpleNamespace(id=111))
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=222),
        permissions=SimpleNamespace(administrator=False, manage_guild=False),
    )

    assert not public_owner_authority.interaction_has_administrator_authority(interaction)
    assert not public_owner_authority.interaction_has_manage_guild_authority(interaction)
    assert not public_owner_authority.interaction_has_channel_management_authority(interaction)
    assert not public_staff_scope.scoped_interaction_is_staff(interaction)
    assert not public_access_control.scoped_interaction_is_server_control(interaction)


def test_non_owner_partial_interaction_fails_closed() -> None:
    interaction = _interaction(user_id=222, owner_id=111)

    assert not public_owner_authority.interaction_is_actual_guild_owner(interaction)
    assert not public_owner_authority.interaction_has_manage_guild_authority(interaction)
    assert not public_access_control.scoped_interaction_is_server_control(interaction)
    assert not public_staff_scope.scoped_interaction_is_staff(interaction)


def test_interaction_owner_authority_does_not_require_user_guild_attachment() -> None:
    guild = SimpleNamespace(id=9001, owner_id=111)
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=111),
    )

    assert public_owner_authority.interaction_is_actual_guild_owner(interaction)
    assert public_access_control.scoped_interaction_is_server_control(interaction)


def test_server_control_owner_fast_path_precedes_member_type_requirement() -> None:
    interaction = _interaction(user_id=111, owner_id=111)

    assert public_access_control.scoped_is_server_control(interaction.user)
    assert public_access_control.scoped_is_ticket_staff(interaction.user)
    assert public_access_control.scoped_interaction_is_ticket_staff(interaction)
    assert public_access_control.scoped_interaction_is_server_control(interaction)


def test_interaction_ticket_staff_owner_does_not_require_user_guild_attachment() -> None:
    guild = SimpleNamespace(id=9001, owner_id=111)
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=111),
        permissions=SimpleNamespace(administrator=False, manage_guild=False),
    )

    assert public_owner_authority.interaction_is_actual_guild_owner(interaction)
    assert public_access_control.scoped_interaction_is_ticket_staff(interaction)


def test_owner_reaches_all_staff_first_management_gates_without_member_shape(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        guild = SimpleNamespace(id=9001, owner_id=111)
        interaction = SimpleNamespace(
            guild=guild,
            user=SimpleNamespace(id=111),
            permissions=SimpleNamespace(
                administrator=False,
                manage_guild=False,
                manage_channels=False,
            ),
        )

        async def forbidden_send(*_args, **_kwargs) -> None:
            raise AssertionError("actual guild owner must not receive a denial")

        monkeypatch.setattr(public_access_control, "reply_once", forbidden_send)
        monkeypatch.setattr(public_design_studio, "safe_send_interaction", forbidden_send)
        monkeypatch.setattr(public_embed_group, "safe_send_interaction", forbidden_send)

        assert await public_access_control.require_server_control(interaction) is True
        assert await public_setup_group._require_setup_permission(interaction) is True
        assert await public_design_studio._require_design_permission(interaction) is True
        assert await public_embed_group._require_embed_permission(interaction) is True

    asyncio.run(scenario())


def test_require_server_control_never_denies_actual_owner(monkeypatch) -> None:
    async def scenario() -> None:
        interaction = _interaction(user_id=111, owner_id=111)

        async def forbidden_reply(*args, **kwargs) -> None:
            raise AssertionError("actual guild owner must not receive a denial")

        monkeypatch.setattr(public_access_control, "reply_once", forbidden_reply)

        assert await public_access_control.require_server_control(interaction) is True

    asyncio.run(scenario())


def test_server_design_accepts_actual_owner_before_member_state() -> None:
    interaction = _interaction(user_id=111, owner_id=111)

    assert public_design_studio._can_user_design(interaction)


def test_shared_public_management_gates_all_accept_partial_owner_state() -> None:
    interaction = _interaction(user_id=111, owner_id=111)

    assert common._staff_check(interaction)
    assert public_staff_scope.scoped_interaction_is_staff(interaction)
    assert public_command_hub._admin_or_manage(interaction)
    assert public_setup_group._admin_or_manage_guild(interaction)
    assert public_setup_overview._admin_or_manage_guild(interaction)
    assert public_diagnostics_group._admin_or_manage_guild(interaction)
    assert public_embed_group._admin_or_manage_guild(interaction)


def test_shared_public_management_gates_reject_partial_non_owner_state() -> None:
    interaction = _interaction(user_id=222, owner_id=111)

    assert not common._staff_check(interaction)
    assert not public_command_hub._admin_or_manage(interaction)
    assert not public_setup_group._admin_or_manage_guild(interaction)
    assert not public_setup_overview._admin_or_manage_guild(interaction)
    assert not public_diagnostics_group._admin_or_manage_guild(interaction)
    assert not public_embed_group._admin_or_manage_guild(interaction)

def test_server_control_denial_hides_permission_recipe_from_nonstaff(monkeypatch) -> None:
    async def scenario() -> None:
        guild = SimpleNamespace(id=9001, owner_id=111)
        user = SimpleNamespace(id=222, guild=guild)
        interaction = SimpleNamespace(guild=guild, user=user)
        replies: list[dict] = []

        monkeypatch.setattr(
            public_access_control,
            "scoped_interaction_is_server_control",
            lambda _interaction: False,
        )
        monkeypatch.setattr(
            public_access_control,
            "scoped_interaction_is_ticket_staff",
            lambda _interaction: False,
        )

        async def capture(_interaction, payload) -> None:
            replies.append(dict(payload))

        monkeypatch.setattr(public_access_control, "reply_once", capture)

        assert await public_access_control.require_server_control(interaction) is False
        assert replies == [{"content": "❌ Staff only.", "ephemeral": True}]
        denied = replies[0]["content"]
        assert "Manage Server" not in denied
        assert "Manage Channels" not in denied
        assert "Administrator" not in denied

    asyncio.run(scenario())


def test_server_control_staff_still_gets_actionable_second_stage_guidance(monkeypatch) -> None:
    async def scenario() -> None:
        role = SimpleNamespace(id=777, mention="<@&777>")
        guild = SimpleNamespace(
            id=9001,
            owner_id=111,
            get_role=lambda role_id: role if int(role_id) == 777 else None,
        )
        user = SimpleNamespace(id=222, guild=guild)
        interaction = SimpleNamespace(guild=guild, user=user)
        replies: list[dict] = []

        monkeypatch.setattr(
            public_access_control,
            "scoped_interaction_is_server_control",
            lambda _interaction: False,
        )
        monkeypatch.setattr(
            public_access_control,
            "scoped_interaction_is_ticket_staff",
            lambda _interaction: True,
        )
        monkeypatch.setattr(
            public_access_control,
            "configured_control_role_ids_for_guild",
            lambda _guild_id: {777},
        )

        async def capture(_interaction, payload) -> None:
            replies.append(dict(payload))

        monkeypatch.setattr(public_access_control, "reply_once", capture)

        assert await public_access_control.require_server_control(interaction) is False
        assert len(replies) == 1
        assert "<@&777>" in replies[0]["content"]
        assert "Administrator" in replies[0]["content"]

    asyncio.run(scenario())


def test_server_design_denial_is_staff_only_before_manage_channels(monkeypatch) -> None:
    async def scenario() -> None:
        guild = SimpleNamespace(id=9001, owner_id=111)
        interaction = SimpleNamespace(guild=guild, user=SimpleNamespace(id=222, guild=guild))
        sent: list[str] = []

        monkeypatch.setattr(
            public_access_control,
            "scoped_interaction_is_ticket_staff",
            lambda _interaction: False,
        )

        async def capture(_interaction, *, content="", **_kwargs) -> bool:
            sent.append(str(content))
            return True

        monkeypatch.setattr(public_design_studio, "safe_send_interaction", capture)

        assert await public_design_studio._require_design_permission(interaction) is False
        assert sent == ["❌ Staff only."]
        assert "Manage Channels" not in sent[0]

    asyncio.run(scenario())


def test_server_design_recognized_staff_gets_native_permission_guidance(monkeypatch) -> None:
    async def scenario() -> None:
        guild = SimpleNamespace(id=9001, owner_id=111)
        interaction = SimpleNamespace(guild=guild, user=SimpleNamespace(id=222, guild=guild))
        sent: list[str] = []

        monkeypatch.setattr(
            public_access_control,
            "scoped_interaction_is_ticket_staff",
            lambda _interaction: True,
        )
        monkeypatch.setattr(
            public_design_studio,
            "_can_user_design",
            lambda _interaction: False,
        )

        async def capture(_interaction, *, content="", **_kwargs) -> bool:
            sent.append(str(content))
            return True

        monkeypatch.setattr(public_design_studio, "safe_send_interaction", capture)

        assert await public_design_studio._require_design_permission(interaction) is False
        assert len(sent) == 1
        assert "Manage Channels" in sent[0]

    asyncio.run(scenario())


def test_staff_first_interaction_gates_use_interaction_authority_not_member_only_helper() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    paths = (
        "stoney_verify/commands_ext/public_access_control.py",
        "stoney_verify/commands_ext/public_design_studio.py",
        "stoney_verify/commands_ext/public_diagnostics_group.py",
        "stoney_verify/commands_ext/public_embed_group.py",
        "stoney_verify/commands_ext/public_setup_group.py",
        "stoney_verify/commands_ext/public_setup_overview.py",
    )
    for relative in paths:
        source = (root / relative).read_text(encoding="utf-8")
        assert "scoped_is_ticket_staff(interaction.user)" not in source

    access = (root / paths[0]).read_text(encoding="utf-8")
    assert "def scoped_interaction_is_ticket_staff(" in access
    assert "interaction_is_actual_guild_owner(interaction)" in access
    assert "interaction_has_administrator_authority(interaction)" in access


def test_staff_management_entrypoints_check_staff_before_native_permission_copy() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    diagnostics = (root / "stoney_verify" / "commands_ext" / "public_diagnostics_group.py").read_text(encoding="utf-8")
    embed = (root / "stoney_verify" / "commands_ext" / "public_embed_group.py").read_text(encoding="utf-8")
    overview = (root / "stoney_verify" / "commands_ext" / "public_setup_overview.py").read_text(encoding="utf-8")
    surface = (root / "stoney_verify" / "commands_ext" / "public_command_surface_v2.py").read_text(encoding="utf-8")
    hub = (root / "stoney_verify" / "commands_ext" / "public_command_hub.py").read_text(encoding="utf-8")

    for source, native_text in (
        (diagnostics, "Diagnostics require **Manage Server**"),
        (embed, "Embed builder requires **Administrator**"),
        (overview, "Server setup requires **Administrator**"),
    ):
        assert 'content="❌ Staff only."' in source
        assert source.index('content="❌ Staff only."') < source.index(native_text)

    surface_logs = surface[surface.index("async def logs("):surface.index("async def profile(", surface.index("async def logs("))]
    assert "_require_setup_permission(interaction)" in surface_logs
    assert "Log settings require **Manage Server**" not in surface_logs

    hub_logs = hub[hub.index("async def logs("):hub.index("async def diagnostics(", hub.index("async def logs("))]
    assert "_require_setup_permission(interaction)" in hub_logs
    assert "Log settings require **Manage Server**" not in hub_logs

    hub_design = hub[hub.index("async def design("):hub.index("async def roles(", hub.index("async def design("))]
    assert "_admin_or_manage(interaction)" not in hub_design
    assert "public_design_bridge" in hub_design
