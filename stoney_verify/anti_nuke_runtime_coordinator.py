from __future__ import annotations

"""Authoritative AntiNuke runtime bootstrap order.

AntiNuke is intentionally composed from several focused runtime modules. This
coordinator owns when those modules are installed so main.py does not duplicate
imports, error handling, or ordering rules. The behavioral modules keep their
existing idempotence flags and policy ownership; this module only owns bootstrap
sequencing.

Two phases are explicit:

* pre-app: Discord-event, incident, containment, self-action, compatibility, and
  readiness layers that must exist before stoney_verify.app is imported;
* post-app: product/UI policy and hostile re-entry protection that require the app
  command surface to have been imported, but still install before bot.run().
"""

from dataclasses import dataclass
import importlib
from typing import Any, Callable

import discord


@dataclass(frozen=True)
class RuntimeLayer:
    key: str
    module: str
    installer: str
    pass_bot: bool
    false_message: str
    failure_message: str


PRE_APP_LAYERS: tuple[RuntimeLayer, ...] = (
    RuntimeLayer(
        "gateway",
        "anti_nuke_gateway_runtime",
        "install_anti_nuke_gateway_runtime",
        True,
        "ℹ️ AntiNuke gateway runtime was already installed; duplicate skipped",
        "⚠️ AntiNuke gateway runtime install failed; canonical Discord-event/REST protection remains active",
    ),
    RuntimeLayer(
        "finalizer",
        "anti_nuke_finalizer_runtime",
        "install_anti_nuke_finalizer_runtime",
        True,
        "ℹ️ AntiNuke finalizer runtime was already installed; duplicate skipped",
        "⚠️ AntiNuke finalizer runtime install failed; existing AntiNuke protection remains active",
    ),
    RuntimeLayer(
        "incident",
        "anti_nuke_incident_runtime",
        "install_anti_nuke_incident_runtime",
        True,
        "⚠️ AntiNuke incident runtime could not replace the audit listener",
        "🚨 AntiNuke incident runtime install failed",
    ),
    RuntimeLayer(
        "hostile_actor",
        "anti_nuke_hostile_actor_runtime",
        "install_hostile_actor_runtime",
        True,
        "ℹ️ Hostile actor reputation runtime was already installed; duplicate skipped",
        "🚨 Hostile actor reputation runtime install failed",
    ),
    RuntimeLayer(
        "lockdown",
        "anti_nuke_lockdown_runtime",
        "install_anti_nuke_lockdown_runtime",
        True,
        "ℹ️ AntiNuke lockdown runtime was already installed; duplicate skipped",
        "🚨 AntiNuke lockdown runtime install failed",
    ),
    RuntimeLayer(
        "self_action",
        "anti_nuke_self_action_runtime",
        "install_anti_nuke_self_action_runtime",
        True,
        "ℹ️ AntiNuke self-action proof was already installed; duplicate skipped",
        "🚨 AntiNuke self-action proof install failed",
    ),
    RuntimeLayer(
        "zero_damage",
        "anti_nuke_zero_damage_runtime",
        "install_anti_nuke_zero_damage_runtime",
        True,
        "ℹ️ AntiNuke zero-damage runtime was already installed; duplicate skipped",
        "🚨 AntiNuke zero-damage runtime install failed",
    ),
    RuntimeLayer(
        "audit_compat",
        "anti_nuke_audit_compat_runtime",
        "install_anti_nuke_audit_compat_runtime",
        True,
        "ℹ️ AntiNuke audit compatibility runtime was already installed; duplicate skipped",
        "🚨 AntiNuke audit compatibility install failed",
    ),
    RuntimeLayer(
        "readiness_gate",
        "anti_nuke_readiness_gate_runtime",
        "install_anti_nuke_readiness_gate_runtime",
        True,
        "ℹ️ AntiNuke strict readiness gate was already installed; duplicate skipped",
        "🚨 AntiNuke strict readiness gate install failed",
    ),
)

POST_APP_LAYERS: tuple[RuntimeLayer, ...] = (
    RuntimeLayer(
        "product_policy",
        "anti_nuke_product_policy_runtime",
        "install_anti_nuke_product_policy_runtime",
        False,
        "ℹ️ AntiNuke product policy runtime was already installed; duplicate skipped",
        "🚨 AntiNuke product policy runtime install failed",
    ),
    RuntimeLayer(
        "reentry_race",
        "anti_nuke_reentry_race_runtime",
        "install_anti_nuke_reentry_race_runtime",
        True,
        "ℹ️ AntiNuke hostile re-entry race guard was already installed; duplicate skipped",
        "🚨 AntiNuke hostile re-entry race guard install failed",
    ),
)


def _resolve_installer(layer: RuntimeLayer) -> Callable[..., Any]:
    module = importlib.import_module(f".{layer.module}", package=__package__)
    installer = getattr(module, layer.installer, None)
    if not callable(installer):
        raise RuntimeError(
            f"AntiNuke runtime layer {layer.key!r} has no callable {layer.installer}"
        )
    return installer


def _install_layer(bot: discord.Client, layer: RuntimeLayer) -> bool:
    try:
        installer = _resolve_installer(layer)
        installed = bool(installer(bot) if layer.pass_bot else installer())
        if not installed:
            print(layer.false_message)
        return installed
    except Exception as exc:
        print(f"{layer.failure_message}: {type(exc).__name__}: {exc}")
        return False


def _install_phase(
    bot: discord.Client,
    layers: tuple[RuntimeLayer, ...],
) -> dict[str, bool]:
    return {layer.key: _install_layer(bot, layer) for layer in layers}


def install_anti_nuke_pre_app(bot: discord.Client) -> dict[str, bool]:
    """Install every AntiNuke layer that must precede app import."""

    return _install_phase(bot, PRE_APP_LAYERS)


def install_anti_nuke_post_app(bot: discord.Client) -> dict[str, bool]:
    """Install app-dependent AntiNuke layers before the Discord login starts."""

    return _install_phase(bot, POST_APP_LAYERS)


__all__ = [
    "POST_APP_LAYERS",
    "PRE_APP_LAYERS",
    "RuntimeLayer",
    "install_anti_nuke_post_app",
    "install_anti_nuke_pre_app",
]
