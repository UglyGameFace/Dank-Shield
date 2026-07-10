from __future__ import annotations

"""Clean up Dank Design confusing/old UI wording and expose lock/unlock.

This does not add another startup guard. It patches the native Dank Design Studio
surface so old repair wording does not fight the newer saved-rule / style-change
flows.

Run from repo root:
    python tools/apply_dank_design_clean_simple_unlock_ui.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "stoney_verify/commands_ext/public_design_studio.py"

OLD_HOME_RECOMMENDED = '''    embed.add_field(
        name="Recommended",
        value="\n".join((
            "🧭 **Fix Mismatched Names** — reviews saved rules first; Live Majority is preview-only when saved rules exist.",
            "⚡ **Change One Style** — add/change one thing, like a separator, while keeping everything else.",
            "👁️ **Preview Saved Design** — shows what saved rules would rename before anything changes.",
        )),
        inline=False,
    )

    embed.add_field(
        name="Edit one thing",
        value="\n".join((
            "🗂️ **Category Editor** — preview, rename, or style one category.",
            "#️⃣ **Channel Editor** — preview, rename, or style one channel.",
        )),
        inline=False,
    )
'''

NEW_HOME_RECOMMENDED = '''    embed.add_field(
        name="Start here — pick one path",
        value="\n".join((
            "👁️ **Preview Saved Design** — safest. Shows exactly what your saved rules would do.",
            "🧭 **Repair Saved Design** — fixes drift back to saved rules; it must preview before Apply.",
            "⚡ **Change One Style** — change one thing, like separator, and leave the rest alone.",
        )),
        inline=False,
    )

    embed.add_field(
        name="Edit one item",
        value="\n".join((
            "🗂️ **Category Editor** — one category: rename, style, lock, or unlock.",
            "#️⃣ **Channel Editor** — one channel: rename, style, lock, or unlock.",
        )),
        inline=False,
    )

    embed.add_field(
        name="Lock / unlock",
        value="\n".join((
            "🔓 **Lock / Unlock Items** — manage category/channel style locks and protected-name policy.",
            "Nothing is permanent: clear one lock, clear all locks, or change protected items back anytime.",
        )),
        inline=False,
    )
'''

LOCK_UNLOCK_INSERT_BEFORE = '''class DesignHomeView(discord.ui.View):
'''

LOCK_UNLOCK_CLASSES = '''def _lock_unlock_home_embed(guild: discord.Guild, options: Mapping[str, Any]) -> discord.Embed:
    counts = _lock_count(options)
    rules = _protection_rules(options) if "_protection_rules" in globals() else {}
    embed = discord.Embed(
        title="🔓 Lock / Unlock Categories & Channels",
        description=(
            "This is the place for anything that should or should not be changed by Dank Design.\n\n"
            "**Style locks** decide the saved format for a category/channel.\n"
            "**Protected names** decide if ticket/log/system names are skipped, font-only, or fully styleable."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Saved style locks",
        value=(
            f"Global: **{'On' if counts['global'] else 'Off'}**\n"
            f"Categories locked: **{counts['categories']}**\n"
            f"Channels locked: **{counts['channels']}**\n\n"
            "Use **Manage Saved Style Locks** to remove one lock, clear stale locks, or clear all."
        ),
        inline=False,
    )
    embed.add_field(
        name="Protected-name overrides",
        value=(
            f"Overrides saved: **{len(rules)}**\n"
            "Use **Protected Names / Unlock** to let default ticket/log/system names use font styling or full styling."
        ),
        inline=False,
    )
    embed.add_field(
        name="Plain-English rule",
        value=(
            "If a category/channel is locked or protected, it is not stuck forever. "
            "Open this page, remove the lock, or change its protection mode."
        ),
        inline=False,
    )
    embed.set_footer(text="Nothing changes from this screen until you choose a lock/protection action, then preview/apply a design.")
    return _clean_design_embed(embed)


class LockUnlockHomeView(discord.ui.View):
    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(timeout=900)
        self.options = options or {}

    @discord.ui.button(label="Manage Saved Style Locks", emoji="🔐", style=discord.ButtonStyle.primary, custom_id="dank_design:lock_unlock_manage_locks", row=0)
    async def manage_locks(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(embed=_format_lock_manager_embed(guild, options, page=0), view=LockManagerView(guild, options, page=0))

    @discord.ui.button(label="Protected Names / Unlock", emoji="🛡️", style=discord.ButtonStyle.primary, custom_id="dank_design:lock_unlock_protected_names", row=0)
    async def protected_names(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(embed=_protection_manager_embed(guild, options), view=ProtectionManagerView())

    @discord.ui.button(label="Category Editor", emoji="🗂️", style=discord.ButtonStyle.secondary, custom_id="dank_design:lock_unlock_category_editor", row=1)
    async def category_editor(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(embed=_category_editor_embed(guild, page=0), view=CategoryEditorPickerView(guild, page=0))

    @discord.ui.button(label="Channel Editor", emoji="#️⃣", style=discord.ButtonStyle.secondary, custom_id="dank_design:lock_unlock_channel_editor", row=1)
    async def channel_editor(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(embed=_channel_editor_embed(guild, page=0), view=ChannelEditorPickerView(guild, page=0))

    @discord.ui.button(label="Back to Design Studio", emoji="🎨", style=discord.ButtonStyle.secondary, custom_id="dank_design:lock_unlock_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        assert interaction.guild is not None
        options = await _load_design_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=_home_embed(interaction.guild, options), view=DesignHomeView(options))


class LockUnlockButton(discord.ui.Button):
    def __init__(self, *, row: int = 4) -> None:
        super().__init__(
            label="Lock / Unlock Items",
            emoji="🔓",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_design:lock_unlock_home",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(embed=_lock_unlock_home_embed(guild, options), view=LockUnlockHomeView(options))


'''


def patch_text(text: str) -> str:
    if OLD_HOME_RECOMMENDED in text:
        text = text.replace(OLD_HOME_RECOMMENDED, NEW_HOME_RECOMMENDED, 1)
    elif "Lock / Unlock Items" not in text:
        raise SystemExit("Could not find old home recommended block")

    replacements = {
        "Fix Mismatched Names": "Repair Saved Design",
        "Find & Fix Inconsistencies": "Repair Saved Design",
        "Review Repairs": "Repair Saved Design",
        "Live Majority": "Detected Server Style",
        "Format Lock Manager": "Saved Style Lock Manager",
        "Format Locks": "Saved Style Locks",
        "Manage Locks": "Manage Saved Locks",
        "Manage Saved Rules": "Manage Saved Style Locks",
        "🛡 **Protected Names / Unlock** — choose what should never be renamed.": "🛡 **Protected Names / Unlock** — lock or unlock default ticket/log/system names.",
        "Use More Tools for problem checks, saved rules, rename protection, rollback, and help.": "Use Lock / Unlock for saved locks and protected names. Nothing is permanent.",
        "Fix Mismatched Names protects saved rules; use Detected Server Style only as a manual preview.": "Repair Saved Design uses saved rules. Change One Style only changes the one style you pick.",
        "Names only • Saved rules win • Detected Server Style is preview-only when locks exist": "Names only • Preview first • Locks/protected names can be changed anytime",
        "• **{other_count} other issue(s)** — open Channel Editor and fix individually.": "• **{other_count} optional/manual item(s)** — choose icons in batches or open Channel Editor only if you want exact control.",
        "How to fix": "What this means",
        "Needs review": "Optional/manual choices",
        "No leading emoji/icon found. Separator-only change keeps emoji behavior unchanged.": "No leading emoji/icon found. This is optional: choose one with Choose Missing Icons, or leave this row unchanged.",
        "No leading emoji found": "No leading emoji/icon found",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    if "class LockUnlockHomeView" not in text:
        if LOCK_UNLOCK_INSERT_BEFORE not in text:
            raise SystemExit("Could not find DesignHomeView insertion point")
        text = text.replace(LOCK_UNLOCK_INSERT_BEFORE, LOCK_UNLOCK_CLASSES + LOCK_UNLOCK_INSERT_BEFORE, 1)

    home_button_old = '''    @discord.ui.button(label="Help", emoji="❓", style=discord.ButtonStyle.secondary, custom_id="dank_design:start_here", row=4)
    async def guide'''
    home_button_new = '''    @discord.ui.button(label="Help", emoji="❓", style=discord.ButtonStyle.secondary, custom_id="dank_design:start_here", row=4)
    async def guide'''
    if "self.add_item(LockUnlockButton(row=4))" not in text:
        init_old = '''        self.add_item(ThemeSelect(_safe_str(options.get("theme_id"), "gothic_clean")))
        self.add_item(StrengthSelect(_safe_int(options.get("strength"), 2)))
'''
        init_new = '''        self.add_item(ThemeSelect(_safe_str(options.get("theme_id"), "gothic_clean")))
        self.add_item(StrengthSelect(_safe_int(options.get("strength"), 2)))
        self.add_item(LockUnlockButton(row=4))
'''
        if init_old not in text:
            raise SystemExit("Could not patch DesignHomeView init")
        text = text.replace(init_old, init_new, 1)

    required = (
        "Lock / Unlock Categories & Channels",
        "Lock / Unlock Items",
        "Manage Saved Style Locks",
        "Protected Names / Unlock",
        "Repair Saved Design",
        "Optional/manual choices",
        "choose icons in batches",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit("Missing expected simplified UI tokens: " + ", ".join(missing))

    forbidden = (
        "Fix Mismatched Names",
        "Find & Fix Inconsistencies",
        "Review Repairs",
        "open Channel Editor and fix individually",
        "should never be renamed",
        "rename protection",
    )
    leftovers = [token for token in forbidden if token in text]
    if leftovers:
        raise SystemExit("Old/confusing design wording still remains: " + ", ".join(leftovers))

    return text


def main() -> None:
    text = PUBLIC.read_text(encoding="utf-8")
    PUBLIC.write_text(patch_text(text), encoding="utf-8")
    print("✅ Dank Design native UI simplified and Lock / Unlock surfaced")


if __name__ == "__main__":
    main()
