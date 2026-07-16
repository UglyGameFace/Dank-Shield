from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

ENGINE = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "full_setup_health_autofix.py"
)
SERVICE = (
    ROOT
    / "stoney_verify"
    / "setup_permission_repair_services.py"
)
FALLBACK = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "verification_member_role_fallback_guard.py"
)
SOLID = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_solid.py"
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _owners(path: Path) -> dict[str, ast.AST]:
    source = _source(path)
    tree = ast.parse(source, filename=str(path))

    return {
        node.name: node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
            ),
        )
    }


def _owner_source(path: Path, name: str) -> str:
    source = _source(path)
    node = _owners(path)[name]

    return ast.get_source_segment(
        source,
        node,
    ) or ""


def test_full_health_module_is_a_pure_engine() -> None:
    source = _source(ENGINE)
    owners = _owners(ENGINE)

    for name in (
        "AuditResult",
        "run_full_audit",
        "run_one_press_repair",
        "build_full_health_embed",
    ):
        assert name in owners

    for name in (
        "_make_health_view",
        "patch_full_setup_health_autofix",
    ):
        assert name not in owners

    for marker in (
        "_PATCHED",
        "FullHealthFixView",
        "public_setup_solid._build_health_embed",
        "public_setup_solid.SetupNavView",
        "public_setup_solid.BackToSetupView",
    ):
        assert marker not in source


def test_full_health_module_has_no_import_time_calls() -> None:
    source = _source(ENGINE)
    tree = ast.parse(source, filename=str(ENGINE))

    calls = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
        )
    ]

    assert calls == []


def test_fallback_helper_contract_remains() -> None:
    source = _source(ENGINE)
    fallback = _source(FALLBACK)

    for marker in (
        "ROLE_KEYS",
        "def _audit_roles(",
        "def _bot_member(",
        "def _can_manage_role(",
        "def _cfg_int(",
        "def _mention(",
        "def _role(",
    ):
        assert marker in source

    for marker in (
        "health.ROLE_KEYS",
        "health._audit_roles",
        "health._bot_member",
        "health._can_manage_role",
        "health._cfg_int",
        "health._mention",
        "health._role",
    ):
        assert marker in fallback


def test_service_owns_its_preview_and_result_views() -> None:
    owners = _owners(SERVICE)
    source = _source(SERVICE)

    assert "PermissionRepairPreviewView" in owners
    assert "PermissionRepairResultView" in owners

    assert "PermissionRepairConfirmView" not in source
    assert "PermissionRepairDoneView" not in source


def test_service_preview_callbacks_stay_canonical() -> None:
    body = _owner_source(
        SERVICE,
        "PermissionRepairPreviewView",
    )

    assert "await apply_permission_repair(interaction)" in body
    assert "await open_permission_repair(interaction)" in body
    assert "await _back_to_advanced_options(interaction)" in body


def test_service_result_callbacks_stay_canonical() -> None:
    body = _owner_source(
        SERVICE,
        "PermissionRepairResultView",
    )

    assert "await open_permission_repair(interaction)" in body
    assert "await _back_to_advanced_options(interaction)" in body


def test_service_has_one_queued_apply_owner() -> None:
    body = _owner_source(
        SERVICE,
        "apply_permission_repair",
    )

    assert body.count("run_interaction_exclusive(") == 1
    assert 'operation_type="setup_permission_repair"' in body
    assert 'concurrency_class="guild_config_write"' in body
    assert 'concurrency_key="setup_permission_repair"' in body
    assert "view=PermissionRepairResultView()" in body


def test_deep_audit_is_diagnostic_only() -> None:
    load_body = _owner_source(
        SERVICE,
        "_load_deep_audit",
    )
    embed_body = _owner_source(
        SERVICE,
        "result_embed",
    )

    assert "await deep_health.run_full_audit(guild)" in load_body
    assert "run_one_press_repair" not in load_body
    assert "run_one_press_repair" not in _source(SERVICE)

    assert "Advanced Diagnostic Blockers" in embed_body
    assert "Advanced Diagnostic Warnings" in embed_body
    assert "Advanced Diagnostic Passing" in embed_body
    assert "do **not** decide whether" in embed_body
    assert "feature-aware **Setup Check**" in embed_body


def test_open_route_reruns_preview_and_deep_audit() -> None:
    body = _owner_source(
        SERVICE,
        "open_permission_repair",
    )

    assert "preview_or_apply(" in body
    assert "apply=False" in body
    assert "await _load_deep_audit(guild)" in body
    assert "view=PermissionRepairPreviewView()" in body


def test_apply_route_reruns_deep_audit_after_safe_fix() -> None:
    body = _owner_source(
        SERVICE,
        "apply_permission_repair",
    )

    queue_index = body.index(
        "run_interaction_exclusive("
    )
    audit_index = body.index(
        "await _load_deep_audit(guild)"
    )

    assert queue_index < audit_index


def test_importing_engine_cannot_replace_solid_owners() -> None:
    engine_source = _source(ENGINE)

    for marker in (
        "_build_health_embed =",
        "SetupNavView =",
        "BackToSetupView =",
        'setattr(solid, "_build_health_embed"',
    ):
        assert marker not in engine_source

    solid_source = _source(SOLID)

    assert "async def _build_health_embed(" in solid_source
    assert "class SetupNavView(" in solid_source
