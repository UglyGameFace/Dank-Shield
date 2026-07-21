from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
HELPER = Path(__file__).resolve()

WRITER = ROOT / "stoney_verify/commands_ext/public_setup_config_writer.py"
DEFAULTS = ROOT / "stoney_verify/commands_ext/public_setup_defaults.py"
FRESH = ROOT / "stoney_verify/commands_ext/public_setup_fresh_choice.py"
CLOSE_TEST = ROOT / "tests/test_setup_close_button_style_behavior.py"
RECONCILE = ROOT / "stoney_verify/setup_resource_reconcile.py"
RECONCILE_TEST = ROOT / "tests/test_setup_voice_resource_reconciliation_behavior.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly 1 match, found {count}")
    return text.replace(old, new, 1)


writer = WRITER.read_text(encoding="utf-8")
defaults = DEFAULTS.read_text(encoding="utf-8")
fresh = FRESH.read_text(encoding="utf-8")
close_test = CLOSE_TEST.read_text(encoding="utf-8")

if RECONCILE.exists() or RECONCILE_TEST.exists():
    raise RuntimeError("reconciliation module/test already exists; inspect before retrying")

# ---------------------------------------------------------------------------
# Durable config-key clearing for stale resource mappings.
# ---------------------------------------------------------------------------
writer = replace_once(
    writer,
    "from typing import Any, Mapping, Optional\n",
    "from typing import Any, Iterable, Mapping, Optional\n",
    "config writer Iterable import",
)

writer_insert = '''\n\ndef _settings_payload_without_keys(\n    original: Optional[Mapping[str, Any]],\n    clear_keys: Iterable[str],\n    metadata: Mapping[str, Any],\n) -> dict[str, Any]:\n    \"\"\"Return the merged JSON payload after explicitly removing keys.\"\"\"\n\n    settings = _settings_payload_update(original, {})\n    for key in {str(item).strip() for item in clear_keys if str(item).strip()}:\n        settings.pop(key, None)\n    for key, value in dict(metadata).items():\n        if value is not None:\n            settings[str(key)] = value\n    return settings\n\n\ndef clear_guild_config_keys_sync(\n    guild_id: int,\n    keys: Iterable[str],\n    *,\n    source: str = \"/dank setup resource reconciliation\",\n    actor: Any = None,\n) -> dict[str, Any]:\n    \"\"\"Explicitly clear stale saved setup keys in flat + JSON config storage.\"\"\"\n\n    sb = get_supabase()\n    if sb is None:\n        raise RuntimeError(\"Supabase is not configured/available.\")\n\n    gid = int(guild_id)\n    existing = _fetch_existing_config_row_sync(gid)\n    if not existing:\n        return {\"guild_id\": str(gid)}\n\n    clear_keys = {\n        str(key).strip()\n        for key in keys\n        if str(key).strip()\n        and str(key).strip() not in _CONTROL_KEYS\n        and str(key).strip() not in _BASE_WRITE_KEYS\n        and str(key).strip() not in _JSON_CONFIG_KEYS\n    }\n    if not clear_keys:\n        return dict(existing)\n\n    stamp = _utc_iso()\n    metadata: dict[str, Any] = {\n        \"config_last_write_mode\": \"explicit_override\",\n        \"config_last_write_source\": str(source or \"/dank setup resource reconciliation\")[:300],\n        \"config_last_write_at\": stamp,\n        \"setup_completed\": False,\n        \"setup_completion_invalidated_at\": stamp,\n    }\n    if actor is not None:\n        metadata[\"configured_by_id\"] = str(getattr(actor, \"id\", \"\") or \"\")\n        metadata[\"configured_by_name\"] = str(actor)\n\n    settings = _settings_payload_without_keys(existing, clear_keys, metadata)\n    columns = {str(key) for key in existing.keys()}\n    flat_clear = {key: None for key in clear_keys if key in columns}\n    flat_metadata = {\n        key: value\n        for key, value in metadata.items()\n        if key in columns\n    }\n    base_fields = {\n        \"guild_id\": str(gid),\n        \"updated_at\": stamp,\n    }\n\n    attempts: list[dict[str, Any]] = []\n    if \"settings\" in columns:\n        attempts.append({**base_fields, \"settings\": settings, **flat_clear, **flat_metadata})\n    if \"config\" in columns:\n        attempts.append({**base_fields, \"config\": settings, **flat_clear, **flat_metadata})\n    if flat_clear or flat_metadata:\n        attempts.append({**base_fields, **flat_clear, **flat_metadata})\n\n    if not attempts:\n        return dict(existing)\n\n    table = _config_table_name()\n    last_error: Optional[Exception] = None\n    for payload in attempts:\n        try:\n            response = (\n                sb.table(table)\n                .update(payload)\n                .eq(\"guild_id\", str(gid))\n                .execute()\n            )\n            rows = getattr(response, \"data\", None) or []\n            if rows and isinstance(rows[0], Mapping):\n                return dict(rows[0])\n            refreshed = _fetch_existing_config_row_sync(gid)\n            return refreshed or payload\n        except Exception as exc:\n            last_error = exc\n\n    raise RuntimeError(f\"Failed clearing guild config keys: {last_error!r}\")\n\n\nasync def clear_guild_config_keys(\n    guild_id: int,\n    keys: Iterable[str],\n    *,\n    source: str = \"/dank setup resource reconciliation\",\n    actor: Any = None,\n) -> dict[str, Any]:\n    result = await asyncio.to_thread(\n        clear_guild_config_keys_sync,\n        int(guild_id),\n        tuple(keys),\n        source=source,\n        actor=actor,\n    )\n    try:\n        from ..guild_config import invalidate_guild_config\n\n        invalidate_guild_config(int(guild_id))\n    except Exception:\n        pass\n    return result\n'''

writer = replace_once(
    writer,
    "\n\ndef apply_public_setup_writer_patch() -> bool:\n",
    writer_insert + "\n\ndef apply_public_setup_writer_patch() -> bool:\n",
    "insert config clear API",
)
writer = replace_once(
    writer,
    '    "upsert_guild_config",\n    "apply_public_setup_writer_patch",\n',
    '    "upsert_guild_config",\n    "clear_guild_config_keys_sync",\n    "clear_guild_config_keys",\n    "apply_public_setup_writer_patch",\n',
    "export config clear API",
)

# ---------------------------------------------------------------------------
# Remember exact bot-created Voice Verify resources when defaults create them.
# ---------------------------------------------------------------------------
defaults = replace_once(
    defaults,
    "    vc_verify_channel: Optional[discord.VoiceChannel] = None\n    vc_queue_channel: Optional[discord.TextChannel] = None\n",
    "    vc_verify_channel: Optional[discord.VoiceChannel] = None\n    vc_queue_channel: Optional[discord.TextChannel] = None\n    vc_verify_preexisting = False\n    vc_queue_preexisting = False\n",
    "voice provenance declarations",
)

old_voice_block = '''    if services["voice"]:\n        vc_verify_channel = (\n            _channel_from_config(\n                guild,\n                cfg,\n                discord.VoiceChannel,\n                "vc_verify_channel_id",\n            )\n            or await _ensure_voice(\n                guild,\n                VC_VERIFY_CHANNEL_NAME,\n                category=start_category,\n                overwrites=voice_ow,\n                notes=notes,\n                created=created,\n                reused=reused,\n            )\n        )\n\n        vc_queue_channel = (\n            _channel_from_config(\n                guild,\n                cfg,\n                discord.TextChannel,\n                "vc_verify_queue_channel_id",\n                "vc_queue_channel_id",\n                "vc_request_channel_id",\n            )\n            or await _ensure_text(\n                guild,\n                VC_QUEUE_CHANNEL_NAME,\n                category=management_category,\n                overwrites=staff_ow,\n                topic=(\n                    "Staff requests and updates for Voice Verify."\n                ),\n                notes=notes,\n                created=created,\n                reused=reused,\n            )\n        )\n'''
new_voice_block = '''    if services["voice"]:\n        configured_vc_verify = _channel_from_config(\n            guild,\n            cfg,\n            discord.VoiceChannel,\n            "vc_verify_channel_id",\n        )\n        vc_verify_preexisting = bool(\n            configured_vc_verify\n            or _voice_by_name(guild, VC_VERIFY_CHANNEL_NAME)\n        )\n        vc_verify_channel = (\n            configured_vc_verify\n            or await _ensure_voice(\n                guild,\n                VC_VERIFY_CHANNEL_NAME,\n                category=start_category,\n                overwrites=voice_ow,\n                notes=notes,\n                created=created,\n                reused=reused,\n            )\n        )\n\n        configured_vc_queue = _channel_from_config(\n            guild,\n            cfg,\n            discord.TextChannel,\n            "vc_verify_queue_channel_id",\n            "vc_queue_channel_id",\n            "vc_request_channel_id",\n        )\n        vc_queue_preexisting = bool(\n            configured_vc_queue\n            or _text_by_name(guild, VC_QUEUE_CHANNEL_NAME)\n        )\n        vc_queue_channel = (\n            configured_vc_queue\n            or await _ensure_text(\n                guild,\n                VC_QUEUE_CHANNEL_NAME,\n                category=management_category,\n                overwrites=staff_ow,\n                topic=(\n                    "Staff requests and updates for Voice Verify."\n                ),\n                notes=notes,\n                created=created,\n                reused=reused,\n            )\n        )\n'''
defaults = replace_once(
    defaults,
    old_voice_block,
    new_voice_block,
    "voice provenance creation block",
)

old_voice_updates = '''    if services["voice"]:\n        updates.update(\n            {\n                "vc_verify_channel_id": item_id(\n                    vc_verify_channel\n                ),\n                "vc_verify_queue_channel_id": item_id(\n                    vc_queue_channel\n                ),\n            }\n        )\n'''
new_voice_updates = '''    if services["voice"]:\n        updates.update(\n            {\n                "vc_verify_channel_id": item_id(\n                    vc_verify_channel\n                ),\n                "vc_verify_queue_channel_id": item_id(\n                    vc_queue_channel\n                ),\n            }\n        )\n        if vc_verify_channel is not None and not vc_verify_preexisting:\n            updates["vc_verify_channel_managed_id"] = item_id(\n                vc_verify_channel\n            )\n        if vc_queue_channel is not None and not vc_queue_preexisting:\n            updates["vc_verify_queue_channel_managed_id"] = item_id(\n                vc_queue_channel\n            )\n'''
defaults = replace_once(
    defaults,
    old_voice_updates,
    new_voice_updates,
    "voice provenance config markers",
)

# ---------------------------------------------------------------------------
# Custom feature picker: respect saved OFF choices, reconcile resources, red Close.
# ---------------------------------------------------------------------------
autofill_anchor = '''    try:\n        current = state.as_payload()\n    except Exception:\n        current = {}\n\n    if any(\n'''
autofill_replacement = '''    try:\n        current = state.as_payload()\n    except Exception:\n        current = {}\n\n    # Once an owner has explicitly saved Custom Setup feature switches, those\n    # choices are authoritative even when every switch is OFF. Do not resurrect\n    # disabled services merely because old Discord resources still exist.\n    try:\n        cfg = await solid.get_guild_config(\n            guild.id,\n            refresh=True,\n        )  # type: ignore[attr-defined]\n    except Exception:\n        cfg = None\n    if str(\n        _auto_cfg_value(cfg, "setup_service_mode_saved_at", "") or ""\n    ).strip():\n        return state, ""\n\n    if any(\n'''
fresh = replace_once(
    fresh,
    autofill_anchor,
    autofill_replacement,
    "saved custom OFF choices stay authoritative",
)

reconcile_helper = '''\n\nasync def _reconcile_voice_resources_if_disabled(\n    guild: discord.Guild,\n    state: Any,\n    *,\n    actor: Any = None,\n) -> str:\n    if bool(getattr(state, "voice", False)):\n        return ""\n    try:\n        from ..setup_resource_reconcile import (\n            reconcile_disabled_voice_verify,\n        )\n\n        return await reconcile_disabled_voice_verify(\n            guild,\n            actor=actor,\n        )\n    except Exception as exc:\n        return (\n            "⚠️ Voice Verify is OFF, but its unused server items could not "\n            f"be reconciled: `{type(exc).__name__}: {str(exc)[:180]}`"\n        )\n'''
fresh = replace_once(
    fresh,
    "\n\nclass CustomServicePresetSelect(discord.ui.Select):\n",
    reconcile_helper + "\n\nclass CustomServicePresetSelect(discord.ui.Select):\n",
    "insert voice reconciliation helper",
)

preset_old = '''        await _save_custom_services(\n            guild.id,\n            dict(flags),\n            interaction.user,\n        )\n        state = await _load_custom_state(guild.id)\n        await interaction.edit_original_response(\n            embed=_custom_services_embed(\n                guild,\n                state,\n                saved_message=f"Saved **{label}**. {desc}",\n            ),\n            view=CustomServiceModeView(state),\n        )\n'''
preset_new = '''        await _save_custom_services(\n            guild.id,\n            dict(flags),\n            interaction.user,\n        )\n        state = await _load_custom_state(guild.id)\n        reconcile_note = await _reconcile_voice_resources_if_disabled(\n            guild,\n            state,\n            actor=interaction.user,\n        )\n        saved_message = f"Saved **{label}**. {desc}"\n        if reconcile_note:\n            saved_message += f"\\n{reconcile_note}"\n        await interaction.edit_original_response(\n            embed=_custom_services_embed(\n                guild,\n                state,\n                saved_message=saved_message,\n            ),\n            view=CustomServiceModeView(state),\n        )\n'''
fresh = replace_once(fresh, preset_old, preset_new, "preset voice reconciliation")

toggle_old = '''            next_state = await _load_custom_state(guild.id)\n            saved_message = (\n                f"Set **{self.short_label}** to "\n                f"**{'ON' if effective_value else 'OFF'}**."\n            )\n'''
toggle_new = '''            next_state = await _load_custom_state(guild.id)\n            saved_message = (\n                f"Set **{self.short_label}** to "\n                f"**{'ON' if effective_value else 'OFF'}**."\n            )\n            reconcile_note = await _reconcile_voice_resources_if_disabled(\n                guild,\n                next_state,\n                actor=interaction.user,\n            )\n            if reconcile_note:\n                saved_message += f"\\n{reconcile_note}"\n'''
fresh = replace_once(fresh, toggle_old, toggle_new, "toggle voice reconciliation")

continue_old = '''    async def continue_guided(\n        self,\n        interaction: discord.Interaction,\n        button: discord.ui.Button,\n    ) -> None:\n        _ = button\n        await recommend._open_guided_setup(interaction)\n'''
continue_new = '''    async def continue_guided(\n        self,\n        interaction: discord.Interaction,\n        button: discord.ui.Button,\n    ) -> None:\n        _ = button\n        guild = interaction.guild\n        if guild is None:\n            return await interaction.response.send_message(\n                "❌ This must be used inside a server.",\n                ephemeral=True,\n            )\n        state = await _load_custom_state(guild.id)\n        reconcile_note = await _reconcile_voice_resources_if_disabled(\n            guild,\n            state,\n            actor=interaction.user,\n        )\n        await recommend._open_guided_setup(\n            interaction,\n            saved_message=reconcile_note,\n        )\n'''
fresh = replace_once(fresh, continue_old, continue_new, "continue setup reconciliation")

fresh = replace_once(
    fresh,
    '''        label="Close",\n        emoji="✖️",\n        style=discord.ButtonStyle.secondary,\n        custom_id="dank_setup_custom:close",\n''',
    '''        label="Close",\n        emoji="✖️",\n        style=discord.ButtonStyle.danger,\n        custom_id="dank_setup_custom:close",\n''',
    "custom picker Close danger style",
)

# Expand the permanent gray-Close source audit to include the custom picker owner.
close_test = replace_once(
    close_test,
    '        ROOT / "stoney_verify/commands_ext/public_setup_recommend.py",\n',
    '        ROOT / "stoney_verify/commands_ext/public_setup_recommend.py",\n        ROOT / "stoney_verify/commands_ext/public_setup_fresh_choice.py",\n',
    "include fresh choice in Close audit",
)

RECONCILE_SOURCE = '''from __future__ import annotations\n\nfrom datetime import timedelta\nfrom typing import Any\n\nimport discord\n\nfrom .guild_config import get_guild_config\n\nVOICE_MAPPING_KEYS = (\n    "vc_verify_channel_id",\n    "voice_verify_channel_id",\n    "voice_verification_channel_id",\n)\nVOICE_QUEUE_MAPPING_KEYS = (\n    "vc_verify_queue_channel_id",\n    "vc_queue_channel_id",\n    "vc_request_channel_id",\n    "vc_verify_requests_channel_id",\n)\nVOICE_MANAGED_KEY = "vc_verify_channel_managed_id"\nVOICE_QUEUE_MANAGED_KEY = "vc_verify_queue_channel_managed_id"\n\n\ndef _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:\n    try:\n        if hasattr(cfg, "get"):\n            value = cfg.get(key)\n            if value is not None:\n                return value\n    except Exception:\n        pass\n    try:\n        value = getattr(cfg, key, None)\n        if value is not None:\n            return value\n    except Exception:\n        pass\n    for bucket in ("settings", "config", "metadata", "meta"):\n        try:\n            nested = cfg.get(bucket) if hasattr(cfg, "get") else getattr(cfg, bucket, None)\n            if isinstance(nested, dict) and nested.get(key) is not None:\n                return nested.get(key)\n        except Exception:\n            continue\n    return default\n\n\ndef _safe_id(value: Any) -> int:\n    try:\n        return int(str(value or "0").strip() or 0)\n    except Exception:\n        return 0\n\n\ndef _ids_from_cfg(cfg: Any, keys: tuple[str, ...]) -> set[int]:\n    return {\n        parsed\n        for key in keys\n        if (parsed := _safe_id(_cfg_value(cfg, key, 0))) > 0\n    }\n\n\ndef _is_voice_channel(channel: Any) -> bool:\n    return bool(\n        isinstance(channel, discord.VoiceChannel)\n        or getattr(channel, "type", None) == discord.ChannelType.voice\n    )\n\n\ndef _is_text_channel(channel: Any) -> bool:\n    return bool(\n        isinstance(channel, discord.TextChannel)\n        or getattr(channel, "type", None) == discord.ChannelType.text\n    )\n\n\nasync def _audit_proves_bot_created(\n    guild: discord.Guild,\n    channel: Any,\n) -> bool:\n    me = getattr(guild, "me", None)\n    if me is None:\n        return False\n    try:\n        if not bool(getattr(me.guild_permissions, "view_audit_log", False)):\n            return False\n    except Exception:\n        return False\n\n    created_at = getattr(channel, "created_at", None)\n    kwargs: dict[str, Any] = {\n        "limit": 20,\n        "action": discord.AuditLogAction.channel_create,\n    }\n    if created_at is not None:\n        try:\n            kwargs["after"] = created_at - timedelta(minutes=5)\n            kwargs["before"] = created_at + timedelta(minutes=5)\n        except Exception:\n            pass\n\n    try:\n        async for entry in guild.audit_logs(**kwargs):\n            target = getattr(entry, "target", None)\n            if _safe_id(getattr(target, "id", 0)) != _safe_id(getattr(channel, "id", 0)):\n                continue\n            user = getattr(entry, "user", None)\n            return _safe_id(getattr(user, "id", 0)) == _safe_id(getattr(me, "id", 0))\n    except Exception:\n        return False\n    return False\n\n\nasync def _text_channel_is_empty(channel: Any) -> bool:\n    try:\n        async for _message in channel.history(limit=1):\n            return False\n        return True\n    except Exception:\n        # If history cannot be inspected, preserve the channel rather than risk\n        # deleting staff verification history.\n        return False\n\n\ndef _add_note(notes: list[str], text: str) -> None:\n    if text and text not in notes:\n        notes.append(text)\n\n\nasync def reconcile_disabled_voice_verify(\n    guild: discord.Guild,\n    *,\n    actor: Any = None,\n) -> str:\n    \"\"\"Detach Voice Verify when OFF and remove only provably bot-owned defaults.\"\"\"\n\n    from .commands_ext import public_setup_defaults as defaults\n    from .commands_ext.public_setup_config_writer import clear_guild_config_keys\n\n    cfg = await get_guild_config(int(guild.id), refresh=True)\n    mapped_voice_ids = _ids_from_cfg(cfg, VOICE_MAPPING_KEYS)\n    mapped_queue_ids = _ids_from_cfg(cfg, VOICE_QUEUE_MAPPING_KEYS)\n    managed_voice_id = _safe_id(_cfg_value(cfg, VOICE_MANAGED_KEY, 0))\n    managed_queue_id = _safe_id(_cfg_value(cfg, VOICE_QUEUE_MANAGED_KEY, 0))\n\n    candidate_voice_ids = set(mapped_voice_ids)\n    candidate_queue_ids = set(mapped_queue_ids)\n    if managed_voice_id > 0:\n        candidate_voice_ids.add(managed_voice_id)\n    if managed_queue_id > 0:\n        candidate_queue_ids.add(managed_queue_id)\n\n    if not candidate_voice_ids and not candidate_queue_ids:\n        return ""\n\n    notes: list[str] = []\n    clear_keys = set(VOICE_MAPPING_KEYS) | set(VOICE_QUEUE_MAPPING_KEYS)\n\n    for channel_id in sorted(candidate_voice_ids):\n        channel = guild.get_channel(channel_id)\n        if channel is None:\n            if channel_id == managed_voice_id:\n                clear_keys.add(VOICE_MANAGED_KEY)\n            continue\n\n        exact_default = str(getattr(channel, "name", "") or "") == defaults.VC_VERIFY_CHANNEL_NAME\n        proven = bool(\n            channel_id == managed_voice_id\n            or await _audit_proves_bot_created(guild, channel)\n        )\n        if not (_is_voice_channel(channel) and exact_default and proven):\n            if channel_id in mapped_voice_ids:\n                _add_note(\n                    notes,\n                    f"Left {getattr(channel, 'mention', channel)} in place because it is not a proven Dank Shield-managed default.",\n                )\n            continue\n\n        members = list(getattr(channel, "members", []) or [])\n        if members:\n            _add_note(\n                notes,\n                f"Kept {getattr(channel, 'mention', channel)} because someone is currently connected.",\n            )\n            continue\n\n        try:\n            await channel.delete(reason="Dank Shield Voice Verify turned OFF")\n            _add_note(notes, "Removed Dank Shield's unused Voice Verify voice channel.")\n            if channel_id == managed_voice_id:\n                clear_keys.add(VOICE_MANAGED_KEY)\n        except Exception as exc:\n            _add_note(\n                notes,\n                f"Could not remove the unused Voice Verify channel: `{type(exc).__name__}`.",\n            )\n\n    for channel_id in sorted(candidate_queue_ids):\n        channel = guild.get_channel(channel_id)\n        if channel is None:\n            if channel_id == managed_queue_id:\n                clear_keys.add(VOICE_QUEUE_MANAGED_KEY)\n            continue\n\n        exact_default = str(getattr(channel, "name", "") or "") == defaults.VC_QUEUE_CHANNEL_NAME\n        proven = bool(\n            channel_id == managed_queue_id\n            or await _audit_proves_bot_created(guild, channel)\n        )\n        if not (_is_text_channel(channel) and exact_default and proven):\n            if channel_id in mapped_queue_ids:\n                _add_note(\n                    notes,\n                    f"Left {getattr(channel, 'mention', channel)} in place because it is not a proven Dank Shield-managed default.",\n                )\n            continue\n\n        if not await _text_channel_is_empty(channel):\n            _add_note(\n                notes,\n                f"Kept {getattr(channel, 'mention', channel)} because it contains staff history or could not be safely inspected.",\n            )\n            continue\n\n        try:\n            await channel.delete(reason="Dank Shield Voice Verify turned OFF")\n            _add_note(notes, "Removed Dank Shield's empty Voice Verify staff-request channel.")\n            if channel_id == managed_queue_id:\n                clear_keys.add(VOICE_QUEUE_MANAGED_KEY)\n        except Exception as exc:\n            _add_note(\n                notes,\n                f"Could not remove the unused Voice Verify request channel: `{type(exc).__name__}`.",\n            )\n\n    try:\n        await clear_guild_config_keys(\n            int(guild.id),\n            clear_keys,\n            source="/dank setup Voice Verify OFF resource reconciliation",\n            actor=actor,\n        )\n        _add_note(notes, "Cleared Voice Verify's saved channel mappings.")\n    except Exception as exc:\n        _add_note(\n            notes,\n            "⚠️ Voice Verify is OFF, but its old channel mappings could not be cleared: "\n            f"`{type(exc).__name__}: {str(exc)[:160]}`",\n        )\n\n    return "\\n".join(notes)\n'''

RECONCILE_TEST_SOURCE = '''from __future__ import annotations\n\nfrom pathlib import Path\nfrom types import SimpleNamespace\n\nimport discord\nimport pytest\n\nfrom stoney_verify import setup_resource_reconcile as reconcile\nfrom stoney_verify.commands_ext import public_setup_config_writer as writer\nfrom stoney_verify.commands_ext import public_setup_fresh_choice as fresh\n\n\nROOT = Path(__file__).resolve().parents[1]\n\n\nclass _State:\n    tickets = False\n    verification = False\n    voice = False\n    spamguard = False\n    moderation = False\n\n    def as_payload(self):\n        return {\n            "tickets_enabled": False,\n            "verification_enabled": False,\n            "voice_verification_enabled": False,\n            "spam_guard_enabled": False,\n            "moderation_enabled": False,\n        }\n\n\n@pytest.mark.asyncio\nasync def test_saved_all_off_custom_state_is_not_resurrected(monkeypatch):\n    async def fake_cfg(*_args, **_kwargs):\n        return {"setup_service_mode_saved_at": "2026-07-21T00:00:00+00:00"}\n\n    async def should_not_detect(*_args, **_kwargs):\n        raise AssertionError("existing-resource detection must not run after explicit save")\n\n    monkeypatch.setattr(fresh.solid, "get_guild_config", fake_cfg)\n    monkeypatch.setattr(fresh, "_detect_existing_service_payload", should_not_detect)\n\n    state = _State()\n    resolved, message = await fresh._autofill_custom_state_from_existing(\n        SimpleNamespace(id=123),\n        state,\n    )\n    assert resolved is state\n    assert message == ""\n\n\ndef test_config_clear_payload_removes_stale_mapping_key():\n    existing = {\n        "guild_id": "1",\n        "settings": {\n            "vc_verify_channel_id": "123",\n            "keep_me": "yes",\n        },\n        "vc_verify_channel_id": "123",\n    }\n    result = writer._settings_payload_without_keys(\n        existing,\n        {"vc_verify_channel_id"},\n        {"setup_completed": False},\n    )\n    assert "vc_verify_channel_id" not in result\n    assert result["keep_me"] == "yes"\n    assert result["setup_completed"] is False\n\n\nclass _FakeChannel:\n    def __init__(self, channel_id: int, name: str, channel_type: discord.ChannelType):\n        self.id = channel_id\n        self.name = name\n        self.type = channel_type\n        self.mention = f"<#${channel_id}>"\n        self.members = []\n        self.deleted = False\n\n    async def delete(self, *, reason: str = ""):\n        assert reason\n        self.deleted = True\n\n    async def history(self, *, limit: int = 1):\n        if False:\n            yield None\n\n\nclass _FakeGuild:\n    def __init__(self, channels):\n        self.id = 999\n        self._channels = {channel.id: channel for channel in channels}\n        self.me = None\n\n    def get_channel(self, channel_id: int):\n        return self._channels.get(int(channel_id))\n\n\n@pytest.mark.asyncio\nasync def test_voice_off_removes_proven_managed_defaults_and_clears_mappings(monkeypatch):\n    voice = _FakeChannel(101, "🎙️ Voice Verification", discord.ChannelType.voice)\n    queue = _FakeChannel(202, "🎙️・vc-verify-queue", discord.ChannelType.text)\n    guild = _FakeGuild([voice, queue])\n    cfg = {\n        "vc_verify_channel_id": "101",\n        "vc_verify_queue_channel_id": "202",\n        "vc_verify_channel_managed_id": "101",\n        "vc_verify_queue_channel_managed_id": "202",\n    }\n\n    async def fake_cfg(*_args, **_kwargs):\n        return cfg\n\n    cleared = {}\n\n    async def fake_clear(guild_id, keys, **kwargs):\n        cleared["guild_id"] = guild_id\n        cleared["keys"] = set(keys)\n        cleared["kwargs"] = kwargs\n        return {}\n\n    monkeypatch.setattr(reconcile, "get_guild_config", fake_cfg)\n    monkeypatch.setattr(writer, "clear_guild_config_keys", fake_clear)\n\n    message = await reconcile.reconcile_disabled_voice_verify(guild)\n\n    assert voice.deleted is True\n    assert queue.deleted is True\n    assert "vc_verify_channel_id" in cleared["keys"]\n    assert "vc_verify_queue_channel_id" in cleared["keys"]\n    assert "vc_verify_channel_managed_id" in cleared["keys"]\n    assert "vc_verify_queue_channel_managed_id" in cleared["keys"]\n    assert "Removed Dank Shield's unused Voice Verify voice channel." in message\n    assert "Cleared Voice Verify's saved channel mappings." in message\n\n\ndef test_default_builder_records_managed_voice_resource_ids():\n    source = (\n        ROOT / "stoney_verify/commands_ext/public_setup_defaults.py"\n    ).read_text(encoding="utf-8")\n    assert 'updates["vc_verify_channel_managed_id"]' in source\n    assert 'updates["vc_verify_queue_channel_managed_id"]' in source\n    assert "not vc_verify_preexisting" in source\n    assert "not vc_queue_preexisting" in source\n\n\ndef test_custom_picker_close_is_red():\n    view = fresh.CustomServiceModeView(_State())\n    close = next(\n        child\n        for child in view.children\n        if isinstance(child, discord.ui.Button)\n        and str(getattr(child, "label", "") or "") == "Close"\n    )\n    assert close.style == discord.ButtonStyle.danger\n'''

for path, source in (
    (WRITER, writer),
    (DEFAULTS, defaults),
    (FRESH, fresh),
    (CLOSE_TEST, close_test),
    (RECONCILE, RECONCILE_SOURCE),
    (RECONCILE_TEST, RECONCILE_TEST_SOURCE),
):
    compile(source, str(path), "exec")

WRITER.write_text(writer, encoding="utf-8")
DEFAULTS.write_text(defaults, encoding="utf-8")
FRESH.write_text(fresh, encoding="utf-8")
CLOSE_TEST.write_text(close_test, encoding="utf-8")
RECONCILE.write_text(RECONCILE_SOURCE, encoding="utf-8")
RECONCILE_TEST.write_text(RECONCILE_TEST_SOURCE, encoding="utf-8")

HELPER.unlink()
subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)

print("✅ Voice Verify OFF now reconciles stale saved channel mappings.")
print("✅ Only proven Dank Shield-managed default Voice Verify resources auto-delete.")
print("✅ Empty bot-managed request channels auto-delete; history-bearing channels are preserved.")
print("✅ Future auto-created Voice Verify resources store exact managed IDs.")
print("✅ Explicitly saved all-OFF Custom Setup choices cannot be resurrected by stale channels.")
print("✅ Choose Core Features Close button is now red and included in the permanent Close-style audit.")
print("✅ Added resource reconciliation regression coverage.")
print("✅ Temporary helper removed from the working tree.")
print("✅ git diff --check passed.")
