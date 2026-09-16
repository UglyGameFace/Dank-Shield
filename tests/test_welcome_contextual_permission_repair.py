from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_contextual_permission_repair.py"
).read_text(encoding="utf-8")
GATE = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_gate.py"
).read_text(encoding="utf-8")
WELCOME = (ROOT / "stoney_verify" / "welcome_event_services.py").read_text(
    encoding="utf-8"
)
CORE = (ROOT / "stoney_verify" / "contextual_permission_repair.py").read_text(
    encoding="utf-8"
)


def test_public_runtime_installs_contextual_repair_explicitly() -> None:
    assert "from .public_contextual_permission_repair import apply_contextual_permission_repair" in GATE
    assert "contextual_repair_ok = apply_contextual_permission_repair()" in GATE


def test_welcome_center_receives_same_screen_fix_issues_control() -> None:
    assert "class WelcomeContextRepairButton" in INTEGRATION
    assert 'custom_id="dank_setup_welcome_events:fix_issues"' in INTEGRATION
    assert "self.add_item(WelcomeContextRepairButton(guild=guild, cfg=cfg))" in INTEGRATION
    assert "welcome.WelcomeEventsCenterView = ContextualWelcomeEventsCenterView" in INTEGRATION


def test_welcome_repair_covers_both_selected_event_channels() -> None:
    assert '"Member-facing Join channel"' in INTEGRATION
    assert '"Private Join/Leave log"' in INTEGRATION
    assert '"welcome"' in INTEGRATION
    assert '"logs"' in INTEGRATION
    assert "contextual.repair_context(" in INTEGRATION
    assert "actor_id=int(interaction.user.id)" in INTEGRATION


def test_private_join_audience_is_not_opened_automatically() -> None:
    assert "will not make a private staff channel public automatically" in INTEGRATION
    assert "set_permissions(" not in INTEGRATION
    assert "set_permissions(" not in CORE


def test_shared_contract_reaudits_and_has_healthy_state() -> None:
    assert "after = audit_context(" in CORE
    assert 'return "Access Healthy", "✅"' in CORE
    assert 'return "Fix Issues", "🛠️"' in CORE
    assert "clear_explicit_denies=False" in CORE


def test_existing_welcome_diagnostics_remain_the_source_of_displayed_health() -> None:
    assert "def _can_post(" in WELCOME
    assert 'return "⚠️ Missing: " + ", ".join(missing)' in WELCOME
    assert "def _join_audience_status(" in WELCOME
