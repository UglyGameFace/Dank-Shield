from __future__ import annotations

"""Server-owned Member Setup automation switch, separate from draft metadata.

Existing ActionSchedule and Builder formats stay compatible. No environment
variables are needed by ordinary server owners; default is strictly OFF.
The only eligible automatic condition is member_setup_revision. Verification
reminder delivery remains solely with its existing worker.
"""

import asyncio
from dataclasses import dataclass
from typing import Mapping

KEY = "action_member_setup_automation_v1"
VERSION = 1


@dataclass(frozen=True)
class MemberSetupAutomation:
    enabled: bool = False

    def to_payload(self) -> dict[str, object]:
        return {"version": VERSION, "enabled": self.enabled}


def parse_automation(cfg: Mapping[str, object]) -> MemberSetupAutomation:
    raw = cfg.get(KEY)
    if raw is None:
        return MemberSetupAutomation()
    if (
        not isinstance(raw, Mapping)
        or type(raw.get("version")) is not int
        or raw["version"] != VERSION
        or type(raw.get("enabled")) is not bool
    ):
        raise ValueError("Unknown or malformed Member Setup automation configuration")
    return MemberSetupAutomation(enabled=raw["enabled"])


def verified_config(cfg: object) -> bool:
    return (
        isinstance(cfg, Mapping)
        and str(cfg.get("source") or "").startswith(("supabase:", "postgres:"))
    )


async def load_automation(guild_id: int):
    from .guild_config import get_guild_config
    if type(guild_id) is not int or guild_id <= 0:
        raise ValueError("Invalid guild")
    cfg = await get_guild_config(guild_id, refresh=True)
    if not verified_config(cfg):
        raise RuntimeError("Automation guild configuration is unavailable")
    return cfg, parse_automation(cfg)


def activation_prerequisites(cfg: Mapping[str, object]) -> str:
    """Validate stored owner state before enabling. Runtime rechecks every pass."""
    from .action_builder_policy import parse_action_builder
    from .action_schedule_policy import parse_schedule
    from .member_setup_service import GUILD_SETUP_KEY, latest_revision, normalize_guild_setup_state

    if not verified_config(cfg):
        return "Server configuration is unavailable."
    try:
        parse_schedule(cfg)
        rule = parse_action_builder(cfg).rule_for("member_setup_revision")
        state = normalize_guild_setup_state(cfg.get(GUILD_SETUP_KEY))
        revision = state.get("current_revision")
        published = latest_revision(state)
        if not rule.included:
            return "Include Member Setup Revision in Action Builder first."
        if not state.get("enabled") or not isinstance(revision, int) or revision <= 0:
            return "Configure and publish Member Setup before enabling reminders."
        if not published or published.get("revision") != revision:
            return "The active Member Setup revision is not published."
    except Exception:
        return "Action Builder, schedule or Member Setup configuration is invalid."
    return ""


async def save_automation_enabled(
    guild_id: int, *, expected_config: Mapping[str, object], enabled: bool,
) -> bool:
    from .guild_config import compare_and_swap_guild_config_key
    from .action_center_postgres import ledger_ready_sync

    if type(enabled) is not bool or type(guild_id) is not int or guild_id <= 0:
        raise ValueError("Invalid automation setting")
    parse_automation(expected_config)
    if not verified_config(expected_config):
        raise RuntimeError("Server settings unavailable")
    if enabled:
        reason = activation_prerequisites(expected_config)
        if reason:
            raise ValueError(reason)
        try:
            ready = await asyncio.wait_for(asyncio.to_thread(ledger_ready_sync), timeout=8)
        except Exception:
            ready = False
        if not ready:
            raise RuntimeError("Private delivery ledger is unavailable. Automation was not enabled.")
    applied, _ = await compare_and_swap_guild_config_key(
        guild_id, KEY, expected=expected_config.get(KEY),
        value=MemberSetupAutomation(enabled).to_payload(),
        source="action_member_setup_automation_toggle",
    )
    return bool(applied)
