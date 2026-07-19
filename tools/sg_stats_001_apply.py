from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if new in text:
        print(f"already applied: {path}")
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one match in {path}, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"applied: {path}")


replace_once(
    "stoney_verify/spam_guard.py",
    '''            else:
                action_taken, quarantine_case = await _apply_mode_action(
                    guild=guild,
                    member=member,
                    settings=settings,
                    reason="Spam Guard: repeated external invite attempts blocked by Invite Shield",
                )

            await _log_trigger(
''',
    '''            else:
                action_taken, quarantine_case = await _apply_mode_action(
                    guild=guild,
                    member=member,
                    settings=settings,
                    reason="Spam Guard: repeated external invite attempts blocked by Invite Shield",
                )

            try:
                from .security_stats import record_spam_guard_action

                await record_spam_guard_action(
                    guild.id,
                    deleted_messages=0,
                    action_taken=action_taken,
                    quarantine_case=quarantine_case,
                )
            except Exception as e:
                _debug(f"security stats record failed guild={guild.id} source=invite-shield error={repr(e)}")

            await _log_trigger(
''',
)

replace_once(
    "stoney_verify/spam_guard.py",
    '''            action_taken, quarantine_case = await _apply_mode_action(
                guild=guild,
                member=member,
                settings=settings,
                reason="Spam guard: probable hacked-account spam burst",
            )

            await _log_trigger(
''',
    '''            action_taken, quarantine_case = await _apply_mode_action(
                guild=guild,
                member=member,
                settings=settings,
                reason="Spam guard: probable hacked-account spam burst",
            )

            try:
                from .security_stats import record_spam_guard_action

                await record_spam_guard_action(
                    guild.id,
                    deleted_messages=delete_count,
                    action_taken=action_taken,
                    quarantine_case=quarantine_case,
                )
            except Exception as e:
                _debug(f"security stats record failed guild={guild.id} source=spam-guard error={repr(e)}")

            await _log_trigger(
''',
)

replace_once(
    "stoney_verify/invite_policy_engine.py",
    '''    try:
        await message.delete()
        decision.delete_succeeded = True
        decision.delete_error = ""
        record_invite_decision(message, decision)
        return True
''',
    '''    try:
        await message.delete()
        decision.delete_succeeded = True
        decision.delete_error = ""
        try:
            from stoney_verify.security_stats import record_security_event

            if message.guild is not None:
                await record_security_event(int(message.guild.id), invites_blocked=1)
        except Exception:
            # Statistics must never turn a successful moderation delete into a failure.
            pass
        record_invite_decision(message, decision)
        return True
''',
)

replace_once(
    "stoney_verify/commands_ext/public_protection_center.py",
    '''from ..guild_config import get_guild_config, invalidate_guild_config, upsert_guild_config
from ..interaction_guard import log_interaction_failure, run_guarded_interaction, safe_send_interaction
from .public_setup_group import _require_setup_permission, dank_group
''',
    '''from ..guild_config import get_guild_config, invalidate_guild_config, upsert_guild_config
from ..interaction_guard import log_interaction_failure, run_guarded_interaction, safe_send_interaction
from ..security_stats import (
    SECURITY_STATS_ENABLED_KEY,
    ensure_security_stats_display,
    refresh_security_stats_display,
)
from .public_setup_group import _require_setup_permission, dank_group
''',
)

replace_once(
    "stoney_verify/commands_ext/public_protection_center.py",
    '''            "**Invite Blocker** = live ON/OFF for Discord invite links.\n"
            "**Block All Links** = stop every URL.\n"
            "**Add Filter/Test** = banned words and bypass tests."
''',
    '''            "**Invite Blocker** = live ON/OFF for Discord invite links.\n"
            "**Block All Links** = stop every URL.\n"
            "**Live Stats** = create locked voice-channel counters using real Spam Guard actions.\n"
            "**Add Filter/Test** = banned words and bypass tests."
''',
)

replace_once(
    "stoney_verify/commands_ext/public_protection_center.py",
    '''    cfg = await get_guild_config(int(guild.id), refresh=True)
    spam, spam_source = await _load_spam_settings(int(guild.id))
    embed = _protection_embed(guild, cfg, spam, spam_source)
''',
    '''    cfg = await get_guild_config(int(guild.id), refresh=True)
    spam, spam_source = await _load_spam_settings(int(guild.id))
    try:
        await refresh_security_stats_display(guild, force=True)
    except Exception as exc:
        log_interaction_failure(
            interaction,
            exc,
            stage="security_stats_refresh_failed",
            action_name="protection.live_stats.refresh",
            fix_hint="The Protection Center still works; check Manage Channels/Manage Roles if the live stats display stops updating.",
        )
    embed = _protection_embed(guild, cfg, spam, spam_source)
''',
)

replace_once(
    "stoney_verify/commands_ext/public_protection_center.py",
    '''        elif custom_id == "dank_protection:edit_spamguard":
            child.label = "Spam Guard Actions"
            child.emoji = "🛡️"


async def _apply_protection_preset(interaction: discord.Interaction, preset: str) -> None:
''',
    '''        elif custom_id == "dank_protection:edit_spamguard":
            child.label = "Spam Guard Actions"
            child.emoji = "🛡️"
        elif custom_id == "dank_protection:live_stats":
            live_stats_on = _cfg_bool(cfg, SECURITY_STATS_ENABLED_KEY, False)
            child.label = f"Live Stats: {'ON' if live_stats_on else 'SET UP'}"
            child.style = discord.ButtonStyle.success if live_stats_on else discord.ButtonStyle.secondary


async def _apply_protection_preset(interaction: discord.Interaction, preset: str) -> None:
''',
)

replace_once(
    "stoney_verify/commands_ext/public_protection_center.py",
    '''        await _guard_protection_action(interaction, "protection.allow_links", action, defer=True)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank_protection:refresh", row=3)
''',
    '''        await _guard_protection_action(interaction, "protection.allow_links", action, defer=True)

    @discord.ui.button(label="Live Stats", emoji="📊", style=discord.ButtonStyle.secondary, custom_id="dank_protection:live_stats", row=3)
    async def live_stats_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button

        async def action() -> None:
            if not await _require_setup_permission(interaction):
                return
            guild = interaction.guild
            if guild is None:
                await _send_ephemeral(interaction, "❌ This must be used inside a server.")
                return
            ok, note = await ensure_security_stats_display(guild)
            if not ok:
                await _send_ephemeral(interaction, note)
                return
            await _refresh_panel(interaction, content=note)

        await _guard_protection_action(
            interaction,
            "protection.live_stats",
            action,
            defer=True,
        )

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank_protection:refresh", row=3)
''',
)

replace_once(
    "tests/test_public_protection_center_native_interaction_static.py",
    '''        "protection.open_test_filter_modal",
        "protection.allow_links",
        "protection.refresh",
''',
    '''        "protection.open_test_filter_modal",
        "protection.allow_links",
        "protection.live_stats",
        "protection.refresh",
''',
)

print("SG-STATS-001 runtime integration applied")
