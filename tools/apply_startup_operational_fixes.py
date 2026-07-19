from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly 1 match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---------------------------------------------------------------------------
# SpamGuard: ON by default, auto-persist missing settings rows, preserve explicit OFF.
# ---------------------------------------------------------------------------
spam = ROOT / "stoney_verify/spam_guard.py"
replace_once(
    spam,
    '''def _default_settings(guild_id: int) -> Dict[str, Any]:
    return {
        "guild_id": str(guild_id),
        "enabled": False,''',
    '''def _default_settings(guild_id: int) -> Dict[str, Any]:
    return {
        "guild_id": str(guild_id),
        "enabled": True,''',
    "SpamGuard runtime default",
)

replace_once(
    spam,
    '''        if status == "ok":
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
            return normalized''',
    '''        if status == "ok":
            row_found = _settings_row_exists(row)
            normalized = _normalize_settings(gid, row or {})
            persisted = bool(row_found)
            bootstrap_reason = "row_found" if row_found else "not_attempted"

            # New guilds and legacy guilds with no security-settings row should
            # immediately receive a durable default row. Existing saved rows are
            # never changed here, so an owner who explicitly disabled SpamGuard
            # stays disabled.
            if not row_found:
                try:
                    persisted, bootstrap_reason = await asyncio.to_thread(
                        _upsert_settings_sync,
                        _settings_payload_for_db(normalized),
                    )
                except Exception as exc:
                    persisted = False
                    bootstrap_reason = f"bootstrap_exception:{type(exc).__name__}"

            effective_row_found = bool(row_found or persisted)
            source = "db" if row_found else ("db-bootstrap" if persisted else "defaults")
            _cache_runtime_settings(
                gid,
                normalized,
                source=source,
                persisted=persisted,
            )
            _record_settings_diag(
                gid,
                action="load",
                status="ok",
                reason=(bootstrap_reason if not row_found else reason),
                row_found=effective_row_found,
                original_row_found=row_found,
                persisted=persisted,
                source=source,
                row_enabled=(_settings_row_enabled_value(row) if row_found else bool(normalized.get("enabled"))),
                row_mode=(_settings_row_mode_value(row) if row_found else str(normalized.get("mode"))),
                effective_enabled=bool(normalized.get("enabled")),
                effective_mode=str(normalized.get("mode")),
            )
            if not row_found:
                if persisted:
                    _settings_debug_throttled(
                        f"settings-bootstrap-row:{gid}",
                        f"settings bootstrap guild={gid} created_default_row=True enabled={bool(normalized.get('enabled'))} mode={normalized.get('mode')} reason={bootstrap_reason}",
                    )
                else:
                    _settings_debug_throttled(
                        f"settings-load-row-missing:{gid}",
                        f"settings load guild={gid} status=ok row_found=False fallback=defaults enabled={bool(normalized.get('enabled'))} mode={normalized.get('mode')} reason={bootstrap_reason}",
                    )
            return normalized''',
    "SpamGuard missing-row bootstrap",
)


# ---------------------------------------------------------------------------
# Setup service defaults: SpamGuard is selected/active by default for normal setup.
# Explicit "... only" custom presets may still turn it off.
# ---------------------------------------------------------------------------
service_modes = ROOT / "stoney_verify/startup_guards/setup_service_modes.py"
replace_once(
    service_modes,
    '        return ServiceState(True, False, False, False, False, "defaults")',
    '        return ServiceState(True, False, False, True, True, "defaults")',
    "service state no-config default",
)
replace_once(
    service_modes,
    '    spamguard = _safe_bool(_cfg_value(cfg, "spam_guard_enabled", False), False)',
    '    spamguard = _safe_bool(_cfg_value(cfg, "spam_guard_enabled", True), True)',
    "service state missing-key default",
)
replace_once(
    service_modes,
    '''def _default_spam_settings(guild_id: int) -> dict[str, Any]:
    return {
        "guild_id": str(int(guild_id)),
        "enabled": False,''',
    '''def _default_spam_settings(guild_id: int) -> dict[str, Any]:
    return {
        "guild_id": str(int(guild_id)),
        "enabled": True,''',
    "setup service SpamGuard default",
)

fresh = ROOT / "stoney_verify/commands_ext/public_setup_fresh_choice.py"
fresh_text = fresh.read_text(encoding="utf-8")
for old, new in (
    ('return {"tickets_enabled": True, "verification_enabled": False, "voice_verification_enabled": False, "spam_guard_enabled": False, "moderation_enabled": True}', 'return {"tickets_enabled": True, "verification_enabled": False, "voice_verification_enabled": False, "spam_guard_enabled": True, "moderation_enabled": True}'),
    ('return {"tickets_enabled": False, "verification_enabled": True, "voice_verification_enabled": False, "spam_guard_enabled": False, "moderation_enabled": False}', 'return {"tickets_enabled": False, "verification_enabled": True, "voice_verification_enabled": False, "spam_guard_enabled": True, "moderation_enabled": True}'),
    ('return {"tickets_enabled": True, "verification_enabled": True, "voice_verification_enabled": True, "spam_guard_enabled": False, "moderation_enabled": True}', 'return {"tickets_enabled": True, "verification_enabled": True, "voice_verification_enabled": True, "spam_guard_enabled": True, "moderation_enabled": True}'),
    ('return {"tickets_enabled": True, "verification_enabled": True, "voice_verification_enabled": bool(choice.needs_voice), "spam_guard_enabled": False, "moderation_enabled": True}', 'return {"tickets_enabled": True, "verification_enabled": True, "voice_verification_enabled": bool(choice.needs_voice), "spam_guard_enabled": True, "moderation_enabled": True}'),
):
    if old not in fresh_text:
        raise SystemExit(f"setup choice default marker missing: {old}")
    fresh_text = fresh_text.replace(old, new)
fresh.write_text(fresh_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Command registration log hygiene: skip notices once per startup, not per module.
# ---------------------------------------------------------------------------
commands = ROOT / "stoney_verify/commands_ext/__init__.py"
replace_once(
    commands,
    '''_COMMANDS_EXT_REGISTERED = False
DEFAULT_COMMAND_PROFILE = "public"''',
    '''_COMMANDS_EXT_REGISTERED = False
_STALE_PRUNE_SKIP_LOGGED = False
_CHILD_PRUNE_SKIP_LOGGED = False
DEFAULT_COMMAND_PROFILE = "public"''',
    "command prune log flags",
)
replace_once(
    commands,
    '''def _remove_stale_top_level_commands(tree: Any, *, reason: str) -> list[str]:
    if _runtime_command_prune_disabled():
        print("🧭 Dank Shield stale top-level command removal skipped; stable command surface active")
        return []''',
    '''def _remove_stale_top_level_commands(tree: Any, *, reason: str) -> list[str]:
    global _STALE_PRUNE_SKIP_LOGGED
    if _runtime_command_prune_disabled():
        if not _STALE_PRUNE_SKIP_LOGGED:
            print("🧭 Dank Shield stale top-level command removal skipped; stable command surface active")
            _STALE_PRUNE_SKIP_LOGGED = True
        return []''',
    "stale prune once-per-startup log",
)
replace_once(
    commands,
    '''def _prune_public_dank_children(*, profile: str, reason: str) -> list[str]:
    if _runtime_command_prune_disabled():
        print("🧭 Dank Shield /dank child prune skipped; stable command surface active")
        return []''',
    '''def _prune_public_dank_children(*, profile: str, reason: str) -> list[str]:
    global _CHILD_PRUNE_SKIP_LOGGED
    if _runtime_command_prune_disabled():
        if not _CHILD_PRUNE_SKIP_LOGGED:
            print("🧭 Dank Shield /dank child prune skipped; stable command surface active")
            _CHILD_PRUNE_SKIP_LOGGED = True
        return []''',
    "child prune once-per-startup log",
)


# ---------------------------------------------------------------------------
# Permission repair: include bot-only activity coverage repairs.
# ---------------------------------------------------------------------------
permission_service = ROOT / "stoney_verify/setup_permission_repair_services.py"
permission_text = permission_service.read_text(encoding="utf-8")
marker = 'async def _build_expanded_targets(guild: discord.Guild) -> tuple[list[Any], list[str], list[str], list[str]]:'
if marker not in permission_text:
    raise SystemExit("permission repair insertion marker missing")
helper = '''def _activity_coverage_channel(channel: Any) -> bool:
    return isinstance(channel, (discord.TextChannel, discord.ForumChannel)) or callable(getattr(channel, "history", None))


def _activity_coverage_expected(channel: Any) -> discord.PermissionOverwrite:
    expected = discord.PermissionOverwrite(
        view_channel=True,
        read_message_history=True,
    )
    if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
        expected.manage_threads = True
    return expected


def _activity_coverage_needs_repair(channel: Any, me: discord.Member) -> bool:
    try:
        permissions = channel.permissions_for(me)
    except Exception:
        return True
    if not bool(getattr(permissions, "view_channel", False)):
        return True
    if not bool(getattr(permissions, "read_message_history", False)):
        return True
    if isinstance(channel, (discord.TextChannel, discord.ForumChannel)) and not bool(getattr(permissions, "manage_threads", False)):
        return True
    return False


def _merge_activity_coverage_targets(
    guild: discord.Guild,
    targets: list[Any],
    seen: set[int],
    notes: list[str],
) -> None:
    """Add explicit bot-only repairs required by authoritative activity tracking.

    This never changes member/staff visibility. It only gives Dank Shield itself
    the durable history/thread access required for accurate inactivity proof.
    """
    from stoney_verify.startup_guards import setup_permission_repair_guard as legacy

    me = legacy._bot_member(guild)
    if not isinstance(me, discord.Member):
        return

    by_channel = {
        int(getattr(item.channel, "id", 0) or 0): item
        for item in targets
        if int(getattr(item.channel, "id", 0) or 0) > 0
    }
    repair_count = 0

    for channel in list(getattr(guild, "channels", []) or []):
        if not _activity_coverage_channel(channel):
            continue
        if not _activity_coverage_needs_repair(channel, me):
            continue

        cid = int(getattr(channel, "id", 0) or 0)
        if cid <= 0:
            continue
        expected = _activity_coverage_expected(channel)
        existing = by_channel.get(cid)
        if existing is not None:
            current = existing.overwrites.get(me, discord.PermissionOverwrite())
            current.view_channel = True
            current.read_message_history = True
            if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                current.manage_threads = True
            existing.overwrites[me] = current
        else:
            legacy._add_target(
                targets,
                seen,
                channel,
                "Authoritative activity coverage",
                {me: expected},
            )
            by_channel[cid] = targets[-1] if targets else None
        repair_count += 1

    if repair_count:
        notes.append(
            f"Activity coverage: {repair_count} channel(s) need Dank Shield bot-only View Channel / Read Message History / Manage Threads access. Member visibility is not changed."
        )


'''
permission_text = permission_text.replace(marker, helper + marker, 1)

call_marker = '''    for item in list(targets):
        if not isinstance(item.channel, discord.CategoryChannel):
            continue
        if item.label in {"Active tickets category", "Ticket archive category", "Staff tools category"}:
            for child in list(getattr(item.channel, "channels", []) or []):
                legacy._add_target(targets, seen, child, f"{item.label} child channel", item.overwrites)

    if not targets:'''
call_replacement = '''    for item in list(targets):
        if not isinstance(item.channel, discord.CategoryChannel):
            continue
        if item.label in {"Active tickets category", "Ticket archive category", "Staff tools category"}:
            for child in list(getattr(item.channel, "channels", []) or []):
                legacy._add_target(targets, seen, child, f"{item.label} child channel", item.overwrites)

    _merge_activity_coverage_targets(guild, targets, seen, notes)

    if not targets:'''
if call_marker not in permission_text:
    raise SystemExit("activity coverage target call marker missing")
permission_text = permission_text.replace(call_marker, call_replacement, 1)
permission_text = permission_text.replace(
    '"Truth-engine repair for configured and exact-name Dank Shield setup targets. "\n            "It fixes channel/category overwrites, then tells you what still requires Discord-level action."',
    '"Truth-engine repair for configured setup targets plus Dank Shield activity-coverage access. "\n            "It fixes safe channel/category overwrites, then tells you what still requires Discord-level action."',
)
permission_text = permission_text.replace(
    '"✅ Can fix saved/exact-name setup channel/category overwrites.\\n"\n                "⚠️ Cannot move the bot role, grant missing bot permissions, or guess ambiguous duplicate channels.\\n"',
    '"✅ Can fix saved/exact-name setup overwrites and Dank Shield\'s own activity-history access.\\n"\n                "⚠️ Cannot move the bot role, grant missing server-wide role permissions, or guess ambiguous duplicate channels.\\n"',
)
permission_service.write_text(permission_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Public docs/audits: 9 commands are intentional; Manage Threads is required.
# ---------------------------------------------------------------------------
readme = ROOT / "README.md"
readme_text = readme.read_text(encoding="utf-8")
readme_text = readme_text.replace("- Read Message History\n- Manage Messages", "- Read Message History\n- Manage Threads\n- Manage Messages", 1)
old_setup = '''You will see three main paths:

```text
Fresh Server
Existing Server
Advanced Setup
```

### 3. Pick the right path

#### Fresh Server

Use this when the server does not already have a support/verification layout.

Dank Shield will create missing recommended items only. It will not delete old channels, old tickets, or existing roles.

Recommended flow:

```text
/dank setup
Fresh Server
Create Missing Defaults Now
Health Check
/ticket-panel post
Open a test ticket
```

#### Existing Server

Use this when the server already has roles/channels/categories.

Recommended flow:

```text
/dank setup
Existing Server
Ticket Basics
Verification Roles
Verification Channels
Logs + Status
Back to Setup
Health Check
/ticket-panel post
Open a test ticket
```

#### Advanced Setup

Use this only when you want to fine-tune names, ticket menu options, logs, status, or category routing.'''
new_setup = '''The normal setup flow is one guided path:

```text
Start Setup
Choose what Dank Shield should do
Set Up This Step
Automatic Setup Check
Fix Next Problem or Test & Launch
```

### 3. Follow the guided steps

Dank Shield asks for one required item at a time. Choose an existing role/channel or let Dank Shield create the missing item when that step supports creation.

Use **More Options** only for secondary tools such as changing setup type, optional settings, manual setup checks, permission repair, or starting over.

SpamGuard is enabled by default for normal new-server setup. Owners can still turn it off explicitly from the protection/settings controls.'''
if old_setup in readme_text:
    readme_text = readme_text.replace(old_setup, new_setup, 1)
readme.write_text(readme_text, encoding="utf-8")

prod = ROOT / "docs/public-production-env.md"
prod_text = prod.read_text(encoding="utf-8")
prod_text = prod_text.replace("commands_ext registration complete. final_global=7 final_guild=0 profile=public", "commands_ext registration complete. final_global=9 final_guild=0 profile=public")
prod_text = prod_text.replace(
    '''1. Invite the bot with the required permissions.
2. Run `/dank setup`.
3. Choose create-missing-items or existing-server setup.
4. Verify ticket panel, verification channel, modlog, roles, and spam guard from setup health.''',
    '''1. Invite the bot with the required permissions, including Manage Threads for authoritative activity coverage.
2. Run `/dank setup`.
3. Choose what Dank Shield should do and follow **Set Up This Step** until Setup Check runs automatically.
4. Fix any reported problem, then use **Test & Launch**.
5. SpamGuard defaults to ON for new/missing settings rows unless an owner explicitly turns it off.''',
)
prod.write_text(prod_text, encoding="utf-8")

audit = ROOT / "tools/audit_public_invite_permissions.py"
audit_text = audit.read_text(encoding="utf-8")
if '    "Manage Threads",' not in audit_text:
    audit_text = audit_text.replace('    "Read Message History",\n', '    "Read Message History",\n    "Manage Threads",\n', 1)
audit.write_text(audit_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Permanent regression contracts.
# ---------------------------------------------------------------------------
(ROOT / "tests/test_spam_guard_default_on_bootstrap_static.py").write_text('''from pathlib import Path

SOURCE = Path("stoney_verify/spam_guard.py").read_text(encoding="utf-8")
MODES = Path("stoney_verify/startup_guards/setup_service_modes.py").read_text(encoding="utf-8")
FRESH = Path("stoney_verify/commands_ext/public_setup_fresh_choice.py").read_text(encoding="utf-8")


def test_spam_guard_runtime_defaults_on():
    block = SOURCE[SOURCE.index("def _default_settings("):SOURCE.index("def _normalize_settings(")]
    assert '"enabled": True' in block
    assert '"enabled": False' not in block


def test_missing_settings_row_is_bootstrapped_to_database():
    load = SOURCE[SOURCE.index("async def get_spam_settings("):SOURCE.index("async def save_spam_settings(")]
    assert "created_default_row=True" in load
    assert "db-bootstrap" in load
    assert "_upsert_settings_sync" in load
    assert "effective_row_found = bool(row_found or persisted)" in load


def test_existing_saved_off_state_is_not_forced_on():
    normalize = SOURCE[SOURCE.index("def _normalize_settings("):SOURCE.index("def _settings_payload_for_db(")]
    assert 'row.get("spam_blocker_enabled", row.get("enabled"))' in normalize


def test_normal_setup_defaults_select_spam_guard():
    assert 'ServiceState(True, False, False, True, True, "defaults")' in MODES
    assert '_cfg_value(cfg, "spam_guard_enabled", True), True' in MODES
    choice_block = FRESH[FRESH.index("def _service_flags_for_choice("):FRESH.index("def _choice_payload(")]
    for key in ("basic_server", "basic_verify", "help_desk", "voice_check", "id_check"):
        assert key in choice_block
    assert choice_block.count('"spam_guard_enabled": True') >= 4
''', encoding="utf-8")

(ROOT / "tests/test_command_prune_log_hygiene_static.py").write_text('''from pathlib import Path

SOURCE = Path("stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")


def test_prune_skip_logs_are_once_per_startup():
    assert "_STALE_PRUNE_SKIP_LOGGED = False" in SOURCE
    assert "_CHILD_PRUNE_SKIP_LOGGED = False" in SOURCE
    assert "if not _STALE_PRUNE_SKIP_LOGGED:" in SOURCE
    assert "if not _CHILD_PRUNE_SKIP_LOGGED:" in SOURCE


def test_module_loop_still_keeps_runtime_pruning_disabled():
    assert 'DANK_DISABLE_RUNTIME_COMMAND_PRUNE", True' in SOURCE
    assert "after_module_registration" in SOURCE
''', encoding="utf-8")

(ROOT / "tests/test_activity_coverage_permission_repair_static.py").write_text('''from pathlib import Path

SOURCE = Path("stoney_verify/setup_permission_repair_services.py").read_text(encoding="utf-8")
README = Path("README.md").read_text(encoding="utf-8")
AUDIT = Path("tools/audit_public_invite_permissions.py").read_text(encoding="utf-8")


def test_permission_repair_can_restore_activity_coverage():
    assert "_merge_activity_coverage_targets" in SOURCE
    assert "read_message_history=True" in SOURCE
    assert "expected.manage_threads = True" in SOURCE
    assert "Member visibility is not changed" in SOURCE


def test_public_permissions_include_manage_threads():
    assert "Manage Threads" in README
    assert '"Manage Threads"' in AUDIT
''', encoding="utf-8")

(ROOT / "tests/test_public_command_count_docs_static.py").write_text('''from pathlib import Path

DOC = Path("docs/public-production-env.md").read_text(encoding="utf-8")
COMMANDS = Path("stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")


def test_public_production_docs_match_current_command_surface():
    assert "final_global=9 final_guild=0 profile=public" in DOC
    assert "final_global=7" not in DOC
    for module in (
        "public_setup_group",
        "public_mod_group",
        "public_ticket_group_clean",
        "public_tickets_group",
        "public_ticket_intake_group",
        "public_ticket_category_group",
        "public_ticket_panel_clean",
        "public_verify_group",
        "public_self_roles_group",
    ):
        assert module in COMMANDS
''', encoding="utf-8")

for path in (
    spam,
    service_modes,
    fresh,
    commands,
    permission_service,
    audit,
    ROOT / "tests/test_spam_guard_default_on_bootstrap_static.py",
    ROOT / "tests/test_command_prune_log_hygiene_static.py",
    ROOT / "tests/test_activity_coverage_permission_repair_static.py",
    ROOT / "tests/test_public_command_count_docs_static.py",
):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")

print("PASS: applied startup operational fixes")
