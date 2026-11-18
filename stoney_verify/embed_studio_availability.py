"""Availability resolution for the finished Embed Studio sub-features.

Historically each sub-feature (scheduled posts, recurring campaigns, forms,
Live Text) was invisible unless an operator env flag was set for the whole
deployment. The membership governing rule replaces that all-or-nothing gate:

1. A legacy env flag remains an operator override: on means available.
2. While Dank Shield Pro is unconfigured (no SKU ids in the environment),
   the features are simply available wherever their storage is ready.
3. Once membership is configured, the features are Pro: guilds covered by a
   Pro account use them; other guilds see an explicit Pro-locked state with
   a route to the Pro panel — never a silently missing feature.

The legacy ``*_enabled()`` functions keep their exact semantics; this module
only layers the membership rule on top of them. The feature modules are
imported lazily inside functions so monkeypatched module attributes in the
existing test suites keep working and no import cycle is created.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from .embed_studio_storage_v2 import storage_v2_enabled
from .services import membership

FEATURE_SCHEDULING = "scheduling"
FEATURE_RECURRING = "recurring"
FEATURE_FORMS = "forms"
FEATURE_LIVE = "live"

STATE_AVAILABLE = "available"
STATE_PRO_LOCKED = "pro_locked"
STATE_DEPLOYMENT_OFF = "deployment_off"

REFUSAL_OFF = "off"
REFUSAL_PRO = "pro"

PREMIUM_FEATURES = {
    FEATURE_SCHEDULING: "embed_studio_scheduling",
    FEATURE_RECURRING: "embed_studio_recurring",
    FEATURE_FORMS: "embed_studio_forms",
    FEATURE_LIVE: "embed_studio_live_text",
}

LABELS = {
    FEATURE_SCHEDULING: "Scheduled posts",
    FEATURE_RECURRING: "Recurring campaigns",
    FEATURE_FORMS: "Forms",
    FEATURE_LIVE: "Live Text",
}

# Shown when the deployment itself cannot serve the feature (studio storage
# is not enabled and no legacy flag is on). Wording matches the messages the
# studio UI has always used for this state.
LEGACY_OFF_MESSAGES = {
    FEATURE_SCHEDULING: "The scheduled sender has not been enabled.",
    FEATURE_RECURRING: "Recurring campaigns have not been enabled.",
    FEATURE_FORMS: "Forms are not enabled on this deployment yet.",
    FEATURE_LIVE: "Live Text is disabled on this deployment.",
}


def _legacy_fn(feature: str) -> Any:
    """Fetch the feature's legacy flag function fresh from its module."""

    if feature == FEATURE_SCHEDULING:
        from .embed_studio_schedules import schedules_enabled
        return schedules_enabled
    if feature == FEATURE_RECURRING:
        from .embed_studio_campaigns import campaigns_enabled
        return campaigns_enabled
    if feature == FEATURE_FORMS:
        from .embed_studio_forms import forms_enabled
        return forms_enabled
    if feature == FEATURE_LIVE:
        from .embed_studio_live_text import live_enabled
        return live_enabled
    raise ValueError(f"Unknown Embed Studio feature: {feature!r}")


def legacy_enabled(feature: str, environ: Mapping[str, str] | None = None) -> bool:
    """The historical operator flag check, with its exact semantics."""

    fn = _legacy_fn(feature)
    if environ is None:
        return bool(fn())
    return bool(fn(environ))


def storage_ready(environ: Mapping[str, str] | None = None) -> bool:
    return storage_v2_enabled(environ)


def sync_button_enabled(feature: str, environ: Mapping[str, str] | None = None) -> bool:
    """Synchronous check for view constructors: may this control be clickable?

    True when the legacy flag is on or the studio storage is ready. When
    membership tiers are configured the button stays clickable for locked
    guilds so the callback can explain the Pro lock, instead of the control
    silently disappearing.
    """

    return legacy_enabled(feature, environ) or storage_ready(environ)


def worker_may_claim(feature: str, environ: Mapping[str, str] | None = None) -> bool:
    """Deployment-level gate for background workers (no guild context yet).

    Per-guild Pro enforcement for claimed rows happens at dispatch time via
    :func:`resolve_state`; a worker only needs to know whether this
    deployment can serve the feature at all.
    """

    return legacy_enabled(feature, environ) or storage_ready(environ)


def service_refusal(
    feature: str, guild_id: int, environ: Mapping[str, str] | None = None,
) -> Optional[str]:
    """Synchronous service-layer gate: None, REFUSAL_OFF or REFUSAL_PRO.

    The service layer has no Discord client, so once membership is
    configured it can only prove coverage from the covered-guild link
    store; the interaction layer performs the full entitlement check.
    Fails closed when the link store cannot be read.
    """

    if legacy_enabled(feature, environ):
        return None
    if not storage_ready(environ):
        return REFUSAL_OFF
    if not membership.membership_configured():
        return None
    try:
        owner = membership.owner_of_guild_sync(int(guild_id or 0))
    except Exception:
        return REFUSAL_PRO
    return None if owner is not None else REFUSAL_PRO


def service_may_serve(
    feature: str, guild_id: int, environ: Mapping[str, str] | None = None,
) -> bool:
    return service_refusal(feature, guild_id, environ) is None


async def resolve_state(
    feature: str, guild_id: int, *, client: Any = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve one guild's state for a feature: available, Pro-locked, or
    deployment-off (studio storage not enabled on this deployment)."""

    if legacy_enabled(feature, environ):
        return STATE_AVAILABLE
    if not storage_ready(environ):
        return STATE_DEPLOYMENT_OFF
    if not membership.membership_configured():
        return STATE_AVAILABLE
    status = await membership.guild_pro_status(int(guild_id or 0), client=client)
    return STATE_PRO_LOCKED if status == membership.STATUS_FREE else STATE_AVAILABLE


async def feature_available(
    feature: str, guild_id: int, *, client: Any = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    state = await resolve_state(feature, guild_id, client=client, environ=environ)
    return state == STATE_AVAILABLE


def pro_locked_text(feature: str) -> str:
    return (
        f"⭐ **Dank Shield Pro** — {LABELS[feature]} is a Pro feature. "
        "Subscribe to Dank Shield Pro and link this server to use it: "
        "open `/dank home` → **Setup & Server Settings** → **Dank Shield Pro**. "
        "The rest of Embed Studio stays free."
    )


async def feature_denial(
    feature: str, guild_id: int, *, client: Any = None,
    environ: Mapping[str, str] | None = None, action: str = "",
) -> Optional[str]:
    """None when the guild may use the feature, else the message to show.

    A Pro-locked refusal is confirmed through the shared guild action guard
    (with the feature's premium_feature name) so the block is recorded the
    same way as every other premium gate; the user-facing text always names
    the feature and the route to the Pro panel.
    """

    state = await resolve_state(feature, guild_id, client=client, environ=environ)
    if state == STATE_AVAILABLE:
        return None
    if state == STATE_DEPLOYMENT_OFF:
        return LEGACY_OFF_MESSAGES[feature]
    try:
        from .guild_action_guard import decide_guild_action
        from .guild_context import get_guild_context

        status = await membership.guild_pro_status(int(guild_id or 0), client=client)
        context = await get_guild_context(int(guild_id or 0))
        decision = decide_guild_action(
            context,
            action=action or f"use {LABELS[feature]}",
            feature="generic",
            premium_feature=PREMIUM_FEATURES[feature],
            pro_status_resolver=membership.pro_status_resolver(status),
        )
        if decision.allowed:
            return None
    except Exception:
        pass
    return pro_locked_text(feature)


__all__ = [
    "FEATURE_FORMS",
    "FEATURE_LIVE",
    "FEATURE_RECURRING",
    "FEATURE_SCHEDULING",
    "LABELS",
    "LEGACY_OFF_MESSAGES",
    "PREMIUM_FEATURES",
    "REFUSAL_OFF",
    "REFUSAL_PRO",
    "STATE_AVAILABLE",
    "STATE_DEPLOYMENT_OFF",
    "STATE_PRO_LOCKED",
    "feature_available",
    "feature_denial",
    "legacy_enabled",
    "pro_locked_text",
    "resolve_state",
    "service_may_serve",
    "service_refusal",
    "storage_ready",
    "sync_button_enabled",
    "worker_may_claim",
]
