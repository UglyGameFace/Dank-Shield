from __future__ import annotations

"""Separate normal containment from the optional Strict Lockdown product tier."""

from types import SimpleNamespace
from typing import Any, Mapping, Optional

import discord

from . import anti_nuke
from . import anti_nuke_guardian_runtime as guardian
from . import anti_nuke_lockdown_runtime as lockdown
from . import anti_nuke_readiness_gate_runtime as readiness
from . import anti_nuke_zero_damage_runtime as zero_damage

_INSTALL_FLAG = "_dank_antinuke_product_policy_installed"
_SETTING_FLAG = "_dank_antinuke_strict_setting_installed"
_PROCESS_FLAG = "_dank_antinuke_product_process_patched"
_GUARDIAN_FLAG = "_dank_antinuke_product_guardian_patched"
STRICT_LOCKDOWN_KEY = readiness.STRICT_LOCKDOWN_KEY
_ALWAYS_FIRST_STRIKE_ACTIONS = frozenset({"member_prune"})
_DIRECT_STRICT_KEYS = frozenset(
    {"channel_delete", "role_delete", "webhook_delete", "message_delete", "message_bulk_delete"}
)
_NORMAL_CONTAIN_NON_PUNITIVE_ACTIONS = frozenset({"message_delete"})


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _is_bot_actor(actor: Any) -> bool:
    if not bool(getattr(actor, "bot", False)):
        return False
    try:
        return int(getattr(actor, "id", 0) or 0) > 0
    except Exception:
        return False


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    return default


def strict_lockdown_active(settings: Mapping[str, Any] | None) -> bool:
    return readiness._strict_lockdown_active(settings)  # noqa: SLF001


def _patch_setting_model() -> bool:
    if bool(getattr(anti_nuke, _SETTING_FLAG, False)):
        return False

    original = anti_nuke.normalize_antinuke_settings
    anti_nuke.ANTINUKE_DEFAULTS.setdefault(STRICT_LOCKDOWN_KEY, False)

    def normalize(cfg: Any) -> dict[str, Any]:
        result = dict(original(cfg))
        result[STRICT_LOCKDOWN_KEY] = _safe_bool(
            _cfg_value(cfg, STRICT_LOCKDOWN_KEY, False),
            False,
        )
        return result

    anti_nuke.normalize_antinuke_settings = normalize
    setattr(anti_nuke, _SETTING_FLAG, True)
    return True


def _closure_value(fn: Any, name: str) -> Any:
    try:
        freevars = tuple(getattr(getattr(fn, "__code__", None), "co_freevars", ()) or ())
        closure = tuple(getattr(fn, "__closure__", ()) or ())
        for key, cell in zip(freevars, closure):
            if key == name:
                return cell.cell_contents
    except Exception:
        return None
    return None


def _strict_action_names() -> frozenset[str]:
    return frozenset(
        set(getattr(lockdown, "_STRICT_GUARDIAN_ACTIONS", frozenset()))
        | set(getattr(zero_damage, "_STRICT_ACTIONS", frozenset()))
    )


def _patch_threshold_policy() -> bool:
    if bool(getattr(anti_nuke, _PROCESS_FLAG, False)):
        return False

    # The earlier lockdown wrapper reads this module-level set at call time.
    # Clearing it restores the canonical engine's behavior in normal Contain:
    # untrusted human operators are first-strike, while trusted operators and
    # operational bots use their configured bounded thresholds.
    lockdown._STRICT_PROCESS_ACTION_KEYS = frozenset()  # noqa: SLF001

    original = anti_nuke._process_claimed_destructive_event  # noqa: SLF001

    async def process(
        guild: discord.Guild,
        *,
        entry: Any,
        action_key: str,
        action_label: str,
        target_label: str,
        threshold_key: str,
        threshold_override: Optional[int] = None,
    ) -> bool:
        try:
            settings = await anti_nuke.get_antinuke_settings(int(guild.id))
        except Exception:
            settings = None
        actor = getattr(entry, "user", None)
        if (
            strict_lockdown_active(settings)
            and action_key in _DIRECT_STRICT_KEYS
            and not _is_bot_actor(actor)
        ):
            threshold_override = 1
        return await original(
            guild,
            entry=entry,
            action_key=action_key,
            action_label=action_label,
            target_label=target_label,
            threshold_key=threshold_key,
            threshold_override=threshold_override,
        )

    anti_nuke._process_claimed_destructive_event = process  # noqa: SLF001
    setattr(anti_nuke, _PROCESS_FLAG, True)
    return True


def _patch_guardian_policy() -> bool:
    if bool(getattr(guardian, _GUARDIAN_FLAG, False)):
        return False

    strict_names = _strict_action_names()

    # #208/#209 intentionally forced these entries to one event. That is now
    # reserved for Strict Lockdown human actors. The canonical engine keeps
    # operational bots on bounded thresholds while unknown bot installs remain
    # governed by the separate bot-add authorization policy.
    for name in strict_names:
        spec = guardian._ACTIONS.get(name)  # noqa: SLF001
        if spec is None:
            continue
        label, threshold_key, counter_key, _override = spec
        guardian._ACTIONS[name] = (  # noqa: SLF001
            label,
            threshold_key,
            counter_key,
            1 if name in _ALWAYS_FIRST_STRIKE_ACTIONS else None,
        )

    current_overwrite = guardian._rollback_untrusted_overwrite  # noqa: SLF001
    current_automod = guardian._rollback_untrusted_automod  # noqa: SLF001
    base_overwrite = _closure_value(current_overwrite, "original_overwrite") or current_overwrite
    base_automod = _closure_value(current_automod, "original_automod") or current_automod

    async def overwrite(guild: Any, entry: Any, actor: Any, action_name: str):
        settings = await anti_nuke.get_antinuke_settings(int(guild.id))
        if (
            strict_lockdown_active(settings)
            and not _is_bot_actor(actor)
            and not anti_nuke._actor_is_owner_or_bot(guild, actor)  # noqa: SLF001
        ):
            actor = SimpleNamespace(id=0, roles=[])
        return await base_overwrite(guild, entry, actor, action_name)

    async def automod(guild: Any, entry: Any, actor: Any, action_name: str):
        settings = await anti_nuke.get_antinuke_settings(int(guild.id))
        if (
            strict_lockdown_active(settings)
            and not _is_bot_actor(actor)
            and not anti_nuke._actor_is_owner_or_bot(guild, actor)  # noqa: SLF001
        ):
            actor = SimpleNamespace(id=0, roles=[])
        return await base_automod(guild, entry, actor, action_name)

    guardian._rollback_untrusted_overwrite = overwrite  # noqa: SLF001
    guardian._rollback_untrusted_automod = automod  # noqa: SLF001

    original_process = guardian._process  # noqa: SLF001

    async def guardian_process(
        guild: Any,
        entry: Any,
        actor: Any,
        action_name: str,
        spec: tuple[str, str, str, Optional[int]],
    ) -> None:
        settings = await anti_nuke.get_antinuke_settings(int(guild.id))
        if (
            action_name in _NORMAL_CONTAIN_NON_PUNITIVE_ACTIONS
            and not strict_lockdown_active(settings)
        ):
            return
        if (
            strict_lockdown_active(settings)
            and action_name in strict_names
            and not _is_bot_actor(actor)
        ):
            label, threshold_key, counter_key, _override = spec
            spec = (label, threshold_key, counter_key, 1)
        await original_process(guild, entry, actor, action_name, spec)

    guardian._process = guardian_process  # noqa: SLF001
    setattr(guardian, _GUARDIAN_FLAG, True)
    return True


def _health_message(prefix: str, missing: list[str], *, strict: bool) -> str:
    items = [str(item) for item in missing if str(item).strip()]
    message = prefix + " **" + "; ".join(items) + "**."
    strict_items = [item for item in items if item.startswith("Strict Lockdown:")]
    bot_items = [item for item in items if item not in strict_items]

    if strict_items:
        message += (
            "\n\n**Strict Lockdown** is the blocker here. Normal **Contain** can remain "
            "enabled with staff permissions; Strict Lockdown requires removing the listed "
            "delegated authority first."
        )
    if bot_items:
        message += (
            "\n\nThese remaining items are Dank Shield containment/readiness requirements. "
            "Fix only the items actually listed above; **Administrator is not required**."
        )
    elif strict and not strict_items:
        message += "\n\nStrict Lockdown has no delegated-authority blocker."
    return message


def install_anti_nuke_product_policy_runtime() -> bool:
    if bool(getattr(anti_nuke, _INSTALL_FLAG, False)):
        return False
    setting = _patch_setting_model()
    threshold = _patch_threshold_policy()
    guardian_policy = _patch_guardian_policy()
    setattr(anti_nuke, _INSTALL_FLAG, True)
    print(
        "Security product policy active: normal contain supports trusted staff; "
        "Strict Lockdown is separate and owner-controlled; "
        f"setting={'patched' if setting else 'ready'}; "
        f"thresholds={'patched' if threshold else 'ready'}; "
        f"guardian={'patched' if guardian_policy else 'ready'}; "
        "ui=native"
    )
    return True


__all__ = [
    "STRICT_LOCKDOWN_KEY",
    "install_anti_nuke_product_policy_runtime",
    "strict_lockdown_active",
]
