from __future__ import annotations

"""Native, phone-friendly Discord Soundboard Studio.

Soundboard bytes live only in short-lived, private review views. Discord is the
authoritative store for published sounds; no parallel sound catalog is created.
"""

import io
from typing import Any

import discord

from stoney_verify.panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from stoney_verify.soundboard_audio import (
    MAX_BATCH, PreparedSound, SoundboardInputError, prepare_attachment,
    validate_sound_name, validate_upload,
)

_ALLOWED_MENTIONS = discord.AllowedMentions.none()


def _perms(interaction: discord.Interaction, *, bot: bool = False) -> Any:
    member = interaction.guild.me if bot and interaction.guild else interaction.user
    return getattr(member, "guild_permissions", None)


def _allowed(interaction: discord.Interaction, action: str, *, bot: bool = False) -> bool:
    p = _perms(interaction, bot=bot)
    return bool(
        p and (
            getattr(p, "administrator", False)
            or getattr(p, "manage_expressions" if action == "manage" else "create_expressions", False)
        )
    )


async def _notice(interaction: discord.Interaction, message: str) -> None:
    options = dict(ephemeral=True, allowed_mentions=_ALLOWED_MENTIONS)
    if interaction.response.is_done():
        await interaction.followup.send(message, **options)
    else:
        await interaction.response.send_message(message, **options)


async def _require(interaction: discord.Interaction, action: str) -> bool:
    if interaction.guild is None:
        await _notice(interaction, "❌ Use Soundboard Studio inside a server, not in DMs.")
        return False
    if not _allowed(interaction, action):
        permission = "Manage Expressions" if action == "manage" else "Create Expressions"
        await _notice(interaction, f"❌ Your server role needs **{permission}**.")
        return False
    if not _allowed(interaction, action, bot=True):
        permission = "Manage Expressions" if action == "manage" else "Create Expressions"
        await _notice(interaction, f"❌ Dank Shield needs **{permission}** in this server.")
        return False
    return True


class _PrivateView(discord.ui.View):
    def __init__(self, owner_id: int, guild_id: int) -> None:
        super().__init__(timeout=min(600, PRIVATE_MENU_TTL_SECONDS))
        self.owner_id = int(owner_id)
        self.guild_id = int(guild_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            int(interaction.user.id) == self.owner_id
            and interaction.guild is not None
            and int(interaction.guild.id) == self.guild_id
        ):
            return True
        await _notice(interaction, "❌ This is someone else's private Soundboard Studio.")
        return False


class _ReviewEdit(discord.ui.Modal, title="Edit sound before uploading"):
    name = discord.ui.TextInput(label="Name (2–32 characters)", max_length=32)
    emoji = discord.ui.TextInput(
        label="Emoji (optional)", required=False, max_length=16, placeholder="🔊"
    )
    volume = discord.ui.TextInput(
        label="Playback volume (0–100%)", max_length=3, default="100"
    )

    def __init__(self, parent: "SoundReviewView") -> None:
        super().__init__()
        self.parent = parent
        sound = parent.current
        self.name.default = sound.name
        self.emoji.default = sound.emoji or ""
        self.volume.default = str(round(sound.volume * 100))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            name = validate_sound_name(str(self.name.value))
            percent = int(str(self.volume.value))
            if not 0 <= percent <= 100:
                raise ValueError
            emoji = str(self.emoji.value or "").strip() or None
            if emoji and len(emoji) > 16:
                raise ValueError
        except (ValueError, SoundboardInputError):
            return await _notice(interaction, "❌ Enter a 2–32 character name and volume from 0–100%.")
        if not self.parent.sounds or self.parent.busy:
            return await _notice(interaction, "This preview is no longer available.")
        current = self.parent.current
        current.name, current.volume, current.emoji = name, percent / 100, emoji
        await _notice(interaction, "✅ Updated the staged sound. It has not been published yet.")
        if self.parent.message is not None:
            try:
                await self.parent.message.edit(embed=self.parent.embed(), view=self.parent)
            except discord.HTTPException:
                pass


class SoundReviewView(_PrivateView):
    def __init__(self, owner_id: int, guild_id: int, sounds: list[PreparedSound]) -> None:
        super().__init__(owner_id, guild_id)
        self.sounds = sounds
        self.index = 0
        self.busy = False
        self.message: Any = None

    @property
    def current(self) -> PreparedSound:
        return self.sounds[self.index]

    def embed(self) -> discord.Embed:
        if not self.sounds:
            return discord.Embed(title="✅ Soundboard uploads finished", color=discord.Color.green())
        item = self.current
        e = discord.Embed(
            title="🔊 Review Soundboard Upload",
            description=(
                f"**{self.index + 1} of {len(self.sounds)}** ready to publish\n"
                f"**Name:** {discord.utils.escape_markdown(item.name)}\n"
                f"**File:** {discord.utils.escape_markdown(item.filename[:80])}\n"
                f"**Length:** up to 5 seconds · **MP3:** {len(item.data) // 1024} KB\n"
                f"**Emoji:** {item.emoji or 'none'} · **Volume:** {round(item.volume * 100)}%\n\n"
                "Preview the actual converted sound, edit its name/emoji/volume, "
                "then publish one or all. Nothing goes live until you confirm."
            ),
            color=discord.Color.blurple(),
        )
        e.set_footer(text="To pick another part of a recording, use /dank upload with trim_start.")
        return e

    def on_timeout(self) -> None:
        self.sounds.clear()
        self.message = None

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary, row=0)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.busy or not self.sounds:
            return await _notice(interaction, "That upload is already being processed.")
        self.index = (self.index - 1) % len(self.sounds)
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, row=0)
    async def next_sound(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.busy or not self.sounds:
            return await _notice(interaction, "That upload is already being processed.")
        self.index = (self.index + 1) % len(self.sounds)
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Preview audio", emoji="🔉", style=discord.ButtonStyle.primary, row=0)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.busy or not self.sounds:
            return await _notice(interaction, "The preview is no longer available.")
        await interaction.response.send_message(
            "🔉 This is the actual 5-second clip Discord will receive.",
            file=discord.File(io.BytesIO(self.current.data), filename="soundboard-preview.mp3"),
            ephemeral=True, allowed_mentions=_ALLOWED_MENTIONS,
        )

    @discord.ui.button(label="Edit", emoji="✏️", style=discord.ButtonStyle.secondary, row=0)
    async def edit_sound(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.busy or not self.sounds:
            return await _notice(interaction, "The preview is no longer available.")
        await interaction.response.send_modal(_ReviewEdit(self))

    async def _publish(self, interaction: discord.Interaction, *, all_sounds: bool) -> None:
        if self.busy or not self.sounds:
            return await _notice(interaction, "There are no pending sounds.")
        if not await _require(interaction, "create"):
            return
        self.busy = True
        await interaction.response.defer()
        selected = list(self.sounds) if all_sounds else [self.current]
        success: list[str] = []
        failure: list[str] = []
        try:
            for item in selected:
                try:
                    await interaction.guild.create_soundboard_sound(
                        name=validate_sound_name(item.name),
                        sound=item.data,
                        volume=item.volume,
                        emoji=item.emoji,
                        reason=f"Dank Shield Soundboard Studio upload by {interaction.user.id}",
                    )
                except discord.Forbidden:
                    failure.append(f"{item.name}: Discord denied Create Expressions")
                except discord.HTTPException as exc:
                    failure.append(f"{item.name}: Discord rejected the sound ({exc.status})")
                else:
                    success.append(item.name)
                    self.sounds.remove(item)
            self.index = min(self.index, max(0, len(self.sounds) - 1))
        finally:
            self.busy = False
        if self.sounds:
            await interaction.edit_original_response(embed=self.embed(), view=self)
        else:
            self.stop()
            await interaction.edit_original_response(
                content="✅ All staged sounds have been published.",
                embed=None, view=None,
            )
        summary = f"✅ Published {len(success)} sound(s)." if success else "No sounds were published."
        if failure:
            summary += "\n❌ " + "\n❌ ".join(failure)[:1200]
        await interaction.followup.send(summary, ephemeral=True, allowed_mentions=_ALLOWED_MENTIONS)

    @discord.ui.button(label="Publish clip", emoji="✅", style=discord.ButtonStyle.success, row=1)
    async def publish_one(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._publish(interaction, all_sounds=False)

    @discord.ui.button(label="Publish all", emoji="📤", style=discord.ButtonStyle.success, row=1)
    async def publish_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._publish(interaction, all_sounds=True)

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.danger, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.busy:
            return await _notice(interaction, "Publishing is already in progress.")
        self.sounds.clear()
        self.stop()
        await interaction.response.edit_message(
            content="Canceled. No remaining clips were uploaded.", embed=None, view=None
        )


async def stage_soundboard_upload(
    interaction: discord.Interaction,
    attachments: list[discord.Attachment],
    *,
    trim_start: float = 0,
) -> None:
    if not await _require(interaction, "create"):
        return
    files = [attachment for attachment in attachments if attachment is not None]
    if not files or len(files) > MAX_BATCH:
        return await _notice(interaction, "Attach 1–3 audio/video files.")
    try:
        for attachment in files:
            validate_upload(attachment)
        start = float(trim_start)
        if not 0 <= start <= 300:
            raise SoundboardInputError("Trim start must be between 0 and 300 seconds.")
    except (ValueError, SoundboardInputError) as exc:
        return await _notice(interaction, f"❌ {exc}")
    await interaction.response.defer(ephemeral=True, thinking=True)
    staged: list[PreparedSound] = []
    errors: list[str] = []
    for attachment in files:
        try:
            staged.append(await prepare_attachment(attachment, start_seconds=start))
        except (SoundboardInputError, discord.HTTPException) as exc:
            errors.append(f"{attachment.filename}: {exc}")
        except OSError:
            errors.append(f"{attachment.filename}: audio conversion unavailable")
    if not staged:
        return await interaction.followup.send(
            "❌ No clips could be prepared.\n" + "\n".join(errors)[:1500],
            ephemeral=True, allowed_mentions=_ALLOWED_MENTIONS,
        )
    view = SoundReviewView(interaction.user.id, interaction.guild.id, staged)
    message = await interaction.followup.send(
        content=("⚠️ Some files failed:\n" + "\n".join(errors)[:900]) if errors else None,
        embed=view.embed(), view=view, ephemeral=True,
        allowed_mentions=_ALLOWED_MENTIONS, wait=True,
    )
    view.message = message


class _SoundSelect(discord.ui.Select):
    def __init__(self, sounds: list[discord.SoundboardSound], page: int) -> None:
        self.sound_ids = {str(s.id) for s in sounds}
        super().__init__(
            placeholder="Choose a sound to inspect",
            options=[
                discord.SelectOption(label=s.name[:100], value=str(s.id))
                for s in sounds
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        value = str(self.values[0])
        if value not in self.sound_ids:
            return await _notice(interaction, "That sound is no longer on this page.")
        sound = await interaction.guild.fetch_soundboard_sound(int(value))
        view = SoundDetailView(interaction.user.id, interaction.guild.id, int(value))
        await interaction.response.send_message(
            embed=_detail_embed(sound), view=view, ephemeral=True,
            allowed_mentions=_ALLOWED_MENTIONS,
        )


def _detail_embed(sound: discord.SoundboardSound) -> discord.Embed:
    return discord.Embed(
        title=f"🔊 {sound.name}",
        description=(
            f"**Sound ID:** `{sound.id}`\n"
            f"**Volume:** {round(sound.volume * 100)}%\n"
            f"**Emoji:** {sound.emoji or 'none'}\n\n"
            "Manage Expressions is required to edit or remove existing sounds."
        ),
        color=discord.Color.blurple(),
    )


class SoundboardBrowseView(_PrivateView):
    def __init__(self, owner_id: int, guild_id: int, sounds: list[discord.SoundboardSound], page: int = 0) -> None:
        super().__init__(owner_id, guild_id)
        self.sounds = sorted(sounds, key=lambda item: item.name.casefold())
        self.page = max(0, min(page, max(0, (len(self.sounds) - 1) // 20)))
        subset = self.sounds[self.page * 20 : self.page * 20 + 20]
        if subset:
            self.add_item(_SoundSelect(subset, self.page))

    def embed(self) -> discord.Embed:
        e = discord.Embed(
            title="🔊 Soundboard Studio",
            description=(
                f"**{len(self.sounds)} sounds** on this server · page {self.page + 1}\n\n"
                "📤 **Upload:** use `/dank upload` → **Soundboard Clip**. "
                "Attach up to 3 files, choose a trim start, preview, and publish. "
                "MP3, WAV, OGG, M4A, FLAC and common video clips are supported.\n\n"
                "📚 **Manage:** pick an existing sound below to rename, set its emoji/volume, "
                "or remove it. No separate file library or subscription required."
            ),
            color=discord.Color.blurple(),
        )
        return e

    @discord.ui.button(label="Previous page", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = SoundboardBrowseView(self.owner_id, self.guild_id, self.sounds, self.page - 1)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    @discord.ui.button(label="Next page", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = SoundboardBrowseView(self.owner_id, self.guild_id, self.sounds, self.page + 1)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.primary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        try:
            sounds = await interaction.guild.fetch_soundboard_sounds()
        except discord.HTTPException:
            return await _notice(interaction, "❌ Could not refresh this server's sounds.")
        view = SoundboardBrowseView(self.owner_id, self.guild_id, sounds, self.page)
        await interaction.edit_original_response(embed=view.embed(), view=view)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="Soundboard Studio closed.", embed=None, view=None)


class _EditExisting(discord.ui.Modal, title="Edit server sound"):
    name = discord.ui.TextInput(label="Sound name", max_length=32)
    emoji = discord.ui.TextInput(label="Emoji (optional)", required=False, max_length=16)
    volume = discord.ui.TextInput(label="Volume 0–100%", max_length=3)

    def __init__(self, guild_id: int, sound: discord.SoundboardSound) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.sound_id = sound.id
        self.name.default = sound.name
        self.emoji.default = str(sound.emoji or "")
        self.volume.default = str(round(sound.volume * 100))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            return await _notice(interaction, "❌ Incorrect server.")
        if not await _require(interaction, "manage"):
            return
        try:
            name = validate_sound_name(str(self.name.value))
            percent = int(str(self.volume.value))
            if not 0 <= percent <= 100:
                raise ValueError
        except (ValueError, SoundboardInputError):
            return await _notice(interaction, "❌ Name must be 2–32 characters; volume 0–100%.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            sound = await interaction.guild.fetch_soundboard_sound(self.sound_id)
            await sound.edit(
                name=name, volume=percent / 100,
                emoji=str(self.emoji.value or "").strip() or None,
                reason=f"Dank Shield Soundboard Studio edit by {interaction.user.id}",
            )
        except discord.HTTPException as exc:
            return await _notice(interaction, f"❌ Discord rejected that change ({exc.status}).")
        await _notice(interaction, "✅ Sound updated. Refresh Studio to see the new details.")


class SoundDetailView(_PrivateView):
    def __init__(self, owner_id: int, guild_id: int, sound_id: int) -> None:
        super().__init__(owner_id, guild_id)
        self.sound_id = sound_id

    @discord.ui.button(label="Edit name / emoji / volume", emoji="✏️", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require(interaction, "manage"):
            return
        try:
            sound = await interaction.guild.fetch_soundboard_sound(self.sound_id)
        except discord.HTTPException:
            return await _notice(interaction, "❌ This sound is no longer available.")
        await interaction.response.send_modal(_EditExisting(self.guild_id, sound))

    @discord.ui.button(label="Delete", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require(interaction, "manage"):
            return
        await interaction.response.edit_message(
            content="⚠️ Delete this sound from the entire server? This cannot be undone.",
            embed=None,
            view=SoundDeleteConfirmView(self.owner_id, self.guild_id, self.sound_id),
        )


class SoundDeleteConfirmView(_PrivateView):
    def __init__(self, owner_id: int, guild_id: int, sound_id: int) -> None:
        super().__init__(owner_id, guild_id)
        self.sound_id = sound_id

    @discord.ui.button(label="Yes, delete sound", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require(interaction, "manage"):
            return
        await interaction.response.defer()
        try:
            sound = await interaction.guild.fetch_soundboard_sound(self.sound_id)
            await sound.delete(reason=f"Dank Shield Soundboard Studio delete by {interaction.user.id}")
        except discord.HTTPException as exc:
            return await _notice(interaction, f"❌ Could not delete sound ({exc.status}).")
        self.stop()
        await interaction.edit_original_response(
            content="✅ Sound removed from this server's native Soundboard.",
            embed=None, view=None,
        )

    @discord.ui.button(label="Keep sound", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="Sound kept.", embed=None, view=None)


async def open_soundboard_studio(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return await _notice(interaction, "❌ Soundboard Studio works inside servers only.")
    if not (_allowed(interaction, "create") or _allowed(interaction, "manage")):
        return await _notice(interaction, "❌ You need Create Expressions or Manage Expressions.")
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        sounds = await interaction.guild.fetch_soundboard_sounds()
    except discord.HTTPException as exc:
        return await _notice(interaction, f"❌ Can't read server sounds ({exc.status}).")
    view = SoundboardBrowseView(interaction.user.id, interaction.guild.id, sounds)
    await interaction.followup.send(
        embed=view.embed(), view=view, ephemeral=True, allowed_mentions=_ALLOWED_MENTIONS,
    )
