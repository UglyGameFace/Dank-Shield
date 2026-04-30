from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Sequence, Tuple


_COMMANDS_EXT_REGISTERED = False
DEFAULT_COMMAND_PROFILE = "public"

CommandRegistrar = Callable[[Any, Any], None]
CommandModuleSpec = Tuple[str, str, str]


# Keep public guard modules first. Staff/access scope must run before modules
# import _staff_check or _require_setup_permission. Setup gate runs after grouped
# public ticket modules exist so it can wrap their shared runtime helpers.
COMMAND_MODULES: List[CommandModuleSpec] = [
    ("public_staff_scope", "register_public_staff_scope", "public per-guild staff permission isolation"),
    ("public_access_control", "register_public_access_control", "public server-control and staff role split"),
    ("public_onboarding", "register_public_onboarding_listeners", "public isolated guild join/leave onboarding lifecycle"),
    ("public_join_removal_safety", "register_public_join_removal_safety", "public fresh-join stale timer cleanup listener"),
    ("public_spam_cleanup_hardening", "register_public_spam_cleanup_hardening", "public spam guard burst cleanup hardening"),
    ("public_setup_start", "register_public_setup_start_commands", "public obvious /stoney setup quick-start command"),
    ("public_setup_review", "register_public_setup_review_commands", "public grouped /stoney setup review command"),
    ("public_setup_logs", "register_public_setup_logs_commands", "public grouped /stoney log channel setup command"),
    ("public_setup_defaults", "register_public_setup_defaults_commands", "public one-click default server setup command"),
    ("public_setup_assistant", "register_public_setup_assistant_commands", "public interactive setup assistant command"),
    ("public_status_reporter", "register_public_status_reporter", "public bot status reports and heartbeat"),
    ("public_modlog_coverage", "register_public_modlog_coverage_listeners", "public supplemental modlog coverage listeners"),
    ("public_setup_by_id", "register_public_setup_by_id_commands", "public grouped /stoney setup by ID fallback command"),
    ("public_setup_picker", "register_public_setup_picker_commands", "public grouped /stoney interactive setup picker"),
    ("public_setup_find", "register_public_setup_find_commands", "public grouped /stoney setup search fallback command"),
    ("public_archive_backfill", "register_public_archive_backfill_commands", "public grouped /stoney ticket archive backfill command"),
    ("public_permission_check", "register_public_permission_check_commands", "public grouped /stoney runtime permission check command"),
    ("public_launch_check", "register_public_launch_check_commands", "public grouped /stoney production launch check command"),
    ("public_tickettool_check", "register_public_tickettool_check_commands", "public grouped /stoney TicketTool parity check command"),
    ("public_production_audit", "register_public_production_audit_commands", "public brutal production readiness audit command"),
    ("public_setup_group", "register_public_setup_group_commands", "public grouped /stoney setup commands"),
    ("public_mod_group", "register_public_mod_group_commands", "public grouped /mod moderation commands"),
    ("public_ticket_group_clean", "register_public_ticket_group_clean_commands", "public grouped /ticket commands with native lifecycle handling"),
    ("public_ticket_delete", "register_public_ticket_delete_commands", "public grouped /ticket delete command"),
    ("public_tickets_group", "register_public_tickets_group_commands", "public grouped /tickets commands"),
    ("public_ticket_intake_group", "register_public_ticket_intake_group_commands", "public grouped /ticket-intake commands"),
    ("public_ticket_category_group", "register_public_ticket_category_group_commands", "public grouped /ticket-category commands"),
    ("public_tickettool_parity_polish", "register_public_tickettool_parity_polish", "public TicketTool parity polish aliases"),
    ("public_setup_gate", "register_public_setup_gate", "public setup readiness gate for ticket commands"),

    # Legacy / advanced command modules.
    ("kick_timers", "register_kick_timer_commands", "kick timer commands"),
    ("vc_flow", "register_vc_flow_commands", "VC flow commands"),
    ("ticket_admin", "register_ticket_admin_commands", "ticket admin commands"),
    ("ticket_panel_admin", "register_ticket_panel_admin_commands", "ticket panel setup/config commands"),
    ("ticket_channel_admin", "register_ticket_channel_admin_commands", "ticket channel admin commands"),
    ("ticket_intake_admin", "register_ticket_intake_admin_commands", "ticket intake admin commands"),
    ("ticket_queue_admin", "register_ticket_queue_admin_commands", "ticket queue admin commands"),
    ("ticket_category_admin", "register_ticket_category_admin_commands", "ticket category admin commands"),
    ("ticket_governance_admin", "register_ticket_governance_admin_commands", "ticket governance admin commands"),
    ("ticket_sla_admin", "register_ticket_sla_admin_commands", "ticket SLA admin commands"),
    ("ticket_resolution_admin", "register_ticket_resolution_admin_commands", "ticket resolution admin commands"),
    ("ticket_macro_admin", "register_ticket_macro_commands", "ticket macro commands"),
    ("ticket_automation_admin", "register_ticket_automation_admin_commands", "ticket automation commands"),
    ("moderation", "register_moderation_commands", "moderation commands"),
    ("role_admin", "register_role_admin_commands", "role admin commands"),
    ("identity_admin", "register_identity_truth_admin_commands", "identity truth admin commands"),
    ("channel_cleanup_admin", "register_channel_cleanup_admin_commands", "channel cleanup admin commands"),
]

_LEGACY_MODULES: Tuple[str, ...] = tuple(
    name for name, _fn, _label in COMMAND_MODULES if not name.startswith("public_")
)

_PUBLIC_MODULES: Tuple[str, ...] = (
    "public_staff_scope",
    "public_access_control",
    "public_onboarding",
    "public_join_removal_safety",
    "public_spam_cleanup_hardening",
    "public_setup_start",
    "public_setup_review",
    "public_setup_logs",
    "public_setup_defaults",
    "public_setup_assistant",
    "public_status_reporter",
    "public_modlog_coverage",
    "public_setup_by_id",
    "public_setup_picker",
    "public_setup_find",
    "public_archive_backfill",
    "public_permission_check",
    "public_launch_check",
    "public_tickettool_check",
    "public_production_audit",
    "public_setup_group",
    "public_mod_group",
    "public_ticket_group_clean",
    "public_ticket_delete",
    "public_tickets_group",
    "public_ticket_intake_group",
    "public_ticket_category_group",
    "public_tickettool_parity_polish",
    "public_setup_gate",

    # Public-safe advanced setup/admin modules.
    # These are guild-scoped and DB-backed, so server owners can configure
    # panels without editing deployment .env values.
    "ticket_panel_admin",
    "role_admin",
    "channel_cleanup_admin",
)

COMMAND_PROFILES: Dict[str, Sequence[str]] = {
    "public": _PUBLIC_MODULES,
    "minimal": tuple(name for name in _PUBLIC_MODULES if name != "channel_cleanup_admin"),
    "full": _LEGACY_MODULES,
    "dev": _LEGACY_MODULES,
}


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
    profile = _env_str("STONEY_COMMAND_PROFILE", DEFAULT_COMMAND_PROFILE).lower()
    return profile or DEFAULT_COMMAND_PROFILE


def _deployment_mode() -> str:
    raw = _env_str("STONEY_DEPLOYMENT_MODE", "").lower()
    if raw:
        return raw
    if _env_bool("STONEY_PRODUCTION_MODE", False):
        return "production"
    if _env_bool("STONEY_PUBLIC_MODE", False):
        return "public"
    return "development"


def _strict_public_guard_enabled() -> bool:
    return _env_bool("STONEY_STRICT_PUBLIC_GUARD", False) or _deployment_mode() in {"prod", "production"}


def _selected_command_modules() -> List[CommandModuleSpec]:
    profile = _command_profile()
    explicit = _env_csv_set("STONEY_COMMAND_MODULES")
    skip = _env_csv_set("STONEY_COMMAND_MODULES_SKIP")
    known_names = {name for name, _fn, _label in COMMAND_MODULES}

    if explicit:
        selected_names = {name for name in explicit if name in known_names}
        unknown = sorted(explicit - known_names)
        if unknown:
            print(f"⚠️ commands_ext: unknown STONEY_COMMAND_MODULES ignored: {unknown}")
    else:
        if profile not in COMMAND_PROFILES:
            print(
                f"⚠️ commands_ext: unknown STONEY_COMMAND_PROFILE={profile!r}; "
                f"falling back to {DEFAULT_COMMAND_PROFILE}"
            )
        selected_names = set(COMMAND_PROFILES.get(profile, COMMAND_PROFILES[DEFAULT_COMMAND_PROFILE]))

    if skip:
        unknown_skip = sorted(skip - known_names)
        if unknown_skip:
            print(f"⚠️ commands_ext: unknown STONEY_COMMAND_MODULES_SKIP ignored: {unknown_skip}")
        selected_names -= known_names.intersection(skip)

    return [spec for spec in COMMAND_MODULES if spec[0] in selected_names]


def _tree_command_counts(tree: Any) -> tuple[int, int]:
    global_count = 0
    guild_count = 0

    try:
        global_count = len(list(tree.get_commands(guild=None) or []))
    except Exception:
        try:
            commands = getattr(tree, "_global_commands", {}) or {}
            global_count = len(commands) if isinstance(commands, dict) else 0
        except Exception:
            global_count = 0

    try:
        guild_commands = getattr(tree, "_guild_commands", {}) or {}
        if isinstance(guild_commands, dict):
            for value in guild_commands.values():
                try:
                    guild_count += len(value or {})
                except Exception:
                    pass
    except Exception:
        guild_count = 0

    return int(global_count), int(guild_count)


def _import_registrar(module_name: str, function_name: str) -> CommandRegistrar:
    module = __import__(f"{__name__}.{module_name}", fromlist=[function_name])
    registrar = getattr(module, function_name)
    if not callable(registrar):
        raise RuntimeError(f"{module_name}.{function_name} is not callable")
    return registrar


def _register_one_module(
    *,
    bot: Any,
    tree: Any,
    module_name: str,
    function_name: str,
    label: str,
    errors: List[str],
) -> None:
    before_global, before_guild = _tree_command_counts(tree)

    try:
        registrar = _import_registrar(module_name, function_name)
        registrar(bot, tree)

        after_global, after_guild = _tree_command_counts(tree)
        print(
            f"✅ commands_ext: registered {label} module={module_name} "
            f"global_delta={after_global - before_global} global_total={after_global} "
            f"guild_delta={after_guild - before_guild} guild_total={after_guild}"
        )
    except Exception as e:
        errors.append(f"{module_name}: {repr(e)}")
        print(f"⚠️ commands_ext: failed registering {label}: {repr(e)}")


def _masked_secret_state(value: str) -> str:
    if not value:
        return "missing"
    if len(value) < 16:
        return f"present-but-too-short(len={len(value)})"
    return f"present(len={len(value)})"


def _public_guard_findings(profile: str) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []

    deployment_mode = _deployment_mode()
    require_auth = _env_bool("BOT_API_REQUIRE_AUTH", True)
    allow_insecure = _env_bool("BOT_API_ALLOW_INSECURE", False)
    shared_secret = _env_str("BOT_API_SHARED_SECRET", "")
    bind_host = _env_str("BOT_API_BIND_HOST", "127.0.0.1")
    expected_guilds = _env_int("STONEY_EXPECTED_PUBLIC_GUILDS", 1)
    auto_shard = _env_bool("DISCORD_AUTO_SHARD", False)

    if profile in {"full", "dev"}:
        msg = (
            f"STONEY_COMMAND_PROFILE={profile!r} exposes the old top-level command surface. "
            "Use public/minimal before beta/public rollout."
        )
        (blockers if deployment_mode in {"public", "prod", "production"} else warnings).append(msg)

    if not require_auth:
        msg = "BOT_API_REQUIRE_AUTH=false leaves the structured bot API unauthenticated."
        (blockers if deployment_mode in {"public", "prod", "production"} else warnings).append(msg)

    if allow_insecure:
        msg = "BOT_API_ALLOW_INSECURE=true is local-dev only and must be false for public use."
        (blockers if deployment_mode in {"public", "prod", "production"} else warnings).append(msg)

    if require_auth and len(shared_secret) < 32:
        msg = (
            "BOT_API_SHARED_SECRET should be a strong random secret with at least 32 characters "
            f"({_masked_secret_state(shared_secret)})."
        )
        (blockers if deployment_mode in {"public", "prod", "production"} else warnings).append(msg)

    if bind_host in {"0.0.0.0", "::"} and not require_auth:
        blockers.append("BOT_API_BIND_HOST is public-facing while API auth is disabled.")

    if expected_guilds >= 100 and not auto_shard:
        warnings.append(
            "STONEY_EXPECTED_PUBLIC_GUILDS is 100+ but DISCORD_AUTO_SHARD is not enabled. "
            "Enable AutoShardedBot before serious public scaling."
        )

    if _env_bool("CLEAR_GLOBAL_COMMANDS_ON_BOOT", False):
        warnings.append(
            "CLEAR_GLOBAL_COMMANDS_ON_BOOT=true is a migration lever. "
            "Turn it back off after old global commands are cleared."
        )

    if _env_str("GUILD_ID", ""):
        warnings.append(
            "GUILD_ID is still set. That is fine for beta or fallback mode, "
            "but production behavior should rely on per-guild DB config."
        )

    return blockers, warnings


def _run_public_startup_guard(profile: str) -> None:
    blockers, warnings = _public_guard_findings(profile)
    deployment_mode = _deployment_mode()
    strict = _strict_public_guard_enabled()

    print(
        f"🧯 public_startup_guard deployment={deployment_mode} "
        f"profile={profile} strict={strict} blockers={len(blockers)} warnings={len(warnings)}"
    )

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

    selected_modules = _selected_command_modules()
    selected_names = [name for name, _fn, _label in selected_modules]
    skipped_names = [name for name, _fn, _label in COMMAND_MODULES if name not in set(selected_names)]

    before_global, before_guild = _tree_command_counts(tree)
    print(
        f"🧩 commands_ext profile profile={profile} "
        f"selected={selected_names} skipped={skipped_names} "
        f"initial_global={before_global} initial_guild={before_guild}"
    )

    for module_name, function_name, label in selected_modules:
        _register_one_module(
            bot=bot,
            tree=tree,
            module_name=module_name,
            function_name=function_name,
            label=label,
            errors=errors,
        )

    _COMMANDS_EXT_REGISTERED = True

    final_global, final_guild = _tree_command_counts(tree)

    if final_global >= 95:
        print(
            f"⚠️ commands_ext command budget high: global={final_global}/100. "
            "Use STONEY_COMMAND_PROFILE=public or minimal before public rollout."
        )

    if errors:
        print(
            f"⚠️ commands_ext registration completed with errors "
            f"final_global={final_global} final_guild={final_guild}:"
        )
        for item in errors:
            print(f"   - {item}")
    else:
        print(
            f"✅ commands_ext registration complete. "
            f"final_global={final_global} final_guild={final_guild} profile={profile}"
        )


__all__ = [
    "register_all_commands",
    "COMMAND_MODULES",
    "COMMAND_PROFILES",
    "DEFAULT_COMMAND_PROFILE",
]
