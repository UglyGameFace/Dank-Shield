from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Sequence, Tuple


_COMMANDS_EXT_REGISTERED = False

# ============================================================
# Command module profiles
# ------------------------------------------------------------
# Discord has a hard 100 global slash-command cap. Public bots should not
# expose every admin tool as a top-level command. The public profile keeps the
# top-level surface small by using grouped commands:
#   /stoney
#   /ticket
#   /tickets
#   /ticket-intake
#   /ticket-category
#
# Default is PUBLIC now because the project is being hardened for public beta.
# If you need the old one-server/dev layout, set:
#   STONEY_COMMAND_PROFILE=full
#
# Public/beta safety knobs:
#   STONEY_PRODUCTION_MODE=true          -> hard fail on unsafe public config
#   STONEY_STRICT_PUBLIC_GUARD=true      -> hard fail on unsafe public config
#   STONEY_EXPECTED_PUBLIC_GUILDS=100    -> warns when sharding is off
#
# Optional explicit controls:
#   STONEY_COMMAND_MODULES=public_setup_group,public_setup_review,public_setup_by_id,public_setup_picker,public_ticket_group,public_tickets_group,public_ticket_intake_group,public_ticket_category_group,moderation
#   STONEY_COMMAND_MODULES_SKIP=ticket_macro_admin,ticket_automation_admin
# ============================================================

DEFAULT_COMMAND_PROFILE = "public"

CommandRegistrar = Callable[[Any, Any], None]
CommandModuleSpec = Tuple[str, str, str]


COMMAND_MODULES: List[CommandModuleSpec] = [
    ("public_setup_review", "register_public_setup_review_commands", "public grouped /stoney setup review command"),
    ("public_setup_by_id", "register_public_setup_by_id_commands", "public grouped /stoney setup by ID fallback command"),
    ("public_setup_picker", "register_public_setup_picker_commands", "public grouped /stoney interactive setup picker"),
    ("public_setup_group", "register_public_setup_group_commands", "public grouped /stoney setup commands"),
    ("public_ticket_group", "register_public_ticket_group_commands", "public grouped /ticket commands"),
    ("public_tickets_group", "register_public_tickets_group_commands", "public grouped /tickets commands"),
    ("public_ticket_intake_group", "register_public_ticket_intake_group_commands", "public grouped /ticket-intake commands"),
    ("public_ticket_category_group", "register_public_ticket_category_group_commands", "public grouped /ticket-category commands"),
    ("kick_timers", "register_kick_timer_commands", "kick timer commands"),
    ("vc_flow", "register_vc_flow_commands", "VC flow commands"),
    ("ticket_admin", "register_ticket_admin_commands", "ticket admin commands"),
    ("ticket_channel_admin", "register_ticket_channel_admin_commands", "ticket channel admin commands"),
    ("ticket_intake_admin", "register_ticket_intake_admin_commands", "ticket intake admin commands"),
    ("ticket_queue_admin", "register_ticket_queue_admin_commands", "ticket queue admin commands"),
    ("ticket_category_admin", "register_ticket_category_admin_commands", "ticket category admin commands"),
    ("ticket_governance_admin", "register_ticket_governance_admin_commands", "ticket governance admin commands"),
    ("ticket_sla_admin", "register_ticket_sla_admin_commands", "ticket SLA admin commands"),
    ("ticket_resolution_admin", "register_ticket_resolution_admin_commands", "ticket resolution admin commands"),
    ("ticket_macro_admin", "register_ticket_macro_admin_commands", "ticket macro admin commands"),
    ("ticket_automation_admin", "register_ticket_automation_admin_commands", "ticket automation commands"),
    ("moderation", "register_moderation_commands", "moderation commands"),
    ("role_admin", "register_role_admin_commands", "role admin commands"),
    ("identity_admin", "register_identity_admin_commands", "identity truth admin commands"),
    ("channel_cleanup_admin", "register_channel_cleanup_admin_commands", "channel cleanup admin commands"),
]

_LEGACY_MODULES: Tuple[str, ...] = tuple(
    name for name, _fn, _label in COMMAND_MODULES if not name.startswith("public_")
)

# Profiles are intentionally conservative.
# - public: grouped setup/ticket/tickets/intake/category plus a smaller legacy admin surface.
# - minimal: emergency/lightweight profile that keeps only grouped setup/tickets + essentials.
# - full/dev: old single-server behavior; all legacy command modules, no duplicate public groups.
COMMAND_PROFILES: Dict[str, Sequence[str]] = {
    "public": (
        "public_setup_review",
        "public_setup_by_id",
        "public_setup_picker",
        "public_setup_group",
        "public_ticket_group",
        "public_tickets_group",
        "public_ticket_intake_group",
        "public_ticket_category_group",
        "moderation",
        "role_admin",
        "channel_cleanup_admin",
    ),
    "minimal": (
        "public_setup_review",
        "public_setup_by_id",
        "public_setup_picker",
        "public_setup_group",
        "public_ticket_group",
        "public_tickets_group",
        "public_ticket_intake_group",
        "public_ticket_category_group",
        "moderation",
        "role_admin",
    ),
    "full": _LEGACY_MODULES,
    "dev": _LEGACY_MODULES,
}


# ============================================================
# Small helpers
# ============================================================

def _csv_set(value: str) -> set[str]:
    out: set[str] = set()
    for part in str(value or "").split(","):
        item = part.strip().lower()
        if item:
            out.add(item)
    return out


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
        if not raw:
            return int(default)
        return int(raw)
    except Exception:
        return int(default)


def _env_str(name: str, default: str = "") -> str:
    try:
        value = os.getenv(name)
        if value is None:
            return default
        return str(value).strip()
    except Exception:
        return default


def _command_profile() -> str:
    try:
        profile = _env_str("STONEY_COMMAND_PROFILE", DEFAULT_COMMAND_PROFILE).strip().lower()
        return profile or DEFAULT_COMMAND_PROFILE
    except Exception:
        return DEFAULT_COMMAND_PROFILE


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
            try:
                print(f"⚠️ commands_ext: unknown STONEY_COMMAND_MODULES ignored: {unknown}")
            except Exception:
                pass
    else:
        selected_names = set(COMMAND_PROFILES.get(profile, COMMAND_PROFILES[DEFAULT_COMMAND_PROFILE]))
        if profile not in COMMAND_PROFILES:
            try:
                print(
                    f"⚠️ commands_ext: unknown STONEY_COMMAND_PROFILE={profile!r}; "
                    f"falling back to {DEFAULT_COMMAND_PROFILE}"
                )
            except Exception:
                pass
            selected_names = set(COMMAND_PROFILES[DEFAULT_COMMAND_PROFILE])

    if skip:
        unknown_skip = sorted(skip - known_names)
        if unknown_skip:
            try:
                print(f"⚠️ commands_ext: unknown STONEY_COMMAND_MODULES_SKIP ignored: {unknown_skip}")
            except Exception:
                pass
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
            if isinstance(commands, dict):
                global_count = len(commands)
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
    module = __import__(
        f"{__name__}.{module_name}",
        fromlist=[function_name],
    )
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
        try:
            print(
                f"✅ commands_ext: registered {label} "
                f"module={module_name} "
                f"global_delta={after_global - before_global} "
                f"global_total={after_global} "
                f"guild_delta={after_guild - before_guild} "
                f"guild_total={after_guild}"
            )
        except Exception:
            pass
    except Exception as e:
        errors.append(f"{module_name}: {repr(e)}")
        try:
            print(f"⚠️ commands_ext: failed registering {label}: {repr(e)}")
        except Exception:
            pass


# ============================================================
# Public / production readiness guard
# ============================================================

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
        if deployment_mode in {"public", "prod", "production"}:
            blockers.append(msg)
        else:
            warnings.append(msg)

    if not require_auth:
        msg = "BOT_API_REQUIRE_AUTH=false leaves the structured bot API unauthenticated."
        if deployment_mode in {"public", "prod", "production"}:
            blockers.append(msg)
        else:
            warnings.append(msg)

    if allow_insecure:
        msg = "BOT_API_ALLOW_INSECURE=true is local-dev only and must be false for public use."
        if deployment_mode in {"public", "prod", "production"}:
            blockers.append(msg)
        else:
            warnings.append(msg)

    if require_auth and len(shared_secret) < 32:
        msg = (
            "BOT_API_SHARED_SECRET should be a strong random secret with at least 32 characters "
            f"({ _masked_secret_state(shared_secret) })."
        )
        if deployment_mode in {"public", "prod", "production"}:
            blockers.append(msg)
        else:
            warnings.append(msg)

    if bind_host in {"0.0.0.0", "::"} and not require_auth:
        blockers.append("BOT_API_BIND_HOST is public-facing while API auth is disabled.")

    if expected_guilds >= 100 and not auto_shard:
        warnings.append(
            "STONEY_EXPECTED_PUBLIC_GUILDS is 100+ but DISCORD_AUTO_SHARD is not enabled. "
            "Enable AutoShardedBot before serious public scaling."
        )

    if _env_bool("CLEAR_GLOBAL_COMMANDS_ON_BOOT", False):
        warnings.append(
            "CLEAR_GLOBAL_COMMANDS_ON_BOOT=true is a migration lever. Turn it back off after old global commands are cleared."
        )

    if _env_str("GUILD_ID", ""):
        warnings.append(
            "GUILD_ID is still set. That is fine for beta, but production logic must rely on per-guild DB config, not one env guild."
        )

    return blockers, warnings


def _run_public_startup_guard(profile: str) -> None:
    blockers, warnings = _public_guard_findings(profile)
    deployment_mode = _deployment_mode()
    strict = _strict_public_guard_enabled()

    try:
        print(
            "🧯 public_startup_guard "
            f"deployment={deployment_mode} "
            f"profile={profile} "
            f"strict={strict} "
            f"blockers={len(blockers)} warnings={len(warnings)}"
        )
        for item in blockers:
            print(f"🚫 public_startup_guard blocker: {item}")
        for item in warnings:
            print(f"⚠️ public_startup_guard warning: {item}")
    except Exception:
        pass

    if strict and blockers:
        joined = " | ".join(blockers)
        raise RuntimeError(f"Public startup guard blocked unsafe deployment: {joined}")


# ============================================================
# Public entrypoint
# ============================================================

def register_all_commands(bot: Any, tree: Any) -> None:
    """
    Central loader for split command modules.

    This lets commands.py stay thin while all real command groups
    live in commands_ext/*.py.

    Safe to call multiple times; it only registers once per process.
    """
    global _COMMANDS_EXT_REGISTERED

    if _COMMANDS_EXT_REGISTERED:
        try:
            print("ℹ️ commands_ext.register_all_commands already ran; skipping duplicate registration.")
        except Exception:
            pass
        return

    errors: list[str] = []
    profile = _command_profile()

    _run_public_startup_guard(profile)

    selected_modules = _selected_command_modules()
    selected_names = [name for name, _fn, _label in selected_modules]
    skipped_names = [name for name, _fn, _label in COMMAND_MODULES if name not in set(selected_names)]

    try:
        before_global, before_guild = _tree_command_counts(tree)
        print(
            "🧩 commands_ext profile "
            f"profile={profile} "
            f"selected={selected_names} "
            f"skipped={skipped_names} "
            f"initial_global={before_global} initial_guild={before_guild}"
        )
    except Exception:
        pass

    for module_name, function_name, label in selected_modules:
        _register_one_module(
            bot=bot,
            tree=tree,
            module_name=module_name,
            function_name=function_name,
            label=label,
            errors=errors,
        )

    # NOTE:
    # Do not register runtime_jobs_admin here yet.
    # The bot is already at Discord's 100 global slash-command limit in legacy mode,
    # and runtime queue stats should be exposed through an existing grouped command.

    _COMMANDS_EXT_REGISTERED = True

    try:
        final_global, final_guild = _tree_command_counts(tree)
        if final_global >= 95:
            print(
                f"⚠️ commands_ext command budget high: global={final_global}/100. "
                "Use STONEY_COMMAND_PROFILE=public or minimal before public rollout."
            )

        if errors:
            print(
                "⚠️ commands_ext registration completed with errors "
                f"final_global={final_global} final_guild={final_guild}:"
            )
            for item in errors:
                print(f"   - {item}")
        else:
            print(
                "✅ commands_ext registration complete. "
                f"final_global={final_global} final_guild={final_guild} "
                f"profile={profile}"
            )
    except Exception:
        pass


__all__ = [
    "register_all_commands",
    "COMMAND_MODULES",
    "COMMAND_PROFILES",
    "DEFAULT_COMMAND_PROFILE",
]
