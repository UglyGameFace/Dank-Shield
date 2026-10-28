from pathlib import Path

from tools.report_postgres_cutover_coverage import coverage


def test_coverage_lists_unmapped_direct_callers(tmp_path: Path):
    package = tmp_path / "stoney_verify"
    package.mkdir()
    (package / "guild_config.py").write_text("get_supabase()\n", encoding="utf-8")
    (package / "unmapped.py").write_text("get_supabase()\n", encoding="utf-8")
    report = coverage(tmp_path)
    assert report["staging_table_count"] >= 16
    assert report["direct_supabase_files"] == 2
    assert report["literal_tables_not_in_staging"] == []
    assert "stoney_verify/guild_config.py" in report["direct_supabase_file_groups"]["guild_configuration"]
    assert "stoney_verify/unmapped.py" in report["other_direct_supabase_files"]
    assert report["cutover_authorized"] is False
    assert report["production_postgresql_ready"] is False


def test_coverage_requires_production_package(tmp_path: Path):
    import pytest
    with pytest.raises(FileNotFoundError):
        coverage(tmp_path)


def test_unstaged_literal_table_is_reported(tmp_path: Path):
    package = tmp_path / "stoney_verify"
    package.mkdir()
    (package / "missing.py").write_text(
        "get_supabase().table('important_new_table').select('*')\n",
        encoding="utf-8",
    )
    report = coverage(tmp_path)
    assert "important_new_table" in report["literal_tables_not_in_staging"]
    assert report["cutover_authorized"] is False

def test_nested_verification_source_is_classified_without_package_prefix(tmp_path: Path):
    package = tmp_path / "stoney_verify"
    verification = package / "member_verification"
    verification.mkdir(parents=True)
    (verification / "joining.py").write_text("get_supabase()\n", encoding="utf-8")
    (package / "unmapped.py").write_text("get_supabase()\n", encoding="utf-8")
    report = coverage(tmp_path)
    assert report["direct_supabase_files"] == 2
    assert "stoney_verify/member_verification/joining.py" in report["direct_supabase_file_groups"]["verification"]
    assert report["other_direct_supabase_files"] == ["stoney_verify/unmapped.py"]
    assert report["production_postgresql_ready"] is False
