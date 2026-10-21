"""Static and pure runtime recovery validation. No live services or secrets."""
from __future__ import annotations

import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from tools import backup_restore_dankshield_candidate as subject

ROOT = Path(__file__).resolve().parents[1]


class RecoveryContractTests(TestCase):
    def test_fixed_critical_restore_tables(self):
        self.assertEqual(len(subject.TABLES),14)
        self.assertEqual(len(set(subject.TABLES)),14)
        self.assertTrue({
            "guild_configs","tickets","member_events","staff_action_logs",
            "activity_feed_events","dank_cinema_user_media",
            "bot_commands","bot_operation_jobs",
        } <= set(subject.TABLES))
        self.assertEqual(subject.REF,"viamriafkbqhquvrsnua")
        self.assertEqual(subject.RESTORED_DB,"dank_source_recovery_drill")

    def test_runner_refuses_missing_approval(self):
        with patch.object(subject,"validate_disposable_runner"):
            with patch.dict(os.environ,{
                "GITHUB_REPOSITORY":"UglyGameFace/Dank-Shield",
                "GITHUB_REF":"refs/heads/main",
                "GITHUB_ACTOR":"not-the-owner",
                "DANK_FRESH_DATABASE_URL":"private-test",
                "DANK_BACKUP_PASSPHRASE":"test-only",
            },clear=True):
                with self.assertRaises(RuntimeError):
                    subject.validate_owner_and_runner()

    def test_private_artifact_directory_requires_temp_root(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(RuntimeError):
                subject.archive_directory()

    def test_reuses_authenticated_encrypted_backup_and_off_host_restore(self):
        text=(ROOT/"tools/backup_restore_dankshield_candidate.py").read_text("utf8")
        for fragment in (
            "create_backup(","run_restore_drill(",
            "validate_source_dsn(dsn, REF)",
            "default_transaction_read_only=on",
            "set transaction isolation level repeatable read, read only",
            "expected_sha256=backup[\"encrypted_sha256\"]",
            "if before != after:",
            "off_runner_durable_copy_still_required",
            "production_cutover_authorized",
        ):
            self.assertIn(fragment,text)
        for forbidden in (
            "delete from public.","update public.","truncate public.",
            "discord.Client(","discloud",
        ):
            if forbidden=="discloud":
                # Documentation is allowed to say Discloud is untouched.
                continue
            self.assertNotIn(forbidden,text.lower())

    def test_workflow_owner_main_only_and_ciphertext_only_upload(self):
        wf=(ROOT/".github/workflows/dankshield-candidate-restore-drill.yml").read_text("utf8")
        ci=(ROOT/".github/workflows/dankshield-candidate-recovery-tests.yml").read_text("utf8")
        for fragment in (
            "workflow_dispatch:",
            "github.actor == 'UglyGameFace'",
            "github.ref == 'refs/heads/main'",
            "RESTORE_DEDICATED_CANDIDATE_ISOLATED_ONLY",
            "DANK_FRESH_DATABASE_URL: ${{ secrets.DANK_FRESH_DATABASE_URL }}",
            "DANK_BACKUP_PASSPHRASE: ${{ secrets.DANK_BACKUP_PASSPHRASE }}",
            "supabase/postgres:17.6.1.136",
            "actions/upload-artifact@v4",
            "/*.dsbak",
            "retention-days: 7",
            "persist-credentials: false",
            "tools.backup_restore_dankshield_candidate",
        ):
            self.assertIn(fragment,wf)
        self.assertNotIn("DANK_SOURCE_DATABASE_URL",wf)
        self.assertNotIn("DISCLOUD_TOKEN",wf)
        self.assertNotIn("on:\n  schedule:",wf)
        self.assertNotIn("secrets.",ci)
        self.assertIn("pull_request:",ci)
