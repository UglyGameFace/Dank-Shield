from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ROOT / "stoney_verify/commands_ext/public_setup_defaults.py"
RECONCILE = ROOT / "stoney_verify/setup_resource_reconcile.py"
RECOMMEND = ROOT / "stoney_verify/commands_ext/public_setup_recommend.py"
HELPER = Path(__file__).resolve()


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly 1 match, found {count}")
    return source.replace(old, new, 1)


def replace_block(source: str, start_marker: str, end_marker: str, new_block: str, label: str) -> str:
    start = source.find(start_marker)
    if start < 0:
        raise RuntimeError(f"{label}: start marker not found")
    end = source.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f"{label}: end marker not found")
    if source.find(start_marker, start + 1) >= 0:
        raise RuntimeError(f"{label}: duplicate start marker found")
    return source[:start] + new_block.rstrip() + "\n\n" + source[end + 2 :]


defaults = DEFAULTS.read_text(encoding="utf-8")
reconcile = RECONCILE.read_text(encoding="utf-8")
recommend = RECOMMEND.read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# 1. Serialize all setup resource creation by event-loop/guild/kind/name.
#    The second concurrent caller re-checks Discord after the first completes.
# ---------------------------------------------------------------------------
defaults = replace_once(
    defaults,
    "import re\nimport unicodedata\n",
    "import asyncio\nimport re\nimport unicodedata\n",
    "defaults asyncio import",
)

lock_anchor = '''def _find_named(items: list[Any], name: str) -> Any:\n    exact = str(name or "").strip().casefold()\n    fuzzy = _key(name)\n    for item in items:\n        if str(getattr(item, "name", "") or "").strip().casefold() == exact:\n            return item\n    for item in items:\n        if fuzzy and _key(getattr(item, "name", "")) == fuzzy:\n            return item\n    return None\n\n\n'''
lock_code = lock_anchor + '''_RESOURCE_CREATE_LOCKS: dict[tuple[int, int, str, str], asyncio.Lock] = {}\n\n\ndef _resource_create_lock(\n    guild: discord.Guild,\n    kind: str,\n    name: str,\n) -> asyncio.Lock:\n    """Serialize same-resource creation within one bot process/event loop."""\n\n    loop = asyncio.get_running_loop()\n    key = (\n        id(loop),\n        int(getattr(guild, "id", 0) or 0),\n        str(kind or "resource"),\n        _key(name),\n    )\n    lock = _RESOURCE_CREATE_LOCKS.get(key)\n    if lock is None:\n        lock = asyncio.Lock()\n        _RESOURCE_CREATE_LOCKS[key] = lock\n    return lock\n\n\n'''
defaults = replace_once(
    defaults,
    lock_anchor,
    lock_code,
    "resource creation lock helper",
)

role_block = '''async def _ensure_role(guild: discord.Guild, name: str, *, create_missing_roles: bool, notes: list[str], created: list[str], reused: list[str]) -> Optional[discord.Role]:\n    async with _resource_create_lock(guild, "role", name):\n        role = _role_by_name(guild, name)\n        if role:\n            _unique(reused, f"Role: {role.mention}")\n            return role\n        if not create_missing_roles:\n            notes.append(f"Role `{name}` was missing and role creation is disabled.")\n            return None\n        ok, reason = _can_manage_roles(guild)\n        if not ok:\n            notes.append(f"Could not create role `{name}`: {reason}")\n            return None\n        try:\n            role = await guild.create_role(\n                name=name,\n                permissions=discord.Permissions.none(),\n                hoist=False,\n                mentionable=False,\n                reason="Dank Shield auto-build missing recommended role",\n            )\n            created.append(f"Role: {role.mention}")\n            return role\n        except Exception as e:\n            notes.append(f"Could not create role `{name}`: {type(e).__name__}")\n            return None\n'''
defaults = replace_block(
    defaults,
    "async def _ensure_role(",
    "\n\nasync def _ensure_category(",
    role_block,
    "locked role ensure",
)

category_block = '''async def _ensure_category(guild: discord.Guild, name: str, *, overwrites: dict[Any, discord.PermissionOverwrite], notes: list[str], created: list[str], reused: list[str]) -> Optional[discord.CategoryChannel]:\n    async with _resource_create_lock(guild, "category", name):\n        category = _category_by_name(guild, name)\n        if category:\n            _unique(reused, f"Category: `{category.name}`")\n            notes.append(f"Reused existing category `{category.name}`; safe bot/staff permission repair will be checked.")\n            return category\n        ok, reason = _can_manage_channels(guild)\n        if not ok:\n            notes.append(f"Could not create category `{name}`: {reason}")\n            return None\n        try:\n            category = await guild.create_category(\n                name=name,\n                overwrites=overwrites,\n                reason="Dank Shield auto-build missing recommended category",\n            )\n            created.append(f"Category: `{category.name}`")\n            return category\n        except Exception as e:\n            notes.append(f"Could not create category `{name}`: {type(e).__name__}")\n            return None\n'''
defaults = replace_block(
    defaults,
    "async def _ensure_category(",
    "\n\nasync def _ensure_text(",
    category_block,
    "locked category ensure",
)

text_block = '''async def _ensure_text(guild: discord.Guild, name: str, *, category: Optional[discord.CategoryChannel], overwrites: dict[Any, discord.PermissionOverwrite], topic: str, notes: list[str], created: list[str], reused: list[str]) -> Optional[discord.TextChannel]:\n    async with _resource_create_lock(guild, "text", name):\n        channel = _text_by_name(guild, name)\n        if channel:\n            _unique(reused, f"Channel: {channel.mention}")\n            notes.append(f"Reused existing channel {channel.mention}; safe bot/staff permission repair will be checked.")\n            return channel\n        ok, reason = _can_manage_channels(guild)\n        if not ok:\n            notes.append(f"Could not create channel `#{name}`: {reason}")\n            return None\n        try:\n            channel = await guild.create_text_channel(\n                name=name,\n                category=category,\n                overwrites=overwrites,\n                topic=topic[:1024] if topic else None,\n                reason="Dank Shield auto-build missing recommended channel",\n            )\n            created.append(f"Channel: {channel.mention}")\n            return channel\n        except Exception as e:\n            notes.append(f"Could not create channel `#{name}`: {type(e).__name__}")\n            return None\n'''
defaults = replace_block(
    defaults,
    "async def _ensure_text(",
    "\n\nasync def _ensure_voice(",
    text_block,
    "locked text ensure",
)

voice_block = '''async def _ensure_voice(guild: discord.Guild, name: str, *, category: Optional[discord.CategoryChannel], overwrites: dict[Any, discord.PermissionOverwrite], notes: list[str], created: list[str], reused: list[str]) -> Optional[discord.VoiceChannel]:\n    async with _resource_create_lock(guild, "voice", name):\n        channel = _voice_by_name(guild, name)\n        if channel:\n            _unique(reused, f"Voice: {channel.mention}")\n            notes.append(f"Reused existing voice channel {channel.mention}; safe bot/staff permission repair will be checked.")\n            return channel\n        ok, reason = _can_manage_channels(guild)\n        if not ok:\n            notes.append(f"Could not create voice channel `{name}`: {reason}")\n            return None\n        try:\n            channel = await guild.create_voice_channel(\n                name=name,\n                category=category,\n                overwrites=overwrites,\n                reason="Dank Shield auto-build missing recommended voice channel",\n            )\n            created.append(f"Voice: {channel.mention}")\n            return channel\n        except Exception as e:\n            notes.append(f"Could not create voice channel `{name}`: {type(e).__name__}")\n            return None\n'''
defaults = replace_block(
    defaults,
    "async def _ensure_voice(",
    "\n\nasync def _resolve_existing_control_role(",
    voice_block,
    "locked voice ensure",
)

# ---------------------------------------------------------------------------
# 2. Reconciliation may automatically delete at most one voice + one queue.
#    Managed ID wins. Legacy audit fallback is allowed only when all aliases
#    converge to exactly one ID. Ambiguous aliases are detached, never deleted.
# ---------------------------------------------------------------------------
reconcile_anchor = '''    notes: list[str] = []\n    clear_keys = set(VOICE_MAPPING_KEYS) | set(VOICE_QUEUE_MAPPING_KEYS)\n\n'''
reconcile_insert = '''    notes: list[str] = []\n    clear_keys = set(VOICE_MAPPING_KEYS) | set(VOICE_QUEUE_MAPPING_KEYS)\n\n    if managed_voice_id > 0:\n        auto_voice_ids = {managed_voice_id}\n    elif len(mapped_voice_ids) == 1:\n        auto_voice_ids = set(mapped_voice_ids)\n    else:\n        auto_voice_ids = set()\n        if len(mapped_voice_ids) > 1:\n            _add_note(\n                notes,\n                "Multiple legacy Voice Verify voice mappings disagree, so Dank Shield detached them without deleting any voice channel.",\n            )\n\n    if managed_queue_id > 0:\n        auto_queue_ids = {managed_queue_id}\n    elif len(mapped_queue_ids) == 1:\n        auto_queue_ids = set(mapped_queue_ids)\n    else:\n        auto_queue_ids = set()\n        if len(mapped_queue_ids) > 1:\n            _add_note(\n                notes,\n                "Multiple legacy Voice Verify request-channel mappings disagree, so Dank Shield detached them without deleting any request channel.",\n            )\n\n'''
reconcile = replace_once(
    reconcile,
    reconcile_anchor,
    reconcile_insert,
    "bounded reconciliation candidate selection",
)

voice_proof_old = '''        proven = bool(\n            channel_id == managed_voice_id\n            or await _audit_proves_bot_created(guild, channel)\n        )\n'''
voice_proof_new = '''        proven = bool(\n            channel_id == managed_voice_id\n            or (\n                managed_voice_id <= 0\n                and channel_id in auto_voice_ids\n                and await _audit_proves_bot_created(guild, channel)\n            )\n        )\n'''
reconcile = replace_once(
    reconcile,
    voice_proof_old,
    voice_proof_new,
    "voice proof bounded to one candidate",
)

queue_proof_old = '''        proven = bool(\n            channel_id == managed_queue_id\n            or await _audit_proves_bot_created(guild, channel)\n        )\n'''
queue_proof_new = '''        proven = bool(\n            channel_id == managed_queue_id\n            or (\n                managed_queue_id <= 0\n                and channel_id in auto_queue_ids\n                and await _audit_proves_bot_created(guild, channel)\n            )\n        )\n'''
reconcile = replace_once(
    reconcile,
    queue_proof_old,
    queue_proof_new,
    "queue proof bounded to one candidate",
)

# ---------------------------------------------------------------------------
# 3. Add a non-destructive entry point under Verification. The destructive
#    action itself lives one level deeper on an explicit review screen.
# ---------------------------------------------------------------------------
verify_anchor = '''    @discord.ui.button(label="Timers & Rules", emoji="⏱️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_verify:rules", row=1)\n    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:\n        _ = button\n        await _open_timers_behavior(interaction)\n\n'''
verify_insert = verify_anchor + '''    @discord.ui.button(label="Review Old Voice Items", emoji="🎙️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_verify:legacy_voice", row=1)\n    async def legacy_voice_items(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:\n        _ = button\n        from stoney_verify import setup_legacy_voice_cleanup_ui\n\n        await setup_legacy_voice_cleanup_ui.open_legacy_voice_cleanup_review(\n            interaction\n        )\n\n'''
recommend = replace_once(
    recommend,
    verify_anchor,
    verify_insert,
    "verification legacy Voice cleanup entry",
)

# Compile every changed/new Python file before any write occurs.
compile(defaults, str(DEFAULTS), "exec")
compile(reconcile, str(RECONCILE), "exec")
compile(recommend, str(RECOMMEND), "exec")
for path in (
    ROOT / "stoney_verify/setup_legacy_voice_cleanup.py",
    ROOT / "stoney_verify/setup_legacy_voice_cleanup_ui.py",
    ROOT / "tests/test_setup_voice_resource_safety_behavior.py",
):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")

DEFAULTS.write_text(defaults, encoding="utf-8")
RECONCILE.write_text(reconcile, encoding="utf-8")
RECOMMEND.write_text(recommend, encoding="utf-8")
HELPER.unlink()

subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)

print("✅ Setup resource creation is serialized by guild/kind/name to block concurrent duplicates.")
print("✅ Voice Verify auto-reconciliation can delete at most one voice and one request channel.")
print("✅ Conflicting legacy aliases are detached without deletion.")
print("✅ Verification now exposes a separate Review Old Voice Items screen.")
print("✅ Legacy cleanup requires explicit owner action and revalidates exact IDs/names/types.")
print("✅ Connected voice channels and history-bearing request channels are preserved.")
print("✅ Temporary helper removed from the working tree.")
print("✅ git diff --check passed.")
