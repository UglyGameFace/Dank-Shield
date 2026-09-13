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
_UI_FLAG = "_dank_antinuke_product_ui_patched"
STRICT_LOCKDOWN_KEY = readiness.STRICT_LOCKDOWN_KEY
_ALWAYS_FIRST_STRIKE_ACTIONS = frozenset({"member_prune"})
_DIRECT_STRICT_KEYS = frozenset(
    {"channel_delete", "role_delete", "webhook_delete", "message_delete", "message_bulk_delete"}
)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


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
    # untrusted operators are first-strike, explicitly trusted operators use
    # their configured bounded thresholds.
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
        if strict_lockdown_active(settings) and action_key in _DIRECT_STRICT_KEYS:
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
    # reserved for Strict Lockdown. The canonical engine still makes unknown
    # actors first-strike in ordinary Contain, while trusted operators retain
    # their configured thresholds.
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
            and not anti_nuke._actor_is_owner_or_bot(guild, actor)  # noqa: SLF001
        ):
            actor = SimpleNamespace(id=0, roles=[])
        return await base_overwrite(guild, entry, actor, action_name)

    async def automod(guild: Any, entry: Any, actor: Any, action_name: str):
        settings = await anti_nuke.get_antinuke_settings(int(guild.id))
        if (
            strict_lockdown_active(settings)
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
        if strict_lockdown_active(settings) and action_name in strict_names:
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


def _patch_ui() -> bool:
    from .commands_ext import public_protection_center as center

    if bool(getattr(center, _UI_FLAG, False)):
        return False

    center.normalize_antinuke_settings = anti_nuke.normalize_antinuke_settings

    async def toggle(interaction: discord.Interaction) -> None:
        if not await center._require_antinuke_owner(interaction):  # noqa: SLF001
            return
        guild = interaction.guild
        if guild is None:
            await center._send_ephemeral(interaction, "This must be used inside a server.")  # noqa: SLF001
            return

        current = await anti_nuke.get_antinuke_settings(int(guild.id))
        target_enabled = not bool(current["antinuke_enabled"])
        candidate = {**current, "antinuke_enabled": target_enabled}
        if target_enabled:
            missing = anti_nuke.antinuke_permission_health(guild, candidate)
            if missing:
                await center._send_ephemeral(  # noqa: SLF001
                    interaction,
                    _health_message(
                        "AntiNuke was not enabled. Readiness blockers:",
                        missing,
                        strict=_safe_bool(candidate.get(STRICT_LOCKDOWN_KEY), False),
                    ),
                )
                return

        saved = await anti_nuke.save_antinuke_settings(
            int(guild.id),
            {"antinuke_enabled": target_enabled},
        )
        await center._refresh_panel(  # noqa: SLF001
            interaction,
            content=(
                "AntiNuke is now **ON**."
                if saved["antinuke_enabled"]
                else "AntiNuke is now **OFF**. Detection settings are saved for later."
            ),
        )

    async def toggle_mode(interaction: discord.Interaction) -> None:
        if not await center._require_antinuke_owner(interaction):  # noqa: SLF001
            return
        guild = interaction.guild
        if guild is None:
            await center._send_ephemeral(interaction, "This must be used inside a server.")  # noqa: SLF001
            return

        current = await anti_nuke.get_antinuke_settings(int(guild.id))
        target_mode = "alert" if current["antinuke_mode"] == "contain" else "contain"
        candidate = {**current, "antinuke_mode": target_mode}
        if target_mode == "alert":
            candidate[STRICT_LOCKDOWN_KEY] = False

        if current["antinuke_enabled"]:
            missing = anti_nuke.antinuke_permission_health(guild, candidate)
            if missing:
                await center._send_ephemeral(  # noqa: SLF001
                    interaction,
                    _health_message(
                        "AntiNuke mode was not changed. Readiness blockers:",
                        missing,
                        strict=_safe_bool(candidate.get(STRICT_LOCKDOWN_KEY), False),
                    ),
                )
                return

        patch: dict[str, Any] = {"antinuke_mode": target_mode}
        if target_mode == "alert":
            patch[STRICT_LOCKDOWN_KEY] = False
        saved = await anti_nuke.save_antinuke_settings(int(guild.id), patch)
        note = f"AntiNuke response mode set to **{saved['antinuke_mode'].upper()}**."
        if target_mode == "alert" and _safe_bool(current.get(STRICT_LOCKDOWN_KEY), False):
            note += " Strict Lockdown was turned off because it only applies to Contain mode."
        await center._refresh_panel(interaction, content=note)  # noqa: SLF001

    async def toggle_strict(interaction: discord.Interaction) -> None:
        if not await center._require_antinuke_owner(interaction):  # noqa: SLF001
            return
        guild = interaction.guild
        if guild is None:
            await center._send_ephemeral(interaction, "This must be used inside a server.")  # noqa: SLF001
            return

        current = await anti_nuke.get_antinuke_settings(int(guild.id))
        if not current["antinuke_enabled"]:
            await center._send_ephemeral(  # noqa: SLF001
                interaction,
                "Enable AntiNuke first. Normal Contain is the recommended starting mode; "
                "Strict Lockdown is an optional additional restriction.",
            )
            return
        if current["antinuke_mode"] != "contain":
            await center._send_ephemeral(  # noqa: SLF001
                interaction,
                "Strict Lockdown only applies to **Contain** mode. Switch the response mode to Contain first.",
            )
            return

        target = not _safe_bool(current.get(STRICT_LOCKDOWN_KEY), False)
        candidate = {**current, STRICT_LOCKDOWN_KEY: target}
        if target:
            missing = anti_nuke.antinuke_permission_health(guild, candidate)
            if missing:
                await center._send_ephemeral(  # noqa: SLF001
                    interaction,
                    _health_message(
                        "Strict Lockdown was not enabled. Readiness blockers:",
                        missing,
                        strict=True,
                    ),
                )
                return

        saved = await anti_nuke.save_antinuke_settings(
            int(guild.id),
            {STRICT_LOCKDOWN_KEY: target},
        )
        await center._refresh_panel(  # noqa: SLF001
            interaction,
            content=(
                "Strict Lockdown is now **ON**. Delegated restricted authority must remain removed."
                if saved[STRICT_LOCKDOWN_KEY]
                else "Strict Lockdown is now **OFF**. Normal Contain remains active."
            ),
        )

    original_embed = center._protection_embed  # noqa: SLF001

    def embed(guild: discord.Guild, cfg: Any, spam: dict[str, Any], spam_source: str):
        built = original_embed(guild, cfg, spam, spam_source)
        settings = anti_nuke.normalize_antinuke_settings(cfg)
        strict = _safe_bool(settings.get(STRICT_LOCKDOWN_KEY), False)
        for index, field in enumerate(list(built.fields)):
            name = str(field.name or "")
            value = str(field.value or "")
            if name.startswith("AntiNuke"):
                lines = value.splitlines()
                insert_at = 2 if len(lines) >= 2 else len(lines)
                lines.insert(
                    insert_at,
                    f"**Strict Lockdown:** {'ON' if strict else 'OFF'}",
                )
                lines.append(
                    "Contain allows staff permissions; explicitly trusted staff use configured thresholds. "
                    "Strict Lockdown is the optional maximum-restriction tier."
                )
                built.set_field_at(index, name=name, value="\n".join(lines)[:1024], inline=False)
            elif name == "What buttons do":
                extra = (
                    "\n**Strict Lockdown** = optional owner-only maximum-restriction policy; "
                    "normal Contain does not require removing staff permissions."
                )
                built.set_field_at(index, name=name, value=(value + extra)[:1024], inline=False)
        return built

    BaseView = center.ProtectionCenterView

    class PolicyProtectionCenterView(BaseView):
        @discord.ui.button(
            label="Strict Lockdown",
            emoji="🔐",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_protection:antinuke_strict_lockdown",
            row=4,
        )
        async def antinuke_strict_lockdown_button(
            self,
            interaction: discord.Interaction,
            button: discord.ui.Button,
        ) -> None:
            _ = button

            async def action() -> None:
                await toggle_strict(interaction)

            await center._guard_protection_action(  # noqa: SLF001
                interaction,
                "protection.antinuke.strict_lockdown",
                action,
                defer=True,
            )

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            cfg = kwargs.get("cfg")
            settings = anti_nuke.normalize_antinuke_settings(cfg or {})
            strict = _safe_bool(settings.get(STRICT_LOCKDOWN_KEY), False)
            for child in list(getattr(self, "children", []) or []):
                if str(getattr(child, "custom_id", "") or "") != "dank_protection:antinuke_strict_lockdown":
                    continue
                child.label = f"Lockdown: {'ON' if strict else 'OFF'}"
                child.style = discord.ButtonStyle.danger if strict else discord.ButtonStyle.secondary
                child.emoji = "🔐" if strict else "🔓"

    center._toggle_antinuke = toggle  # noqa: SLF001
    center._toggle_antinuke_mode = toggle_mode  # noqa: SLF001
    center._toggle_antinuke_strict_lockdown = toggle_strict  # noqa: SLF001
    center._protection_embed = embed  # noqa: SLF001
    center.ProtectionCenterView = PolicyProtectionCenterView
    setattr(center, _UI_FLAG, True)
    return True


def install_anti_nuke_product_policy_runtime() -> bool:
    if bool(getattr(anti_nuke, _INSTALL_FLAG, False)):
        return False
    setting = _patch_setting_model()
    threshold = _patch_threshold_policy()
    guardian_policy = _patch_guardian_policy()
    ui = _patch_ui()
    setattr(anti_nuke, _INSTALL_FLAG, True)
    print(
        "Security product policy active: normal contain supports trusted staff; "
        "Strict Lockdown is separate and owner-controlled; "
        f"setting={'patched' if setting else 'ready'}; "
        f"thresholds={'patched' if threshold else 'ready'}; "
        f"guardian={'patched' if guardian_policy else 'ready'}; "
        f"ui={'patched' if ui else 'ready'}"
    )
    return True


__all__ = [
    "STRICT_LOCKDOWN_KEY",
    "install_anti_nuke_product_policy_runtime",
    "strict_lockdown_active",
]
