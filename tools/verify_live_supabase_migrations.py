"""Fail-closed preflight for canonical live DankShield Supabase migration deployment.

This utility never connects to databases, reads secrets, or applies SQL. The
GitHub Action fetches already-applied migrations from the verified live project
into a disposable workspace before invoking this offline verifier.
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path


class MigrationHistoryMismatch(ValueError):
    """Unapproved migration history, malformed version, or edited applied SQL."""


def migration_version(path: Path) -> str:
    match = re.fullmatch(r"(\d{14})_[A-Za-z0-9_]+\.sql", path.name)
    if match is None:
        raise MigrationHistoryMismatch(f"Invalid migration filename: {path.name}")
    return match.group(1)


def _migration_map(folder: Path) -> dict[str, Path]:
    files = list(folder.glob("*.sql"))
    mapping = {migration_version(path): path for path in files}
    if len(mapping) != len(files):
        raise MigrationHistoryMismatch(f"Duplicate version timestamp in {folder.name}")
    return mapping


def stage_live_migrations(repo: Path, workdir: Path) -> tuple[int, int, int]:
    """Verify remote history against immutable baseline, stage future-only SQL.

    No project IDs are read from these files. The calling workflow must verify
    target project ref before any link or fetch.
    """
    baseline_file = repo / "supabase_live" / "production_baseline_versions.txt"
    source = repo / "supabase_live" / "migrations"
    destination = workdir / "supabase" / "migrations"
    if not baseline_file.is_file() or not source.is_dir() or not destination.is_dir():
        raise MigrationHistoryMismatch("Live baseline or isolated migration directory missing")
    versions = [
        line.strip()
        for line in baseline_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    baseline = set(versions)
    if len(baseline) < 25 or len(versions) != len(baseline):
        raise MigrationHistoryMismatch("Missing or duplicated original production baseline")
    if any(not re.fullmatch(r"\d{14}", value) for value in baseline):
        raise MigrationHistoryMismatch("Invalid baseline timestamp format")
    if not {"20261023014508", "20261023014518"}.issubset(baseline):
        raise MigrationHistoryMismatch("Known applied live 4:20 migration versions missing")
    remote = _migration_map(destination)
    future = _migration_map(source)
    if any(version <= max(baseline) for version in future):
        raise MigrationHistoryMismatch("New migration timestamp predates live baseline")
    missing = baseline - set(remote)
    unexpected = set(remote) - baseline - set(future)
    if missing or unexpected:
        raise MigrationHistoryMismatch(
            f"Live migration history drift: missing={sorted(missing)} "
            f"unreviewed_remote={sorted(unexpected)}"
        )
    for version, file in sorted(future.items()):
        if version in remote:
            if remote[version].read_bytes().rstrip() != file.read_bytes().rstrip():
                raise MigrationHistoryMismatch(
                    f"Already-applied migration {version} differs from reviewed SQL"
                )
        else:
            shutil.copyfile(file, destination / file.name)
    return len(baseline), len(future), len(set(future) - set(remote))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--workdir", required=True, type=Path)
    args = parser.parse_args()
    try:
        original, reviewed, pending = stage_live_migrations(args.repo, args.workdir)
    except MigrationHistoryMismatch as exc:
        parser.exit(1, f"Blocked unsafe production migration: {exc}\n")
    print(
        f"Verified {original} applied live versions, {reviewed} reviewed future "
        f"migrations, {pending} pending. Legacy project SQL excluded."
    )


if __name__ == "__main__":
    main()
