from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from stoney_verify.commands_ext import common
from stoney_verify.commands_ext import public_staff_scope
from stoney_verify.commands_ext import public_verify_command_center


ROOT = Path(__file__).resolve().parents[1]




def _owner_interaction(*, owner_id: int = 111) -> SimpleNamespace:
    guild = SimpleNamespace(id=999, owner_id=owner_id)
    user = SimpleNamespace(id=owner_id, guild=guild)
    return SimpleNamespace(guild=guild, user=user)


def test_server_owner_is_staff_without_role_or_permission_resolution(monkeypatch) -> None:
    interaction = _owner_interaction(owner_id=111)

    monkeypatch.setattr(
        public_staff_scope,
        "_cached_runtime_config",
        lambda _guild_id: {},
    )

    assert public_staff_scope.scoped_is_staff(interaction.user) is True
    assert public_staff_scope.scoped_interaction_is_staff(interaction) is True
    assert common._staff_check(interaction) is True


def test_non_owner_without_member_state_or_staff_config_fails_closed(monkeypatch) -> None:
    guild = SimpleNamespace(id=999, owner_id=111)
    user = SimpleNamespace(id=222, guild=guild)
    interaction = SimpleNamespace(guild=guild, user=user)

    monkeypatch.setattr(
        public_staff_scope,
        "_cached_runtime_config",
        lambda _guild_id: {},
    )

    assert public_staff_scope.scoped_is_staff(user) is False
    assert public_staff_scope.scoped_interaction_is_staff(interaction) is False
    assert common._staff_check(interaction) is False


def test_verification_center_accepts_actual_server_owner(monkeypatch) -> None:
    async def scenario() -> None:
        interaction = _owner_interaction(owner_id=111)

        async def forbidden_private(*args, **kwargs) -> None:
            raise AssertionError("server owner must not receive Staff only")

        monkeypatch.setattr(
            public_verify_command_center,
            "_private",
            forbidden_private,
        )

        assert await public_verify_command_center._require_staff(interaction) is True

    asyncio.run(scenario())


def test_verification_center_accepts_resolved_administrator_without_cached_owner(monkeypatch) -> None:
    async def scenario() -> None:
        guild = SimpleNamespace(id=999, owner_id=0, owner=None)
        interaction = SimpleNamespace(
            guild=guild,
            user=SimpleNamespace(id=111),
            permissions=SimpleNamespace(administrator=True, manage_guild=True),
        )

        async def forbidden_private(*args, **kwargs) -> None:
            raise AssertionError("resolved Administrator must not receive Staff only")

        monkeypatch.setattr(
            public_verify_command_center,
            "_private",
            forbidden_private,
        )

        assert await public_verify_command_center._require_staff(interaction) is True

    asyncio.run(scenario())


def test_configured_ticket_staff_roles_support_object_and_dict_configs(monkeypatch) -> None:
    monkeypatch.setattr(
        public_staff_scope,
        "_cached_runtime_config",
        lambda _guild_id: SimpleNamespace(
            staff_role_id="111",
            vc_staff_role_id=222,
            effective_vc_staff_role_id="222",
        ),
    )
    assert public_staff_scope.configured_ticket_staff_role_ids(1) == [111, 222]

    monkeypatch.setattr(
        public_staff_scope,
        "_cached_runtime_config",
        lambda _guild_id: {
            "staff_role_id": "333",
            "vc_staff_role_id": None,
            "effective_vc_staff_role_id": 444,
        },
    )
    assert public_staff_scope.configured_ticket_staff_role_ids(2) == [333, 444]


def test_cold_public_config_cache_fails_safe_to_no_staff_roles(monkeypatch) -> None:
    monkeypatch.setattr(public_staff_scope, "_cached_runtime_config", lambda _guild_id: {})
    assert public_staff_scope.configured_ticket_staff_role_ids(999) == []


def test_ticket_ui_and_permission_sync_use_per_guild_staff_truth() -> None:
    source = (ROOT / "stoney_verify/commands_ext/public_staff_scope.py").read_text(encoding="utf-8")
    assert "ticket_panel._is_staff_member = scoped_is_staff" in source
    assert "common._staff_check = scoped_interaction_is_staff" in source
    assert "def scoped_interaction_is_staff" in source
    assert "from .public_owner_authority import is_actual_guild_owner" in source
    assert "is_actual_guild_owner" in source
    assert "ticket_transcripts._is_staff_member = scoped_is_staff" in source
    assert "ticket_service._default_staff_role_ids = configured_ticket_staff_role_ids" in source
    assert "install_transcript_claim_runtime_guards(ticket_transcripts)" in source
    assert "install_ticket_admin_claim_guard(" in source
    assert "install_api_claim_runtime_guards(ticket_api_server)" in source
    assert "install_tasks_command_queue_claim_guard(ticket_tasks_queue)" in source
    assert "install_bot_command_worker_claim_guard(ticket_bot_worker)" in source
    assert "install_explicit_system_action_guards(" in source
    assert "legacy_timer_module=legacy_ticket_timers" in source
    assert "modern_timer_module=modern_ticket_timers" in source
    assert 'globals().get("STAFF_ROLE_ID")' not in source
    assert 'globals().get("MOD_ROLE_ID")' not in source
    assert 'globals().get("ADMIN_ROLE_ID")' not in source


def test_ticket_staff_scope_fails_closed_when_a_critical_patch_is_missing() -> None:
    source = (ROOT / "stoney_verify/commands_ext/public_staff_scope.py").read_text(encoding="utf-8")
    assert 'installed = {' in source
    assert '"ticket_panel": False' in source
    assert '"ticket_transcripts": False' in source
    assert '"ticket_permissions": False' in source
    assert '"ticket_claim_runtime": False' in source
    assert '"ticket_admin_claim_runtime": False' in source
    assert '"ticket_api_claim_runtime": False' in source
    assert '"ticket_tasks_queue_claim_runtime": False' in source
    assert '"ticket_bot_worker_claim_runtime": False' in source
    assert '"ticket_explicit_system_runtime": False' in source
    assert "missing = sorted" in source
    assert "failed closed; missing patches" in source
    assert 'if not _PATCHED:' in source


def test_commands_bootstrap_cannot_swallow_ticket_security_failure() -> None:
    source = (ROOT / "stoney_verify/commands.py").read_text(encoding="utf-8")

    strict_call = "_register_public_ticket_security_scope_strict(bot.tree)"
    general_try = "try:\n    register_all_commands(bot, bot.tree)"
    assert strict_call in source
    assert general_try in source
    assert source.index(strict_call) < source.index(general_try)

    assert "raise RuntimeError(\n            \"Dank Shield ticket security bootstrap failed closed" in source

    extra_start = source.index("def register_extra_commands(tree) -> None:")
    extra_strict = source.index("_register_public_ticket_security_scope_strict(tree)", extra_start)
    extra_general = source.index("register_all_commands(bot, tree)", extra_start)
    assert extra_start < extra_strict < extra_general
