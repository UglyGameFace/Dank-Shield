from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    result, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one regex match, found {count}")
    return result


# ---------------------------------------------------------------------------
# Central service enforcement
# ---------------------------------------------------------------------------
service_path = ROOT / "stoney_verify/tickets_new/service.py"
service = service_path.read_text(encoding="utf-8")

service = replace_once(
    service,
    "from .counter_allocator import reserve_next_ticket_number as reserve_persistent_ticket_number\n",
    "from .counter_allocator import reserve_next_ticket_number as reserve_persistent_ticket_number\n"
    "from .claim_policy import TicketActionDecision, evaluate_ticket_action\n",
    "service policy import",
)

owner_helper = '''def _ticket_owner_id(row: Optional[Dict[str, Any]]) -> int:\n    if not isinstance(row, dict):\n        return 0\n    for key in ("user_id", "owner_id", "requester_id"):\n        try:\n            value = int(str(row.get(key) or "0") or 0)\n            if value > 0:\n                return value\n        except Exception:\n            continue\n    return 0\n'''
owner_with_policy = owner_helper + '''\n\nasync def authorize_ticket_action(\n    *,\n    channel_id: int | str,\n    actor: Optional[discord.Member | discord.User],\n    action: str,\n    allow_requester_cancel: bool = False,\n    system_action: bool = False,\n    row: Optional[Dict[str, Any]] = None,\n) -> TicketActionDecision:\n    ticket_row = row if isinstance(row, dict) else await _ticket_row_for_channel_id(channel_id)\n    decision = evaluate_ticket_action(\n        ticket_row,\n        actor_id=_actor_id(actor),\n        action=action,\n        system_action=bool(system_action),\n        allow_requester_cancel=bool(allow_requester_cancel),\n    )\n    if not decision.allowed:\n        _service_debug(\n            f"claim-policy denied channel={channel_id} action={decision.action} "\n            f"actor={decision.actor_id} owner={decision.owner_id} "\n            f"claimed_by={decision.claimed_by_id} status={decision.status} "\n            f"code={decision.code}"\n        )\n    elif decision.code == "system_action":\n        _service_debug(\n            f"claim-policy system channel={channel_id} action={decision.action} "\n            f"status={decision.status}"\n        )\n    return decision\n'''
service = replace_once(service, owner_helper, owner_with_policy, "service authorization helper")

# Staff roles can see unclaimed tickets but cannot speak until an explicit
# claimant member overwrite is applied.
service = service.replace(
    '''            overwrites[role] = discord.PermissionOverwrite(\n                view_channel=True,\n                send_messages=True,\n                manage_messages=True,\n                read_message_history=True,\n                attach_files=True,\n                embed_links=True,\n            )\n''',
    '''            overwrites[role] = discord.PermissionOverwrite(\n                view_channel=True,\n                send_messages=False,\n                manage_messages=False,\n                read_message_history=True,\n                attach_files=False,\n                embed_links=False,\n            )\n''',
    1,
)
service = service.replace(
    '''        overwrites[role] = discord.PermissionOverwrite(\n            view_channel=True,\n            send_messages=True,\n            manage_messages=True,\n            read_message_history=True,\n            attach_files=True,\n            embed_links=True,\n        )\n''',
    '''        overwrites[role] = discord.PermissionOverwrite(\n            view_channel=True,\n            send_messages=False,\n            manage_messages=False,\n            read_message_history=True,\n            attach_files=False,\n            embed_links=False,\n        )\n''',
    1,
)

open_permissions_marker = '''async def _apply_open_permissions(\n    channel: discord.TextChannel,\n    owner: Optional[discord.Member],\n    *,\n    staff_role_ids: Optional[list[int]] = None,\n) -> None:\n    if owner is None:\n        return\n\n    guild = channel.guild\n'''
open_permissions_replacement = '''async def _apply_open_permissions(\n    channel: discord.TextChannel,\n    owner: Optional[discord.Member],\n    *,\n    staff_role_ids: Optional[list[int]] = None,\n) -> None:\n    if owner is None:\n        row = await _ticket_row_for_channel_id(channel.id)\n        await _sync_claimant_channel_permissions(channel, row=row, closed=False)\n        return\n\n    guild = channel.guild\n'''
service = replace_once(service, open_permissions_marker, open_permissions_replacement, "open permission owner fallback")

service = replace_once(
    service,
    '''    try:\n        await channel.edit(overwrites=overwrites, reason="Ticket reopened; owner reply restored")\n    except Exception as e:\n        print(f"⚠️ Failed applying open permissions for {channel.id}: {repr(e)}")\n\n\ndef _member_has_role_id''',
    '''    try:\n        await channel.edit(overwrites=overwrites, reason="Ticket reopened; owner reply restored")\n    except Exception as e:\n        print(f"⚠️ Failed applying open permissions for {channel.id}: {repr(e)}")\n\n    row = await _ticket_row_for_channel_id(channel.id)\n    await _sync_claimant_channel_permissions(channel, row=row, closed=False)\n\n\nasync def _sync_claimant_channel_permissions(\n    channel: discord.TextChannel,\n    *,\n    row: Optional[Dict[str, Any]] = None,\n    previous_claimed_by: int = 0,\n    closed: bool = False,\n) -> None:\n    """Make Claim the only path that grants staff write access."""\n    ticket_row = row if isinstance(row, dict) else await _ticket_row_for_channel_id(channel.id)\n    claimant_id = _ticket_claimed_by_id(ticket_row)\n    guild = channel.guild\n\n    for role_id in _default_staff_role_ids(guild_id=guild.id):\n        role = guild.get_role(int(role_id))\n        if role is None:\n            continue\n        try:\n            await channel.set_permissions(\n                role,\n                view_channel=True,\n                send_messages=False,\n                manage_messages=False,\n                read_message_history=True,\n                attach_files=False,\n                embed_links=False,\n                reason="Claim-first ticket policy: staff must claim before interacting",\n            )\n        except Exception as exc:\n            print(f"⚠️ claim-first staff-role overwrite failed channel={channel.id} role={role_id}: {exc!r}")\n\n    prior_id = _safe_int(previous_claimed_by, 0)\n    if prior_id > 0 and prior_id != claimant_id:\n        prior_member = guild.get_member(prior_id)\n        if prior_member is not None:\n            try:\n                await channel.set_permissions(\n                    prior_member,\n                    overwrite=None,\n                    reason="Ticket claim changed; remove previous claimant access",\n                )\n            except Exception as exc:\n                print(f"⚠️ previous claimant overwrite cleanup failed channel={channel.id} member={prior_id}: {exc!r}")\n\n    if claimant_id > 0:\n        claimant = guild.get_member(claimant_id)\n        if claimant is not None:\n            try:\n                can_write = not closed and _ticket_status(ticket_row) in {"open", "claimed"}\n                await channel.set_permissions(\n                    claimant,\n                    view_channel=True,\n                    send_messages=can_write,\n                    manage_messages=can_write,\n                    read_message_history=True,\n                    attach_files=can_write,\n                    embed_links=can_write,\n                    reason="Ticket claimant access",\n                )\n            except Exception as exc:\n                print(f"⚠️ claimant overwrite failed channel={channel.id} member={claimant_id}: {exc!r}")\n\n\nasync def _sync_claimant_permissions_by_channel_id(\n    channel_id: int | str,\n    *,\n    row: Optional[Dict[str, Any]] = None,\n    previous_claimed_by: int = 0,\n    closed: bool = False,\n) -> None:\n    try:\n        channel = bot.get_channel(int(channel_id))\n    except Exception:\n        channel = None\n    if isinstance(channel, discord.TextChannel):\n        await _sync_claimant_channel_permissions(\n            channel,\n            row=row,\n            previous_claimed_by=previous_claimed_by,\n            closed=closed,\n        )\n\n\ndef _member_has_role_id''',
    "claimant permission helpers",
)

service = replace_once(
    service,
    '''    try:\n        await channel.edit(overwrites=overwrites, reason="Ticket closed; owner reply locked")\n    except Exception as e:\n        print(f"⚠️ Failed applying closed permissions for {channel.id}: {repr(e)}")\n''',
    '''    try:\n        await channel.edit(overwrites=overwrites, reason="Ticket closed; owner reply locked")\n    except Exception as e:\n        print(f"⚠️ Failed applying closed permissions for {channel.id}: {repr(e)}")\n\n    row = await _ticket_row_for_channel_id(channel.id)\n    await _sync_claimant_channel_permissions(channel, row=row, closed=True)\n''',
    "closed claimant permissions",
)

service = replace_once(
    service,
    '''        row_before = await _ticket_row_for_channel_id(channel.id)\n        status_before = _ticket_status(row_before)\n\n        if status_before == "deleted":\n''',
    '''        row_before = await _ticket_row_for_channel_id(channel.id)\n        status_before = _ticket_status(row_before)\n\n        decision = await authorize_ticket_action(\n            channel_id=channel.id,\n            actor=closed_by,\n            action="close",\n            allow_requester_cancel=True,\n            system_action=closed_by is None,\n            row=row_before,\n        )\n        if not decision.allowed:\n            return False\n\n        if status_before == "deleted":\n''',
    "close policy guard",
)

service = replace_once(
    service,
    '''        row_before = await _ticket_row_for_channel_id(channel_id)\n        status_before = _ticket_status(row_before)\n\n        if status_before == "deleted":\n            return True\n''',
    '''        row_before = await _ticket_row_for_channel_id(channel_id)\n        status_before = _ticket_status(row_before)\n\n        decision = await authorize_ticket_action(\n            channel_id=channel_id,\n            actor=deleted_by,\n            action="delete",\n            system_action=deleted_by is None,\n            row=row_before,\n        )\n        if not decision.allowed:\n            return False\n\n        if status_before == "deleted":\n            return True\n''',
    "delete policy guard",
)

service = replace_once(
    service,
    '''    async with lock:\n        row_before = await _ticket_row_for_channel_id(channel_id)\n        if isinstance(row_before, dict):\n            same_url = _safe_str(row_before.get("transcript_url")) == _safe_str(transcript_url)\n''',
    '''    async with lock:\n        row_before = await _ticket_row_for_channel_id(channel_id)\n        decision = await authorize_ticket_action(\n            channel_id=channel_id,\n            actor=actor,\n            action="transcript",\n            system_action=actor is None,\n            row=row_before,\n        )\n        if not decision.allowed:\n            return False\n\n        if isinstance(row_before, dict):\n            same_url = _safe_str(row_before.get("transcript_url")) == _safe_str(transcript_url)\n''',
    "transcript policy guard",
)

service = replace_once(
    service,
    '''            _service_debug(f"assign success channel={channel_id} to={target_staff_id}")\n''',
    '''            await _sync_claimant_permissions_by_channel_id(\n                channel_id,\n                row=ticket_row,\n                previous_claimed_by=existing_claimed_by,\n                closed=False,\n            )\n            _service_debug(f"assign success channel={channel_id} to={target_staff_id}")\n''',
    "claim permission refresh",
)

service = replace_regex_once(
    service,
    r'''        actor_id = _actor_id\(actor\) or 0\n        actor_is_elevated = _actor_is_elevated_staff\(actor\)\n\n        if actor is not None and actor_id > 0:\n            if actor_id != existing_claimed_by and not actor_is_elevated:\n                _service_debug\(\n                    f"unclaim rejected channel=\{channel_id\} "\n                    f"reason=not-owner-of-claim actor=\{actor_id\} claimed_by=\{existing_claimed_by\}"\n                \)\n                return False\n''',
    '''        decision = await authorize_ticket_action(\n            channel_id=channel_id,\n            actor=actor,\n            action="unclaim",\n            system_action=actor is None,\n            row=row,\n        )\n        if not decision.allowed:\n            return False\n''',
    "remove elevated unclaim bypass",
)
service = replace_once(
    service,
    '''            _service_debug(f"unclaim success channel={channel_id} previous={existing_claimed_by}")\n''',
    '''            await _sync_claimant_permissions_by_channel_id(\n                channel_id,\n                row=ticket_row,\n                previous_claimed_by=existing_claimed_by,\n                closed=False,\n            )\n            _service_debug(f"unclaim success channel={channel_id} previous={existing_claimed_by}")\n''',
    "unclaim permission refresh",
)

service = replace_regex_once(
    service,
    r'''        existing_claimed_by = _ticket_claimed_by_id\(row\)\n        actor_id = _actor_id\(actor\) or 0\n        actor_is_elevated = _actor_is_elevated_staff\(actor\)\n\n        if existing_claimed_by > 0 and actor is not None:\n            if actor_id != existing_claimed_by and not actor_is_elevated:\n                _service_debug\(\n                    f"transfer rejected channel=\{channel_id\} "\n                    f"reason=actor-does-not-own-claim actor=\{actor_id\} claimed_by=\{existing_claimed_by\}"\n                \)\n                return False\n''',
    '''        existing_claimed_by = _ticket_claimed_by_id(row)\n        decision = await authorize_ticket_action(\n            channel_id=channel_id,\n            actor=actor,\n            action="transfer",\n            system_action=actor is None,\n            row=row,\n        )\n        if not decision.allowed:\n            return False\n''',
    "remove elevated transfer bypass",
)
service = replace_once(
    service,
    '''            _service_debug(\n                f"transfer success channel={channel_id} "\n                f"from={existing_claimed_by} to={target_staff_id}"\n            )\n''',
    '''            await _sync_claimant_permissions_by_channel_id(\n                channel_id,\n                row=ticket_row,\n                previous_claimed_by=existing_claimed_by,\n                closed=False,\n            )\n            _service_debug(\n                f"transfer success channel={channel_id} "\n                f"from={existing_claimed_by} to={target_staff_id}"\n            )\n''',
    "transfer permission refresh",
)

service = replace_once(
    service,
    '''        row_before = await _ticket_row_for_channel_id(channel_id)\n        if row_before and _ticket_status(row_before) == "deleted":\n            _service_debug(f"set-priority rejected channel={channel_id} reason=deleted")\n            return False\n''',
    '''        row_before = await _ticket_row_for_channel_id(channel_id)\n        decision = await authorize_ticket_action(\n            channel_id=channel_id,\n            actor=actor,\n            action="priority",\n            system_action=actor is None,\n            row=row_before,\n        )\n        if not decision.allowed:\n            return False\n        if row_before and _ticket_status(row_before) == "deleted":\n            _service_debug(f"set-priority rejected channel={channel_id} reason=deleted")\n            return False\n''',
    "priority policy guard",
)

service = replace_once(
    service,
    '''        row_before = await _ticket_row_for_channel_id(channel_id)\n        if row_before and _ticket_status(row_before) == "deleted":\n            _service_debug(f"note rejected channel={channel_id} reason=deleted")\n            return False\n''',
    '''        row_before = await _ticket_row_for_channel_id(channel_id)\n        decision = await authorize_ticket_action(\n            channel_id=channel_id,\n            actor=author,\n            action="note",\n            row=row_before,\n        )\n        if not decision.allowed:\n            return False\n        if row_before and _ticket_status(row_before) == "deleted":\n            _service_debug(f"note rejected channel={channel_id} reason=deleted")\n            return False\n''',
    "note policy guard",
)

service = replace_once(
    service,
    '''        row_before = await _ticket_row_for_channel_id(channel_id)\n        status_before = _ticket_status(row_before)\n\n        if status_before == "deleted":\n            _service_debug(f"reopen rejected channel={channel_id} reason=deleted")\n''',
    '''        row_before = await _ticket_row_for_channel_id(channel_id)\n        status_before = _ticket_status(row_before)\n\n        decision = await authorize_ticket_action(\n            channel_id=channel_id,\n            actor=actor,\n            action="reopen",\n            system_action=actor is None,\n            row=row_before,\n        )\n        if not decision.allowed:\n            return False\n\n        if status_before == "deleted":\n            _service_debug(f"reopen rejected channel={channel_id} reason=deleted")\n''',
    "reopen policy guard",
)

service = replace_once(
    service,
    '''        row_before = await _ticket_row_for_channel_id(channel.id)\n        status_before = _ticket_status(row_before)\n\n        if status_before == "deleted":\n            _service_debug(f"reopen-channel rejected channel={channel.id} reason=deleted")\n''',
    '''        row_before = await _ticket_row_for_channel_id(channel.id)\n        status_before = _ticket_status(row_before)\n\n        decision = await authorize_ticket_action(\n            channel_id=channel.id,\n            actor=actor,\n            action="reopen",\n            system_action=actor is None,\n            row=row_before,\n        )\n        if not decision.allowed:\n            return False\n\n        if status_before == "deleted":\n            _service_debug(f"reopen-channel rejected channel={channel.id} reason=deleted")\n''',
    "reopen channel policy guard",
)

service = replace_once(
    service,
    '''    "find_open_ticket_for_owner",\n''',
    '''    "authorize_ticket_action",\n    "find_open_ticket_for_owner",\n''',
    "service export",
)
service_path.write_text(service, encoding="utf-8")


# ---------------------------------------------------------------------------
# Ticket panel: every staff interaction except Claim is claimant-gated.
# ---------------------------------------------------------------------------
panel_path = ROOT / "stoney_verify/tickets_new/panel.py"
panel = panel_path.read_text(encoding="utf-8")
panel = replace_once(
    panel,
    '''from .service import (\n    add_internal_note,\n''',
    '''from .service import (\n    add_internal_note,\n    authorize_ticket_action,\n''',
    "panel service policy import",
)

panel = replace_once(
    panel,
    '''    async def _runner() -> None:\n        await action()\n''',
    '''    async def _runner() -> None:\n        channel = interaction.channel\n        member = _resolve_member(interaction)\n        if (\n            isinstance(channel, discord.TextChannel)\n            and isinstance(member, discord.Member)\n            and _is_staff_member(member)\n            and label != "claim ticket"\n        ):\n            row = await _ticket_row_for_channel(channel)\n            if isinstance(row, dict):\n                action_map = {\n                    "unclaim ticket": "unclaim",\n                    "transfer ticket": "transfer",\n                    "set priority": "priority",\n                    "add internal note": "note",\n                    "view internal notes": "view_notes",\n                    "list macros": "macro",\n                    "send macro": "macro",\n                    "close ticket": "close",\n                    "ticket info": "view_info",\n                }\n                decision = await authorize_ticket_action(\n                    channel_id=channel.id,\n                    actor=member,\n                    action=action_map.get(label, "interaction"),\n                    row=row,\n                )\n                if not decision.allowed:\n                    await _safe_followup(interaction, f"❌ {decision.message}")\n                    return\n        await action()\n''',
    "panel global claim gate",
)

panel = replace_once(
    panel,
    '''    row = await _ticket_row_for_channel(channel)\n    if not _ticket_is_open_like(channel, row):\n        return await _safe_followup(interaction, _open_panel_state_error(channel, row))\n\n    try:\n        await prompt_ticket_close_confirmation(\n''',
    '''    row = await _ticket_row_for_channel(channel)\n    if not _ticket_is_open_like(channel, row):\n        return await _safe_followup(interaction, _open_panel_state_error(channel, row))\n\n    decision = await authorize_ticket_action(\n        channel_id=channel.id,\n        actor=member,\n        action="close",\n        allow_requester_cancel=True,\n        row=row,\n    )\n    if not decision.allowed:\n        return await _safe_followup(interaction, f"❌ {decision.message}")\n\n    try:\n        await prompt_ticket_close_confirmation(\n''',
    "requester close policy",
)
panel_path.write_text(panel, encoding="utf-8")


# ---------------------------------------------------------------------------
# Macros are interactions and cannot bypass the claimant lock.
# ---------------------------------------------------------------------------
macros_path = ROOT / "stoney_verify/tickets_new/macros_service.py"
macros = macros_path.read_text(encoding="utf-8")
macros = replace_once(
    macros,
    '''try:\n    from .service import mark_ticket_closed as service_mark_ticket_closed\nexcept Exception:\n    service_mark_ticket_closed = None  # type: ignore\n''',
    '''try:\n    from .service import (\n        authorize_ticket_action,\n        mark_ticket_closed as service_mark_ticket_closed,\n    )\nexcept Exception:\n    authorize_ticket_action = None  # type: ignore\n    service_mark_ticket_closed = None  # type: ignore\n''',
    "macro service import",
)
macros = replace_once(
    macros,
    '''        if _ticket_is_deleted(row):\n            return {\n''',
    '''        if authorize_ticket_action is None:\n            return {\n                "ok": False,\n                "message": "Ticket authorization service is unavailable.",\n                "macro": macro,\n                "content": content,\n            }\n\n        decision = await authorize_ticket_action(\n            channel_id=channel.id,\n            actor=actor,\n            action="macro",\n            row=row,\n        )\n        if not decision.allowed:\n            return {\n                "ok": False,\n                "message": decision.message,\n                "macro": macro,\n                "content": content,\n            }\n\n        if _ticket_is_deleted(row):\n            return {\n''',
    "macro claim gate",
)
macros_path.write_text(macros, encoding="utf-8")


# ---------------------------------------------------------------------------
# Runtime enforcement for staff messages and external rename bypasses.
# ---------------------------------------------------------------------------
events_path = ROOT / "stoney_verify/ticket_events.py"
events = events_path.read_text(encoding="utf-8")
events = replace_once(
    events,
    '''from .globals import TICKET_CATEGORY_ID, TRANSCRIPTS_CHANNEL_ID\n''',
    '''from .globals import STAFF_ROLE_ID, TICKET_CATEGORY_ID, TRANSCRIPTS_CHANNEL_ID\nfrom .tickets_new.claim_policy import (\n    evaluate_ticket_action,\n    is_staff_member as claim_policy_is_staff_member,\n    ticket_owner_id as claim_policy_ticket_owner_id,\n)\n''',
    "ticket event policy import",
)

message_marker = '''async def _handle_message(message: discord.Message) -> None:\n'''
message_helpers = '''async def _enforce_claim_first_staff_message(message: discord.Message) -> bool:\n    channel = message.channel\n    author = message.author\n    if not isinstance(channel, discord.TextChannel) or not isinstance(author, discord.Member):\n        return True\n\n    try:\n        row = await _find_ticket_row_by_channel_id(channel.id)\n    except Exception:\n        row = None\n    if not isinstance(row, dict):\n        return True\n\n    owner_id = claim_policy_ticket_owner_id(row)\n    if owner_id > 0 and int(author.id) == owner_id:\n        return True\n\n    staff_ids = tuple(\n        role_id for role_id in (_safe_int(STAFF_ROLE_ID, 0),) if role_id > 0\n    )\n    if not claim_policy_is_staff_member(author, staff_role_ids=staff_ids):\n        return True\n\n    decision = evaluate_ticket_action(\n        row,\n        actor_id=author.id,\n        action="message",\n    )\n    if decision.allowed:\n        return True\n\n    try:\n        await message.delete(\n            reason=f"Claim-first ticket policy: {decision.code}"\n        )\n    except Exception as exc:\n        print(\n            f"⚠️ ticket_claim_policy could not delete unauthorized staff message "\n            f"guild={channel.guild.id} channel={channel.id} actor={author.id} "\n            f"code={decision.code} error={type(exc).__name__}"\n        )\n\n    try:\n        await channel.send(\n            f"🔒 {author.mention}, {decision.message}",\n            delete_after=12,\n            allowed_mentions=discord.AllowedMentions(users=[author], roles=False, everyone=False),\n        )\n    except Exception:\n        pass\n\n    print(\n        f"🚨 ticket_claim_policy blocked staff message guild={channel.guild.id} "\n        f"channel={channel.id} actor={author.id} owner={decision.owner_id} "\n        f"claimed_by={decision.claimed_by_id} code={decision.code}"\n    )\n    return False\n\n\n''' + message_marker
events = replace_once(events, message_marker, message_helpers, "staff message enforcement helper")

events = replace_once(
    events,
    '''    await _handle_message_activity(\n        message.channel,\n        source="event_message_activity",\n    )\n''',
    '''    if not await _enforce_claim_first_staff_message(message):\n        return\n\n    await _handle_message_activity(\n        message.channel,\n        source="event_message_activity",\n    )\n''',
    "staff message enforcement call",
)
events = replace_once(
    events,
    '''    await _handle_message_activity(\n        after.channel,\n        source="event_message_edit",\n    )\n''',
    '''    if not await _enforce_claim_first_staff_message(after):\n        return\n\n    await _handle_message_activity(\n        after.channel,\n        source="event_message_edit",\n    )\n''',
    "edited staff message enforcement call",
)

transition_pattern = r'''            if not was_closed and is_closed:\n.*?                _debug\(\n                    f"reopen-detect write-failed -> channel=\{after.id\} name='\{after.name\}'"\n                \)\n'''
transition_replacement = '''            row = await _find_ticket_row_by_channel_id(after.id)\n            db_status = _row_status(row)\n\n            unauthorized_close_rename = not was_closed and is_closed and db_status != "closed"\n            unauthorized_reopen_rename = was_closed and is_open and db_status == "closed"\n\n            if unauthorized_close_rename or unauthorized_reopen_rename:\n                _remember_self_mutation(after.id)\n                try:\n                    await after.edit(\n                        name=str(before.name or after.name),\n                        reason="Claim-first ticket policy: lifecycle changes must use Dank Shield controls",\n                    )\n                    print(\n                        f"🚨 ticket_claim_policy lifecycle-bypass reverted guild={after.guild.id} "\n                        f"channel={after.id} before={before.name!r} attempted={after.name!r} "\n                        f"db_status={db_status}"\n                    )\n                except Exception as exc:\n                    print(\n                        f"❌ ticket_claim_policy lifecycle-bypass revert failed guild={after.guild.id} "\n                        f"channel={after.id} db_status={db_status} error={exc!r}"\n                    )\n                return\n'''
events = replace_regex_once(events, transition_pattern, transition_replacement, "external lifecycle rename guard")

events = replace_once(
    events,
    '''    lock = _channel_lock(channel.id)\n    async with lock:\n        try:\n            ok = await _mark_deleted_after_external_channel_delete(channel)\n''',
    '''    lock = _channel_lock(channel.id)\n    async with lock:\n        try:\n            row_before = await _find_ticket_row_by_channel_id(channel.id)\n            print(\n                f"🚨 ticket_claim_policy external channel deletion detected "\n                f"guild={channel.guild.id} channel={channel.id} "\n                f"status={_row_status(row_before)}; Discord-native deletion cannot be prevented"\n            )\n            ok = await _mark_deleted_after_external_channel_delete(channel)\n''',
    "external delete violation log",
)
events_path.write_text(events, encoding="utf-8")


# ---------------------------------------------------------------------------
# Verification controls and delete flow.
# ---------------------------------------------------------------------------
transcripts_path = ROOT / "stoney_verify/transcripts.py"
transcripts = transcripts_path.read_text(encoding="utf-8")
transcripts = replace_once(
    transcripts,
    '''from .tickets_new.service import (\n    attach_transcript_to_ticket,\n''',
    '''from .tickets_new.service import (\n    attach_transcript_to_ticket,\n    authorize_ticket_action,\n''',
    "transcript service policy import",
)
transcripts = replace_once(
    transcripts,
    '''async def _approve_verification_service(*args, **kwargs) -> Dict[str, Any]:\n    try:\n        from .verification_new.service import approve_verification as _approve_verification\n        return await _approve_verification(*args, **kwargs)\n''',
    '''async def _approve_verification_service(*args, **kwargs) -> Dict[str, Any]:\n    try:\n        channel = kwargs.get("channel")\n        staff_member = kwargs.get("staff_member")\n        if isinstance(channel, discord.TextChannel):\n            decision = await authorize_ticket_action(\n                channel_id=channel.id,\n                actor=staff_member,\n                action="verification_review",\n            )\n            if not decision.allowed:\n                return {"ok": False, "message": decision.message}\n        from .verification_new.service import approve_verification as _approve_verification\n        return await _approve_verification(*args, **kwargs)\n''',
    "verification approve claim gate",
)
transcripts = replace_once(
    transcripts,
    '''async def _deny_verification_service(*args, **kwargs) -> Dict[str, Any]:\n    try:\n        from .verification_new.service import deny_verification as _deny_verification\n        return await _deny_verification(*args, **kwargs)\n''',
    '''async def _deny_verification_service(*args, **kwargs) -> Dict[str, Any]:\n    try:\n        channel = kwargs.get("channel")\n        staff_member = kwargs.get("staff_member")\n        if isinstance(channel, discord.TextChannel):\n            decision = await authorize_ticket_action(\n                channel_id=channel.id,\n                actor=staff_member,\n                action="verification_review",\n            )\n            if not decision.allowed:\n                return {"ok": False, "message": decision.message}\n        from .verification_new.service import deny_verification as _deny_verification\n        return await _deny_verification(*args, **kwargs)\n''',
    "verification deny claim gate",
)

transcripts = replace_regex_once(
    transcripts,
    r'''            if await _ticket_is_open_like\(channel\):\n                try:\n                    closed_ok = await mark_ticket_closed\(.*?                try:\n                    await _post_staff_closed_message\(channel, interaction.user\)\n                except Exception:\n                    pass\n\n            is_ghost = await _detect_is_ghost_ticket\(channel\)''',
    '''            if await _ticket_is_open_like(channel):\n                return await _reply_ephemeral(\n                    interaction,\n                    "❌ Close the ticket first, then use Delete as a separate action.",\n                )\n\n            is_ghost = await _detect_is_ghost_ticket(channel)''',
    "remove one-click close-and-delete",
)
transcripts_path.write_text(transcripts, encoding="utf-8")

print("Applied ticket claim-first enforcement across service, UI, macros, events, and verification controls.")
