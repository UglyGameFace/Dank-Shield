from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
RECOMMEND = ROOT / "stoney_verify/commands_ext/public_setup_recommend.py"
DEFAULTS = ROOT / "stoney_verify/commands_ext/public_setup_defaults.py"
FRESH = ROOT / "stoney_verify/commands_ext/public_setup_fresh_choice.py"
LEGACY_UI = ROOT / "stoney_verify/setup_legacy_voice_cleanup_ui.py"
TEST = ROOT / "tests/test_setup_voice_session_contract_behavior.py"
HELPER = Path(__file__).resolve()


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly 1 match, found {count}")
    return source.replace(old, new, 1)


def replace_between(
    source: str,
    start_marker: str,
    end_marker: str,
    replacement: str,
    label: str,
) -> str:
    start = source.find(start_marker)
    if start < 0:
        raise RuntimeError(f"{label}: start marker not found")
    end = source.find(end_marker, start + len(start_marker))
    if end < 0:
        raise RuntimeError(f"{label}: end marker not found")
    return source[:start] + replacement + source[end:]


def replace_region_block(
    source: str,
    region_start: str,
    region_end: str,
    block_start: str,
    block_end: str,
    replacement: str,
    label: str,
) -> str:
    rs = source.find(region_start)
    if rs < 0:
        raise RuntimeError(f"{label}: region start not found")
    re = source.find(region_end, rs + len(region_start))
    if re < 0:
        raise RuntimeError(f"{label}: region end not found")
    bs = source.find(block_start, rs, re)
    if bs < 0:
        raise RuntimeError(f"{label}: block start not found")
    be = source.find(block_end, bs + len(block_start), re)
    if be < 0:
        raise RuntimeError(f"{label}: block end not found")
    return source[:bs] + replacement + source[be:]


recommend = RECOMMEND.read_text(encoding="utf-8")
defaults = DEFAULTS.read_text(encoding="utf-8")
fresh = FRESH.read_text(encoding="utf-8")
legacy_ui = LEGACY_UI.read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# 1. Voice and queue checks must validate the Discord channel type.
# ---------------------------------------------------------------------------
has_channel_anchor = '''def _has_channel(guild: discord.Guild, cfg: Any, *keys: str) -> bool:\n    for key in keys:\n        if guild.get_channel(_attr_id(cfg, key)) is not None:\n            return True\n    return False\n\n'''
typed_helper = has_channel_anchor + '''\ndef _has_typed_channel(\n    guild: discord.Guild,\n    cfg: Any,\n    expected_type: type,\n    *keys: str,\n) -> bool:\n    """Require a saved setup ID to resolve to the expected Discord type."""\n\n    for key in keys:\n        channel = guild.get_channel(_attr_id(cfg, key))\n        if isinstance(channel, expected_type):\n            return True\n    return False\n\n'''
recommend = replace_once(
    recommend,
    has_channel_anchor,
    typed_helper,
    "typed setup channel helper",
)

# Retire the legacy product assumption that every approved member should have
# permanent access to the shared Voice Verify room.
recommend = replace_between(
    recommend,
    "def _verified_role_voice_access(\n",
    "def _setup_choice_label(cfg: Any) -> str:\n",
    "",
    "remove approved-role Voice access helper",
)

health_voice = '''    if services["voice"]:\n        if _has_typed_channel(\n            guild,\n            cfg,\n            discord.VoiceChannel,\n            "vc_verify_channel_id",\n            "vc_verify_vc_id",\n            "voice_verify_channel_id",\n        ):\n            passing.append(\n                "The Voice Verify room is chosen."\n            )\n        else:\n            blockers.append(\n                "Choose a valid voice channel used for Voice Verify."\n            )\n\n        if _has_typed_channel(\n            guild,\n            cfg,\n            discord.TextChannel,\n            "vc_verify_queue_channel_id",\n            "vc_queue_channel_id",\n            "vc_request_channel_id",\n            "vc_verify_requests_channel_id",\n        ):\n            passing.append(\n                "The private staff Voice Verify request channel is chosen."\n            )\n        else:\n            blockers.append(\n                "Choose a valid text channel where staff receive Voice Verify requests."\n            )\n\n        passing.append(\n            "Voice Verify room access is session-based: only the active requester "\n            "and assigned staff receive temporary access."\n        )\n\n'''
recommend = replace_region_block(
    recommend,
    "async def _build_plain_setup_health_embed(\n",
    "def _build_setup_help_embed() -> discord.Embed:\n",
    '    if services["voice"]:\n',
    '    if (\n        services["id"]',
    health_voice,
    "health Voice session contract",
)

progress_voice = '''    if services["voice"]:\n        check(\n            "Voice Verify room",\n            _has_typed_channel(\n                guild,\n                cfg,\n                discord.VoiceChannel,\n                "vc_verify_channel_id",\n                "vc_verify_vc_id",\n                "voice_verify_channel_id",\n            ),\n            "Press **Continue Setup** to choose the private Voice Verify room.",\n        )\n\n        check(\n            "Voice Verify staff requests",\n            _has_typed_channel(\n                guild,\n                cfg,\n                discord.TextChannel,\n                "vc_verify_queue_channel_id",\n                "vc_queue_channel_id",\n                "vc_request_channel_id",\n                "vc_verify_requests_channel_id",\n            ),\n            "Press **Continue Setup** to choose the private text channel for staff Voice Verify requests.",\n        )\n\n'''
recommend = replace_region_block(
    recommend,
    "async def _setup_progress(\n",
    "def _enabled_feature_text(state: SetupServiceState) -> str:\n",
    '    if services["voice"]:\n',
    '    if services["id"]:\n',
    progress_voice,
    "progress typed Voice resources",
)

guided_voice = '''    if services["voice"]:\n        if not _has_typed_channel(\n            guild,\n            cfg,\n            discord.VoiceChannel,\n            "vc_verify_channel_id",\n            "vc_verify_vc_id",\n            "voice_verify_channel_id",\n        ):\n            return (\n                "channels",\n                "Set Up the Private Voice Verify Room",\n                (\n                    "Dank Shield will connect or create the private room used "\n                    "only by the active requester and assigned staff."\n                ),\n                "voice_verify_channel",\n            )\n\n        if not _has_typed_channel(\n            guild,\n            cfg,\n            discord.TextChannel,\n            "vc_verify_queue_channel_id",\n            "vc_queue_channel_id",\n            "vc_request_channel_id",\n            "vc_verify_requests_channel_id",\n        ):\n            return (\n                "channels",\n                "Set Up Voice Verify Staff Requests",\n                (\n                    "Dank Shield will connect or create the private text channel "\n                    "where staff receive and claim Voice Verify requests."\n                ),\n                "voice_verify_staff_channel",\n            )\n\n'''
recommend = replace_region_block(
    recommend,
    "async def _guided_setup_target(\n",
    "_GUIDED_ONE_ITEM_SPECS: dict[str, dict[str, Any]] = {\n",
    '    if services["voice"]:\n',
    '    if (\n        services["id"]',
    guided_voice,
    "guided Voice session contract",
)

# Remove the orange approved-role permission screen from the remaining generic
# permissions dispatcher.
recommend = replace_region_block(
    recommend,
    "async def _open_guided_target(\n",
    "async def _open_guided_setup(\n",
    '        if requirement_key == "verified_voice_access":\n',
    '        services = _selected_setup_services(cfg)\n',
    "",
    "remove approved-role Voice permission screen",
)

recommend = replace_once(
    recommend,
    '''        "description": (\n            "Choose the voice channel used for staff checks, "\n            "or let Dank Shield create the normal voice channel."\n        ),\n''',
    '''        "description": (\n            "Choose the private room used only during an active Voice Verify "\n            "session, or let Dank Shield create it together with the staff "\n            "request channel."\n        ),\n''',
    "private Voice room description",
)
recommend = replace_once(
    recommend,
    '''        "description": (\n            "Choose the private text channel where staff receive "\n            "Voice Verify requests, or let Dank Shield create it."\n        ),\n''',
    '''        "description": (\n            "Choose the private text channel where staff receive and claim "\n            "Voice Verify requests, or let Dank Shield create it together "\n            "with the session room."\n        ),\n''',
    "staff Voice queue description",
)

# ---------------------------------------------------------------------------
# 2. Guided creation treats the Voice room and staff queue as one feature.
# ---------------------------------------------------------------------------
bundle_code = '''    return item, notes, created, reused\n\n\nasync def _guided_create_voice_bundle(\n    guild: discord.Guild,\n    cfg: Any,\n) -> tuple[dict[str, str], list[str], list[str], list[str]]:\n    """Connect/create both required Voice Verify resources in one action."""\n\n    payload: dict[str, str] = {}\n    notes: list[str] = []\n    created: list[str] = []\n    reused: list[str] = []\n\n    for requirement_key in (\n        "voice_verify_channel",\n        "voice_verify_staff_channel",\n    ):\n        item, item_notes, item_created, item_reused = (\n            await _guided_create_exact_item(\n                guild,\n                cfg,\n                requirement_key,\n            )\n        )\n        notes.extend(item_notes)\n        created.extend(item_created)\n        reused.extend(item_reused)\n\n        item_id = int(getattr(item, "id", 0) or 0)\n        if item_id <= 0:\n            notes.append(\n                f"Could not connect `{requirement_key}` yet."\n            )\n            continue\n\n        payload.update(\n            _guided_item_payload(\n                requirement_key,\n                item_id,\n            )\n        )\n        payload.update(\n            _guided_managed_resource_patch(\n                requirement_key,\n                item_id,\n                created=bool(item_created),\n            )\n        )\n\n    return payload, notes, created, reused\n\n\n'''
recommend = replace_once(
    recommend,
    "    return item, notes, created, reused\n\n\nasync def _guided_create_item(\n",
    bundle_code + "async def _guided_create_item(\n",
    "guided Voice bundle helper",
)

new_guided_create = '''async def _guided_create_item(\n    interaction: discord.Interaction,\n    requirement_key: str,\n) -> None:\n    if not await solid._require_setup_permission(interaction):\n        return\n\n    guild = interaction.guild\n    if guild is None:\n        return await interaction.response.send_message(\n            "❌ This must be used inside a server.",\n            ephemeral=True,\n        )\n\n    await solid._safe_defer_update(interaction)\n\n    if not await _guided_step_is_current(guild, requirement_key):\n        return await _open_guided_setup(interaction)\n\n    cfg = await get_guild_config(\n        guild.id,\n        refresh=True,\n    )\n\n    if requirement_key in {\n        "voice_verify_channel",\n        "voice_verify_staff_channel",\n    }:\n        payload, notes, created, reused = (\n            await _guided_create_voice_bundle(guild, cfg)\n        )\n\n        if payload:\n            await solid._save_config(interaction, payload)\n\n        voice_id = int(payload.get("vc_verify_channel_id", "0") or 0)\n        queue_id = int(\n            payload.get("vc_verify_queue_channel_id", "0") or 0\n        )\n\n        if voice_id <= 0 or queue_id <= 0:\n            details = "\\n".join(notes)[-700:] or (\n                "Discord could not connect both required Voice Verify items yet."\n            )\n            return await _open_guided_setup(\n                interaction,\n                saved_message=(\n                    "Connected the Voice Verify items that were available. "\n                    "The next guided step will show what is still missing.\\n"\n                    + details\n                ),\n            )\n\n        result = (\n            "Created"\n            if created\n            else "Found and connected"\n        )\n        return await _open_guided_setup(\n            interaction,\n            saved_message=(\n                f"{result} the private Voice Verify room and the private "\n                "staff request channel together. Room access is granted only "\n                "to the active requester and assigned staff during a session."\n            ),\n        )\n\n    item, notes, created, reused = await _guided_create_exact_item(\n        guild,\n        cfg,\n        requirement_key,\n    )\n\n    item_id = int(getattr(item, "id", 0) or 0)\n    payload = _guided_item_payload(\n        requirement_key,\n        item_id,\n    )\n    payload.update(\n        _guided_managed_resource_patch(\n            requirement_key,\n            item_id,\n            created=bool(created),\n        )\n    )\n\n    if item_id <= 0 or not payload:\n        embed = _guided_item_embed(requirement_key)\n        embed.add_field(\n            name="I could not create it",\n            value=(\n                "\\n".join(notes)[-1000:]\n                or (\n                    "Discord did not create the item. "\n                    "Check the bot permissions and try again."\n                )\n            ),\n            inline=False,\n        )\n        return await solid._edit_or_followup(\n            interaction,\n            embed=embed,\n            view=GuidedOneItemView(\n                requirement_key=requirement_key,\n            ),\n        )\n\n    await solid._save_config(interaction, payload)\n\n    result = (\n        "Created this item for you."\n        if created\n        else "Found the matching item and connected it."\n    )\n    await _open_guided_setup(\n        interaction,\n        saved_message=(\n            f"{result} Moving to the next setup step."\n        ),\n    )\n\n\n'''
recommend = replace_between(
    recommend,
    "async def _guided_create_item(\n",
    "class GuidedExistingRoleSelect(discord.ui.RoleSelect):\n",
    new_guided_create,
    "replace guided create item behavior",
)

# ---------------------------------------------------------------------------
# 3. New Voice rooms are private until runtime grants per-member session access.
# ---------------------------------------------------------------------------
private_overwrites = '''def _voice_overwrites(\n    guild: discord.Guild,\n    staff_role: Optional[discord.Role],\n    control_role: Optional[discord.Role],\n    unverified_role: Optional[discord.Role],\n) -> dict[Any, discord.PermissionOverwrite]:\n    """Base Voice Verify room access; runtime grants active member overrides."""\n\n    denied = discord.PermissionOverwrite(\n        view_channel=False,\n        connect=False,\n        speak=False,\n    )\n    overwrites: dict[Any, discord.PermissionOverwrite] = {\n        guild.default_role: denied,\n    }\n\n    bot_member = _bot_member(guild)\n    if bot_member:\n        overwrites[bot_member] = discord.PermissionOverwrite(\n            view_channel=True,\n            connect=True,\n            speak=True,\n            move_members=True,\n            manage_channels=True,\n        )\n\n    if unverified_role and not unverified_role.is_default():\n        overwrites[unverified_role] = discord.PermissionOverwrite(\n            view_channel=False,\n            connect=False,\n            speak=False,\n        )\n\n    # Staff/control roles do not receive broad room access. The VC session\n    # owner grants a member-specific overwrite only to the staff member who\n    # accepted/claimed the active request.\n    for role in (staff_role, control_role):\n        if role and not role.is_default():\n            overwrites[role] = discord.PermissionOverwrite(\n                view_channel=False,\n                connect=False,\n                speak=False,\n                move_members=False,\n            )\n\n    return overwrites\n\n\n'''
defaults = replace_between(
    defaults,
    "def _voice_overwrites(\n",
    "def _target_label(target: Any) -> str:\n",
    private_overwrites,
    "private Voice Verify defaults",
)

# ---------------------------------------------------------------------------
# 4. Voice OFF should lead directly to exact legacy cleanup review when needed.
# ---------------------------------------------------------------------------
cleanup_route = '''async def _open_legacy_voice_cleanup_if_needed(\n    interaction: discord.Interaction,\n    guild: discord.Guild,\n    result_message: str,\n    *,\n    already_deferred: bool,\n) -> bool:\n    """Open explicit cleanup review when legacy Voice items remain after OFF."""\n\n    if not str(result_message or "").strip():\n        return False\n\n    from .. import setup_legacy_voice_cleanup\n    from .. import setup_legacy_voice_cleanup_ui\n\n    preview = await (\n        setup_legacy_voice_cleanup.find_legacy_voice_cleanup_candidates(\n            guild\n        )\n    )\n    if preview.blocked_reason or not preview.has_candidates:\n        return False\n\n    await setup_legacy_voice_cleanup_ui.open_legacy_voice_cleanup_review(\n        interaction,\n        result_message=str(result_message),\n        already_deferred=already_deferred,\n    )\n    return True\n\n\n'''
fresh = replace_once(
    fresh,
    "\n\nclass CustomServicePresetSelect(discord.ui.Select):\n",
    "\n\n" + cleanup_route + "class CustomServicePresetSelect(discord.ui.Select):\n",
    "legacy cleanup routing helper",
)

fresh = replace_once(
    fresh,
    '''        if reconcile_note:\n            saved_message += f"\\n{reconcile_note}"\n        await interaction.edit_original_response(\n''',
    '''        if reconcile_note:\n            saved_message += f"\\n{reconcile_note}"\n            if await _open_legacy_voice_cleanup_if_needed(\n                interaction,\n                guild,\n                saved_message,\n                already_deferred=True,\n            ):\n                return\n        await interaction.edit_original_response(\n''',
    "preset Voice OFF cleanup route",
)

fresh = replace_once(
    fresh,
    '''        if dependency_note:\n            saved_message += f"\\n{dependency_note}"\n\n        await interaction.edit_original_response(\n''',
    '''        if dependency_note:\n            saved_message += f"\\n{dependency_note}"\n\n        if changed and not bool(getattr(next_state, "voice", False)):\n            if await _open_legacy_voice_cleanup_if_needed(\n                interaction,\n                guild,\n                saved_message,\n                already_deferred=True,\n            ):\n                return\n\n        await interaction.edit_original_response(\n''',
    "toggle Voice OFF cleanup route",
)

fresh = replace_once(
    fresh,
    '''        state = await _load_custom_state(guild.id)\n        reconcile_note = await _reconcile_voice_resources_if_disabled(\n            guild,\n            state,\n            actor=interaction.user,\n        )\n        await recommend._open_guided_setup(\n''',
    '''        await solid._safe_defer_update(interaction)\n        state = await _load_custom_state(guild.id)\n        reconcile_note = await _reconcile_voice_resources_if_disabled(\n            guild,\n            state,\n            actor=interaction.user,\n        )\n        if reconcile_note and await _open_legacy_voice_cleanup_if_needed(\n            interaction,\n            guild,\n            reconcile_note,\n            already_deferred=True,\n        ):\n            return\n        await recommend._open_guided_setup(\n''',
    "continue Voice OFF cleanup route",
)

new_open_review = '''async def open_legacy_voice_cleanup_review(\n    interaction: discord.Interaction,\n    *,\n    result_message: str = "",\n    already_deferred: bool = False,\n) -> None:\n    if not await solid._require_setup_permission(interaction):\n        return\n    guild = interaction.guild\n    if guild is None:\n        return await interaction.response.send_message(\n            "❌ This must be used inside a server.",\n            ephemeral=True,\n        )\n\n    if not already_deferred:\n        await solid._safe_defer_update(interaction)\n    embed, preview = await _build_review_embed(\n        guild,\n        result_message=result_message,\n    )\n    await solid._edit_or_followup(\n        interaction,\n        embed=embed,\n        view=LegacyVoiceCleanupReviewView(\n            voice_id=preview.voice_id,\n            queue_id=preview.queue_id,\n            can_remove=preview.has_candidates,\n        ),\n    )\n\n\n'''
legacy_ui = replace_between(
    legacy_ui,
    "async def open_legacy_voice_cleanup_review(\n",
    "__all__ = [\n",
    new_open_review,
    "deferred legacy cleanup review entry",
)

# Validate every resulting file before making any write.
for path, text in (
    (RECOMMEND, recommend),
    (DEFAULTS, defaults),
    (FRESH, fresh),
    (LEGACY_UI, legacy_ui),
    (TEST, TEST.read_text(encoding="utf-8")),
):
    compile(text, str(path), "exec")

RECOMMEND.write_text(recommend, encoding="utf-8")
DEFAULTS.write_text(defaults, encoding="utf-8")
FRESH.write_text(fresh, encoding="utf-8")
LEGACY_UI.write_text(legacy_ui, encoding="utf-8")
HELPER.unlink()

subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)

print("✅ Removed permanent Verified-role access from Voice Verify setup.")
print("✅ Voice room and staff queue checks now enforce Voice/Text channel types.")
print("✅ Guided creation connects/creates the Voice room and staff queue together.")
print("✅ New Voice Verify rooms are private until runtime grants session access.")
print("✅ Voice OFF routes unproven legacy items to explicit exact-item cleanup review.")
print("✅ Temporary helper removed from the working tree.")
print("✅ git diff --check passed.")
