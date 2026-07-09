from __future__ import annotations

"""Boundary-based native-guard migration for Dank Design Style Change issue-review flows.

Run from repo root:
    python tools/apply_p0_int_design_style_change_native_guard.py
"""

from pathlib import Path

TARGET = Path("stoney_verify/commands_ext/public_design_studio.py")

START_MARKER = "class StyleChangeFixMissingEmojiModal(discord.ui.Modal):"
END_MARKER = "class StyleChangePreviewView(DesignPreviewView):"

ACTION_NAMES = (
    "design.style_change.missing_icons_submit",
    "design.style_change.apply_safe_only",
    "design.style_change.fix_missing_icons_modal",
)


def replace_section(text: str, replacement: str) -> str:
    start = text.find(START_MARKER)
    if start < 0:
        raise SystemExit(f"Refusing to patch style-change flow: start marker not found: {START_MARKER!r}.")
    end = text.find(END_MARKER, start)
    if end < 0:
        raise SystemExit(f"Refusing to patch style-change flow: end marker not found: {END_MARKER!r}.")
    return text[:start] + replacement + text[end:]


def main() -> None:
    path = TARGET
    text = path.read_text(encoding="utf-8")

    if "async def _guard_design_action(" not in text or "safe_send_interaction" not in text:
        raise SystemExit("Missing native design interaction helpers. Apply exact-format guard migration first.")

    current_region = text[text.find(START_MARKER): text.find(END_MARKER, text.find(START_MARKER))]
    if all(name in current_region for name in ACTION_NAMES) and "        async def action() -> None:" in current_region:
        print("Already applied: Dank Design style-change native guard slice is present.")
        return

    new_style_change_region = r'''class StyleChangeFixMissingEmojiModal(discord.ui.Modal):
    def __init__(self, *, items: list[dict[str, Any]], separator_id: str) -> None:
        super().__init__(title="Choose Missing Icons")
        self.separator_id = _safe_str(separator_id, "none")
        self.item_keys: list[str] = []

        for index, item in enumerate(items[:5], start=1):
            channel_id = _safe_str(item.get("channel_id"), "")
            self.item_keys.append(channel_id)
            before = _safe_str(item.get("before"), "channel")
            base = _safe_str(item.get("base_name"), before)
            field = discord.ui.TextInput(
                label=f"{index}. Emoji for {base}"[:45],
                placeholder=f"Example: 🎮 for {before}"[:100],
                min_length=1,
                max_length=12,
                required=True,
            )
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        async def action() -> None:
            if not await _require_design_permission(interaction):
                return

            guild = interaction.guild
            assert guild is not None

            key = _key(int(guild.id), int(interaction.user.id))
            pending = _PENDING.get(key)
            if not pending:
                await safe_send_interaction(
                    interaction,
                    content="This preview expired. Run Style Change again.",
                    ephemeral=True,
                    action_name="design.style_change.missing_icons.expired",
                )
                return

            items = list(pending.get("items") or [])
            separator_id = _safe_str(pending.get("separator_id"), self.separator_id)

            values_by_channel: dict[str, str] = {}
            for channel_id, child in zip(self.item_keys, self.children):
                values_by_channel[channel_id] = _safe_str(getattr(child, "value", ""), "")

            for item in items:
                channel_id = _safe_str(item.get("channel_id"), "")
                if channel_id not in values_by_channel:
                    continue

                manual_emoji = values_by_channel[channel_id]
                after, warnings, blockers = _style_change_after_with_manual_emoji(
                    _safe_str(item.get("before"), ""),
                    separator_id,
                    manual_emoji,
                )

                item["after"] = after
                item["warnings"] = warnings
                item["blockers"] = blockers
                item["status"] = "failed" if blockers else ("changed" if after != item.get("before") else "unchanged")
                item["style_change_manual_emoji"] = manual_emoji

            pending["items"] = items
            _PENDING[key] = pending

            embed, view = _style_change_rebuild_preview_response(guild, pending)
            await interaction.response.edit_message(embed=embed, view=view)

        await _guard_design_action(interaction, "design.style_change.missing_icons_submit", action, defer=False)


class StyleChangeApplySafeOnlyButton(discord.ui.Button):
    def __init__(self, *, row: int = 2) -> None:
        super().__init__(
            label="Apply Safe Ones Only",
            emoji="✅",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_design:style_change_skip_issues",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        async def action() -> None:
            if not await _require_design_permission(interaction):
                return

            guild = interaction.guild
            assert guild is not None

            key = _key(int(guild.id), int(interaction.user.id))
            pending = _PENDING.get(key)
            if not pending:
                await safe_send_interaction(
                    interaction,
                    content="This preview expired. Run Style Change again.",
                    ephemeral=True,
                    action_name="design.style_change.apply_safe.expired",
                )
                return

            items = list(pending.get("items") or [])
            safe_items: list[dict[str, Any]] = []

            for item in items:
                if item.get("status") == "failed":
                    skipped = dict(item)
                    skipped["after"] = skipped.get("before")
                    skipped["status"] = "protected"
                    skipped["protected"] = True
                    skipped["warnings"] = ["Skipped by user from Style Change issues review."]
                    skipped["blockers"] = []
                    safe_items.append(skipped)
                else:
                    safe_items.append(item)

            pending["items"] = safe_items
            pending["style_change_skipped_issues"] = True
            _PENDING[key] = pending

            separator_id = _safe_str(pending.get("separator_id"), "none")
            embed = _style_change_preview_embed(guild, safe_items, separator_id=separator_id)
            embed.title = "👁️ Style Change Preview · Safe Changes Only"
            embed.add_field(
                name="Skipped issues",
                value="Needs-review rows were left untouched. Apply will only rename safe rows.",
                inline=False,
            )

            await interaction.response.edit_message(
                embed=embed,
                view=StyleChangePreviewView(can_apply=True, has_blockers=False),
            )

        await _guard_design_action(interaction, "design.style_change.apply_safe_only", action, defer=False)


class StyleChangeFixMissingEmojiButton(discord.ui.Button):
    def __init__(self, *, row: int = 2) -> None:
        super().__init__(
            label="Choose Missing Icons",
            emoji="😀",
            style=discord.ButtonStyle.primary,
            custom_id="dank_design:style_change_fix_missing_emojis",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        async def action() -> None:
            if not await _require_design_permission(interaction):
                return

            guild = interaction.guild
            assert guild is not None

            key = _key(int(guild.id), int(interaction.user.id))
            pending = _PENDING.get(key)
            if not pending:
                await safe_send_interaction(
                    interaction,
                    content="This preview expired. Run Style Change again.",
                    ephemeral=True,
                    action_name="design.style_change.fix_missing.expired",
                )
                return

            items = list(pending.get("items") or [])
            missing = _style_change_missing_emoji_items(items)

            if not missing:
                await safe_send_interaction(
                    interaction,
                    content="No missing-emoji rows found in this preview.",
                    ephemeral=True,
                    action_name="design.style_change.fix_missing.none",
                )
                return

            # Discord modals support at most 5 text inputs. Open the first batch,
            # then rebuild the preview after submit so this button can handle
            # the next unresolved batch instead of dead-ending the flow.
            batch = missing[:5]
            separator_id = _safe_str(pending.get("separator_id"), "none")
            await interaction.response.send_modal(
                StyleChangeFixMissingEmojiModal(items=batch, separator_id=separator_id)
            )

        await _guard_design_action(interaction, "design.style_change.fix_missing_icons_modal", action, defer=False)


'''
    text = replace_section(text, new_style_change_region)
    path.write_text(text, encoding="utf-8")
    print("Applied: Dank Design style-change native guard slice.")


if __name__ == "__main__":
    main()
