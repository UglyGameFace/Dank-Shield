from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPAM = ROOT / "stoney_verify/spam_guard.py"
SERVICE = ROOT / "stoney_verify/startup_guards/setup_service_modes.py"
COMMANDS = ROOT / "stoney_verify/commands_ext/__init__.py"
ACTIVITY = ROOT / "stoney_verify/members_new/activity_reconciliation.py"
PROD_DOC = ROOT / "docs/public-production-env.md"
LAUNCH_DOC = ROOT / "docs/PUBLIC_LAUNCH_CHECKLIST.md"
TEST_SPAM = ROOT / "tests/test_spam_guard_default_enabled_static.py"
TEST_LOGS = ROOT / "tests/test_command_prune_log_hygiene_static.py"
TEST_ACTIVITY = ROOT / "tests/test_activity_scope_relevance_static.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# Spam Guard: ON by default, and missing DB rows are created automatically.
# Existing explicit rows still win, including an intentional enabled=False.
# ---------------------------------------------------------------------------
spam = SPAM.read_text(encoding="utf-8")
spam = replace_once(
    spam,
    '        "enabled": False,\n        "mode": "timeout",',
    '        "enabled": True,\n        "mode": "timeout",',
    "SpamGuard runtime default",
)

persist_helper = '''async def _persist_missing_default_settings(
    guild_id: int,
    settings: Dict[str, Any],
) -> Tuple[Dict[str, Any], bool, str]:
    """Persist the secure default when a guild has no settings row yet.

    Missing rows are not an opt-out. New guilds and older guilds that never
    saved Spam Guard settings start protected. An existing row with
    spam_blocker_enabled=false remains an explicit owner choice and is never
    overwritten by this helper.
    """

    gid = int(guild_id)
    normalized = _normalize_settings(gid, settings)
    persisted = False
    reason = "settings_table_unavailable" if _SETTINGS_TABLE_AVAILABLE is False else "not_attempted"

    if _SETTINGS_TABLE_AVAILABLE is not False:
        try:
            persisted, reason = await asyncio.to_thread(
                _upsert_settings_sync,
                _settings_payload_for_db(normalized),
            )
        except Exception as exc:
            persisted = False
            reason = f"default_persist_exception:{type(exc).__name__}"

    _cache_runtime_settings(
        gid,
        normalized,
        source="db-default-created" if persisted else "runtime-default",
        persisted=persisted,
    )
    _record_settings_diag(
        gid,
        action="load",
        status="ok" if persisted else "default_runtime_only",
        reason="row_missing_default_created" if persisted else reason,
        row_found=bool(persisted),
        row_created=bool(persisted),
        source="db-default-created" if persisted else "runtime-default",
        effective_enabled=bool(normalized.get("enabled")),
        effective_mode=str(normalized.get("mode")),
    )
    _settings_debug_throttled(
        f"settings-load-row-missing-default:{gid}",
        "settings load "
        f"guild={gid} row_missing=True default_enabled={bool(normalized.get('enabled'))} "
        f"persisted={persisted} reason={reason}",
    )
    return normalized, persisted, reason
'''
marker = 'async def get_spam_settings(guild_id: int) -> Dict[str, Any]:\n'
if persist_helper.splitlines()[0] not in spam:
    spam = spam.replace(marker, persist_helper + "\n\n" + marker, 1)

old_ok = '''        if status == "ok":
            row_found = _settings_row_exists(row)
            normalized = _normalize_settings(gid, row or {})
            source = "db" if row_found else "db-empty"
            _cache_runtime_settings(
                gid,
                normalized,
                source=source,
                persisted=row_found,
            )
            _record_settings_diag(
                gid,
                action="load",
                status="ok",
                reason=reason,
                row_found=row_found,
                source=source,
                row_enabled=_settings_row_enabled_value(row),
                row_mode=_settings_row_mode_value(row),
                effective_enabled=bool(normalized.get("enabled")),
                effective_mode=str(normalized.get("mode")),
            )
            if not row_found:
                _settings_debug_throttled(
                    f"settings-load-row-missing:{gid}",
                    f"settings load guild={gid} status=ok row_found=False fallback=defaults enabled={bool(normalized.get('enabled'))} mode={normalized.get('mode')} reason={reason}",
                )
            return normalized'''
new_ok = '''        if status == "ok":
            row_found = _settings_row_exists(row)
            if not row_found:
                normalized_default = _default_settings(gid)
                normalized, _persisted, _persist_reason = await _persist_missing_default_settings(
                    gid,
                    normalized_default,
                )
                return normalized

            normalized = _normalize_settings(gid, row or {})
            _cache_runtime_settings(
                gid,
                normalized,
                source="db",
                persisted=True,
            )
            _record_settings_diag(
                gid,
                action="load",
                status="ok",
                reason=reason,
                row_found=True,
                source="db",
                row_enabled=_settings_row_enabled_value(row),
                row_mode=_settings_row_mode_value(row),
                effective_enabled=bool(normalized.get("enabled")),
                effective_mode=str(normalized.get("mode")),
            )
            return normalized'''
spam = replace_once(spam, old_ok, new_ok, "missing SpamGuard row persistence")
SPAM.write_text(spam, encoding="utf-8")

service = SERVICE.read_text(encoding="utf-8")
service = replace_once(
    service,
    '        return ServiceState(True, False, False, False, False, "defaults")',
    '        return ServiceState(True, False, False, True, True, "defaults")',
    "setup service state missing-config default",
)
service = replace_once(
    service,
    '    spamguard = _safe_bool(_cfg_value(cfg, "spam_guard_enabled", False), False)',
    '    spamguard = _safe_bool(_cfg_value(cfg, "spam_guard_enabled", True), True)',
    "setup service state SpamGuard default",
)
service = replace_once(
    service,
    '        "enabled": False,\n        "mode": "timeout",',
    '        "enabled": True,\n        "mode": "timeout",',
    "setup SpamGuard settings default",
)
service = replace_once(
    service,
    '    data["enabled"] = _safe_bool(data.get("enabled", data.get("spam_blocker_enabled")), False)',
    '    data["enabled"] = _safe_bool(data.get("enabled", data.get("spam_blocker_enabled")), True)',
    "setup SpamGuard normalizer default",
)
SERVICE.write_text(service, encoding="utf-8")


# ---------------------------------------------------------------------------
# Command registration: runtime pruning is intentionally disabled in public
# production. Log that fact once, not once after every module registration.
# ---------------------------------------------------------------------------
commands = COMMANDS.read_text(encoding="utf-8")
commands = replace_once(
    commands,
    '_COMMANDS_EXT_REGISTERED = False\nDEFAULT_COMMAND_PROFILE = "public"',
    '_COMMANDS_EXT_REGISTERED = False\n_RUNTIME_PRUNE_DISABLED_NOTICE_LOGGED = False\nDEFAULT_COMMAND_PROFILE = "public"',
    "prune notice state",
)
notice_helper = '''def _log_runtime_prune_disabled_once() -> None:
    global _RUNTIME_PRUNE_DISABLED_NOTICE_LOGGED
    if _RUNTIME_PRUNE_DISABLED_NOTICE_LOGGED:
        return
    _RUNTIME_PRUNE_DISABLED_NOTICE_LOGGED = True
    print("🧭 Dank Shield runtime command pruning disabled; stable public command surface active")
'''
commands = commands.replace(
    'def _remove_stale_top_level_commands(tree: Any, *, reason: str) -> list[str]:\n',
    notice_helper + '\n\ndef _remove_stale_top_level_commands(tree: Any, *, reason: str) -> list[str]:\n',
    1,
)
commands = replace_once(
    commands,
    '    if _runtime_command_prune_disabled():\n        print("🧭 Dank Shield stale top-level command removal skipped; stable command surface active")\n        return []',
    '    if _runtime_command_prune_disabled():\n        _log_runtime_prune_disabled_once()\n        return []',
    "stale command prune notice",
)
commands = replace_once(
    commands,
    '    if _runtime_command_prune_disabled():\n        print("🧭 Dank Shield /dank child prune skipped; stable command surface active")\n        return []',
    '    if _runtime_command_prune_disabled():\n        _log_runtime_prune_disabled_once()\n        return []',
    "child prune notice",
)
COMMANDS.write_text(commands, encoding="utf-8")


# ---------------------------------------------------------------------------
# Activity continuity: fail closed only for channels that ordinary members can
# actually use. Staff-only/moderator-only channels no longer invalidate the
# entire member-activity proof window. Member-visible unreadable channels still
# block continuity and report the exact permission problem.
# ---------------------------------------------------------------------------
activity = ACTIVITY.read_text(encoding="utf-8")
relevance_helpers = '''def _role_is_staffish_for_activity(role: Any) -> bool:
    permissions = getattr(role, "permissions", None)
    if permissions is None:
        return False
    return any(
        bool(getattr(permissions, name, False))
        for name in (
            "administrator",
            "manage_guild",
            "manage_channels",
            "manage_messages",
            "moderate_members",
            "kick_members",
            "ban_members",
        )
    )


def _channel_is_member_relevant(
    channel: Any,
    guild: discord.Guild,
) -> bool:
    """Return True when a non-staff server role can view this channel.

    Activity continuity is used to judge ordinary member inactivity. A private
    moderator/staff channel that the bot cannot read should not invalidate the
    entire guild's member proof window. A channel visible to @everyone or any
    non-staff role remains fail-closed and must be readable by Dank Shield.
    """

    default_role = getattr(guild, "default_role", None)
    if default_role is not None:
        try:
            if bool(getattr(channel.permissions_for(default_role), "view_channel", False)):
                return True
        except Exception:
            pass

    for role in list(getattr(guild, "roles", []) or []):
        try:
            if role is default_role or bool(getattr(role, "managed", False)):
                continue
            if _role_is_staffish_for_activity(role):
                continue
            if bool(getattr(channel.permissions_for(role), "view_channel", False)):
                return True
        except Exception:
            continue

    return False
'''
activity = activity.replace(
    'def audit_guild_activity_scope(\n    guild: discord.Guild,\n) -> str:\n',
    relevance_helpers + '\n\ndef audit_guild_activity_scope(\n    guild: discord.Guild,\n) -> str:\n',
    1,
)
activity = replace_once(
    activity,
    '''        if (
            (has_history or is_thread_parent)
            and not _can_read_history(channel, member)
        ):
            return (
                "Activity coverage is incomplete because Dank "
                "Shield cannot view/read history in "
                f"#{getattr(channel, 'name', channel.id)} "
                f"({int(channel.id)})."
            )''',
    '''        if (
            (has_history or is_thread_parent)
            and _channel_is_member_relevant(channel, guild)
            and not _can_read_history(channel, member)
        ):
            return (
                "Activity coverage is incomplete because Dank Shield cannot "
                "View Channel + Read Message History in member-visible channel "
                f"#{getattr(channel, 'name', channel.id)} ({int(channel.id)}). "
                "Grant those two permissions to Dank Shield for accurate inactivity checks."
            )''',
    "member-relevant unreadable channel scope",
)
activity = replace_once(
    activity,
    '''            if (
                not manage_threads
                and _private_threads_may_exist(
                    channel,
                    guild,
                )
            ):''',
    '''            if (
                _channel_is_member_relevant(channel, guild)
                and not manage_threads
                and _private_threads_may_exist(
                    channel,
                    guild,
                )
            ):''',
    "member-relevant private thread scope",
)
activity = replace_once(
    activity,
    '''    for thread in list(
        getattr(guild, "threads", []) or []
    ):
        if not _can_read_history(thread, member):''',
    '''    for thread in list(
        getattr(guild, "threads", []) or []
    ):
        parent = getattr(thread, "parent", None)
        relevant = _channel_is_member_relevant(parent, guild) if parent is not None else True
        if relevant and not _can_read_history(thread, member):''',
    "member-relevant active thread scope",
)
ACTIVITY.write_text(activity, encoding="utf-8")


# ---------------------------------------------------------------------------
# The runtime's 9 global app commands are intentional: 8 slash commands plus
# the View Dank Profile user context command. Fix stale docs instead of deleting
# valid public product commands.
# ---------------------------------------------------------------------------
prod = PROD_DOC.read_text(encoding="utf-8")
prod = prod.replace(
    'commands_ext registration complete. final_global=7 final_guild=0 profile=public',
    'commands_ext registration complete. final_global=9 final_guild=0 profile=public',
)
PROD_DOC.write_text(prod, encoding="utf-8")

launch = LAUNCH_DOC.read_text(encoding="utf-8")
launch = launch.replace(
    '''/ticket
/tickets
/ticket-category
/ticket-panel
/verify''',
    '''/ticket
/tickets
/ticket-intake
/ticket-category
/ticket-panel
/verify

User context command:
View Dank Profile''',
)
launch = launch.replace(
    "local global commands: ['dank', 'mod', 'ticket', 'tickets', 'ticket-category', 'ticket-panel', 'verify']",
    "local global commands: ['dank', 'mod', 'ticket', 'tickets', 'ticket-intake', 'ticket-category', 'ticket-panel', 'verify', 'View Dank Profile']",
)
LAUNCH_DOC.write_text(launch, encoding="utf-8")


# ---------------------------------------------------------------------------
# Permanent regression contracts.
# ---------------------------------------------------------------------------
TEST_SPAM.write_text('''from pathlib import Path

SPAM = Path("stoney_verify/spam_guard.py").read_text(encoding="utf-8")
SERVICE = Path("stoney_verify/startup_guards/setup_service_modes.py").read_text(encoding="utf-8")


def test_runtime_spamguard_defaults_on():
    start = SPAM.index("def _default_settings(")
    end = SPAM.index("def _normalize_settings(", start)
    block = SPAM[start:end]
    assert '"enabled": True' in block
    assert '"enabled": False' not in block


def test_missing_settings_rows_are_persisted_as_enabled_defaults():
    assert "async def _persist_missing_default_settings(" in SPAM
    assert "row_missing_default_created" in SPAM
    assert "_settings_payload_for_db(normalized)" in SPAM
    assert "if not row_found:" in SPAM
    assert "await _persist_missing_default_settings(" in SPAM


def test_setup_service_defaults_include_spamguard():
    assert 'ServiceState(True, False, False, True, True, "defaults")' in SERVICE
    assert '_cfg_value(cfg, "spam_guard_enabled", True), True' in SERVICE
    assert '"enabled": True' in SERVICE


def test_existing_explicit_disabled_rows_are_not_overwritten():
    load_start = SPAM.index('if status == "ok":')
    load_end = SPAM.index('if status == "missing_table":', load_start)
    block = SPAM[load_start:load_end]
    assert "if not row_found:" in block
    assert 'source="db"' in block
    assert "_normalize_settings(gid, row or {})" in block
''', encoding="utf-8")

TEST_LOGS.write_text('''from pathlib import Path

SOURCE = Path("stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")


def test_runtime_prune_disabled_notice_is_emitted_once():
    assert "_RUNTIME_PRUNE_DISABLED_NOTICE_LOGGED = False" in SOURCE
    assert "def _log_runtime_prune_disabled_once()" in SOURCE
    assert SOURCE.count("_log_runtime_prune_disabled_once()") >= 3
    assert "/dank child prune skipped; stable command surface active" not in SOURCE
    assert "stale top-level command removal skipped; stable command surface active" not in SOURCE
''', encoding="utf-8")

TEST_ACTIVITY.write_text('''from pathlib import Path

SOURCE = Path("stoney_verify/members_new/activity_reconciliation.py").read_text(encoding="utf-8")


def test_activity_scope_only_fails_closed_for_member_relevant_channels():
    assert "def _channel_is_member_relevant(" in SOURCE
    assert "def _role_is_staffish_for_activity(" in SOURCE
    assert "_channel_is_member_relevant(channel, guild)" in SOURCE
    assert "member-visible channel" in SOURCE
    assert "Grant those two permissions to Dank Shield" in SOURCE


def test_staff_only_channels_do_not_invalidate_ordinary_member_proof_window():
    helper_start = SOURCE.index("def _channel_is_member_relevant(")
    helper_end = SOURCE.index("def audit_guild_activity_scope(", helper_start)
    helper = SOURCE[helper_start:helper_end]
    for permission in ("administrator", "manage_guild", "manage_channels", "manage_messages", "moderate_members"):
        assert permission in helper


def test_member_relevant_private_threads_still_fail_closed():
    assert "_channel_is_member_relevant(channel, guild)" in SOURCE
    assert "_private_threads_may_exist(" in SOURCE
    assert "relevant and not _can_read_history(thread, member)" in SOURCE
''', encoding="utf-8")

for path in (SPAM, SERVICE, COMMANDS, ACTIVITY, TEST_SPAM, TEST_LOGS, TEST_ACTIVITY):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")

print("PASS: applied runtime startup hygiene and SpamGuard secure defaults")
