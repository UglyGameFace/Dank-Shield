from pathlib import Path

from tools.audit_supabase_production_callers import inventory, scan_source


def test_scan_identifies_direct_and_candidate_calls(tmp_path: Path):
    source = tmp_path / "example.py"
    source.write_text(
        "client = get_supabase()\n"
        "client.table('tickets').select('*').execute()\n"
        "client.rpc('fn', {}).execute()\n"
        "reset_supabase()\n",
        encoding="utf-8",
    )
    hits = scan_source(source)
    assert [(hit["line"], hit["call"], hit["classification"]) for hit in hits] == [
        (1, "get_supabase", "direct"),
        (2, "table", "candidate"),
        (3, "rpc", "candidate"),
        (4, "reset_supabase", "direct"),
    ]


def test_inventory_only_scans_production_package(tmp_path: Path):
    package = tmp_path / "stoney_verify"
    package.mkdir()
    (package / "guild_config.py").write_text("get_supabase()\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("get_supabase()\n", encoding="utf-8")
    report = inventory(tmp_path)
    assert report["direct_calls"] == 1
    assert report["candidate_calls"] == 0
    assert report["files_with_calls"] == 1
    assert report["findings"][0]["file"] == "stoney_verify/guild_config.py"


def test_no_execution_of_scanned_source(tmp_path: Path):
    source = tmp_path / "unsafe.py"
    source.write_text(
        "raise RuntimeError('should never execute')\nget_supabase()\n",
        encoding="utf-8",
    )
    assert scan_source(source)[0]["call"] == "get_supabase"
