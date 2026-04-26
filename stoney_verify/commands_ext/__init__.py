from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Sequence, Tuple


_COMMANDS_EXT_REGISTERED = False

# ============================================================
# Command module profiles
# ------------------------------------------------------------
# Discord has a hard 100 global slash-command cap. This bot is already at
# that ceiling in the full/dev profile. Public bots need a much smaller global
# command surface while the heavy admin tools move into grouped commands,
# dashboard flows, buttons, menus, and modals.
#
# Default is FULL so the current dev/single-server setup does not break.
# For public scale testing, use:
#   STONEY_COMMAND_PROFILE=public
#
# Optional explicit controls:
#   STONEY_COMMAND_MODULES=public_ticket_group,public_tickets_group,moderation
#   STONEY_COMMAND_MODULES_SKIP=ticket_macro_admin,ticket_automation_admin
# ============================================================

CommandRegistrar = Callable[[Any, Any], None]
CommandModuleSpec = Tuple[str, str, str]


COMMAND_MODULES: List[CommandModuleSpec] = [
    ("public_ticket_group", "register_public_ticket_group_commands", "public grouped /ticket commands"),
    ("public_tickets_group", "register_public_tickets_group_commands", "public grouped /tickets commands"),
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
    ("ticket_automation_admin", "register_ticket_automation_admin_commands", "ticket automation admin commands"),
    ("moderation", "register_moderation_commands", "moderation commands"),
    ("role_admin", "register_role_admin_commands", "role admin commands"),
    ("identity_admin", "register_identity_admin_commands", "identity truth admin commands"),
    ("channel_cleanup_admin", "register_channel_cleanup_admin_commands", "channel cleanup admin commands"),
]

_LEGACY_MODULES: Tuple[str, ...] = tuple(
    name for name, _fn, _label in COMMAND_MODULES if not name.startswith("public_")
)

# Profiles are intentionally conservative.
# - full/dev: current behavior; all legacy command modules, no duplicate public groups.
# - public: grouped ticket/tickets plus a smaller legacy admin surface.
# - minimal: emergency/lightweight profile that keeps only grouped tickets + essentials.
COMMAND_PROFILES: Dict[str, Sequence[str]] = {
    "full": _LEGACY_MODULES,
    "dev": _LEGACY_MODULES,
    "public": (
        "public_ticket_group",
        "public_tickets_group",
        "ticket_intake_admin",
        "moderation",
        "role_admin",
        "channel_cleanup_admin",
    ),
    "minimal": (
        "public_ticket_group",
        "public_tickets_group",
        "moderation",
        "role_admin",
    ),
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


def _command_profile() -> str:
    try:
        profile = str(os.getenv("STONEY_COMMAND_PROFILE", "full") or "full").strip().lower()
        return profile or "full"
    except Exception:
        return "full"


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
        selected_names = set(COMMAND_PROFILES.get(profile, COMMAND_PROFILES["full"]))
        if profile not in COMMAND_PROFILES:
            try:
                print(
                    f"⚠️ commands_ext: unknown STONEY_COMMAND_PROFILE={profile!r}; "
                    "falling back to full"
                )
            except Exception:
                pass
            selected_names = set(COMMAND_PROFILES["full"])

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
    selected_modules = _selected_command_modules()
    selected_names = [name for name, _fn, _label in selected_modules]
    skipped_names = [name for name, _fn, _label in COMMAND_MODULES if name not in set(selected_names)]

    try:
        before_global, before_guild = _tree_command_counts(tree)
        print(
            "🧩 commands_ext profile "
            f"profile={_command_profile()} "
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
    # The bot is already at Discord's 100 global slash-command limit, and adding
    # runtime_jobs_status globally crashes startup with CommandLimitReached.
    # We will expose runtime queue stats through an existing command group later.

    _COMMANDS_EXT_REGISTERED = True

    try:
        final_global, final_guild = _tree_command_counts(tree)
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
                f"profile={_command_profile()}"
            )
    except Exception:
        pass


__all__ = [
    "register_all_commands",
    "COMMAND_MODULES",
    "COMMAND_PROFILES",
]
