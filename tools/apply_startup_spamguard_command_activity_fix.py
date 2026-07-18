from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPAM = ROOT / "stoney_verify/spam_guard.py"
SERVICE_MODES = ROOT / "stoney_verify/startup_guards/setup_service_modes.py"
COMMANDS = ROOT / "stoney_verify/commands_ext/__init__.py"
SLASH = ROOT / "stoney_verify/startup_guards/slash_command_cleanup.py"
AUDIT = ROOT / "tools/audit_public_command_friction.py"
ACTIVITY = ROOT / "stoney_verify/members_new/activity_reconciliation.py"
DOC = ROOT / "docs/public-production-env.md"
TEST = ROOT / "tests/test_startup_spamguard_command_activity_cleanup.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# Spam Guard: default ON, persist missing rows automatically, preserve explicit
# saved Off values, and initialize brand-new guilds as soon as the bot joins.
# ---------------------------------------------------------------------------
spam = SPAM.read_text(encoding="utf-8")
spam = replace_once(
    spam,
    '        "enabled": False,\n        "mode": "timeout",',
    '        "enabled": True,\n        "mode": "timeout",',
    "Spam Guard default enabled",
)
spam = replace_once(
    spam,
    '    return _safe_bool((row or {}).get("spam_blocker_enabled", (row or {}).get("enabled")), False)',
    '    return _safe_bool((row or {}).get("spam_blocker_enabled", (row or {}).get("enabled")), True)',
    "Spam Guard row enabled diagnostic default",
)
old_ok_block = '''        if status == "ok":
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
new_ok_block = '''        if status == "ok":
            row_found = _settings_row_exists(row)
            normalized = _normalize_settings(gid, row or {})
            persisted = bool(row_found)
            effective_reason = reason

            # A readable table with no row is a normal first-run state, not a
            # reason to leave protection runtime-only. Create the canonical
            # default row immediately. New defaults are ON; explicit saved Off
            # rows still normalize to Off and are never overwritten here.
            if not row_found:
                try:
                    persisted, create_reason = await asyncio.to_thread(
                        _upsert_settings_sync,
                        _settings_payload_for_db(normalized),
                    )
                    effective_reason = "row_created_default_on" if persisted else f"{reason};{create_reason}"
                except Exception as e:
                    persisted = False
                    effective_reason = f"{reason};auto_create_exception:{type(e).__name__}"

            effective_row_found = bool(row_found or persisted)
            source = "db" if row_found else ("db-created" if persisted else "db-empty")
            _cache_runtime_settings(
                gid,
                normalized,
                source=source,
                persisted=persisted,
            )
            _record_settings_diag(
                gid,
                action="load",
                status="ok" if persisted or row_found else "not_persisted",
                reason=effective_reason,
                row_found=effective_row_found,
                persisted=persisted,
                source=source,
                row_enabled=(bool(normalized.get("enabled")) if effective_row_found else _settings_row_enabled_value(row)),
                row_mode=(str(normalized.get("mode")) if effective_row_found else _settings_row_mode_value(row)),
                effective_enabled=bool(normalized.get("enabled")),
                effective_mode=str(normalized.get("mode")),
            )
            if not row_found:
                if persisted:
                    _settings_debug_throttled(
                        f"settings-load-row-created:{gid}",
                        f"settings load guild={gid} row_missing=True auto_created=True enabled={bool(normalized.get('enabled'))} mode={normalized.get('mode')}",
                    )
                else:
                    _settings_debug_throttled(
                        f"settings-load-row-missing:{gid}",
                        f"settings load guild={gid} row_missing=True auto_created=False runtime_default_enabled={bool(normalized.get('enabled'))} mode={normalized.get('mode')} reason={effective_reason}",
                    )
            return normalized'''
spam = replace_once(spam, old_ok_block, new_ok_block, "Spam Guard missing-row auto-create")

join_marker = '''@bot.listen("on_ready")
async def _spam_guard_warm_settings_cache():'''
join_listener = '''@bot.listen("on_guild_join")
async def _spam_guard_initialize_new_guild(guild: discord.Guild):
    """Persist the safe default immediately when Dank Shield joins a server."""
    try:
        settings = await get_spam_settings(int(guild.id))
        cached = _cached_runtime_settings(int(guild.id)) or {}
        _debug(
            "new guild settings initialized "
            f"guild={guild.id} enabled={bool(settings.get('enabled'))} "
            f"persisted={bool(cached.get('__meta_persisted'))}"
        )
    except Exception as e:
        _debug(f"new guild settings initialization failed guild={getattr(guild, 'id', 0)} error={repr(e)}")


@bot.listen("on_ready")
async def _spam_guard_warm_settings_cache():'''
spam = replace_once(spam, join_marker, join_listener, "Spam Guard guild join initializer")
SPAM.write_text(spam, encoding="utf-8")


# ---------------------------------------------------------------------------
# /dank setup SpamGuard truth layer must share the same ON-by-default behavior.
# Service selection remains separate: users may choose not to include SpamGuard
# in setup checks while the runtime safety guard still defaults to active.
# ---------------------------------------------------------------------------
service = SERVICE_MODES.read_text(encoding="utf-8")
service = replace_once(
    service,
    '        "enabled": False,\n        "mode": "timeout",',
    '        "enabled": True,\n        "mode": "timeout",',
    "Setup SpamGuard fallback default",
)
service = replace_once(
    service,
    '    data["enabled"] = _safe_bool(data.get("enabled", data.get("spam_blocker_enabled")), False)',
    '    data["enabled"] = _safe_bool(data.get("enabled", data.get("spam_blocker_enabled")), True)',
    "Setup SpamGuard normalize default",
)
service = replace_once(
    service,
    '        guard_active=_safe_bool(settings.get("enabled", settings.get("spam_blocker_enabled")), False),',
    '        guard_active=_safe_bool(settings.get("enabled", settings.get("spam_blocker_enabled")), True),',
    "Setup SpamGuard actual state default",
)
service = replace_once(
    service,
    '        "spam_blocker_enabled": _safe_bool(settings.get("enabled", settings.get("spam_blocker_enabled")), False),',
    '        "spam_blocker_enabled": _safe_bool(settings.get("enabled", settings.get("spam_blocker_enabled")), True),',
    "Setup SpamGuard persistence default",
)
SERVICE_MODES.write_text(service, encoding="utf-8")


# ---------------------------------------------------------------------------
# Command surface: the current nine globals are intentional. Make that contract
# explicit, align /dank cleanup allowlists, and stop skip-log spam.
# ---------------------------------------------------------------------------
commands = COMMANDS.read_text(encoding="utf-8")
commands = replace_once(
    commands,
    '_COMMANDS_EXT_REGISTERED = False\nDEFAULT_COMMAND_PROFILE = "public"',
    '_COMMANDS_EXT_REGISTERED = False\n_RUNTIME_SKIP_LOGGED: set[str] = set()\nDEFAULT_COMMAND_PROFILE = "public"',
    "command skip log state",
)
old_allowed = '''_ALLOWED_DANK_CHILDREN = {
    "setup",
    "overview",
    "status",
    "diagnostics",
    "protection",
    "help",
    "commands",
    "cleanup",
    "members",
    "member-logs",
    "welcome",
    "roles",
    "modlog",
    "embed",
    "design",
}'''
new_allowed = '''_ALLOWED_DANK_CHILDREN = {
    "setup",
    "status",
    "diagnostics",
    "protection",
    "help",
    "commands",
    "cleanup",
    "members",
    "member-logs",
    "profile",
    "roles",
    "design",
}

_EXPECTED_PUBLIC_TOP_LEVEL_COMMANDS = {
    "dank",
    "mod",
    "ticket",
    "tickets",
    "ticket-intake",
    "ticket-category",
    "ticket-panel",
    "verify",
    "View Dank Profile",
}'''
commands = replace_once(commands, old_allowed, new_allowed, "canonical /dank allowed children")
helper_marker = '''def _remove_stale_top_level_commands(tree: Any, *, reason: str) -> list[str]:'''
helper = '''def _log_runtime_skip_once(key: str, message: str) -> None:
    clean = str(key or "default")
    if clean in _RUNTIME_SKIP_LOGGED:
        return
    _RUNTIME_SKIP_LOGGED.add(clean)
    print(message)


def _public_top_level_names(tree: Any) -> set[str]:
    try:
        return {
            str(getattr(command, "name", "")).strip()
            for command in list(tree.get_commands(guild=None) or [])
            if str(getattr(command, "name", "")).strip()
        }
    except Exception:
        return set()


def _validate_public_top_level_surface(tree: Any, profile: str) -> None:
    if not _public_profile_like(profile):
        return
    actual = _public_top_level_names(tree)
    missing = sorted(_EXPECTED_PUBLIC_TOP_LEVEL_COMMANDS - actual)
    unexpected = sorted(actual - _EXPECTED_PUBLIC_TOP_LEVEL_COMMANDS)
    if missing or unexpected:
        print(
            "⚠️ commands_ext public command surface mismatch "
            f"expected={len(_EXPECTED_PUBLIC_TOP_LEVEL_COMMANDS)} actual={len(actual)} "
            f"missing={missing} unexpected={unexpected}"
        )
        return
    print(
        "✅ commands_ext public command surface verified "
        f"global={len(actual)} names={sorted(actual)}"
    )


def _remove_stale_top_level_commands(tree: Any, *, reason: str) -> list[str]:'''
commands = replace_once(commands, helper_marker, helper, "command surface helpers")
commands = replace_once(
    commands,
    '        print("🧭 Dank Shield stale top-level command removal skipped; stable command surface active")',
    '        _log_runtime_skip_once("stale_top_level", "🧭 Dank Shield stale top-level command removal skipped; stable command surface active")',
    "top-level prune skip log once",
)
commands = replace_once(
    commands,
    '        print("🧭 Dank Shield /dank child prune skipped; stable command surface active")',
    '        _log_runtime_skip_once("dank_children", "🧭 Dank Shield /dank child prune skipped; stable command surface active")',
    "/dank prune skip log once",
)
commands = replace_once(
    commands,
    '''    final_global, final_guild = _tree_command_counts(tree)

    if final_global >= 95:''',
    '''    final_global, final_guild = _tree_command_counts(tree)
    _validate_public_top_level_surface(tree, profile)

    if final_global >= 95:''',
    "public top-level surface validation call",
)
COMMANDS.write_text(commands, encoding="utf-8")


slash = SLASH.read_text(encoding="utf-8")
old_slash_allowed = '''ALLOWED_DANK_CHILDREN = {
    "setup",
    "help",
    "commands",
    "spam",
    "cleanup",
    "members",
}'''
new_slash_allowed = '''ALLOWED_DANK_CHILDREN = {
    "setup",
    "status",
    "diagnostics",
    "protection",
    "help",
    "commands",
    "cleanup",
    "members",
    "member-logs",
    "profile",
    "roles",
    "design",
}'''
slash = replace_once(slash, old_slash_allowed, new_slash_allowed, "slash cleanup allowed children")
slash = replace_once(
    slash,
    'COMMAND_CLEANUP_EPOCH = "2026-06-14-verify-panel-command-v2"',
    'COMMAND_CLEANUP_EPOCH = "2026-07-18-public-surface-v3"',
    "command cleanup epoch",
)
SLASH.write_text(slash, encoding="utf-8")


audit = AUDIT.read_text(encoding="utf-8")
old_audit_allowed = '''EXPECTED_ALLOWED_DANK_CHILDREN = {
    "setup",
    "help",
    "commands",
    "spam",
    "cleanup",
    "members",
}'''
new_audit_allowed = '''EXPECTED_ALLOWED_DANK_CHILDREN = {
    "setup",
    "status",
    "diagnostics",
    "protection",
    "help",
    "commands",
    "cleanup",
    "members",
    "member-logs",
    "profile",
    "roles",
    "design",
}'''
audit = replace_once(audit, old_audit_allowed, new_audit_allowed, "public command audit allowed children")
AUDIT.write_text(audit, encoding="utf-8")


# ---------------------------------------------------------------------------
# Activity coverage: permissions cannot be safely self-granted across private
# channels, so remain fail-closed but report every actionable permission issue
# (bounded) instead of only the first channel.
# ---------------------------------------------------------------------------
activity = ACTIVITY.read_text(encoding="utf-8")
start = activity.index("def audit_guild_activity_scope(\n")
end = activity.index("def _thread_is_relevant(\n", start)
new_scope = '''def audit_guild_activity_scope(
    guild: discord.Guild,
) -> str:
    """Return a bounded, actionable explanation when durable coverage is incomplete."""
    member = getattr(guild, "me", None)

    if not isinstance(member, discord.Member):
        return (
            "Could not resolve Dank Shield's guild member permissions. "
            "Inactivity cleanup stays review-only until permissions can be verified."
        )

    issues: list[str] = []

    for channel in list(getattr(guild, "channels", []) or []):
        has_history = callable(getattr(channel, "history", None))
        is_thread_parent = isinstance(channel, (discord.TextChannel, discord.ForumChannel))

        if (has_history or is_thread_parent) and not _can_read_history(channel, member):
            issues.append(
                f"#{getattr(channel, 'name', channel.id)} ({int(channel.id)}): "
                "grant View Channel + Read Message History"
            )

        if isinstance(channel, discord.TextChannel):
            permissions = _permissions_for(channel, member)
            manage_threads = bool(getattr(permissions, "manage_threads", False))
            if not manage_threads and _private_threads_may_exist(channel, guild):
                issues.append(
                    f"#{channel.name} ({int(channel.id)}): grant Manage Threads "
                    "or disable private-thread creation"
                )

    for thread in list(getattr(guild, "threads", []) or []):
        if not _can_read_history(thread, member):
            issues.append(
                f"thread {getattr(thread, 'name', thread.id)} ({int(thread.id)}): "
                "grant View Channel + Read Message History"
            )

    if not issues:
        return ""

    # Deduplicate while preserving Discord's visible order, then bound startup
    # and report text so one large server cannot flood logs/interactions.
    unique = list(dict.fromkeys(issues))
    shown = unique[:5]
    extra = len(unique) - len(shown)
    detail = "; ".join(shown)
    if extra > 0:
        detail += f"; +{extra} more channel/thread permission issue(s)"

    return (
        "Activity coverage is incomplete, so inactivity cleanup stays review-only. "
        "Fix Dank Shield's channel permissions: " + detail + "."
    )


'''
activity = activity[:start] + new_scope + activity[end:]
ACTIVITY.write_text(activity, encoding="utf-8")


# ---------------------------------------------------------------------------
# Production docs: nine globals are intentional; old seven-command expectation
# and obsolete setup instructions were stale.
# ---------------------------------------------------------------------------
doc = DOC.read_text(encoding="utf-8")
doc = replace_once(
    doc,
    "commands_ext registration complete. final_global=7 final_guild=0 profile=public",
    "commands_ext registration complete. final_global=9 final_guild=0 profile=public",
    "production command count docs",
)
doc = replace_once(
    doc,
    '''## Public setup flow

For each Discord server:

1. Invite the bot with the required permissions.
2. Run `/dank setup`.
3. Choose create-missing-items or existing-server setup.
4. Verify ticket panel, verification channel, modlog, roles, and spam guard from setup health.

Never fix a public server by putting that server's IDs into Discloud env. That creates cross-server leakage risk.''',
    '''## Intentional public command surface

A healthy public build currently registers **9 global application-command surfaces**:

- `/dank`
- `/mod`
- `/ticket`
- `/tickets`
- `/ticket-intake`
- `/ticket-category`
- `/ticket-panel`
- `/verify`
- `View Dank Profile` (user context menu)

The count includes the context-menu command. A change from this list should be reviewed deliberately rather than treated as harmless command drift.

## Public setup flow

For each Discord server:

1. Invite the bot with the required permissions.
2. Run `/dank setup`.
3. Press **Start Setup**, choose what the server should use, then follow **Set Up This Step** until the automatic setup check is ready.
4. Use **Test & Launch** after required setup passes.
5. Spam Guard runtime protection defaults to **On** for new or missing settings rows. An owner may still explicitly turn it Off.
6. Inactivity cleanup remains review-only whenever Dank Shield cannot read required channel history; fix the exact permissions reported by the activity coverage warning.

Never fix a public server by putting that server's IDs into Discloud env. That creates cross-server leakage risk.''',
    "production setup flow docs",
)
DOC.write_text(doc, encoding="utf-8")


# ---------------------------------------------------------------------------
# Permanent regression coverage.
# ---------------------------------------------------------------------------
TEST.write_text('''from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPAM = (ROOT / "stoney_verify/spam_guard.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "stoney_verify/startup_guards/setup_service_modes.py").read_text(encoding="utf-8")
COMMANDS = (ROOT / "stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")
SLASH = (ROOT / "stoney_verify/startup_guards/slash_command_cleanup.py").read_text(encoding="utf-8")
ACTIVITY = (ROOT / "stoney_verify/members_new/activity_reconciliation.py").read_text(encoding="utf-8")
DOC = (ROOT / "docs/public-production-env.md").read_text(encoding="utf-8")


def _function_source(text: str, name: str) -> str:
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"function {name} not found")


def _literal_set(text: str, name: str) -> set[str]:
    tree = ast.parse(text)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            value = node.value
            if isinstance(value, (ast.Set, ast.Tuple, ast.List)):
                return {
                    str(item.value)
                    for item in value.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                }
    raise AssertionError(f"literal set {name} not found")


def test_spam_guard_defaults_on_and_missing_rows_are_persisted() -> None:
    defaults = _function_source(SPAM, "_default_settings")
    loader = _function_source(SPAM, "get_spam_settings")
    assert '"enabled": True' in defaults
    assert "row_created_default_on" in loader
    assert "_upsert_settings_sync" in loader
    assert 'source = "db" if row_found else ("db-created" if persisted else "db-empty")' in loader
    assert '@bot.listen("on_guild_join")' in SPAM
    assert "_spam_guard_initialize_new_guild" in SPAM


def test_setup_spam_guard_fallback_matches_runtime_default() -> None:
    defaults = _function_source(SERVICE, "_default_spam_settings")
    normalize = _function_source(SERVICE, "_normalize_spam_settings")
    actual = _function_source(SERVICE, "_load_spam_actual_state")
    assert '"enabled": True' in defaults
    assert 'data.get("spam_blocker_enabled")), True)' in normalize
    assert 'settings.get("spam_blocker_enabled")), True)' in actual


def test_public_command_surface_is_explicitly_nine_and_prune_logs_once() -> None:
    expected = {
        "dank", "mod", "ticket", "tickets", "ticket-intake",
        "ticket-category", "ticket-panel", "verify", "View Dank Profile",
    }
    assert _literal_set(COMMANDS, "_EXPECTED_PUBLIC_TOP_LEVEL_COMMANDS") == expected
    assert "_validate_public_top_level_surface(tree, profile)" in COMMANDS
    assert "_log_runtime_skip_once" in COMMANDS
    assert '_log_runtime_skip_once("dank_children"' in COMMANDS
    assert COMMANDS.count("/dank child prune skipped; stable command surface active") == 1


def test_cleanup_allowlists_match_real_public_dank_surface() -> None:
    expected = {
        "setup", "status", "diagnostics", "protection", "help", "commands",
        "cleanup", "members", "member-logs", "profile", "roles", "design",
    }
    assert _literal_set(COMMANDS, "_ALLOWED_DANK_CHILDREN") == expected
    assert _literal_set(SLASH, "ALLOWED_DANK_CHILDREN") == expected
    assert 'COMMAND_CLEANUP_EPOCH = "2026-07-18-public-surface-v3"' in SLASH


def test_activity_permission_failure_is_actionable_and_bounded() -> None:
    scope = _function_source(ACTIVITY, "audit_guild_activity_scope")
    assert "inactivity cleanup stays review-only" in scope
    assert "View Channel + Read Message History" in scope
    assert "Manage Threads" in scope
    assert "unique[:5]" in scope
    assert "+{extra} more channel/thread permission issue(s)" in scope


def test_production_docs_match_current_public_runtime() -> None:
    assert "final_global=9 final_guild=0 profile=public" in DOC
    assert "View Dank Profile" in DOC
    assert "Spam Guard runtime protection defaults to **On**" in DOC
    assert "Set Up This Step" in DOC
''', encoding="utf-8")


for path in (SPAM, SERVICE_MODES, COMMANDS, SLASH, AUDIT, ACTIVITY, TEST):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")

print("PASS: applied startup, Spam Guard, command surface, and activity health fixes")
