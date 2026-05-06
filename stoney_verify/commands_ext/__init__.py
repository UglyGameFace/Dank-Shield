from __future__ import annotations

import builtins
import os
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Sequence, Tuple

_COMMANDS_EXT_REGISTERED = False
DEFAULT_COMMAND_PROFILE = "public"
CommandRegistrar = Callable[[Any, Any], None]
CommandModuleSpec = Tuple[str, str, str]

# Ticket Tool-style command policy:
# - public profile exposes only the obvious daily command families
# - advanced/admin repair tools stay out of public autocomplete by default
# - dev/full profiles are for testing, diagnostics, migration, and legacy work
# - setup customization belongs inside /dank setup, not as random top-level commands
COMMAND_MODULES: List[CommandModuleSpec] = [
    ("public_staff_scope", "register_public_staff_scope", "core: per-server staff permission isolation"),
    ("public_access_control", "register_public_access_control", "core: server-control and staff role split"),
    ("public_onboarding", "register_public_onboarding_listeners", "core: isolated guild join/leave onboarding lifecycle"),
    ("public_join_removal_safety", "register_public_join_removal_safety", "core: fresh-join stale timer cleanup listener"),
    ("public_spam_cleanup_hardening", "register_public_spam_cleanup_hardening", "core: spam guard burst cleanup hardening"),
    ("public_setup_solid", "register_public_setup_solid_commands", "core: guided /dank setup UI"),
    ("public_setup_recommend", "register_public_setup_recommend_commands", "core: setup recommendations inside /dank setup"),
    ("public_setup_recovery", "register_public_setup_recovery_commands", "core: setup recovery/start-over center"),
    ("public_setup_cleanup", "register_public_setup_cleanup_commands", "core: selective setup cleanup tools"),
    ("public_setup_fresh_choice", "register_public_setup_fresh_choice_commands", "core: fresh-server build choices"),
    ("public_setup_full_customization", "register_public_setup_full_customization_commands", "core: setup customization picker flow"),
    ("public_status_reporter", "register_public_status_reporter", "core: bot status reports and heartbeat"),
    ("public_modlog_coverage", "register_public_modlog_coverage_listeners", "core: supplemental modlog coverage listeners"),
    ("public_setup_group", "register_public_setup_group_commands", "core: /dank command group"),
    ("public_help_group", "register_public_help_group_commands", "core: /dank help and command catalog"),
    ("public_cleanup_group", "register_public_cleanup_group_commands", "core: /dank cleanup commands"),
    ("public_spam_group", "register_public_spam_group_commands", "core: /dank spam commands"),
    ("public_members_group", "register_public_members_group_commands", "core: /dank members activity review commands"),
    ("public_members_cleanup_group", "register_public_members_cleanup_group_commands", "core: confirmed /dank members cleanup command"),
    ("public_mod_group", "register_public_mod_group_commands", "core: grouped /mod moderation commands"),
    ("public_ticket_group_clean", "register_public_ticket_group_clean_commands", "core: grouped /ticket commands"),
    ("public_ticket_delete", "register_public_ticket_delete_commands", "core: /ticket delete command"),
    ("public_tickets_group", "register_public_tickets_group_commands", "core: grouped /tickets commands"),
    ("public_ticket_category_group", "register_public_ticket_category_group_commands", "core: grouped /ticket-category commands"),
    ("public_ticket_panel_clean", "register_public_ticket_panel_clean", "core: public Create Ticket panel tools"),
    ("public_verify_group", "register_public_verify_group_commands", "core: grouped /verify commands"),
    ("public_setup_gate", "register_public_setup_gate", "core: setup readiness gate for ticket commands"),

    # Advanced/admin repair modules. These are intentionally NOT in the public
    # profile. Load them with STONEY_COMMAND_PROFILE=public-admin only when an
    # owner/admin needs direct repair commands outside /dank setup.
    ("public_setup_start", "register_public_setup_start_commands", "admin: legacy /dank setup quick-start fallback"),
    ("public_setup_review", "register_public_setup_review_commands", "admin: setup review diagnostic command"),
    ("public_setup_logs", "register_public_setup_logs_commands", "admin: direct setup log command"),
    ("public_setup_defaults", "register_public_setup_defaults_commands", "admin: direct setup defaults command"),
    ("public_setup_assistant", "register_public_setup_assistant_commands", "admin: setup assistant command"),
    ("public_setup_by_id", "register_public_setup_by_id_commands", "admin: setup by raw Discord ID fallback"),
    ("public_setup_picker", "register_public_setup_picker_commands", "admin: setup picker fallback command"),
    ("public_setup_find", "register_public_setup_find_commands", "admin: setup discovery/find command"),
    ("public_archive_backfill", "register_public_archive_backfill_commands", "admin: archive backfill repair command"),
    ("public_permission_check", "register_public_permission_check_commands", "admin: permission diagnostic command"),
    ("public_launch_check", "register_public_launch_check_commands", "admin: release readiness command"),
    ("public_tickettool_check", "register_public_tickettool_check_commands", "admin: TicketTool comparison/readiness command"),
    ("public_production_audit", "register_public_production_audit_commands", "admin/dev: production audit command"),
    ("ticket_panel_admin_safe", "register_ticket_panel_admin_commands", "admin: ticket panel setup/config commands"),
    ("panel_bootstrap_admin", "register_panel_bootstrap_admin_commands", "admin: panel bootstrap/self-heal commands"),

    # Legacy/dev modules. These are not public-product commands.
    ("role_admin", "register_role_admin_commands", "legacy/dev: top-level role admin commands"),
    ("channel_cleanup_admin", "register_channel_cleanup_admin_commands", "legacy/dev: top-level channel cleanup admin commands"),
    ("kick_timers", "register_kick_timer_commands", "legacy/dev: kick timer commands"),
    ("vc_flow", "register_vc_flow_commands", "legacy/dev: VC flow commands"),
    ("ticket_admin", "register_ticket_admin_commands", "legacy/dev: ticket admin commands"),
    ("ticket_channel_admin", "register_ticket_channel_admin_commands", "legacy/dev: ticket channel admin commands"),
    ("ticket_intake_admin", "register_ticket_intake_admin_commands", "legacy/dev: ticket intake admin commands"),
    ("ticket_queue_admin", "register_ticket_queue_admin_commands", "legacy/dev: ticket queue admin commands"),
    ("ticket_category_admin", "register_ticket_category_admin_commands", "legacy/dev: ticket category admin commands"),
    ("ticket_governance_admin", "register_ticket_governance_admin_commands", "legacy/dev: ticket governance commands"),
    ("ticket_sla_admin", "register_ticket_sla_admin_commands", "legacy/dev: ticket SLA commands"),
    ("ticket_resolution_admin", "register_ticket_resolution_admin_commands", "legacy/dev: ticket resolution commands"),
    ("ticket_macro_admin", "register_ticket_macro_commands", "legacy/dev: ticket macro commands"),
    ("ticket_automation_admin", "register_ticket_automation_admin_commands", "legacy/dev: ticket automation commands"),
    ("moderation", "register_moderation_commands", "legacy/dev: moderation commands"),
    ("identity_admin", "register_identity_truth_admin_commands", "legacy/dev: identity truth admin commands"),
]

_LEGACY_MODULES: Tuple[str, ...] = tuple(
    name for name, _fn, _label in COMMAND_MODULES if not name.startswith("public_") and name not in {"ticket_panel_admin_safe", "panel_bootstrap_admin"}
)

_PUBLIC_CORE_MODULES: Tuple[str, ...] = (
    "public_staff_scope",
    "public_access_control",
    "public_onboarding",
    "public_join_removal_safety",
    "public_spam_cleanup_hardening",
    "public_setup_solid",
    "public_setup_recommend",
    "public_setup_recovery",
    "public_setup_cleanup",
    "public_setup_fresh_choice",
    "public_setup_full_customization",
    "public_status_reporter",
    "public_modlog_coverage",
    "public_setup_group",
    "public_help_group",
    "public_cleanup_group",
    "public_spam_group",
    "public_members_group",
    "public_members_cleanup_group",
    "public_mod_group",
    "public_ticket_group_clean",
    "public_ticket_delete",
    "public_tickets_group",
    "public_ticket_category_group",
    "public_ticket_panel_clean",
    "public_verify_group",
    "public_setup_gate",
)

_PUBLIC_ADMIN_EXTRA_MODULES: Tuple[str, ...] = (
    "public_setup_start",
    "public_setup_review",
    "public_setup_logs",
    "public_setup_defaults",
    "public_setup_assistant",
    "public_setup_by_id",
    "public_setup_picker",
    "public_setup_find",
    "public_archive_backfill",
    "public_permission_check",
    "public_launch_check",
    "public_tickettool_check",
    "public_production_audit",
    "ticket_panel_admin_safe",
    "panel_bootstrap_admin",
)

COMMAND_PROFILES: Dict[str, Sequence[str]] = {
    "public": _PUBLIC_CORE_MODULES,
    "minimal": tuple(x for x in _PUBLIC_CORE_MODULES if x not in {"public_spam_group", "public_cleanup_group", "public_members_group", "public_members_cleanup_group"}),
    "public-admin": _PUBLIC_CORE_MODULES + _PUBLIC_ADMIN_EXTRA_MODULES,
    "full": _LEGACY_MODULES,
    "dev": _LEGACY_MODULES + _PUBLIC_CORE_MODULES + _PUBLIC_ADMIN_EXTRA_MODULES,
}

_STALE_TOP_LEVEL_COMMANDS: Tuple[str, ...] = (
    "stoney",
    "ticket-intake",
    "spam_guard",
    "spam_guard_status",
    "fix_unverified",
    "set_verified",
    "set_resident",
    "grant_vr",
    "verify_diagnose",
    "fix_unverified_member",
    "verify_status",
    "repair_verify_ui",
    "recompute_member_risk",
    "recompute_all_member_risk",
    "channel_cleanup_status",
    "run_channel_cleanup",
    "purge_channel_messages",
    "ticket_setup_status",
    "ticket_setup_discover",
    "ticket_setup_save_discovered",
    "ticket_setup_set_channel",
    "ticket_setup_set_role",
    "ticket_panel_list",
    "ticket_panel_show",
    "ticket_panel_bind_categories",
    "ticket_panel_rules",
    "ticket_panel_rules_set",
    "ticket_panel_runtime",
    "ticket_panel_bootstrap_status",
    "ticket_panel_bootstrap_run",
    "ticket_panel_bootstrap_all",
    "ticket_panel_bootstrap_start",
    "ticket_panel_bootstrap_once",
    "ticket_panel_bootstrap_stop",
)

_CONFUSING_STONEY_CHILDREN: Tuple[str, ...] = (
    "config-cache",
    "current",
    "archive-backfill",
    "cache",
    "config",
    "db-check",
    "health",
    "launch-check",
    "modlog-check",
    "permission-check",
    "production-audit",
    "refresh-config",
    "setup-access",
    "setup-assistant",
    "setup-defaults",
    "setup-find",
    "setup-logs",
    "setup-picker",
    "setup-review",
    "setup-status",
    "setup-tickets",
    "setup-verify",
    "setup-verify-ids",
    "tickettool-check",
)

_ALLOWED_STONEY_CHILDREN = {"setup", "help", "commands", "cleanup", "spam", "members"}

_COMPACT_SUPPRESS_PREFIXES: Tuple[str, ...] = (
    "✅ public_",
    "✅ commands_ext: registered",
    "✅ public_ticket_panel_commands:",
    "✅ Ticket panel buttons registered",
    "🧹 commands_ext pruned /dank during registration",
    "🧹 public_spam_group removed legacy top-level spam commands",
)

_COMPACT_SUPPRESS_CONTAINS: Tuple[str, ...] = (
    " global_delta=",
    " guild_delta=",
    " attached ",
    " registered ",
    " active",
)


def _csv_set(value: str) -> set[str]:
    return {part.strip().lower() for part in str(value or "").split(",") if part.strip()}


def _env_csv_set(name: str) -> set[str]:
    try:
        return _csv_set(os.getenv(name, "") or "")
    except Exception:
        return set()


def _env_bool(name: str, default: bool = False) -> bool:
    try:
        raw = os.getenv(name, "")
        if raw is None or str(raw).strip() == "":
            return bool(default)
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _env_int(name: str, default: int = 0) -> int:
    try:
        raw = str(os.getenv(name, "") or "").strip()
        return int(raw) if raw else int(default)
    except Exception:
        return int(default)


def _env_str(name: str, default: str = "") -> str:
    try:
        value = os.getenv(name)
        return str(value).strip() if value is not None and str(value).strip() else default
    except Exception:
        return default


def _command_profile() -> str:
    return _env_str("STONEY_COMMAND_PROFILE", DEFAULT_COMMAND_PROFILE).lower() or DEFAULT_COMMAND_PROFILE


def _command_log_style() -> str:
    return _env_str("STONEY_COMMAND_LOG_STYLE", _env_str("STONEY_STARTUP_LOG_STYLE", "compact")).lower()


def _verbose_command_logs() -> bool:
    return _command_log_style() in {"verbose", "debug", "trace", "full"}


def _should_suppress_command_line(text: str) -> bool:
    if _verbose_command_logs():
        return False
    line = str(text or "")
    if not line:
        return False
    if line.startswith(("⚠️", "🚫", "❌", "Traceback", "RuntimeError", "Error")):
        return False
    if any(line.startswith(prefix) for prefix in _COMPACT_SUPPRESS_PREFIXES):
        return True
    if line.startswith("✅ ") and any(token in line for token in _COMPACT_SUPPRESS_CONTAINS):
        return True
    return False


@contextmanager
def _compact_command_print_filter() -> Iterator[None]:
    if _verbose_command_logs():
        yield
        return

    original_print = builtins.print

    def filtered_print(*args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            text = " ".join(str(arg) for arg in args)
            if _should_suppress_command_line(text):
                return None
        except Exception:
            pass
        return original_print(*args, **kwargs)

    builtins.print = filtered_print
    try:
        yield
    finally:
        builtins.print = original_print


def _deployment_mode() -> str:
    raw = _env_str("STONEY_DEPLOYMENT_MODE", "").lower()
    if raw:
        return raw
    if _env_bool("STONEY_PRODUCTION_MODE", False):
        return "production"
    if _env_bool("STONEY_PUBLIC_MODE", False):
        return "public"
    return "development"


def _public_profile_like(profile: str) -> bool:
    return str(profile or "").strip().lower() in {"public", "minimal"}


def _selected_command_modules() -> List[CommandModuleSpec]:
    profile = _command_profile()
    explicit = _env_csv_set("STONEY_COMMAND_MODULES")
    extra = _env_csv_set("STONEY_COMMAND_MODULES_EXTRA")
    skip = _env_csv_set("STONEY_COMMAND_MODULES_SKIP")
    known = {name for name, _fn, _label in COMMAND_MODULES}
    selected = {name for name in explicit if name in known} if explicit else set(COMMAND_PROFILES.get(profile, COMMAND_PROFILES[DEFAULT_COMMAND_PROFILE]))
    selected |= known.intersection(extra)
    selected -= known.intersection(skip)
    for label, values in (("STONEY_COMMAND_MODULES", explicit), ("STONEY_COMMAND_MODULES_EXTRA", extra), ("STONEY_COMMAND_MODULES_SKIP", skip)):
        unknown = sorted(values - known)
        if unknown:
            print(f"⚠️ commands_ext: unknown {label} ignored: {unknown}")
    return [spec for spec in COMMAND_MODULES if spec[0] in selected]


def _tree_command_counts(tree: Any) -> tuple[int, int]:
    try:
        global_count = len(list(tree.get_commands(guild=None) or []))
    except Exception:
        global_count = len(getattr(tree, "_global_commands", {}) or {})
    guild_count = 0
    try:
        for value in (getattr(tree, "_guild_commands", {}) or {}).values():
            guild_count += len(value or {})
    except Exception:
        pass
    return int(global_count), int(guild_count)


def _child_names(group: Any) -> list[str]:
    try:
        return sorted(str(getattr(cmd, "name", "")) for cmd in list(getattr(group, "commands", []) or []) if str(getattr(cmd, "name", "")).strip())
    except Exception:
        return []


def _remove_stale_top_level_commands(tree: Any, *, reason: str) -> list[str]:
    removed: list[str] = []
    for name in _STALE_TOP_LEVEL_COMMANDS:
        try:
            if tree.get_command(name, guild=None) is not None:
                tree.remove_command(name, guild=None)
                removed.append(name)
        except Exception:
            pass
    if removed and _verbose_command_logs():
        print(f"🧹 commands_ext removed stale top-level commands reason={reason}: {removed}")
    return removed


def _prune_public_stoney_children(*, profile: str, reason: str) -> list[str]:
    if not _public_profile_like(profile):
        return []
    try:
        from .public_setup_group import stoney_group
    except Exception:
        return []
    before = _child_names(stoney_group)
    removed: list[str] = []
    for name in _CONFUSING_STONEY_CHILDREN:
        try:
            if stoney_group.get_command(name) is not None:
                stoney_group.remove_command(name)
                removed.append(name)
        except Exception:
            pass
    after = _child_names(stoney_group)
    unexpected = [name for name in after if name not in _ALLOWED_STONEY_CHILDREN]
    if unexpected or (removed and _verbose_command_logs()):
        print(f"🧹 commands_ext pruned /dank during registration reason={reason} before={before} after={after} removed={removed} unexpected_remaining={unexpected}")
    return removed


def _import_registrar(module_name: str, function_name: str) -> CommandRegistrar:
    module = __import__(f"{__name__}.{module_name}", fromlist=[function_name])
    registrar = getattr(module, function_name)
    if not callable(registrar):
        raise RuntimeError(f"{module_name}.{function_name} is not callable")
    return registrar


def _register_one_module(*, bot: Any, tree: Any, module_name: str, function_name: str, label: str, errors: List[str]) -> tuple[int, int]:
    before_global, before_guild = _tree_command_counts(tree)
    try:
        with _compact_command_print_filter():
            _import_registrar(module_name, function_name)(bot, tree)
        after_global, after_guild = _tree_command_counts(tree)
        delta_global = after_global - before_global
        delta_guild = after_guild - before_guild
        if _verbose_command_logs():
            print(f"✅ commands_ext: registered {label} module={module_name} global_delta={delta_global} global_total={after_global} guild_delta={delta_guild} guild_total={after_guild}")
        return delta_global, delta_guild
    except Exception as e:
        errors.append(f"{module_name}: {repr(e)}")
        print(f"⚠️ commands_ext: failed registering {label}: {repr(e)}")
        return 0, 0


def _log_stoney_setup_surface() -> tuple[list[str], list[str]]:
    try:
        from .public_setup_group import stoney_group
        child_names = _child_names(stoney_group)
        advanced = [name for name in child_names if name in _CONFUSING_STONEY_CHILDREN]
        if advanced or _verbose_command_logs():
            print(f"🧭 commands_ext /dank surface setup_present={'setup' in child_names} advanced_aliases={advanced} direct_children={child_names}")
        return child_names, advanced
    except Exception as e:
        print(f"⚠️ commands_ext could not inspect /dank setup surface: {repr(e)}")
        return [], []


def _public_guard_findings(profile: str) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    deployment = _deployment_mode()
    require_auth = _env_bool("BOT_API_REQUIRE_AUTH", True)
    allow_insecure = _env_bool("BOT_API_ALLOW_INSECURE", False)
    secret = _env_str("BOT_API_SHARED_SECRET", "")
    host = _env_str("BOT_API_BIND_HOST", "127.0.0.1")
    expected_guilds = _env_int("STONEY_EXPECTED_PUBLIC_GUILDS", 1)
    production_like = deployment in {"public", "prod", "production"}

    if profile in {"full", "dev"}:
        (blockers if production_like else warnings).append(f"STONEY_COMMAND_PROFILE={profile!r} exposes legacy/dev command surfaces.")
    if profile == "public-admin":
        warnings.append("STONEY_COMMAND_PROFILE='public-admin' exposes advanced admin repair commands. Use 'public' for normal release.")
    if not require_auth:
        (blockers if production_like else warnings).append("BOT_API_REQUIRE_AUTH=false leaves the structured bot API unauthenticated.")
    if allow_insecure:
        (blockers if production_like else warnings).append("BOT_API_ALLOW_INSECURE=true is local-dev only and must be false for public use.")
    if require_auth and len(secret) < 32:
        (blockers if production_like else warnings).append("BOT_API_SHARED_SECRET should be at least 32 characters.")
    if host in {"0.0.0.0", "::"} and not require_auth:
        blockers.append("BOT_API_BIND_HOST is public-facing while API auth is disabled.")
    if expected_guilds >= 100 and not _env_bool("DISCORD_AUTO_SHARD", False):
        warnings.append("STONEY_EXPECTED_PUBLIC_GUILDS is 100+ but DISCORD_AUTO_SHARD is not enabled.")
    if _env_bool("CLEAR_GLOBAL_COMMANDS_ON_BOOT", False):
        warnings.append("CLEAR_GLOBAL_COMMANDS_ON_BOOT=true is legacy and ignored in public mode.")
    if _env_str("GUILD_ID", ""):
        warnings.append("GUILD_ID is still set. Production behavior should rely on per-guild DB config.")
    return blockers, warnings


def _run_public_startup_guard(profile: str) -> None:
    blockers, warnings = _public_guard_findings(profile)
    strict = _env_bool("STONEY_STRICT_PUBLIC_GUARD", False) or _deployment_mode() in {"prod", "production"}
    print(f"🧯 public_startup_guard deployment={_deployment_mode()} profile={profile} strict={strict} blockers={len(blockers)} warnings={len(warnings)}")
    for item in blockers:
        print(f"🚫 public_startup_guard blocker: {item}")
    for item in warnings:
        print(f"⚠️ public_startup_guard warning: {item}")
    if strict and blockers:
        raise RuntimeError(f"Public startup guard blocked unsafe deployment: {' | '.join(blockers)}")


def register_all_commands(bot: Any, tree: Any) -> None:
    global _COMMANDS_EXT_REGISTERED
    if _COMMANDS_EXT_REGISTERED:
        print("ℹ️ commands_ext.register_all_commands already ran; skipping duplicate registration.")
        return

    errors: list[str] = []
    profile = _command_profile()
    _run_public_startup_guard(profile)
    pre_removed = _remove_stale_top_level_commands(tree, reason="before_module_registration")
    selected_modules = _selected_command_modules()
    selected_names = [name for name, _fn, _label in selected_modules]
    skipped_names = [name for name, _fn, _label in COMMAND_MODULES if name not in set(selected_names)]
    before_global, before_guild = _tree_command_counts(tree)

    if _verbose_command_logs():
        print(f"🧩 commands_ext profile={profile} selected={selected_names} skipped={skipped_names} initial_global={before_global} initial_guild={before_guild}")
    else:
        print(f"🧩 commands_ext profile={profile} modules={len(selected_names)} skipped={len(skipped_names)} initial_global={before_global} initial_guild={before_guild} log=compact")

    total_global_delta = 0
    total_guild_delta = 0
    prune_removed = 0

    for module_name, function_name, label in selected_modules:
        delta_global, delta_guild = _register_one_module(bot=bot, tree=tree, module_name=module_name, function_name=function_name, label=label, errors=errors)
        total_global_delta += delta_global
        total_guild_delta += delta_guild
        removed = _prune_public_stoney_children(profile=profile, reason=f"after_{module_name}")
        prune_removed += len(removed)

    child_names, advanced = _log_stoney_setup_surface()
    post_removed = _remove_stale_top_level_commands(tree, reason="after_module_registration")
    removed = _prune_public_stoney_children(profile=profile, reason="after_module_registration")
    prune_removed += len(removed)
    _COMMANDS_EXT_REGISTERED = True
    final_global, final_guild = _tree_command_counts(tree)

    if final_global >= 95:
        print(f"⚠️ commands_ext command budget high: global={final_global}/100.")

    if errors:
        print(f"⚠️ commands_ext registration completed with errors final_global={final_global} final_guild={final_guild}:")
        for item in errors:
            print(f"   - {item}")
    else:
        print(
            "✅ commands_ext registration complete. "
            f"final_global={final_global} final_guild={final_guild} profile={profile} "
            f"modules={len(selected_names)} delta_global={total_global_delta} delta_guild={total_guild_delta} "
            f"dank_children={child_names} advanced_aliases={advanced} "
            f"stale_removed={len(pre_removed) + len(post_removed)} dank_pruned={prune_removed} log={_command_log_style()}"
        )


__all__ = ["register_all_commands", "COMMAND_MODULES", "COMMAND_PROFILES", "DEFAULT_COMMAND_PROFILE"]
