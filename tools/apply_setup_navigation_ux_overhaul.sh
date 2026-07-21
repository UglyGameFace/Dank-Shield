#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

cd ~/Dank-Shield || exit 1

git fetch origin
git checkout fix/setup-navigation-ux-overhaul
git pull --ff-only origin fix/setup-navigation-ux-overhaul

python - <<'PY'
from pathlib import Path


def replace_between(text: str, start: str, end: str, replacement: str, *, label: str) -> str:
    start_at = text.find(start)
    if start_at < 0:
        raise SystemExit(f"ERROR: {label} start marker not found")
    end_at = text.find(end, start_at)
    if end_at < 0:
        raise SystemExit(f"ERROR: {label} end marker not found")
    return text[:start_at] + replacement.rstrip() + "\n\n" + text[end_at:]


# 1. Canonical setup state compatibility + completion invalidation.
path = Path("stoney_verify/setup_service_state.py")
text = path.read_text(encoding="utf-8")

property_marker = '''    @property
    def any_enabled(self) -> bool:
'''
property_block = '''    @property
    def verification(self) -> bool:
        """Compatibility alias used by the existing custom feature picker."""
        return bool(self.simple_verify)

    @property
    def voice(self) -> bool:
        return bool(self.voice_verify)

    @property
    def spamguard(self) -> bool:
        return bool(self.spam_guard)

    @property
    def moderation(self) -> bool:
        return bool(self.logs)

'''
if "def verification(self) -> bool:" not in text:
    if property_marker not in text:
        raise SystemExit("ERROR: setup state property marker not found")
    text = text.replace(property_marker, property_block + property_marker, 1)

payload_marker = '''    def enabled_labels(self) -> list[str]:
'''
payload_alias = '''    def as_payload(self) -> dict[str, bool]:
        """Compatibility alias for existing feature-picker views."""
        return self.as_service_payload()

'''
if "def as_payload(self) -> dict[str, bool]:" not in text:
    if payload_marker not in text:
        raise SystemExit("ERROR: setup state payload marker not found")
    text = text.replace(payload_marker, payload_alias + payload_marker, 1)

completion_marker = '''async def mark_setup_completed(
'''
completion_helper = '''async def invalidate_setup_completion(
    guild_id: int,
    *,
    reason: str = "Setup configuration changed",
) -> None:
    """Mark a previously finished setup as needing review again."""
    from .commands_ext.public_setup_config_writer import upsert_guild_config

    await upsert_guild_config(
        int(guild_id),
        {
            "setup_completed": False,
            "setup_completion_invalidated_at": now_utc().isoformat(),
            "setup_completion_invalidated_reason": str(reason or "")[:300],
            "__config_write_mode": "explicit_override",
            "__config_write_source": "/dank setup completion invalidation",
        },
    )
    invalidate_guild_config(int(guild_id))


'''
if "async def invalidate_setup_completion(" not in text:
    if completion_marker not in text:
        raise SystemExit("ERROR: setup completion marker not found")
    text = text.replace(completion_marker, completion_helper + completion_marker, 1)

old_all = '''    "load_setup_service_state",
    "mark_setup_completed",
'''
new_all = '''    "invalidate_setup_completion",
    "load_setup_service_state",
    "mark_setup_completed",
'''
if '"invalidate_setup_completion",' not in text:
    if old_all not in text:
        raise SystemExit("ERROR: setup state __all__ marker not found")
    text = text.replace(old_all, new_all, 1)
path.write_text(text, encoding="utf-8")


# 2. Protected setup writer invalidates Finished after edits.
path = Path("stoney_verify/commands_ext/public_setup_config_writer.py")
text = path.read_text(encoding="utf-8")
writer_marker = '''def upsert_guild_config_sync(
'''
writer_helper = '''_COMPLETION_METADATA_KEYS = {
    "setup_completed",
    "setup_completed_at",
    "setup_completed_by_id",
    "setup_completed_by_name",
    "setup_completion_invalidated_at",
    "setup_completion_invalidated_reason",
}


def _completion_aware_updates(updates: Mapping[str, Any]) -> dict[str, Any]:
    """Invalidate Finished after a real setup edit."""
    final = dict(updates)
    if "setup_completed" in final:
        return final

    functional_keys = [
        str(key)
        for key in final
        if str(key) not in _CONTROL_KEYS
        and str(key) not in _BASE_WRITE_KEYS
        and str(key) not in _COMPLETION_METADATA_KEYS
        and not str(key).startswith("config_last_")
    ]
    if functional_keys:
        final["setup_completed"] = False
        final["setup_completion_invalidated_at"] = _utc_iso()
    return final


'''
if "def _completion_aware_updates(" not in text:
    if writer_marker not in text:
        raise SystemExit("ERROR: setup writer function marker not found")
    text = text.replace(writer_marker, writer_helper + writer_marker, 1)

old_sync_start = '''    existing = _fetch_existing_config_row_sync(gid)
    safe_updates, blocked, changed, mode, source = _filter_safe_updates(existing, updates)

    if _safe_bool(updates.get("__config_write_dry_run"), False):
'''
new_sync_start = '''    existing = _fetch_existing_config_row_sync(gid)
    normalized_updates = _completion_aware_updates(updates)
    safe_updates, blocked, changed, mode, source = _filter_safe_updates(
        existing,
        normalized_updates,
    )

    if _safe_bool(normalized_updates.get("__config_write_dry_run"), False):
'''
if old_sync_start not in text:
    raise SystemExit("ERROR: setup writer normalization block not found")
text = text.replace(old_sync_start, new_sync_start, 1)
path.write_text(text, encoding="utf-8")


# 3. Custom Setup stops using startup_guards as its state owner.
path = Path("stoney_verify/commands_ext/public_setup_fresh_choice.py")
text = path.read_text(encoding="utf-8")
import_marker = '''from ..globals import now_utc
'''
native_import = '''from ..globals import now_utc
from ..setup_service_state import (
    load_setup_service_state,
    normalize_custom_service_patch,
    save_custom_service_state,
)
'''
if "from ..setup_service_state import (" not in text:
    if import_marker not in text:
        raise SystemExit("ERROR: custom setup import marker not found")
    text = text.replace(import_marker, native_import, 1)

custom_patch = '''def _custom_service_config_patch(payload: dict[str, Any]) -> dict[str, Any]:
    """Use the canonical native service-state normalizer."""
    return normalize_custom_service_patch(payload)
'''
text = replace_between(
    text,
    "def _custom_service_config_patch(",
    "def _service_hint_text(",
    custom_patch,
    label="custom service normalizer",
)

custom_io = '''async def _save_custom_services(
    guild_id: int,
    payload: dict[str, bool],
    actor: Any,
) -> None:
    await save_custom_service_state(int(guild_id), dict(payload), actor=actor)


async def _load_custom_state(guild_id: int) -> Any:
    return await load_setup_service_state(int(guild_id))
'''
text = replace_between(
    text,
    "async def _save_custom_services(",
    "_CUSTOM_SERVICE_FLAG_KEYS =",
    custom_io + "\n\n_CUSTOM_SERVICE_FLAG_KEYS =",
    label="custom setup persistence",
)
path.write_text(text, encoding="utf-8")


# 4. Setup Home, Setup Check, and Test all use canonical state.
path = Path("stoney_verify/commands_ext/public_setup_recommend.py")
text = path.read_text(encoding="utf-8")

old_doc = '''"""Plain-language public /dank setup home.

This module patches the hardened setup flow from public_setup_solid.py into a
simple first-run screen. It deliberately avoids developer/product terms.

Public language rules:
- Say Dank Shield, not Dank Shield.
- Use plain labels: Basic server, Help desk, ID check, Voice check,
  ID + voice check, Custom setup.
- No forced forms by default.
- Do not show raw role/channel IDs as public setup instructions.
"""
'''
new_doc = '''"""Canonical plain-language product flow for public ``/dank setup``.

The low-level builders remain in ``public_setup_solid``. This module owns the
customer-facing home, guided path, review, testing, completion, and navigation
language.
"""
'''
if old_doc not in text:
    raise SystemExit("ERROR: recommend module docstring marker not found")
text = text.replace(old_doc, new_doc, 1)

recommend_import_marker = '''from ..guild_config import get_guild_config
'''
recommend_imports = '''from ..guild_config import get_guild_config
from ..setup_service_state import (
    SetupServiceState,
    load_setup_service_state,
    mark_setup_completed,
    service_state_from_config,
)
'''
if "from ..setup_service_state import (" not in text:
    if recommend_import_marker not in text:
        raise SystemExit("ERROR: recommend state import marker not found")
    text = text.replace(recommend_import_marker, recommend_imports, 1)

selected_services = '''def _selected_setup_services(cfg: Any) -> dict[str, bool]:
    """Return the one canonical feature selection for every setup screen."""
    state = service_state_from_config(cfg)
    return {
        "tickets": bool(state.tickets),
        "verify": bool(state.verification_enabled),
        "basic_verify": bool(state.simple_verify),
        "voice": bool(state.voice_verify),
        "id": bool(state.id_verify),
        "spam_guard": bool(state.spam_guard),
        "logs": bool(state.logs),
    }
'''
text = replace_between(
    text,
    "def _selected_setup_services(",
    "def _missing_setup_permissions(",
    selected_services,
    label="canonical selected services",
)

product_payload = '''def _enabled_feature_text(state: SetupServiceState) -> str:
    labels = state.enabled_labels()
    if not labels:
        return "No features are selected yet."
    return " • ".join(f"**{label}**" for label in labels)


async def _product_main_setup_payload(
    guild: discord.Guild,
) -> tuple[discord.Embed, discord.ui.View]:
    progress_text, done, total, next_step = await _setup_progress(guild)
    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception:
        cfg = None

    state = service_state_from_config(cfg)
    started = bool(state.setup_choice)
    ready = bool(total and done >= total)
    completed = bool(ready and state.completed)
    issues = [
        line.strip()
        for line in str(progress_text or "").splitlines()
        if line.strip().startswith(("⚠️", "🚫", "❌"))
    ][:3]

    if not started:
        status = "Not started"
        recommended = "Press **Start Setup** and choose what this server needs."
    elif completed:
        status = "Setup finished"
        recommended = (
            "Your setup is saved and marked finished. Open **View Setup Summary** "
            "to review it, or use **Edit / More Options** when you want to change something."
        )
    elif ready:
        status = "Ready for testing"
        recommended = (
            "Press **Test Your Setup**. When the enabled features work, press **Finish Setup**."
        )
    else:
        status = "Needs attention"
        recommended = str(next_step or "Press Continue Setup.")[:350]

    embed = discord.Embed(
        title="🚀 Dank Shield Setup",
        description=(
            "Follow the recommended next step. Settings you do not need stay under "
            "**Edit / More Options**."
        ),
        color=discord.Color.green() if completed or ready else discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="Status",
        value=(
            f"**{status}**\n"
            f"Setup plan: **{state.setup_label}**\n"
            f"`{done}/{total}` required steps complete"
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name="Enabled Features",
        value=_enabled_feature_text(state)[:1024],
        inline=False,
    )
    embed.add_field(name="Recommended Next Step", value=recommended[:1024], inline=False)
    embed.add_field(
        name="Needs Attention",
        value="\n".join(issues)[:900] if issues else "✅ No required setup problem is blocking you.",
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • /dank setup")
    return embed, ProductSetupHomeView(ready=ready, started=started, completed=completed)
'''
text = replace_between(
    text,
    "async def _product_main_setup_payload(",
    "class SetupChoiceSelect(",
    product_payload,
    label="setup home payload",
)

product_home_view = '''class ProductSetupHomeView(discord.ui.View):
    """Setup Home with one clear primary action."""

    def __init__(self, *, ready: bool = False, started: bool = False, completed: bool = False) -> None:
        super().__init__(timeout=900)
        self.ready = bool(ready)
        self.started = bool(started)
        self.completed = bool(completed)

        if self.completed:
            self.continue_setup.label = "View Setup Summary"
            self.continue_setup.emoji = "✅"
            self.more_options.label = "Edit / More Options"
        elif self.ready:
            self.continue_setup.label = "Test Your Setup"
            self.continue_setup.emoji = "🧪"
        elif self.started:
            self.continue_setup.label = "Continue Setup"
            self.continue_setup.emoji = "➡️"
        else:
            self.continue_setup.label = "Start Setup"
            self.continue_setup.emoji = "▶️"

    @discord.ui.button(
        label="Start Setup",
        emoji="▶️",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_home:continue",
        row=0,
    )
    async def continue_setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if self.completed:
            await _open_completed_summary(interaction)
            return
        if self.ready:
            await _open_test_launch(interaction)
            return
        if self.started:
            await _open_guided_setup(interaction)
            return
        await _open_choose_setup_type(interaction)

    @discord.ui.button(
        label="More Options",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_home:more_options",
        row=1,
    )
    async def more_options(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_manage_setup(interaction)
'''
text = replace_between(
    text,
    "class ProductSetupHomeView(",
    "class ContinueSetupView(",
    product_home_view,
    label="setup home view",
)

launch_state = '''async def _launch_state(guild: discord.Guild) -> dict[str, Any]:
    state = await load_setup_service_state(guild.id)
    return {
        "tickets": bool(state.tickets),
        "basic_verify": bool(state.simple_verify),
        "voice_verify": bool(state.voice_verify),
        "id_verify": bool(state.id_verify),
        "spam_guard": bool(state.spam_guard),
        "logs": bool(state.logs),
        "completed": bool(state.completed),
        "setup_choice": state.setup_choice,
        "setup_label": state.setup_label,
    }
'''
text = replace_between(
    text,
    "async def _launch_state(",
    "def _launch_state_text(",
    launch_state,
    label="launch state",
)

launch_text = '''def _launch_state_text(state: dict[str, Any]) -> str:
    lines: list[str] = []
    if state.get("tickets"):
        lines.append("🎫 **Tickets**")
    if state.get("basic_verify"):
        lines.append("✅ **Simple Verify**")
    if state.get("voice_verify"):
        lines.append("🎙️ **Voice Verify**")
    if state.get("id_verify"):
        lines.append("🪪 **ID/Web Verify**")
    if state.get("spam_guard"):
        lines.append("🛡️ **SpamGuard**")
    if state.get("logs"):
        lines.append("🧾 **Logs**")
    return "\n".join(lines) or "No features are enabled."
'''
text = replace_between(
    text,
    "def _launch_state_text(",
    "async def _open_test_launch(",
    launch_text,
    label="launch state text",
)

launch_open = '''async def _open_test_launch(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

    await solid._safe_defer_update(interaction)
    target, _title, _explanation, _key = await _guided_setup_target(guild)
    if target != "ready":
        return await _open_health_check(interaction, already_deferred=True)

    state = await _launch_state(guild)
    actions: list[str] = []
    if state.get("tickets"):
        actions.append("Post the ticket panel and create one test ticket. Try the staff controls, then delete the test ticket.")
    if state.get("basic_verify"):
        actions.append("Post the Simple Verify panel and test it with a second account.")
    if state.get("voice_verify"):
        actions.append("Use a second account to request Voice Verify and confirm staff receive the request.")
    if state.get("id_verify"):
        actions.append("Test the private ID/Web flow with an approved staff test account.")
    if state.get("spam_guard"):
        actions.append("Review SpamGuard in a private test channel and confirm its actions appear in the configured log.")
    if state.get("logs"):
        actions.append("Confirm the test actions appear in the correct log channels.")

    numbered = "\n".join(f"{index}. {action}" for index, action in enumerate(actions, start=1))
    embed = discord.Embed(
        title="🧪 Setup Test Tools" if state.get("completed") else "🧪 Test Your Setup",
        description=(
            "Only features enabled for this server are shown below. Nothing is posted until you press a matching button."
        ),
        color=discord.Color.green(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Setup Plan", value=f"**{state.get('setup_label') or 'Current setup'}**", inline=False)
    embed.add_field(name="Enabled Features", value=_launch_state_text(state), inline=False)
    embed.add_field(name="Test These", value=numbered[:1024] or "Run Setup Check before testing.", inline=False)
    if not state.get("completed"):
        embed.add_field(
            name="When Everything Works",
            value=(
                "Press **Finish Setup**. Setup Home will then show **Setup finished** instead of sending you back here."
            ),
            inline=False,
        )
    embed.set_footer(text=f"Guild {guild.id} • enabled features only")
    await solid._edit_or_followup(interaction, embed=embed, view=LaunchTestView(state))


async def _finish_setup(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

    await solid._safe_defer_update(interaction)
    target, _title, _explanation, _key = await _guided_setup_target(guild)
    if target != "ready":
        return await _open_health_check(interaction, already_deferred=True)

    state = await mark_setup_completed(guild.id, actor=interaction.user)
    embed = discord.Embed(
        title="✅ Setup Finished",
        description=(
            "Dank Shield saved this setup as finished. Setup Home will no longer send you into the testing screen."
        ),
        color=discord.Color.green(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Enabled Features", value=_enabled_feature_text(state), inline=False)
    embed.add_field(
        name="Changing Something Later",
        value=(
            "Any future setup edit automatically changes this server back to **Needs review** until you test and finish it again."
        ),
        inline=False,
    )
    await solid._edit_or_followup(interaction, embed=embed, view=FinishedSetupView())


async def _open_completed_summary(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

    await solid._safe_defer_update(interaction)
    embed = await solid._build_current_setup_embed(guild)
    embed.title = "✅ Setup Summary"
    embed.description = (
        "This server is marked **Setup finished**. Use **Test Again** for the enabled test tools or **Edit Setup** to make changes."
    )
    await solid._edit_or_followup(interaction, embed=embed, view=FinishedSetupView())
'''
text = replace_between(
    text,
    "async def _open_test_launch(",
    "async def _guided_setup_target(",
    launch_open,
    label="test and finish flow",
)

launch_view = '''class LaunchTestView(discord.ui.View):
    """Only render actions for features this guild actually enabled."""

    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        super().__init__(timeout=900)
        self.state = dict(state or {})
        actions: list[tuple[str, str, discord.ButtonStyle, str, Any]] = []

        if self.state.get("tickets"):
            actions.extend([
                ("Post Ticket Panel", "🎫", discord.ButtonStyle.success, "dank_setup_launch:post_ticket_panel", self._post_ticket_panel),
                ("Create Test Ticket", "🧪", discord.ButtonStyle.success, "dank_setup_launch:create_test_ticket", self._create_test_ticket),
            ])
        if self.state.get("basic_verify"):
            actions.append(("Post Simple Verify Panel", "✅", discord.ButtonStyle.success, "dank_setup_launch:post_basic_verify", self._post_basic_verify))
        if not self.state.get("completed"):
            actions.append(("Finish Setup", "🏁", discord.ButtonStyle.primary, "dank_setup_launch:finish", self._finish))
        actions.extend([
            ("Review Setup", "🩺", discord.ButtonStyle.secondary, "dank_setup_launch:health", self._review),
            ("Setup Home", "🏠", discord.ButtonStyle.secondary, "dank_setup_launch:home", self._home),
        ])

        for index, (label, emoji, style, custom_id, callback) in enumerate(actions):
            button = discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=custom_id, row=index // 2)
            button.callback = callback
            self.add_item(button)

    async def _post_ticket_panel(self, interaction: discord.Interaction) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        state = await _launch_state(guild)
        if not state.get("tickets"):
            return await interaction.response.send_message("🎫 Tickets are OFF. Open **Edit Setup** to turn them on.", ephemeral=True)
        try:
            from .public_ticket_panel_commands import post_ticket_panel_callback
            await post_ticket_panel_callback(interaction)
        except Exception as exc:
            await interaction.response.send_message(
                "❌ Could not post the ticket panel: " f"`{type(exc).__name__}: {str(exc)[:220]}`",
                ephemeral=True,
            )

    async def _post_basic_verify(self, interaction: discord.Interaction) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        state = await _launch_state(guild)
        if not state.get("basic_verify"):
            return await interaction.response.send_message("✅ Simple Verify is OFF. Open **Edit Setup** to turn it on.", ephemeral=True)
        try:
            from .public_verify_basic_panel import verify_panel
            await verify_panel(interaction)
        except Exception as exc:
            await interaction.response.send_message(
                "❌ Could not post the Simple Verify panel: " f"`{type(exc).__name__}: {str(exc)[:220]}`",
                ephemeral=True,
            )

    async def _create_test_ticket(self, interaction: discord.Interaction) -> None:
        await _create_setup_test_ticket(interaction)

    async def _finish(self, interaction: discord.Interaction) -> None:
        await _finish_setup(interaction)

    async def _review(self, interaction: discord.Interaction) -> None:
        await _open_health_check(interaction)

    async def _home(self, interaction: discord.Interaction) -> None:
        await _home_edit(interaction)


class FinishedSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Test Again", emoji="🧪", style=discord.ButtonStyle.secondary, custom_id="dank_setup_finished:test", row=0)
    async def test_again(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_test_launch(interaction)

    @discord.ui.button(label="Edit Setup", emoji="⚙️", style=discord.ButtonStyle.primary, custom_id="dank_setup_finished:edit", row=0)
    async def edit_setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_manage_setup(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_finished:home", row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)
'''
text = replace_between(
    text,
    "class LaunchTestView(",
    "def _patch() -> None:",
    launch_view + "\n\ndef _patch() -> None:",
    label="dynamic launch view",
)
path.write_text(text, encoding="utf-8")


# 5. Solid helper uses canonical state; universal nav is compact.
path = Path("stoney_verify/commands_ext/public_setup_solid.py")
text = path.read_text(encoding="utf-8")
solid_import_marker = '''from ..guild_config import get_guild_config, invalidate_guild_config
'''
solid_imports = '''from ..guild_config import get_guild_config, invalidate_guild_config
from ..setup_service_state import service_state_from_config
'''
if "from ..setup_service_state import service_state_from_config" not in text:
    if solid_import_marker not in text:
        raise SystemExit("ERROR: solid state import marker not found")
    text = text.replace(solid_import_marker, solid_imports, 1)

setup_doc_features = '''def _setup_doc_features(cfg: Any) -> dict[str, bool]:
    """Use the same feature truth as Setup Home and Test Your Setup."""
    state = service_state_from_config(cfg)
    return {
        "tickets": bool(state.tickets),
        "basic_verify": bool(state.simple_verify),
        "vc_verify": bool(state.voice_verify),
        "logs": bool(state.logs),
    }
'''
text = replace_between(
    text,
    "def _setup_doc_features(",
    "_LAYOUT_ONLY_PHRASES =",
    setup_doc_features + "\n\n_LAYOUT_ONLY_PHRASES =",
    label="solid canonical features",
)

nav_view = '''class SetupNavView(discord.ui.View):
    """Compact universal escape row for setup sub-screens."""

    def __init__(self) -> None:
        super().__init__(timeout=900)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        try:
            item_label = getattr(item, "label", None) or getattr(item, "placeholder", None) or getattr(item, "custom_id", None) or "setup item"
        except Exception:
            item_label = "setup item"
        await safe_interaction_error(
            interaction,
            title="Setup Action Failed",
            error=error,
            hint=f"The **{item_label}** action failed safely. Nothing was changed. Press **Setup Home** or reopen `/dank setup`.",
            view=self,
        )

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:nav_home", row=4)
    async def setup_home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed, view = await _build_main_setup_payload(guild)
        await _edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:nav_close", row=4)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="Setup Closed",
            description="Nothing else was changed. Run `/dank setup` whenever you want to continue.",
            color=discord.Color.dark_grey(),
        )
        await _edit_or_followup(interaction, embed=embed, view=None)
'''
text = replace_between(
    text,
    "class SetupNavView(",
    "BackToSetupView = SetupNavView",
    nav_view + "\n\nBackToSetupView = SetupNavView",
    label="compact shared setup navigation",
)
path.write_text(text, encoding="utf-8")


# 6. Behavioral regression coverage.
Path("tests/test_setup_navigation_ux_overhaul_behavior.py").write_text(
'''from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify.commands_ext import public_setup_config_writer as writer
from stoney_verify.commands_ext import public_setup_recommend as recommend
from stoney_verify.commands_ext import public_setup_solid as solid


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def labels(view: discord.ui.View) -> list[str]:
    return [str(getattr(child, "label", "") or "") for child in view.children if isinstance(child, discord.ui.Button)]


def button(view: discord.ui.View, custom_id: str) -> discord.ui.Button:
    matches = [
        child for child in view.children
        if isinstance(child, discord.ui.Button)
        and str(getattr(child, "custom_id", "") or "") == custom_id
    ]
    assert len(matches) == 1
    return matches[0]


def test_custom_setup_does_not_invent_tickets() -> None:
    services = recommend._selected_setup_services({
        "setup_choice": "custom_setup",
        "verification_enabled": True,
        "tickets_enabled": False,
    })
    assert services["tickets"] is False
    assert services["basic_verify"] is True


def test_launch_hides_actions_for_features_that_are_off() -> None:
    view = recommend.LaunchTestView({
        "tickets": False,
        "basic_verify": True,
        "voice_verify": False,
        "id_verify": False,
        "spam_guard": False,
        "logs": False,
        "completed": False,
    })
    assert labels(view) == [
        "Post Simple Verify Panel",
        "Finish Setup",
        "Review Setup",
        "Setup Home",
    ]


def test_finished_launch_does_not_offer_finish_again() -> None:
    view = recommend.LaunchTestView({
        "tickets": True,
        "basic_verify": False,
        "completed": True,
    })
    assert "Finish Setup" not in labels(view)
    assert "Post Simple Verify Panel" not in labels(view)
    assert "Post Ticket Panel" in labels(view)


def test_launch_summary_lists_only_enabled_features() -> None:
    rendered = recommend._launch_state_text({
        "tickets": False,
        "basic_verify": True,
        "voice_verify": False,
        "id_verify": False,
        "spam_guard": True,
        "logs": True,
    })
    assert "Simple Verify" in rendered
    assert "SpamGuard" in rendered
    assert "Logs" in rendered
    assert "Tickets" not in rendered
    assert "OFF" not in rendered


def test_finished_home_opens_summary_instead_of_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    async def summary(interaction: Any) -> None:
        events.append("summary")

    async def launch(interaction: Any) -> None:
        events.append("launch")

    monkeypatch.setattr(recommend, "_open_completed_summary", summary)
    monkeypatch.setattr(recommend, "_open_test_launch", launch)
    view = recommend.ProductSetupHomeView(started=True, ready=True, completed=True)
    assert button(view, "dank_setup_home:continue").label == "View Setup Summary"
    run(button(view, "dank_setup_home:continue").callback(SimpleNamespace()))
    assert events == ["summary"]


def test_setup_writer_invalidates_completion_after_edit() -> None:
    payload = writer._completion_aware_updates({
        "ticket_prefix": "help",
        "__config_write_mode": "setup_builder",
    })
    assert payload["setup_completed"] is False
    assert payload["setup_completion_invalidated_at"]


def test_finish_write_is_not_invalidated() -> None:
    payload = writer._completion_aware_updates({
        "setup_completed": True,
        "setup_completed_at": "now",
    })
    assert payload["setup_completed"] is True


def test_shared_submenu_navigation_is_compact() -> None:
    view = solid.SetupNavView()
    assert labels(view) == ["Setup Home", "Close"]
    counts: dict[int, int] = {}
    for child in view.children:
        row = int(getattr(child, "row", 0) or 0)
        counts[row] = counts.get(row, 0) + 1
    assert all(count <= 2 for count in counts.values())
''',
    encoding="utf-8",
)

print("✅ Canonical Custom Setup state wired")
print("✅ Real Setup Finished phase added")
print("✅ Test screen now shows enabled actions only")
print("✅ Setup edits automatically invalidate completion")
print("✅ Shared submenu navigation reduced to Setup Home + Close")
print("✅ Active custom feature persistence removed from startup_guards")
PY

python -m py_compile \
  stoney_verify/setup_service_state.py \
  stoney_verify/commands_ext/public_setup_config_writer.py \
  stoney_verify/commands_ext/public_setup_fresh_choice.py \
  stoney_verify/commands_ext/public_setup_recommend.py \
  stoney_verify/commands_ext/public_setup_solid.py \
  tests/test_setup_navigation_ux_overhaul_behavior.py

if grep -n 'startup_guards.*setup_service_modes' stoney_verify/commands_ext/public_setup_fresh_choice.py; then
  echo "ERROR: active custom setup still imports setup_service_modes"
  exit 1
else
  echo "✅ Custom Setup uses native setup_service_state"
fi

if grep -n '_cfg_value(cfg, "tickets_enabled", True)' stoney_verify/commands_ext/public_setup_recommend.py; then
  echo "ERROR: stale Tickets=ON launch default still exists"
  exit 1
else
  echo "✅ Test flow no longer defaults Tickets to ON"
fi

git diff --check

git add \
  stoney_verify/setup_service_state.py \
  stoney_verify/commands_ext/public_setup_config_writer.py \
  stoney_verify/commands_ext/public_setup_fresh_choice.py \
  stoney_verify/commands_ext/public_setup_recommend.py \
  stoney_verify/commands_ext/public_setup_solid.py \
  tests/test_setup_navigation_ux_overhaul_behavior.py

git commit -m "Unify setup state navigation and completion"
git push origin fix/setup-navigation-ux-overhaul
