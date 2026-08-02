from __future__ import annotations

"""One-shot branch patcher used because the repository connector writes whole files.

The accompanying temporary workflow runs this script once, commits the precise
edits, and removes both temporary files in the same commit.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    invite_path = ROOT / "stoney_verify" / "invite_policy_engine.py"
    replace_once(
        invite_path,
        "import discord\n\nINVITE_RE = re.compile(",
        "import discord\n\nfrom . import durable_invite_stats\n\nINVITE_RE = re.compile(",
        "invite policy durable stats import",
    )

    replace_once(
        invite_path,
        '''        try:\n            from stoney_verify.security_stats import record_security_event\n\n            if message.guild is not None:\n                await record_security_event(int(message.guild.id), invites_blocked=1)\n        except Exception:\n            # Statistics must never turn a successful moderation delete into a failure.\n            pass\n''',
        '''        try:\n            stats_result = await durable_invite_stats.record_deleted_invite_decision(\n                message,\n                decision,\n            )\n            if stats_result.queued:\n                print(\n                    "⚠️ invite_policy stats event queued for durable retry "\n                    f"guild={decision.guild_id} event={stats_result.event_hash[:12]} "\n                    f"blocked={stats_result.blocked_count}"\n                )\n        except Exception as exc:\n            # A successful moderation delete stays successful, but stats failures\n            # are never silent and the durable service owns retry/reconciliation.\n            print(\n                "⚠️ invite_policy durable stats recording failed "\n                f"guild={decision.guild_id} message={getattr(message, 'id', 0)} "\n                f"error={type(exc).__name__}: {str(exc)[:220]}"\n            )\n''',
        "invite policy delete stats block",
    )

    bootstrap_path = ROOT / "stoney_verify" / "startup_guards" / "auto_schema_bootstrap.py"
    replace_once(
        bootstrap_path,
        '''_BOOTSTRAP_MIGRATION_FILES = (\n    "20260711_member_activity_truth_ledger.sql",\n)''',
        '''_BOOTSTRAP_MIGRATION_FILES = (\n    "20260711_member_activity_truth_ledger.sql",\n    "202608020001_durable_invite_stats.sql",\n)''',
        "auto schema durable invite stats migration",
    )

    # Remove the one-shot machinery before committing the real implementation.
    (ROOT / "tools" / "apply_durable_invite_stats_patch.py").unlink(missing_ok=True)
    (ROOT / ".github" / "workflows" / "apply-durable-invite-stats-patch.yml").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
