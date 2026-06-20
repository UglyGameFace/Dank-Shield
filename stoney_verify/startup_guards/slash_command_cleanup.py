from __future__ import annotations
import os

def _dank_disable_runtime_command_prune() -> bool:
    return str(os.getenv("DANK_DISABLE_RUNTIME_COMMAND_PRUNE", "true")).strip().lower() in {"1", "true", "yes", "on"}


"""Slash command cleanup guard for Dank Shield.

Why this exists:
- During the TicketTool-style command consolidation, old top-level commands like
  /spam_guard, /grant_vr, /ticket_panel_rules_set, and the old /dank root were
  replaced by grouped public commands such as /dank setup, /dank spam,
  /verify grant-vr, and /ticket-panel post.
- Discord keeps previously synced global or guild commands until the next
  successful sync for that scope.
- This guard strips stale aliases from the local CommandTree right before sync.
- In public/production mode, it avoids repeating unchanged global syncs on every
  restart. Re-syncing the same command surface constantly makes Discord clients
  more likely to show stale "command is outdated" notices.
- In public/production mode, stale guild-scoped beta command copies are cleared
  only for explicitly configured cleanup guild IDs. This prevents Discord mobile
  from showing duplicate global+guild slash suggestions without touching random
  public/customer guilds.
- A cleanup epoch is stored with the sync hash. When public command cleanup rules
  change, the epoch forces one clean global sync so Discord receives the pruned
  command surface even if the command hash is unchanged.
- A dangerous emergency wipe still exists behind
  DANK_DANGEROUS_CLEAR_ALL_GLOBAL_COMMANDS_ON_BOOT=true.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from discord import app_commands


_PATCHED = False
_ORIGINAL_SYNC = None
_ORIGINAL_CLEAR_COMMANDS = None

# Bump this value when public command cleanup rules change and Discord needs one
# guaranteed global sync after deployment. This avoids stale global /dank or
# old dev command cache while still allowing future unchanged syncs to be skipped.
COMMAND_CLEANUP_EPOCH = "2026-06-14-verify-panel-command-v2"

STALE_TOP_LEVEL_COMMANDS = {
    "stoney",
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
}

CONFUSING_DANK_CHILDREN = {
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
    "scoreboard",
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
}

ALLOWED_DANK_CHILDREN = {
    "setup",
    "help",
    "commands",
    "spam",
    "cleanup",
    "members",
}

CONFUSING_DANK_CHILDREN = CONFUSING_DANK_CHILDREN
ALLOWED_DANK_CHILDREN = ALLOWED_DANK_CHILDREN


def _env_true(name: str, default: bool = False) -> bool:
    try:
        raw = os.getenv(name, "")
        if not raw:
            return bool(default)
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _env_explicit_true(name: str) -> bool:
    try:
        raw = os.getenv(name)
        if raw is None:
            return False
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return False


def _env_str(name: str, default: str = "") -> str:
    try:
        raw = os.getenv(name)
        if raw is None:
            return default
        text = str(raw).strip()
        return text if text else default
    except Exception:
        return default


def _env_int_set(name: str) -> set[int]:
    out: set[int] = set()
    try:
        raw = _env_str(name, "")
        if not raw:
            return out
        for item in raw.replace(";", ",").replace(" ", ",").split(","):
            text = str(item or "").strip()
            if not text:
                continue
            try:
                value = int(text)
                if value > 0:
                    out.add(value)
            except Exception:
                continue
    except Exception:
        pass
    return out


def _public_scope_enabled() -> bool:
    profile = _env_str("DANK_COMMAND_PROFILE", "public").lower()
    deployment = _env_str("DANK_DEPLOYMENT_MODE", "").lower()
    if not deployment:
        if _env_true("DANK_PRODUCTION_MODE", False):
            deployment = "production"
        elif _env_true("DANK_PUBLIC_MODE", False):
            deployment = "public"
        else:
            deployment = "development"
    return profile in {"public", "minimal"} or deployment in {"public", "prod", "production"}


def _guild_from_sync_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Optional[Any]:
    try:
        if "guild" in kwargs:
            return kwargs.get("guild")
        if args:
            return args[0]
    except Exception:
        pass
    return None


def _scope_label(guild: Optional[Any]) -> str:
    if guild is None:
        return "global"
    try:
        return f"guild:{int(getattr(guild, 'id', guild))}"
    except Exception:
        return "guild:unknown"


def _guild_id(guild: Optional[Any]) -> int:
    try:
        return int(getattr(guild, "id", guild) or 0)
    except Exception:
        return 0


def _guild_command_cleanup_allowlist() -> set[int]:
    """Guilds where stale guild-scoped command copies may be cleared.

    Defaulting to GUILD_ID keeps cleanup limited to the configured beta/home
    guild instead of touching every public guild the bot is installed in.
    Additional IDs can be listed in DANK_GUILD_COMMAND_CLEANUP_IDS or
    DANK_GUILD_COMMAND_CLEANUP_IDS.
    """
    allowed: set[int] = set()
    allowed |= _env_int_set("DANK_GUILD_COMMAND_CLEANUP_IDS")
    allowed |= _env_int_set("DANK_GUILD_COMMAND_CLEANUP_IDS")
    for name in ("GUILD_ID", "DANK_BETA_GUILD_ID", "DANK_BETA_GUILD_ID"):
        allowed |= _env_int_set(name)
    return {gid for gid in allowed if gid > 0}


def _safe_command_names(tree: app_commands.CommandTree[Any], *, guild: Optional[Any] = None) -> list[str]:
    try:
        return [str(cmd.name) for cmd in tree.get_commands(guild=guild)]
    except Exception:
        try:
            return [str(cmd.name) for cmd in tree.get_commands()]
        except Exception:
            return []


def _safe_child_names(group: Any) -> list[str]:
    try:
        return sorted(
            str(getattr(cmd, "name", ""))
            for cmd in list(getattr(group, "commands", []) or [])
            if str(getattr(cmd, "name", "")).strip()
        )
    except Exception:
        return []


def _command_payload(command: Any) -> Any:
    try:
        payload = command.to_dict()
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass

    children: list[Any] = []
    try:
        for child in list(getattr(command, "commands", []) or []):
            children.append(_command_payload(child))
    except Exception:
        children = []

    params: list[str] = []
    try:
        for param in list(getattr(command, "parameters", []) or []):
            params.append(str(getattr(param, "name", param)))
    except Exception:
        params = []

    return {
        "name": str(getattr(command, "name", "")),
        "description": str(getattr(command, "description", "")),
        "children": children,
        "parameters": params,
    }


def _command_surface_hash(tree: app_commands.CommandTree[Any], *, guild: Optional[Any] = None) -> str:
    try:
        commands = list(tree.get_commands(guild=guild))
    except Exception:
        commands = list(tree.get_commands())

    payload = [_command_payload(cmd) for cmd in sorted(commands, key=lambda c: str(getattr(c, "name", "")))]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()


def _sync_state_path() -> Path:
    raw = _env_str("DANK_COMMAND_SYNC_STATE_FILE", "")
    if raw:
        return Path(raw)
    return Path(".dank_command_sync_state.json")


def _read_sync_state() -> dict[str, Any]:
    path = _sync_state_path()
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_sync_state(state: dict[str, Any]) -> None:
    path = _sync_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, sort_keys=True, indent=2), encoding="utf-8")
    except Exception as e:
        try:
            print(f"⚠️ slash_command_cleanup could not write sync state: {type(e).__name__}: {e}")
        except Exception:
            pass


def _should_skip_unchanged_sync(*, guild: Optional[Any], surface_hash: str) -> bool:
    if guild is not None:
        return False
    if not _public_scope_enabled():
        return False
    if _env_true("DANK_FORCE_COMMAND_SYNC_ON_BOOT", False):
        return False
    if not _env_true("DANK_SKIP_UNCHANGED_GLOBAL_SYNC", True):
        return False

    state = _read_sync_state()
    return (
        str(state.get("global", "")) == str(surface_hash)
        and str(state.get("cleanup_epoch", "")) == COMMAND_CLEANUP_EPOCH
    )


def _should_clear_public_guild_command_copy(guild: Optional[Any]) -> bool:
    if guild is None:
        return False
    if not _public_scope_enabled():
        return False
    if _env_explicit_true("DANK_SYNC_BETA_GUILD_COMMANDS"):
        return False
    gid = _guild_id(guild)
    if gid <= 0:
        return False
    if _env_explicit_true("DANK_CLEAR_ANY_GUILD_COMMAND_COPY_ON_BOOT"):
        return True
    return gid in _guild_command_cleanup_allowlist()


def _remember_sync_hash(*, guild: Optional[Any], surface_hash: str) -> None:
    if guild is not None:
        return
    if not _public_scope_enabled():
        return
    state = _read_sync_state()
    state["global"] = str(surface_hash)
    state["cleanup_epoch"] = COMMAND_CLEANUP_EPOCH
    _write_sync_state(state)


def _get_command(tree: app_commands.CommandTree[Any], name: str, *, guild: Optional[Any]) -> Optional[Any]:
    try:
        return tree.get_command(name, guild=guild)
    except TypeError:
        try:
            return tree.get_command(name)
        except Exception:
            return None
    except Exception:
        return None


def _remove_command(tree: app_commands.CommandTree[Any], name: str, *, guild: Optional[Any]) -> bool:
    try:
        tree.remove_command(name, guild=guild)
        return True
    except TypeError:
        try:
            tree.remove_command(name)
            return True
        except Exception:
            return False
    except Exception:
        return False


def remove_stale_top_level_commands(
    tree: app_commands.CommandTree[Any],
    *,
    reason: str = "manual",
    guild: Optional[Any] = None,
) -> list[str]:
    removed: list[str] = []

    for name in sorted(STALE_TOP_LEVEL_COMMANDS):
        existing = _get_command(tree, name, guild=guild)
        if existing is None:
            continue
        if _remove_command(tree, name, guild=guild):
            removed.append(name)

    if removed:
        try:
            print(
                "🧹 slash_command_cleanup removed stale top-level commands "
                f"scope={_scope_label(guild)} reason={reason}: {removed}"
            )
        except Exception:
            pass

    return removed


def _prune_public_group_children(
    tree: app_commands.CommandTree[Any],
    *,
    group_name: str,
    reason: str,
    guild: Optional[Any] = None,
) -> list[str]:
    group = _get_command(tree, group_name, guild=guild)
    if group is None or not hasattr(group, "remove_command"):
        return []

    before = _safe_child_names(group)
    removed: list[str] = []

    for name in sorted(CONFUSING_DANK_CHILDREN):
        try:
            existing = group.get_command(name)
        except Exception:
            existing = None
        if existing is None:
            continue
        try:
            group.remove_command(name)
            removed.append(name)
        except Exception:
            continue

    after = _safe_child_names(group)
    unexpected = [name for name in after if name not in ALLOWED_DANK_CHILDREN]

    if removed or unexpected:
        try:
            print(
                f"🧹 slash_command_cleanup pruned /{group_name} public surface "
                f"scope={_scope_label(guild)} reason={reason} before={before} "
                f"after={after} removed={removed} unexpected_remaining={unexpected}"
            )
        except Exception:
            pass

    return removed


def prune_public_stoney_children(
    tree: app_commands.CommandTree[Any],
    *,
    reason: str = "manual",
    guild: Optional[Any] = None,
) -> list[str]:
    if not _public_scope_enabled():
        return []

    removed: list[str] = []
    removed.extend(_prune_public_group_children(tree, group_name="dank", reason=reason, guild=guild))
    removed.extend(_prune_public_group_children(tree, group_name="stoney", reason=reason, guild=guild))
    return removed


def _should_block_global_clear(guild: Optional[Any]) -> bool:
    if guild is not None:
        return False
    if not _public_scope_enabled():
        return False
    if not _env_true("CLEAR_GLOBAL_COMMANDS_ON_BOOT", False):
        return False
    if _env_true("DANK_DANGEROUS_CLEAR_ALL_GLOBAL_COMMANDS_ON_BOOT", False):
        return False
    return True


def _install_command_registration_compat() -> None:
    """Keep registration-time pruning aligned with pre-sync pruning.

    commands_ext has an earlier registration-phase prune pass that runs before
    CommandTree.sync. The final pre-sync guard is authoritative, but keeping the
    registration pass aligned prevents noisy unexpected_remaining logs and makes
    startup errors meaningful.
    """
    try:
        from stoney_verify import commands_ext

        children = tuple(getattr(commands_ext, "_CONFUSING_DANK_CHILDREN", ()) or ())
        if "scoreboard" not in children:
            setattr(commands_ext, "_CONFUSING_DANK_CHILDREN", children + ("scoreboard",))
    except Exception as e:
        try:
            print(f"⚠️ slash_command_cleanup could not align commands_ext prune list: {type(e).__name__}: {e}")
        except Exception:
            pass

    try:
        from stoney_verify.commands_ext import public_access_control

        if not hasattr(public_access_control, "register_public_access_control"):
            def register_public_access_control(bot: Any = None, tree: Any = None) -> bool:
                return bool(public_access_control.install_public_access_control())

            public_access_control.register_public_access_control = register_public_access_control  # type: ignore[attr-defined]
    except Exception as e:
        try:
            print(f"⚠️ slash_command_cleanup could not add public_access_control registrar: {type(e).__name__}: {e}")
        except Exception:
            pass


def install_slash_command_cleanup_guard() -> None:
    global _PATCHED, _ORIGINAL_SYNC, _ORIGINAL_CLEAR_COMMANDS

    _install_command_registration_compat()

    if _PATCHED:
        return

    _ORIGINAL_SYNC = app_commands.CommandTree.sync
    _ORIGINAL_CLEAR_COMMANDS = app_commands.CommandTree.clear_commands

    async def _patched_sync(self: app_commands.CommandTree[Any], *args: Any, **kwargs: Any):
        guild = _guild_from_sync_args(args, kwargs)

        if _should_clear_public_guild_command_copy(guild):
            try:
                _ORIGINAL_CLEAR_COMMANDS(self, guild=guild)  # type: ignore[misc]
                result = await _ORIGINAL_SYNC(self, *args, **kwargs)  # type: ignore[misc]
                print(
                    "🧹 slash_command_cleanup cleared allowed guild-scoped command copy in public mode "
                    f"scope={_scope_label(guild)} commands={len(result)} "
                    "set DANK_SYNC_BETA_GUILD_COMMANDS=true only for intentional test-guild copies"
                )
                return result
            except Exception as e:
                print(f"⚠️ slash_command_cleanup failed clearing guild command copy scope={_scope_label(guild)}: {type(e).__name__}: {e}")

        remove_stale_top_level_commands(self, reason="pre_sync", guild=guild)
        prune_public_stoney_children(self, reason="pre_sync", guild=guild)
        names = _safe_command_names(self, guild=guild)
        surface_hash = _command_surface_hash(self, guild=guild)

        try:
            state = _read_sync_state()
            previous_epoch = str(state.get("cleanup_epoch", "")) or "none"
            print(
                "🧹 slash_command_cleanup pre-sync command surface "
                f"scope={_scope_label(guild)} count={len(names)} names={names} "
                f"hash={surface_hash[:12]} cleanup_epoch={COMMAND_CLEANUP_EPOCH} "
                f"previous_epoch={previous_epoch}"
            )
        except Exception:
            pass

        if _should_skip_unchanged_sync(guild=guild, surface_hash=surface_hash):
            try:
                print(
                    "🧹 slash_command_cleanup skipped unchanged global slash sync "
                    f"hash={surface_hash[:12]} cleanup_epoch={COMMAND_CLEANUP_EPOCH} "
                    "set DANK_FORCE_COMMAND_SYNC_ON_BOOT=true to force"
                )
            except Exception:
                pass
            return []

        result = await _ORIGINAL_SYNC(self, *args, **kwargs)  # type: ignore[misc]
        _remember_sync_hash(guild=guild, surface_hash=surface_hash)
        return result

    def _patched_clear_commands(self: app_commands.CommandTree[Any], *args: Any, **kwargs: Any):
        guild = kwargs.get("guild", None)
        if _should_block_global_clear(guild):
            try:
                print(
                    "🛑 slash_command_cleanup blocked CLEAR_GLOBAL_COMMANDS_ON_BOOT in public scope. "
                    "Use DANK_DANGEROUS_CLEAR_ALL_GLOBAL_COMMANDS_ON_BOOT=true for an intentional one-time wipe."
                )
            except Exception:
                pass
            return None
        return _ORIGINAL_CLEAR_COMMANDS(self, *args, **kwargs)  # type: ignore[misc]

    app_commands.CommandTree.sync = _patched_sync  # type: ignore[assignment]
    app_commands.CommandTree.clear_commands = _patched_clear_commands  # type: ignore[assignment]

    _PATCHED = True
    try:
        print(
            "🧹 slash_command_cleanup loaded; stale alias cleanup + public /dank surface pruning active "
            f"cleanup_epoch={COMMAND_CLEANUP_EPOCH}"
        )
    except Exception:
        pass


install_slash_command_cleanup_guard()


__all__ = [
    "ALLOWED_DANK_CHILDREN",
    "ALLOWED_DANK_CHILDREN",
    "COMMAND_CLEANUP_EPOCH",
    "CONFUSING_DANK_CHILDREN",
    "CONFUSING_DANK_CHILDREN",
    "STALE_TOP_LEVEL_COMMANDS",
    "install_slash_command_cleanup_guard",
    "prune_public_stoney_children",
    "remove_stale_top_level_commands",
]
