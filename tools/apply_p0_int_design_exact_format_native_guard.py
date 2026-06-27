from __future__ import annotations

"""Safely apply the P0-INT Dank Design exact-format native guard slice.

Why this exists:
- public_design_studio.py is very large.
- The GitHub connector should not blindly replace the whole file from snippets.
- The previous patch artifact is a review artifact, not a git-apply patch.

Run from repo root:
    python tools/apply_p0_int_design_exact_format_native_guard.py
"""

from pathlib import Path

TARGET = Path("stoney_verify/commands_ext/public_design_studio.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Refusing to patch {label}: expected 1 match, found {count}.")
    return text.replace(old, new, 1)


def main() -> None:
    path = TARGET
    text = path.read_text(encoding="utf-8")

    if "async def _guard_design_action(" in text and "design.exact.save_preview" in text:
        print("Already applied: Dank Design exact-format native guard slice is present.")
        return

    text = replace_once(
        text,
        "from typing import Any, Mapping",
        "from typing import Any, Awaitable, Callable, Mapping",
        "typing imports",
    )

    text = replace_once(
        text,
        "import discord\n\nfrom stoney_verify.services import server_design_studio as studio",
        "import discord\n\nfrom stoney_verify.interaction_guard import run_guarded_interaction, safe_send_interaction\nfrom stoney_verify.services import server_design_studio as studio",
        "interaction guard import",
    )

    text = replace_once(
        text,
        "_FORMAT_EDITOR_DRAFTS: dict[str, dict[str, Any]] = {}\n\n\n",
        "_FORMAT_EDITOR_DRAFTS: dict[str, dict[str, Any]] = {}\n\n_DESIGN_ERROR_GUIDANCE = (\n    \"Nothing was changed unless the success message says it was. Reopen `/dank design`, \"\n    \"then check `/dank diagnostics` with the Error ID if it keeps happening.\"\n)\n\n\n",
        "design error guidance",
    )

    old_permission = '''async def _require_design_permission(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        await interaction.response.send_message("❌ This command must be used inside a server.", ephemeral=True)
        return False
    if not _can_user_design(interaction):
        await interaction.response.send_message("❌ Server Design Studio requires **Manage Channels**. It never requires Administrator.", ephemeral=True)
        return False
    return True


'''
    new_permission = '''async def _require_design_permission(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        await safe_send_interaction(
            interaction,
            content="❌ This command must be used inside a server.",
            ephemeral=True,
            action_name="design.permission.server_required",
        )
        return False
    if not _can_user_design(interaction):
        await safe_send_interaction(
            interaction,
            content="❌ Server Design Studio requires **Manage Channels**. It never requires Administrator.",
            ephemeral=True,
            action_name="design.permission.manage_channels_required",
        )
        return False
    return True


async def _guard_design_action(
    interaction: discord.Interaction,
    action_name: str,
    action: Callable[[], Awaitable[None]],
    *,
    defer: bool = False,
) -> None:
    await run_guarded_interaction(
        interaction,
        action,
        defer=defer,
        ephemeral=True,
        action_name=action_name,
        error_title="❌ Dank Design action failed safely",
        error_guidance=_DESIGN_ERROR_GUIDANCE,
    )


'''
    text = replace_once(text, old_permission, new_permission, "design permission/helper")

    old_open = '''async def _open_exact_format_editor(interaction: discord.Interaction, *, scope: str, target_id: int) -> None:
    if not await _require_design_permission(interaction):
        return

    guild = interaction.guild
    assert guild is not None

    try:
        options = await _load_design_options(int(guild.id))
        lock = _initial_editor_lock(options, scope=scope, target_id=int(target_id), guild=guild)
        key = _format_editor_key(int(guild.id), int(interaction.user.id), scope, int(target_id))
        _FORMAT_EDITOR_DRAFTS[key] = lock

        embed = _exact_format_embed(guild, scope=scope, target_id=int(target_id), lock=lock)
        view = ExactFormatEditorViewFactory(guild, scope, int(target_id), lock)

        await interaction.response.edit_message(embed=embed, view=view)
    except Exception as exc:
        message = (
            "❌ Custom Format could not open. "
            f"`{type(exc).__name__}: {_safe_str(exc)[:160]}`"
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except Exception:
            raise


'''
    new_open = '''async def _open_exact_format_editor(interaction: discord.Interaction, *, scope: str, target_id: int) -> None:
    async def action() -> None:
        if not await _require_design_permission(interaction):
            return

        guild = interaction.guild
        assert guild is not None

        options = await _load_design_options(int(guild.id))
        lock = _initial_editor_lock(options, scope=scope, target_id=int(target_id), guild=guild)
        key = _format_editor_key(int(guild.id), int(interaction.user.id), scope, int(target_id))
        _FORMAT_EDITOR_DRAFTS[key] = lock

        embed = _exact_format_embed(guild, scope=scope, target_id=int(target_id), lock=lock)
        view = ExactFormatEditorViewFactory(guild, scope, int(target_id), lock)

        await interaction.response.edit_message(embed=embed, view=view)

    await _guard_design_action(interaction, f"design.exact.open.{scope}", action, defer=False)


'''
    text = replace_once(text, old_open, new_open, "open exact format editor")

    old_layout = '''    @discord.ui.button(label="Show Examples", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="dank_design:exact_layout_examples", row=4)
    async def layout_examples(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        key = _format_editor_key(int(guild.id), int(interaction.user.id), self.scope, self.target_id)
        lock = dict(_FORMAT_EDITOR_DRAFTS.get(key) or {})
        if not lock:
            options = await _load_design_options(int(guild.id))
            lock = _initial_editor_lock(options, scope=self.scope, target_id=self.target_id, guild=guild)
            _FORMAT_EDITOR_DRAFTS[key] = lock
        await interaction.response.edit_message(
            embed=_separator_gallery_embed(guild, scope=self.scope, target_id=self.target_id, lock=lock, page=0),
            view=SeparatorExamplesView(guild, scope=self.scope, target_id=self.target_id, lock=lock, page=0),
        )

'''
    new_layout = '''    @discord.ui.button(label="Show Examples", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="dank_design:exact_layout_examples", row=4)
    async def layout_examples(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def action() -> None:
            if not await _require_design_permission(interaction):
                return
            guild = interaction.guild
            assert guild is not None
            key = _format_editor_key(int(guild.id), int(interaction.user.id), self.scope, self.target_id)
            lock = dict(_FORMAT_EDITOR_DRAFTS.get(key) or {})
            if not lock:
                options = await _load_design_options(int(guild.id))
                lock = _initial_editor_lock(options, scope=self.scope, target_id=self.target_id, guild=guild)
                _FORMAT_EDITOR_DRAFTS[key] = lock
            await interaction.response.edit_message(
                embed=_separator_gallery_embed(guild, scope=self.scope, target_id=self.target_id, lock=lock, page=0),
                view=SeparatorExamplesView(guild, scope=self.scope, target_id=self.target_id, lock=lock, page=0),
            )

        await _guard_design_action(interaction, "design.exact.examples", action, defer=False)

'''
    text = replace_once(text, old_layout, new_layout, "exact layout examples button")

    text = replace_once(
        text,
        '''    @discord.ui.button(label="Save Rule & Preview", emoji="👁️", style=discord.ButtonStyle.primary, custom_id="dank_design:exact_save_preview", row=4)
    async def save_and_preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _save_exact_and_preview(interaction, scope=self.scope, target_id=self.target_id)


''',
        '''    @discord.ui.button(label="Save Rule & Preview", emoji="👁️", style=discord.ButtonStyle.primary, custom_id="dank_design:exact_save_preview", row=4)
    async def save_and_preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def action() -> None:
            await _save_exact_and_preview(interaction, scope=self.scope, target_id=self.target_id)

        await _guard_design_action(interaction, "design.exact.save_preview", action, defer=False)


''',
        "exact save preview button",
    )

    old_server_style = '''    @discord.ui.button(label="Server Style", emoji="🧭", style=discord.ButtonStyle.secondary, custom_id="dank_design:exact_use_majority", row=4)
    async def use_server_style(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None

        options = await _load_design_options(int(guild.id))
        current = _live_majority_exact_lock(guild, options, scope=self.scope, target_id=self.target_id)
        if not current:
            return await interaction.response.send_message(
                "I could not detect a clear server style yet. Use Save Rule & Preview before applying.",
                ephemeral=True,
            )

        key = _format_editor_key(int(guild.id), int(interaction.user.id), self.scope, self.target_id)
        _FORMAT_EDITOR_DRAFTS[key] = current

        await interaction.response.edit_message(
            embed=_exact_format_embed(guild, scope=self.scope, target_id=self.target_id, lock=current),
            view=ExactFormatEditorViewFactory(guild, self.scope, self.target_id, current),
        )

'''
    new_server_style = '''    @discord.ui.button(label="Server Style", emoji="🧭", style=discord.ButtonStyle.secondary, custom_id="dank_design:exact_use_majority", row=4)
    async def use_server_style(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def action() -> None:
            if not await _require_design_permission(interaction):
                return
            guild = interaction.guild
            assert guild is not None

            options = await _load_design_options(int(guild.id))
            current = _live_majority_exact_lock(guild, options, scope=self.scope, target_id=self.target_id)
            if not current:
                await safe_send_interaction(
                    interaction,
                    content="I could not detect a clear server style yet. Use Save Rule & Preview before applying.",
                    ephemeral=True,
                    action_name="design.exact.server_style.no_majority",
                )
                return

            key = _format_editor_key(int(guild.id), int(interaction.user.id), self.scope, self.target_id)
            _FORMAT_EDITOR_DRAFTS[key] = current

            await interaction.response.edit_message(
                embed=_exact_format_embed(guild, scope=self.scope, target_id=self.target_id, lock=current),
                view=ExactFormatEditorViewFactory(guild, self.scope, self.target_id, current),
            )

        await _guard_design_action(interaction, "design.exact.server_style", action, defer=False)

'''
    text = replace_once(text, old_server_style, new_server_style, "exact server style button")

    text = replace_once(
        text,
        '''    @discord.ui.button(label="Emoji", emoji="😀", style=discord.ButtonStyle.secondary, custom_id="dank_design:exact_emoji", row=4)
    async def set_emoji(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        await interaction.response.send_modal(CustomEmojiModal(scope=self.scope, target_id=self.target_id))

''',
        '''    @discord.ui.button(label="Emoji", emoji="😀", style=discord.ButtonStyle.secondary, custom_id="dank_design:exact_emoji", row=4)
    async def set_emoji(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def action() -> None:
            if not await _require_design_permission(interaction):
                return
            await interaction.response.send_modal(CustomEmojiModal(scope=self.scope, target_id=self.target_id))

        await _guard_design_action(interaction, "design.exact.emoji_modal", action, defer=False)

''',
        "exact emoji button",
    )

    old_back = '''    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design:exact_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        if self.scope == "category":
            category = guild.get_channel(self.target_id)
            if isinstance(category, discord.CategoryChannel):
                await interaction.response.edit_message(embed=_category_action_embed(category), view=CategoryEditorActionView(self.target_id))
            else:
                await interaction.response.edit_message(embed=_category_editor_embed(guild, page=0), view=CategoryEditorPickerView(guild, page=0))
        else:
            channel = guild.get_channel(self.target_id)
            if channel is not None:
                await interaction.response.edit_message(embed=_channel_action_embed(channel), view=ChannelEditorActionView(self.target_id))
            else:
                await interaction.response.edit_message(embed=_channel_editor_embed(guild, page=0), view=ChannelEditorPickerView(guild, page=0))

'''
    new_back = '''    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design:exact_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def action() -> None:
            if not await _require_design_permission(interaction):
                return
            guild = interaction.guild
            assert guild is not None
            if self.scope == "category":
                category = guild.get_channel(self.target_id)
                if isinstance(category, discord.CategoryChannel):
                    await interaction.response.edit_message(embed=_category_action_embed(category), view=CategoryEditorActionView(self.target_id))
                else:
                    await interaction.response.edit_message(embed=_category_editor_embed(guild, page=0), view=CategoryEditorPickerView(guild, page=0))
            else:
                channel = guild.get_channel(self.target_id)
                if channel is not None:
                    await interaction.response.edit_message(embed=_channel_action_embed(channel), view=ChannelEditorActionView(self.target_id))
                else:
                    await interaction.response.edit_message(embed=_channel_editor_embed(guild, page=0), view=ChannelEditorPickerView(guild, page=0))

        await _guard_design_action(interaction, "design.exact.back", action, defer=False)

'''
    text = replace_once(text, old_back, new_back, "exact back button")

    path.write_text(text, encoding="utf-8")
    print("Applied: Dank Design exact-format native guard slice.")


if __name__ == "__main__":
    main()
