"""Operator-only full Supabase pg_dump archival tool.

Default = instructions without database access. --create is explicit; --verify
does not contact any database. Does NOT back up Discloud/voice bot, does not
copy into a GitHub artifact, and never performs production database writes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from stoney_verify.source_pg_backup import (
    _postgres_tools_are_17, _sha256_file,
    create_backup, verify_archive_toc
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create/verify a local AES-256-GCM encrypted pg_dump backup"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--create", action="store_true")
    modes.add_argument("--verify", metavar="FILE")
    parser.add_argument("--output-dir", default="",
                        help="existing absolute private directory (mode 0700)")
    args = parser.parse_args()
    if not args.create and not args.verify:
        print(json.dumps({
            "status": "plan_only", "source_data_changed": False,
            "required": [
                "pg_dump and pg_restore version 17",
                "DANK_SOURCE_DATABASE_URL (Supabase PostgreSQL connection string)",
                "DANK_MIGRATION_SOURCE_REF (exact Supabase project ref)",
                "DANK_BACKUP_PASSPHRASE (unique, strong; keep separately)",
                "explicit private durable backup output folder",
                "recovery restore drill and independently stored copy",
            ],
            "warning": "No database contacted and no data backed up yet.",
        }))
        return 0

    try:
        secret = os.environ.get("DANK_BACKUP_PASSPHRASE", "")
        if args.verify:
            _postgres_tools_are_17()
            file = Path(args.verify).expanduser().resolve(strict=True)
            if not file.is_file() or file.is_symlink():
                raise ValueError("encrypted archive file required")
            verified = verify_archive_toc(file, secret)
            print(json.dumps({
                "status": "authenticated_toc_verified",
                "encrypted_sha256": _sha256_file(file),
                **verified,
            }, sort_keys=True))
            return 0
        if not args.output_dir:
            raise ValueError("explicit destination folder required")
        result = create_backup(
            dsn=os.environ.get("DANK_SOURCE_DATABASE_URL", ""),
            source_ref=os.environ.get("DANK_MIGRATION_SOURCE_REF", ""),
            passphrase=secret, output_dir=Path(args.output_dir),
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        # pg_dump/crypto errors can contain credentials or data paths.
        print(json.dumps({
            "status": "failed", "failure_type": type(exc).__name__,
            "warning": "No source or destination database records were modified.",
        }))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
