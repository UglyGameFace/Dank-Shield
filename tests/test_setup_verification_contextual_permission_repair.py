from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from stoney_verify.commands_ext import public_contextual_permission_repair as integration


class FakeGuild:
    def __init__(self, guild_id: int = 777) -> None:
        self.id = guild_id
        self.me = SimpleNamespace(guild_permissions=SimpleNamespace())


def _services(**overrides):
    values = {
        "tickets": False,
        "verify": False,
        "basic_verify": False,
        "voice": False,
        "id": False,
        "spam_guard": False,
        "logs": False,
    }
    values.update(overrides)
    return values


def test_setup_targets_follow_enabled_service_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        integration.setup,
        "_selected_setup_services",
        lambda _cfg: _services(tickets=True, verify=True, voice=True, logs=False),
    )
    cfg = {
        "verify_channel_id": "10",
        "vc_verify_channel_id": "11",
        "vc_verify_queue_channel_id": "12",
        "ticket_panel_channel_id": "20",
        "ticket_category_id": "21",
        "ticket_archive_category_id": "22",
        "transcripts_channel_id": "23",
        "modlog_channel_id": "30",
    }

    targets = integration._setup_targets(FakeGuild(), cfg)
    by_id = {item.channel_id: (item.feature, item.label) for item in targets}

    assert by_id[10][0] == "general"
    assert by_id[11][0] == "general"
    assert by_id[12][0] == "general"
    assert by_id[20][0] == "tickets"
    assert by_id[21][0] == "tickets"
    assert by_id[22][0] == "tickets"
    assert by_id[23][0] == "tickets"
    assert 30 not in by_id


def test_verification_scope_never_pulls_ticket_or_log_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        integration.setup,
        "_selected_setup_services",
        lambda _cfg: _services(tickets=True, verify=True, voice=True, logs=True),
    )
    cfg = {
        "verify_channel_id": "10",
        "vc_verify_channel_id": "11",
        "vc_verify_queue_channel_id": "12",
        "ticket_category_id": "21",
        "modlog_channel_id": "30",
    }

    targets = integration._setup_targets(FakeGuild(), cfg, verification_only=True)
    assert [item.channel_id for item in targets] == [10, 11, 12]


def test_disabled_verification_services_do_not_repair_stale_saved_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        integration.setup,
        "_selected_setup_services",
        lambda _cfg: _services(verify=False, voice=False),
    )
    cfg = {
        "verify_channel_id": "10",
        "vc_verify_channel_id": "11",
        "vc_verify_queue_channel_id": "12",
    }

    assert integration._setup_targets(FakeGuild(), cfg, verification_only=True) == ()


def test_channel_effective_bits_are_not_server_prerequisites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = FakeGuild()
    guild.me.guild_permissions = SimpleNamespace(
        administrator=False,
        view_channel=False,
        send_messages=False,
        embed_links=False,
        read_message_history=False,
        attach_files=False,
        manage_roles=False,
        manage_channels=False,
        manage_messages=False,
    )
    monkeypatch.setattr(
        integration.setup,
        "_selected_setup_services",
        lambda _cfg: _services(logs=True),
    )

    # Logs only needs channel-effective posting/history access. Those bits belong
    # to the contextual target audit and must not become fake server blockers.
    assert integration._server_permission_issues(guild, {}) == []


def test_feature_level_server_prerequisite_stays_manual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = FakeGuild()
    guild.me.guild_permissions = SimpleNamespace(
        administrator=False,
        view_channel=False,
        send_messages=False,
        embed_links=False,
        read_message_history=False,
        attach_files=False,
        manage_roles=False,
        manage_channels=True,
        manage_messages=True,
    )
    monkeypatch.setattr(
        integration.setup,
        "_selected_setup_services",
        lambda _cfg: _services(verify=True),
    )

    issues = integration._server_permission_issues(guild, {}, verification_only=True)
    assert len(issues) == 1
    assert "Manage Roles" in issues[0]
    assert "View Channels" not in issues[0]
    assert "Send Messages" not in issues[0]


def test_verification_mapping_gaps_remain_manual(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        integration.setup,
        "_selected_setup_services",
        lambda _cfg: _services(verify=True, voice=True),
    )
    monkeypatch.setattr(
        integration,
        "_server_permission_issues",
        lambda *_args, **_kwargs: [],
    )

    issues = integration._setup_manual_issues(
        FakeGuild(),
        {},
        verification_only=True,
    )

    assert any("no verification start channel" in item.lower() for item in issues)
    assert any("no Voice Verify room" in item for item in issues)
    assert any("no private staff request channel" in item for item in issues)


def test_setup_repair_button_uses_shared_state_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = FakeGuild()
    monkeypatch.setattr(integration, "_setup_targets", lambda *_args, **_kwargs: ())

    monkeypatch.setattr(
        integration.contextual,
        "audit_context",
        lambda *_args, **_kwargs: SimpleNamespace(healthy=False, repairable_count=2),
    )
    repairable = integration.SetupContextRepairButton(guild=guild, cfg={})
    assert repairable.label == "Fix Issues"
    assert repairable.disabled is False

    monkeypatch.setattr(
        integration.contextual,
        "audit_context",
        lambda *_args, **_kwargs: SimpleNamespace(healthy=True, repairable_count=0),
    )
    healthy = integration.SetupContextRepairButton(guild=guild, cfg={})
    assert healthy.label == "Access Healthy"
    assert healthy.disabled is True

    monkeypatch.setattr(
        integration.contextual,
        "audit_context",
        lambda *_args, **_kwargs: SimpleNamespace(healthy=False, repairable_count=0),
    )
    manual = integration.SetupContextRepairButton(guild=guild, cfg={}, manual_issues=["manual"])
    assert manual.label == "Manual Fix Needed"
    assert manual.disabled is False


def test_setup_button_repairs_then_reopens_same_health_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    guild = FakeGuild()
    cfg = {"verify_channel_id": "10"}
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        integration.contextual,
        "audit_context",
        lambda *_args, **_kwargs: SimpleNamespace(healthy=False, repairable_count=1),
    )
    monkeypatch.setattr(
        integration,
        "_setup_targets",
        lambda *_args, **_kwargs: (
            integration.contextual.ContextualRepairTarget(10, "general", "Verification start channel"),
        ),
    )

    async def allow(_interaction):
        return True

    async def defer(_interaction):
        calls["deferred"] = True

    async def config(_guild_id, *, refresh=False):
        assert refresh is True
        return cfg

    async def guided(_guild):
        return "ready", "", "", ""

    async def repair(_guild, targets, *, actor_id, manual_issues=()):
        calls["repair_targets"] = tuple(targets)
        calls["actor_id"] = actor_id
        calls["manual_issues"] = tuple(manual_issues)
        return SimpleNamespace(summary=lambda: "✅ Access re-check passed.")

    async def reopen(_interaction, *, saved_message="", already_deferred=False):
        calls["saved_message"] = saved_message
        calls["already_deferred"] = already_deferred

    monkeypatch.setattr(integration.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(integration.solid, "_safe_defer_update", defer)
    monkeypatch.setattr(integration, "get_guild_config", config)
    monkeypatch.setattr(integration.setup, "_guided_setup_target", guided)
    monkeypatch.setattr(integration.contextual, "repair_context", repair)
    monkeypatch.setattr(integration.setup, "_open_health_check", reopen)
    monkeypatch.setattr(integration, "_setup_manual_issues", lambda *_args, **_kwargs: [])

    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=99),
        response=SimpleNamespace(send_message=None),
    )
    button = integration.SetupContextRepairButton(guild=guild, cfg=cfg)
    asyncio.run(button.callback(interaction))

    assert calls["deferred"] is True
    assert calls["actor_id"] == 99
    assert calls["already_deferred"] is True
    assert calls["saved_message"] == "✅ Access re-check passed."


def test_health_check_decorates_current_review_owner_instead_of_replacing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = FakeGuild()
    captured: dict[str, object] = {}

    class LiveReviewView(integration.discord.ui.View):
        def __init__(self, *, ready: bool) -> None:
            super().__init__(timeout=900)
            self.ready = ready

    async def allow(_interaction):
        return True

    async def health_embed(_guild):
        return integration.discord.Embed(title="Setup Check")

    async def guided(_guild):
        return "ready", "", "", ""

    async def config(_guild_id, *, refresh=False):
        assert refresh is True
        return {}

    async def edit(_interaction, *, embed=None, view=None, **_kwargs):
        captured["embed"] = embed
        captured["view"] = view

    monkeypatch.setattr(integration.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(integration.setup, "_build_plain_setup_health_embed", health_embed)
    monkeypatch.setattr(integration.setup, "_guided_setup_target", guided)
    monkeypatch.setattr(integration, "get_guild_config", config)
    monkeypatch.setattr(integration, "_setup_manual_issues", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(integration, "_setup_targets", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        integration.contextual,
        "audit_context",
        lambda *_args, **_kwargs: SimpleNamespace(healthy=True, repairable_count=0),
    )
    monkeypatch.setattr(integration.setup, "SetupReviewView", LiveReviewView)
    monkeypatch.setattr(integration.solid, "_edit_or_followup", edit)

    interaction = SimpleNamespace(
        guild=guild,
        response=SimpleNamespace(send_message=None),
    )
    asyncio.run(
        integration._contextual_open_health_check(
            interaction,
            already_deferred=True,
        )
    )

    view = captured["view"]
    assert isinstance(view, LiveReviewView)
    assert view.ready is True
    repair_buttons = [
        child
        for child in view.children
        if getattr(child, "custom_id", "") == "dank_setup_review:contextual_repair"
    ]
    assert len(repair_buttons) == 1
    assert repair_buttons[0].label == "Access Healthy"


def test_runtime_patch_covers_setup_check_and_verification_channels_without_clobbering_review_owner() -> None:
    source = __import__("pathlib").Path(integration.__file__).read_text(encoding="utf-8")
    assert "setup._open_health_check = _contextual_open_health_check" in source
    assert "view = setup.SetupReviewView(ready=ready)" in source
    assert "setup.SetupReviewView =" not in source
    assert "solid.VerificationChannelsPickerView = contextual_verification_view" in source
    assert "_VERIFICATION_VIEW_CLASS = base_verification_view" in source
    assert 'custom_id="dank_setup_review:contextual_repair"' in source
    assert 'custom_id="stoney_solid:verification_contextual_repair"' in source
    assert "set_permissions(" not in source
