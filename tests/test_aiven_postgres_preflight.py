"""Safety and URI validation contracts for owner-only Aiven preflight."""
from pathlib import Path

import pytest

from tools.probe_aiven_postgres import (
    ProbeConfigurationError,
    check_ca_pem,
    parse_aiven_uri,
)

ROOT = Path(__file__).resolve().parents[1]


def test_connection_uses_aiven_domain_and_decodes_password_privately():
    cfg = parse_aiven_uri('postgres://avnadmin:pass%40word@pg-example.aivencloud.com:13039/defaultdb?sslmode=require')
    assert cfg == {
        'host': 'pg-example.aivencloud.com', 'port': 13039,
        'user': 'avnadmin', 'password': 'pass@word', 'dbname': 'defaultdb',
    }


@pytest.mark.parametrize('uri', [
    '',
    'https://avnadmin:password@pg-example.aivencloud.com:13039/defaultdb',
    'postgres://avnadmin:password@not-aivencloud.com:13039/defaultdb',
    'postgres://avnadmin:password@evil.aivencloud.com.attacker.com:13039/defaultdb',
    'postgres://avnadmin:password@pg-example.aivencloud.com/defaultdb',
    'postgres://avnadmin:password@pg-example.aivencloud.com:99999/defaultdb',
    'postgres://avnadmin:password@pg-example.aivencloud.com:13039/',
    'postgres://avnadmin@pg-example.aivencloud.com:13039/defaultdb',
])
def test_reject_invalid_connection_uri(uri):
    with pytest.raises(ProbeConfigurationError):
        parse_aiven_uri(uri)


def test_certificate_gate():
    assert check_ca_pem('') is False
    assert check_ca_pem('-----BEGIN CERTIFICATE-----\nABC\n-----END CERTIFICATE-----') is True
    with pytest.raises(ProbeConfigurationError):
        check_ca_pem('random text')


def test_workflow_manual_owner_only_and_secret_isolated():
    file = ROOT / '.github/workflows/aiven-postgres-readonly-preflight.yml'
    content = file.read_text(encoding='utf-8')
    assert '  workflow_dispatch:' in content
    for event in ('push', 'pull_request', 'schedule', 'workflow_run', 'workflow_call'):
        assert f'  {event}:' not in content
    assert "github.ref == 'refs/heads/main' && github.actor == 'UglyGameFace'" in content
    assert 'contents: read' in content
    assert 'secrets.AIVEN_POSTGRES_URI' in content
    assert 'secrets.AIVEN_POSTGRES_CA_PEM' in content
    assert 'python -m tools.probe_aiven_postgres' in content
    assert 'secrets.DISCLOUD_TOKEN' not in content
    assert 'discloud/deploy-action' not in content


def test_probe_is_read_only_and_does_not_log_errors():
    file = ROOT / 'tools/probe_aiven_postgres.py'
    content = file.read_text(encoding='utf-8')
    assert 'default_transaction_read_only=on' in content
    assert 'SELECT current_setting(' in content
    assert 'pg_catalog.pg_stat_ssl' in content
    assert '"verify-full"' in content
    assert '"require"' in content
    assert 'print("Aiven connection preflight failed (error type: " + type(exc).__name__ + ").")' in content
    assert 'print(str(exc))' not in content
