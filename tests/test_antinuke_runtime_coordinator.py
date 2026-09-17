from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from stoney_verify import anti_nuke_runtime_coordinator as coordinator


def _layer(
    key: str,
    *,
    pass_bot: bool = True,
    false_message: str | None = None,
    failure_message: str | None = None,
) -> coordinator.RuntimeLayer:
    return coordinator.RuntimeLayer(
        key=key,
        module=f"fake_{key}",
        installer=f"install_{key}",
        pass_bot=pass_bot,
        false_message=false_message or f"{key} already installed",
        failure_message=failure_message or f"{key} failed",
    )


def test_phase_tables_preserve_exact_runtime_order() -> None:
    assert tuple(layer.key for layer in coordinator.PRE_APP_LAYERS) == (
        "gateway",
        "finalizer",
        "incident",
        "hostile_actor",
        "lockdown",
        "self_action",
        "zero_damage",
        "audit_compat",
        "readiness_gate",
    )
    assert tuple(layer.key for layer in coordinator.POST_APP_LAYERS) == (
        "product_policy",
        "reentry_race",
    )


def test_phase_tables_preserve_existing_installer_contracts() -> None:
    expected_pre = (
        ("anti_nuke_gateway_runtime", "install_anti_nuke_gateway_runtime", True),
        ("anti_nuke_finalizer_runtime", "install_anti_nuke_finalizer_runtime", True),
        ("anti_nuke_incident_runtime", "install_anti_nuke_incident_runtime", True),
        ("anti_nuke_hostile_actor_runtime", "install_hostile_actor_runtime", True),
        ("anti_nuke_lockdown_runtime", "install_anti_nuke_lockdown_runtime", True),
        ("anti_nuke_self_action_runtime", "install_anti_nuke_self_action_runtime", True),
        ("anti_nuke_zero_damage_runtime", "install_anti_nuke_zero_damage_runtime", True),
        ("anti_nuke_audit_compat_runtime", "install_anti_nuke_audit_compat_runtime", True),
        ("anti_nuke_readiness_gate_runtime", "install_anti_nuke_readiness_gate_runtime", True),
    )
    expected_post = (
        ("anti_nuke_product_policy_runtime", "install_anti_nuke_product_policy_runtime", False),
        ("anti_nuke_reentry_race_runtime", "install_anti_nuke_reentry_race_runtime", True),
    )

    assert tuple(
        (layer.module, layer.installer, layer.pass_bot)
        for layer in coordinator.PRE_APP_LAYERS
    ) == expected_pre
    assert tuple(
        (layer.module, layer.installer, layer.pass_bot)
        for layer in coordinator.POST_APP_LAYERS
    ) == expected_post


def test_install_phase_runs_every_layer_in_order_and_honors_bot_argument(
    monkeypatch,
) -> None:
    bot = SimpleNamespace(id=123)
    layers = (
        _layer("first", pass_bot=True),
        _layer("second", pass_bot=False),
        _layer("third", pass_bot=True),
    )
    calls: list[tuple[str, tuple[object, ...]]] = []

    def fake_resolve(layer: coordinator.RuntimeLayer):
        def installer(*args):
            calls.append((layer.key, args))
            return True

        return installer

    monkeypatch.setattr(coordinator, "_resolve_installer", fake_resolve)

    result = coordinator._install_phase(bot, layers)  # noqa: SLF001

    assert result == {"first": True, "second": True, "third": True}
    assert calls == [
        ("first", (bot,)),
        ("second", ()),
        ("third", (bot,)),
    ]


def test_install_phase_continues_after_layer_failure(monkeypatch, capsys) -> None:
    bot = SimpleNamespace(id=456)
    layers = (
        _layer("first"),
        _layer("broken", failure_message="broken layer failed"),
        _layer("last"),
    )
    calls: list[str] = []

    def fake_resolve(layer: coordinator.RuntimeLayer):
        def installer(*_args):
            calls.append(layer.key)
            if layer.key == "broken":
                raise RuntimeError("boom")
            return True

        return installer

    monkeypatch.setattr(coordinator, "_resolve_installer", fake_resolve)

    result = coordinator._install_phase(bot, layers)  # noqa: SLF001

    assert result == {"first": True, "broken": False, "last": True}
    assert calls == ["first", "broken", "last"]
    assert "broken layer failed: RuntimeError: boom" in capsys.readouterr().out


def test_false_installer_result_reports_duplicate_without_stopping_phase(
    monkeypatch,
    capsys,
) -> None:
    bot = SimpleNamespace(id=789)
    layers = (
        _layer("duplicate", false_message="duplicate layer skipped"),
        _layer("next"),
    )

    def fake_resolve(layer: coordinator.RuntimeLayer):
        if layer.key == "duplicate":
            return lambda *_args: False
        return lambda *_args: True

    monkeypatch.setattr(coordinator, "_resolve_installer", fake_resolve)

    result = coordinator._install_phase(bot, layers)  # noqa: SLF001

    assert result == {"duplicate": False, "next": True}
    assert "duplicate layer skipped" in capsys.readouterr().out


def test_main_uses_single_antinuke_phase_boundaries_without_regressing_invite_fix() -> None:
    source = Path("main.py").read_text(encoding="utf-8")

    invite_surface = source.index("    _install_invite_policy_message_surface_runtime()")
    invite_reconcile = source.index("    _install_invite_reconciliation_runtime()")
    pre_app = source.index("    install_anti_nuke_pre_app(bot)")
    app_import = source.index("from stoney_verify.app import run as _run_dank_shield")
    post_app = source.index("    install_anti_nuke_post_app(bot)")
    spam_abuse = source.index("    _install_spam_guard_abuse_runtimes()")
    bot_run = source.index("    _run_dank_shield()")

    assert (
        invite_surface
        < invite_reconcile
        < pre_app
        < app_import
        < post_app
        < spam_abuse
        < bot_run
    )

    for retired_wrapper in (
        "def _install_anti_nuke_gateway_runtime",
        "def _install_anti_nuke_finalizer_runtime",
        "def _install_anti_nuke_incident_runtime",
        "def _install_hostile_actor_runtime",
        "def _install_anti_nuke_lockdown_runtime",
        "def _install_anti_nuke_self_action_runtime",
        "def _install_anti_nuke_zero_damage_runtime",
        "def _install_anti_nuke_audit_compat_runtime",
        "def _install_anti_nuke_readiness_gate_runtime",
        "def _install_anti_nuke_product_policy_runtime",
        "def _install_anti_nuke_reentry_race_runtime",
    ):
        assert retired_wrapper not in source
