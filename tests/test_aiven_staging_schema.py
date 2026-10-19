"""Fail-closed checks for Aiven's manual-only, empty staging operation."""
from pathlib import Path

import pytest

from tools.aiven_empty_staging_schema import (
    INSTALL_CONFIRMATION, MIGRATIONS, ProbeConfigurationError,
    RELATIONS, state_guard, validate_target,
)

ROOT = Path(__file__).resolve().parents[1]
URI = "postgresql://avnadmin:secret@pg-3e03c30c-owner.l.aivencloud.com:14567/defaultdb"
CA = "-----BEGIN CERTIFICATE-----\nABC\n-----END CERTIFICATE-----\n"


def test_preview_requires_real_aiven_service_identity():
    cfg = validate_target(URI, CA, "preview", "")
    assert cfg["host"] == "pg-3e03c30c-owner.l.aivencloud.com"
    for uri in (
        URI.replace("pg-3e03c30c-", "pg-other-"),
        URI.replace("aivencloud.com", "evil.com"),
    ):
        with pytest.raises(ProbeConfigurationError):
            validate_target(uri, CA, "preview", "")


def test_schema_install_requires_exact_confirmation_and_certificate():
    with pytest.raises(ProbeConfigurationError):
        validate_target(URI, CA, "install", "")
    with pytest.raises(ProbeConfigurationError):
        validate_target(URI, "", "preview", "")
    assert validate_target(URI, CA, "install", INSTALL_CONFIRMATION)
    with pytest.raises(ProbeConfigurationError):
        validate_target(URI, CA, "delete", INSTALL_CONFIRMATION)


def test_empty_schema_or_already_installed_only():
    assert state_guard((180000, True, 8_000_000, 0, (False, False), (False,) * 3)) == "empty"
    assert state_guard((180000, True, 8_000_000, 0, (True, True), (True,) * 3)) == "already_installed"
    for state in (
        (170000, True, 8_000_000, 0, (False, False), (False,) * 3),
        (180000, False, 8_000_000, 0, (False, False), (False,) * 3),
        (180000, True, 8_000_000, 1, (False, False), (False,) * 3),
        (180000, True, 108_000_000, 0, (False, False), (False,) * 3),
        (180000, True, 8_000_000, 0, (True, False), (True, False, False)),
    ):
        with pytest.raises(RuntimeError):
            state_guard(state)


def test_schema_only_three_known_ddl_files_and_no_new_routing():
    assert len(MIGRATIONS) == len(RELATIONS) == 3
    for name in MIGRATIONS:
        assert (ROOT / "postgres/migrations" / name).is_file()
    code = (ROOT / "tools/aiven_empty_staging_schema.py").read_text()
    assert 'sslmode": "verify-full"' in code
    assert 'set transaction read only' in code
    assert "No source rows imported." in code
    assert "prepare=False" in code
    assert "print(str(exc))" not in code


def test_workflow_must_be_manual_only_and_explicitly_guarded():
    flow = (ROOT / ".github/workflows/aiven-empty-staging-schema.yml").read_text()
    assert "  workflow_dispatch:" in flow
    for name in ("push", "schedule", "pull_request", "workflow_run", "workflow_call"):
        assert "  " + name + ":" not in flow
    assert "github.ref == 'refs/heads/main'" in flow
    assert "github.actor == 'UglyGameFace'" in flow
    assert "secrets.AIVEN_POSTGRES_URI" in flow
    assert "secrets.AIVEN_POSTGRES_CA_PEM" in flow
    assert "contents: read" in flow
    assert "CREATE_EMPTY_AIVEN_STAGING_ONLY" in flow
    assert "DISCOLOUD_TOKEN" not in flow
    assert "supabase db push" not in flow
