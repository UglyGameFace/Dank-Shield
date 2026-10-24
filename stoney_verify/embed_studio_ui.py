"""One menu-first Embed Studio shared by Community Tools and Server Setup.

No new slash-command root, background worker, or global draft cache. A draft
belongs to its staff interaction; named templates and bot post references use
the existing per-guild atomic config store.
"""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from dataclasses import dataclass, field
from typing import Any, Callable
from datetime import datetime, timezone
import asyncio
import secrets
import time

import discord

from .embed_studio_core import (
    STUDIO_KEY, MAX_EMBEDS, MAX_FIELDS, MAX_TEMPLATES,
    StudioValidationError, new_project, validate_project, render_embeds,
    link_view, default_embed, parse_library, library_payload, https_url, draft_snapshot,
    MAX_PORTABLE_BYTES, export_portable_project, import_portable_project,
)

_MENTIONS = discord.AllowedMentions.none()


@dataclass
class StudioSession:
    guild_id: int
    owner_id: int
    project: dict[str, Any] = field(default_factory=new_project)
    channel_id: int = 0
    active: int = 0
    post_key: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    saved_version: int = 0
    dirty: bool = False
    save_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def apply(self, mutation: Callable[[dict[str, Any]], None]) -> None:
        candidate = deepcopy(self.project)
        mutation(candidate)
        checked = validate_project(candidate)
        self.history.append(self.project)
        self.history = self.history[-10:]
        self.project = checked
        self.dirty = True
        self.active = min(self.active, len(self.project["embeds"]) - 1)

    def undo(self) -> bool:
        if not self.history:
            return False
        self.project = self.history.pop()
        self.dirty = True
        self.active = min(self.active, len(self.project["embeds"]) - 1)
        return True


def _staff(interaction: discord.Interaction) -> bool:
    member = interaction.user
    guild = interaction.guild
    return bool(
        guild is not None and isinstance(member, discord.Member)
        and (
            int(member.id) == int(guild.owner_id)
            or member.guild_permissions.administrator
            or member.guild_permissions.manage_guild
        )
    )


async def _say(interaction: discord.Interaction, content: str, **kwargs: Any) -> None:
    kwargs.setdefault("ephemeral", True)
    kwargs.setdefault("allowed_mentions", _MENTIONS)
    if interaction.response.is_done():
        await interaction.followup.send(content, **kwargs)
    else:
        await interaction.response.send_message(content, **kwargs)


class _StudioView(discord.ui.View):
    def __init__(self, session: StudioSession, *, timeout: int = 900) -> None:
        super().__init__(timeout=timeout)
        self.session = session

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.guild is None or int(interaction.guild.id) != self.session.guild_id
            or int(interaction.user.id) != self.session.owner_id
        ):
            await _say(interaction, "Open your own Embed Studio in the server to use these controls.")
            return False
        if not _staff(interaction):
            await _say(interaction, "Embed Studio needs Manage Server or Administrator access.")
            return False
        return True


def _summary(session: StudioSession) -> discord.Embed:
    project = session.project
    current = project["embeds"][session.active]
    embed = discord.Embed(
        title="🧱 Dank Shield Embed Studio",
        description=(
            "Build your message with the buttons below. The next screen shows "
            "only what you need. Nothing goes public until you review and publish."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Current draft",
        value=(
            f"Embed **{session.active + 1}/{len(project['embeds'])}**"
            f" • {len(current['fields'])}/25 fields"
            f" • {len(project['links'])}/5 link buttons"
            f" • {len(session.history)} undo steps"
        ),
        inline=False,
    )
    embed.add_field(
        name="Role controls",
        value=(
            f"{len(project['role_actions'])} optional role(s)"
            + (f" • {project['role_mode']}" if project["role_actions"] else " • none")
        ),
        inline=True,
    )
    embed.add_field(
        name="Destination",
        value=f"<#{session.channel_id}>" if session.channel_id else "Choose a channel below",
        inline=True,
    )
    embed.add_field(
        name="Mode",
        value="Edit a previously published message" if session.post_key else "New draft",
        inline=True,
    )
    embed.set_footer(text="Text • Appearance • Media • Fields • Links • Roles • Layout • Save • Preview")
    return embed


def _preview_embed(session: StudioSession) -> discord.Embed:
    return render_embeds(session.project)[session.active]


async def _home(interaction: discord.Interaction, session: StudioSession, *, notice: str = "") -> None:
    autosave_warning = await _autosave(interaction, session)
    message = (
        ("✅ " + notice + "\n" if notice else "")
        + ("⚠️ " + autosave_warning + "\n" if autosave_warning else "")
        + "Private editor • choose a section; Publish requires confirmation."
    )
    current = _preview_embed(session)
    summary = _summary(session)
    # Discord's 6000-character cap applies across every displayed embed.
    # A full-length single embed must not be made invalid by editor chrome.
    preview_embeds = [current] if len(current) + len(summary) > 6000 else [summary, current]
    kwargs = {
        "content": message,
        "embeds": preview_embeds,
        "view": StudioHomeView(session),
        "allowed_mentions": _MENTIONS,
    }
    if interaction.response.is_done():
        await interaction.edit_original_response(**kwargs)
    elif interaction.message is not None:
        await interaction.response.edit_message(**kwargs)
    else:
        await interaction.response.send_message(**kwargs, ephemeral=True)


def _error(exc: Exception) -> str:
    if isinstance(exc, StudioValidationError):
        return str(exc)
    return "The draft could not be updated. Nothing was published."


class DestinationSelect(discord.ui.ChannelSelect):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(
            placeholder="1. Choose where to publish…",
            min_values=1, max_values=1, row=4,
            channel_types=[discord.ChannelType.text],
        )
        self.session = session

    async def callback(self, interaction: discord.Interaction) -> None:
        chosen = self.values[0] if self.values else None
        guild = interaction.guild
        channel = guild.get_channel(int(getattr(chosen, "id", 0) or 0)) if guild else None
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != self.session.guild_id:
            return await _say(interaction, "Choose a normal text channel in this server.")
        self.session.channel_id = int(channel.id)
        self.session.dirty = True
        if self.session.post_key:
            self.session.post_key = ""  # Never edit a post after switching destination.
        await _home(interaction, self.session, notice="Destination selected.")


class StudioHomeView(_StudioView):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session)
        self.add_item(DestinationSelect(session))

    @discord.ui.button(label="Write", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def write(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(StudioTextModal(self.session))

    @discord.ui.button(label="Appearance", emoji="🎨", style=discord.ButtonStyle.secondary, row=0)
    async def appearance(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(StudioAppearanceModal(self.session))

    @discord.ui.button(label="Images", emoji="🖼️", style=discord.ButtonStyle.secondary, row=0)
    async def images(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(StudioImagesModal(self.session))

    @discord.ui.button(label="Fields", emoji="📋", style=discord.ButtonStyle.secondary, row=0)
    async def fields(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _fields(interaction, self.session)

    @discord.ui.button(label="Link Buttons", emoji="🔗", style=discord.ButtonStyle.secondary, row=1)
    async def links(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _links(interaction, self.session)

    @discord.ui.button(label="Embeds", emoji="🧩", style=discord.ButtonStyle.secondary, row=1)
    async def embeds(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _embeds(interaction, self.session)

    @discord.ui.button(label="Role Controls", emoji="🎭", style=discord.ButtonStyle.secondary, row=1)
    async def role_controls(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _role_controls(interaction, self.session)

    @discord.ui.button(label="Undo", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def undo(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not self.session.undo():
            return await _say(interaction, "No changes to undo.")
        await _home(interaction, self.session, notice="Last edit undone.")

    @discord.ui.button(label="Templates", emoji="💾", style=discord.ButtonStyle.secondary, row=2)
    async def templates(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _templates(interaction, self.session)

    @discord.ui.button(label="Edit Posted", emoji="🛠️", style=discord.ButtonStyle.secondary, row=2)
    async def edit_posted(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _published(interaction, self.session)

    @discord.ui.button(label="Starter Layouts", emoji="✨", style=discord.ButtonStyle.secondary, row=2)
    async def starter_layouts(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _starters(interaction, self.session)

    @discord.ui.button(label="Reload Saved", emoji="📂", style=discord.ButtonStyle.secondary, row=3)
    async def reload_saved(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _reload_saved(interaction, self.session)

    @discord.ui.button(label="JSON Files", emoji="📦", style=discord.ButtonStyle.secondary, row=3)
    async def portable(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _portable_menu(interaction, self.session)

    @discord.ui.button(label="Preview & Publish", emoji="👁️", style=discord.ButtonStyle.success, row=3)
    async def preview(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _review(interaction, self.session)



class StudioJsonUploadModal(discord.ui.Modal, title="Import Embed Studio JSON"):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(timeout=300)
        self.session = session
        self.upload = discord.ui.FileUpload(min_values=1, max_values=1, required=True)
        self.add_item(discord.ui.Label(
            text="Select a Studio .json file",
            description="Up to 64 KiB. Import changes your private draft only.",
            component=self.upload,
        ))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if (
            interaction.guild is None or int(interaction.guild.id) != self.session.guild_id
            or int(interaction.user.id) != self.session.owner_id
            or not _staff(interaction)
        ):
            return await _say(interaction, "Import requires your own server's staff editor.")
        if len(self.upload.values) != 1:
            return await _say(interaction, "Choose exactly one JSON file.")
        attachment = self.upload.values[0]
        if (
            not str(attachment.filename).lower().endswith(".json")
            or not 0 < int(attachment.size) <= MAX_PORTABLE_BYTES
        ):
            return await _say(interaction, "Select a .json Studio export up to 64 KiB.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = await asyncio.wait_for(attachment.read(use_cached=True), timeout=10)
            model = import_portable_project(data, guild_id=self.session.guild_id)
            # Import is only an editor mutation. No published edit target,
            # channel selection, role grant or automatic posting is imported.
            self.session.apply(lambda project: (project.clear(), project.update(model)))
        except StudioValidationError as exc:
            return await _say(interaction, f"Import rejected: {exc}")
        except (discord.HTTPException, asyncio.TimeoutError, OSError, ValueError):
            return await _say(interaction, "Could not safely read that JSON file. Your draft was kept.")
        except Exception:
            return await _say(interaction, "Import failed. Your current draft remains unchanged.")
        self.session.active = 0
        self.session.post_key = ""
        await _home(interaction, self.session, notice="JSON imported as a private draft. Review before publishing.")


class StudioJsonMenu(_StudioView):
    @discord.ui.button(label="Export JSON File", emoji="📤", style=discord.ButtonStyle.primary, row=0)
    async def export(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        try:
            data = export_portable_project(self.session.project, guild_id=self.session.guild_id)
        except StudioValidationError as exc:
            return await _say(interaction, str(exc))
        # Separate ephemeral attachment: the editor and existing draft stay intact.
        await _say(
            interaction,
            "Your portable Embed Studio JSON is ready. Import it later from JSON Files.",
            file=discord.File(BytesIO(data), filename="dank-embed-studio.json"),
        )

    @discord.ui.button(label="Import JSON File", emoji="📥", style=discord.ButtonStyle.secondary, row=0)
    async def import_file(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(StudioJsonUploadModal(self.session))

    @discord.ui.button(label="Back to Studio", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _portable_menu(interaction: discord.Interaction, session: StudioSession) -> None:
    await _menu(
        interaction,
        heading="📦 Portable Embed Studio JSON",
        description=(
            "Export your full embed design to a private JSON attachment, or import a "
            "Studio JSON file through Discord's upload dialog. Imports replace only "
            "your current private draft, never a published post. Destination and "
            "published-message edit targets are never copied. Self-role controls "
            "can only move within their original server."
        ),
        view=StudioJsonMenu(session),
    )


class StarterSelect(discord.ui.Select):
    def __init__(self, session: StudioSession) -> None:
        from .embed_studio_presets import PRESETS
        self.session = session
        super().__init__(
            placeholder="Pick a ready-to-edit layout…",
            options=[discord.SelectOption(label=name, value=key) for key, name in PRESETS],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from .embed_studio_presets import project_for_preset
        self.session.history.append(self.session.project)
        self.session.history = self.session.history[-10:]
        self.session.project = project_for_preset(self.values[0])
        self.session.dirty = True
        self.session.active = 0
        self.session.post_key = ""
        await _home(interaction, self.session, notice="Starter layout loaded. Make it yours before publishing.")


class StarterMenu(_StudioView):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session)
        self.add_item(StarterSelect(session))

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _starters(interaction: discord.Interaction, session: StudioSession) -> None:
    await _menu(
        interaction,
        heading="✨ Start with a Layout",
        description=(
            "Choose an announcement, rules, event, server info, FAQ, "
            "or feature showcase. Everything is editable, and nothing posts automatically."
        ),
        view=StarterMenu(session),
    )

class _ProjectModal(discord.ui.Modal):
    def __init__(self, session: StudioSession, title: str) -> None:
        super().__init__(title=title, timeout=900)
        self.session = session

    async def commit(self, interaction: discord.Interaction, mutation: Callable[[dict[str, Any]], None]) -> None:
        if not _staff(interaction) or int(interaction.user.id) != self.session.owner_id:
            return await _say(interaction, "This editor belongs to another staff member.")
        try:
            self.session.apply(mutation)
        except (StudioValidationError, ValueError) as exc:
            return await _say(interaction, f"❌ {_error(exc)}")
        await _home(interaction, self.session, notice="Draft updated.")


class StudioTextModal(_ProjectModal):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session, "Write Embed Text")
        e = session.project["embeds"][session.active]
        self.title_input = discord.ui.TextInput(label="Headline", required=False, max_length=256, default=e["title"])
        self.body_input = discord.ui.TextInput(label="Description", required=False, style=discord.TextStyle.paragraph, max_length=4096, default=e["description"])
        self.content_input = discord.ui.TextInput(label="Text above embeds (optional)", required=False, style=discord.TextStyle.paragraph, max_length=1900, default=session.project["content"][:1900])
        for control in (self.title_input, self.body_input, self.content_input):
            self.add_item(control)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        idx = self.session.active
        await self.commit(interaction, lambda p: p["embeds"][idx].update(
            title=str(self.title_input.value or ""),
            description=str(self.body_input.value or ""),
        ) or p.update(content=str(self.content_input.value or "")))


class StudioAppearanceModal(_ProjectModal):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session, "Appearance & Author")
        e = session.project["embeds"][session.active]
        controls = [
            ("color", "Color: six-digit HEX", 7, False),
            ("author_name", "Author / small headline", 256, False),
            ("author_url", "Author HTTPS link (optional)", 2048, False),
            ("footer", "Footer text", 2048, False),
            ("title_url", "Clicking headline opens HTTPS URL", 2048, False),
        ]
        self.inputs = {}
        for key, name, length, required in controls:
            field = discord.ui.TextInput(
                label=name, required=required, max_length=length,
                default=e[key], style=discord.TextStyle.paragraph if key == "footer" else discord.TextStyle.short,
            )
            self.inputs[key] = field
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values = {key: str(item.value or "") for key, item in self.inputs.items()}
        await self.commit(interaction, lambda p: p["embeds"][self.session.active].update(values))


class StudioImagesModal(_ProjectModal):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session, "Images & Icons")
        e = session.project["embeds"][session.active]
        specs = (
            ("image", "Large image HTTPS URL"),
            ("thumbnail", "Small thumbnail HTTPS URL"),
            ("author_icon", "Author icon HTTPS URL"),
            ("footer_icon", "Footer icon HTTPS URL"),
        )
        self.inputs = {}
        for key, label in specs:
            item = discord.ui.TextInput(label=label, required=False, max_length=2048, default=e[key])
            self.inputs[key] = item
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values = {key: str(item.value or "") for key, item in self.inputs.items()}
        await self.commit(interaction, lambda p: p["embeds"][self.session.active].update(values))


async def _menu(interaction: discord.Interaction, *, heading: str, description: str, view: discord.ui.View) -> None:
    session = getattr(view, "session", None)
    if isinstance(session, StudioSession):
        warning = await _autosave(interaction, session)
        if warning:
            description = "⚠️ " + warning + "\n\n" + description
    embed = discord.Embed(title=heading, description=description[:3900], color=discord.Color.blurple())
    kwargs = {"content": None, "embed": embed, "view": view, "allowed_mentions": _MENTIONS}
    if interaction.response.is_done():
        await interaction.edit_original_response(**kwargs)
    elif interaction.message is not None:
        await interaction.response.edit_message(**kwargs)
    else:
        await interaction.response.send_message(**kwargs, ephemeral=True)


class FieldModal(_ProjectModal):
    def __init__(self, session: StudioSession, index: int | None = None) -> None:
        super().__init__(session, "Add/Edit Embed Field")
        self.index = index
        fields = session.project["embeds"][session.active]["fields"]
        f = fields[index] if index is not None and index < len(fields) else {}
        self.name_input = discord.ui.TextInput(label="Field heading", max_length=256, default=f.get("name"))
        self.value_input = discord.ui.TextInput(label="Field text", style=discord.TextStyle.paragraph, max_length=1024, default=f.get("value"))
        self.inline_input = discord.ui.TextInput(label="Small column? yes/no", required=False, max_length=3, default="yes" if f.get("inline") else "no")
        for item in (self.name_input, self.value_input, self.inline_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        inline = str(self.inline_input.value or "no").strip().lower()
        if inline not in {"yes", "no", "y", "n"}:
            return await _say(interaction, "Use yes or no for the small-column setting.")
        f = {"name": str(self.name_input.value or ""), "value": str(self.value_input.value or ""), "inline": inline in {"yes", "y"}}
        idx = self.session.active
        def change(p: dict[str, Any]) -> None:
            fields = p["embeds"][idx]["fields"]
            if self.index is None:
                if len(fields) >= MAX_FIELDS:
                    raise StudioValidationError("This embed already has 25 fields.")
                fields.append(f)
            elif self.index < len(fields):
                fields[self.index] = f
            else:
                raise StudioValidationError("That field no longer exists.")
        await self.commit(interaction, change)


class FieldSelect(discord.ui.Select):
    def __init__(self, session: StudioSession) -> None:
        fields = session.project["embeds"][session.active]["fields"]
        choices = [
            discord.SelectOption(label=f"{i + 1}. {f['name'][:85]}", value=str(i))
            for i, f in enumerate(fields[:25])
        ]
        if not choices:
            choices = [discord.SelectOption(label="No fields added yet", value="none")]
        super().__init__(placeholder="Select a field to edit, move or remove", options=choices, row=0)
        self.session = session

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "none":
            return await _say(interaction, "Press Add Field first.")
        await _menu(interaction, heading="Field controls", description="Edit, move up/down, or remove this field.", view=FieldActions(self.session, int(self.values[0])))


class FieldListView(_StudioView):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session)
        self.add_item(FieldSelect(session))

    @discord.ui.button(label="Add Field", emoji="➕", style=discord.ButtonStyle.primary, row=1)
    async def add(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(FieldModal(self.session))

    @discord.ui.button(label="Back to Studio", emoji="◀️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


class FieldActions(_StudioView):
    def __init__(self, session: StudioSession, index: int) -> None:
        super().__init__(session)
        self.index = index

    @discord.ui.button(label="Edit", emoji="✏️", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(FieldModal(self.session, self.index))

    @discord.ui.button(label="Move Up", emoji="⬆️", style=discord.ButtonStyle.secondary)
    async def up(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.move(interaction, -1)

    @discord.ui.button(label="Move Down", emoji="⬇️", style=discord.ButtonStyle.secondary)
    async def down(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.move(interaction, 1)

    async def move(self, interaction: discord.Interaction, offset: int) -> None:
        idx, next_idx = self.index, self.index + offset
        def mutation(p: dict[str, Any]) -> None:
            fields = p["embeds"][self.session.active]["fields"]
            if not (0 <= idx < len(fields) and 0 <= next_idx < len(fields)):
                raise StudioValidationError("Field is already at the edge.")
            fields[idx], fields[next_idx] = fields[next_idx], fields[idx]
        try:
            self.session.apply(mutation)
        except StudioValidationError as exc:
            return await _say(interaction, str(exc))
        self.index = next_idx
        await _fields(interaction, self.session)

    @discord.ui.button(label="Remove", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def remove(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        idx = self.index
        self.session.apply(lambda p: p["embeds"][self.session.active]["fields"].pop(idx))
        await _fields(interaction, self.session)

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _fields(interaction, self.session)


async def _fields(interaction: discord.Interaction, session: StudioSession) -> None:
    items = session.project["embeds"][session.active]["fields"]
    description = (
        f"**{len(items)}/25 fields**. Fields can appear side by side or stacked.\n"
        "Use Add Field or choose one below to edit, reorder or remove it."
    )
    await _menu(interaction, heading="📋 Embed Fields", description=description, view=FieldListView(session))


class LinkModal(_ProjectModal):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session, "Add URL Button")
        self.label_input = discord.ui.TextInput(label="Button text", max_length=80, placeholder="Read the rules")
        self.url_input = discord.ui.TextInput(label="HTTPS destination", max_length=2048, placeholder="https://example.com")
        for item in (self.label_input, self.url_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        link = {"label": str(self.label_input.value or ""), "url": str(self.url_input.value or "")}
        def change(p: dict[str, Any]) -> None:
            if len(p["links"]) >= 5:
                raise StudioValidationError("Remove an existing button before adding another.")
            p["links"].append(link)
        await self.commit(interaction, change)


class LinkSelect(discord.ui.Select):
    def __init__(self, session: StudioSession) -> None:
        options = [
            discord.SelectOption(label=f"{i + 1}. {link['label'][:80]}", value=str(i))
            for i, link in enumerate(session.project["links"])
        ]
        if not options:
            options = [discord.SelectOption(label="No link buttons yet", value="none")]
        super().__init__(placeholder="Choose a URL button to remove", options=options, row=0)
        self.session = session

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "none":
            return await _say(interaction, "Press Add URL Button first.")
        idx = int(self.values[0])
        self.session.apply(lambda p: p["links"].pop(idx))
        await _links(interaction, self.session)


class LinkMenu(_StudioView):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session)
        self.add_item(LinkSelect(session))

    @discord.ui.button(label="Add URL Button", emoji="➕", style=discord.ButtonStyle.primary, row=1)
    async def add(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(LinkModal(self.session))

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _links(interaction: discord.Interaction, session: StudioSession) -> None:
    await _menu(
        interaction,
        heading="🔗 Link Buttons",
        description="Add up to five safe clickable HTTPS buttons. They keep working after a restart. Select any existing button to remove it.",
        view=LinkMenu(session),
    )




async def _add_studio_role(interaction: discord.Interaction, session: StudioSession, role: discord.Role) -> None:
    guild = interaction.guild
    if guild is None or guild.id != session.guild_id or not _staff(interaction) or interaction.user.id != session.owner_id:
        return await _say(interaction, "This role editor belongs to another server or staff member.")
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        from .guild_config import get_guild_config
        from .commands_ext.public_self_roles_group import _profile_cosmetic_role_blocker
        config = await get_guild_config(int(guild.id), refresh=True)
        if str(config.get("source") or "").startswith("unavailable:"):
            return await _say(interaction, "Cannot verify role safety right now. Nothing added.")
        reason = _profile_cosmetic_role_blocker(guild, role, config)
        if reason:
            return await _say(interaction, f"That role is not eligible for optional self-selection: {reason}")
        if any(a["role_id"] == str(role.id) for a in session.project["role_actions"]):
            return await _say(interaction, "That role is already offered in this embed.")
        def add(p: dict[str, Any]) -> None:
            p["role_actions"].append({"label": role.name[:80], "role_id": str(role.id)})
            if len(p["role_actions"]) > 5 or len(p["role_actions"]) + len(p["links"]) > 5:
                p["role_mode"] = "dropdown"
        session.apply(add)
    except StudioValidationError as exc:
        return await _say(interaction, str(exc))
    except Exception as exc:
        return await _say(interaction, f"Role safety could not be verified ({type(exc).__name__}). Nothing added.")
    await _role_controls(interaction, session)


class StudioRoleSelect(discord.ui.RoleSelect):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(
            placeholder="Quick pick an optional server role…",
            min_values=1, max_values=1, row=0,
        )
        self.session = session

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        picked = self.values[0] if self.values else None
        role = guild.get_role(int(getattr(picked, "id", 0))) if guild else None
        if not isinstance(role, discord.Role):
            return await _say(interaction, "Choose a role belonging to this server.")
        await _add_studio_role(interaction, self.session, role)


async def _browse_studio_roles(interaction: discord.Interaction, session: StudioSession) -> None:
    guild = interaction.guild
    if guild is None:
        return await _say(interaction, "Open the Studio inside your server.")
    await interaction.response.defer(ephemeral=True)
    try:
        from .guild_config import get_guild_config
        from .commands_ext.public_self_roles_group import _profile_cosmetic_role_blocker
        from .ui.resource_browser import DankGuildResourceBrowserView
        config = await get_guild_config(int(guild.id), refresh=True)
        if str(config.get("source") or "").startswith("unavailable:"):
            return await _say(interaction, "Role safety cannot be checked at the moment.")
    except Exception:
        return await _say(interaction, "Role browsing could not load. Nothing changed.")

    def allowed(role: Any) -> bool:
        return (
            isinstance(role, discord.Role)
            and str(role.id) not in {a["role_id"] for a in session.project["role_actions"]}
            and not _profile_cosmetic_role_blocker(guild, role, config)
        )

    async def picked(pick_interaction: discord.Interaction, resource: Any) -> None:
        if not isinstance(resource, discord.Role):
            return await _say(pick_interaction, "Choose a role.")
        await _add_studio_role(pick_interaction, session, resource)

    async def back(back_interaction: discord.Interaction) -> None:
        await _role_controls(back_interaction, session)

    view = DankGuildResourceBrowserView(
        guild=guild, author_id=session.owner_id, resource_kinds=("role",),
        on_pick=picked, custom_id="dank:embed_studio:search_roles:v1",
        title="Search-Safe Studio Role Picker",
        placeholder="Search or browse safe optional roles…",
        predicate=allowed, on_home=back,
        home_label="Back to Role Controls",
        empty_message="No more safe roles are available to add.",
    )
    await _menu(interaction, heading="🔎 Search-Safe Role Browser",
                description="Find a safe role by normal words, styled name, previous name, mention or ID.", view=view)

class StudioExistingRoleSelect(discord.ui.Select):
    def __init__(self, session: StudioSession) -> None:
        self.session = session
        actions = session.project["role_actions"]
        options = [
            discord.SelectOption(label=f"{i+1}. {a['label'][:80]}", value=str(i))
            for i, a in enumerate(actions)
        ] or [discord.SelectOption(label="No roles yet", value="none")]
        super().__init__(placeholder="Manage an existing role button…", options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "none":
            return await _say(interaction, "Add a role first.")
        await _menu(
            interaction,
            heading="🎭 Role Action Settings",
            description="Change the displayed label, move the button, or remove it. Role IDs cannot be edited manually.",
            view=StudioRoleActionView(self.session, int(self.values[0])),
        )


class StudioRoleLabelModal(discord.ui.Modal, title="Rename Role Action"):
    def __init__(self, session: StudioSession, index: int) -> None:
        super().__init__(timeout=900)
        self.session = session
        self.index = index
        action = session.project["role_actions"][index]
        self.label_input = discord.ui.TextInput(label="Button or menu label", default=action["label"], max_length=80)
        self.add_item(self.label_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _staff(interaction) or interaction.user.id != self.session.owner_id:
            return await _say(interaction, "This editor belongs to another staff member.")
        try:
            idx = self.index
            text = str(self.label_input.value or "")
            self.session.apply(lambda p: p["role_actions"][idx].update(label=text))
        except (StudioValidationError, IndexError) as exc:
            return await _say(interaction, str(exc))
        await _home(interaction, self.session, notice="Role label updated.")


class StudioRoleActionView(_StudioView):
    def __init__(self, session: StudioSession, index: int) -> None:
        super().__init__(session)
        self.index = index

    @discord.ui.button(label="Rename", emoji="✏️", style=discord.ButtonStyle.primary)
    async def rename(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(StudioRoleLabelModal(self.session, self.index))

    @discord.ui.button(label="Move Up", emoji="⬆️", style=discord.ButtonStyle.secondary)
    async def up(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.move(interaction, -1)

    @discord.ui.button(label="Move Down", emoji="⬇️", style=discord.ButtonStyle.secondary)
    async def down(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.move(interaction, 1)

    async def move(self, interaction: discord.Interaction, delta: int) -> None:
        idx, target = self.index, self.index + delta
        def change(p: dict[str, Any]) -> None:
            items = p["role_actions"]
            if not 0 <= idx < len(items) or not 0 <= target < len(items):
                raise StudioValidationError("Role is already at the edge.")
            items[idx], items[target] = items[target], items[idx]
        try:
            self.session.apply(change)
        except StudioValidationError as exc:
            return await _say(interaction, str(exc))
        self.index = target
        await _role_controls(interaction, self.session)

    @discord.ui.button(label="Remove", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def remove(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if self.index >= len(self.session.project["role_actions"]):
            return await _say(interaction, "That role is already gone.")
        self.session.apply(lambda p: p["role_actions"].pop(self.index))
        await _role_controls(interaction, self.session)

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _role_controls(interaction, self.session)


class StudioRolesMenu(_StudioView):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session)
        self.add_item(StudioRoleSelect(session))
        self.add_item(StudioExistingRoleSelect(session))

    @discord.ui.button(label="Buttons / Dropdown", emoji="🔁", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_mode(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        target = "dropdown" if self.session.project["role_mode"] == "buttons" else "buttons"
        try:
            self.session.apply(lambda p: p.update(role_mode=target))
        except StudioValidationError as exc:
            return await _say(interaction, str(exc))
        await _role_controls(interaction, self.session)

    @discord.ui.button(label="Find Role", emoji="🔎", style=discord.ButtonStyle.primary, row=2)
    async def search(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _browse_studio_roles(interaction, self.session)

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _role_controls(interaction: discord.Interaction, session: StudioSession) -> None:
    mode = session.project["role_mode"]
    items = session.project["role_actions"]
    names = "\n".join(f"• {i+1}. {item['label']}" for i, item in enumerate(items))
    await _menu(
        interaction, heading="🎭 Role Buttons & Dropdown",
        description=(
            f"**{len(items)}/10** optional self-assignable roles • layout: **{mode}**\n"
            "Choose a role below; Dank Shield checks permissions and safe-role rules. "
            "Members can add or remove their own offered roles, never staff or access roles.\n"
            "Buttons: up to five combined with link buttons. "
            "Dropdown: up to ten options alongside link buttons.\n\n"
            + (names or "No roles added.")
        ),
        view=StudioRolesMenu(session),
    )

class EmbedMenu(_StudioView):
    @discord.ui.button(label="Toggle Timestamp", emoji="🕒", style=discord.ButtonStyle.secondary, row=0)
    async def timestamp(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        idx = self.session.active
        self.session.apply(lambda p: p["embeds"][idx].update(timestamp=not p["embeds"][idx]["timestamp"]))
        await _home(interaction, self.session, notice="Timestamp setting changed.")

    @discord.ui.button(label="Previous", emoji="◀️", style=discord.ButtonStyle.secondary, row=0)
    async def previous(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.session.active = (self.session.active - 1) % len(self.session.project["embeds"])
        await _home(interaction, self.session)

    @discord.ui.button(label="Next", emoji="▶️", style=discord.ButtonStyle.secondary, row=0)
    async def next(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.session.active = (self.session.active + 1) % len(self.session.project["embeds"])
        await _home(interaction, self.session)

    @discord.ui.button(label="Add Embed", emoji="➕", style=discord.ButtonStyle.primary, row=1)
    async def add(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        try:
            self.session.apply(lambda p: p["embeds"].append(default_embed()))
        except StudioValidationError as exc:
            return await _say(interaction, str(exc))
        self.session.active = len(self.session.project["embeds"]) - 1
        await _home(interaction, self.session)

    @discord.ui.button(label="Duplicate", emoji="📋", style=discord.ButtonStyle.secondary, row=1)
    async def duplicate(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        try:
            self.session.apply(lambda p: p["embeds"].insert(self.session.active + 1, deepcopy(p["embeds"][self.session.active])))
        except StudioValidationError as exc:
            return await _say(interaction, str(exc))
        self.session.active += 1
        await _home(interaction, self.session)

    @discord.ui.button(label="Remove Current", emoji="🗑️", style=discord.ButtonStyle.danger, row=2)
    async def remove(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if len(self.session.project["embeds"]) < 2:
            return await _say(interaction, "A message needs at least one embed.")
        self.session.apply(lambda p: p["embeds"].pop(self.session.active))
        await _home(interaction, self.session)

    @discord.ui.button(label="Back to Studio", emoji="🏠", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _embeds(interaction: discord.Interaction, session: StudioSession) -> None:
    await _menu(
        interaction,
        heading="🧩 Multi-Embed Layout",
        description=f"Editing embed {session.active + 1} of {len(session.project['embeds'])}. Add, duplicate, remove or switch. Discord permits up to {MAX_EMBEDS} embeds with 6000 characters combined.",
        view=EmbedMenu(session),
    )


async def _load_library(guild_id: int) -> tuple[Any, dict[str, Any]]:
    from .guild_config import get_guild_config
    source = await get_guild_config(guild_id, refresh=True)
    if str(source.get("source") or "").startswith("unavailable:"):
        raise StudioValidationError("Template storage is offline. Draft and preview still work.")
    return source, parse_library(source)


async def _save_library(guild_id: int, source: Any, lib: dict[str, Any]) -> bool:
    from .guild_config import compare_and_swap_guild_config_key
    value = library_payload(lib)
    ok, _saved = await compare_and_swap_guild_config_key(
        guild_id, STUDIO_KEY, expected=source.get(STUDIO_KEY),
        value=value, source="dank_embed_studio",
    )
    return bool(ok)



async def _autosave(interaction: discord.Interaction, session: StudioSession) -> str:
    """Persist validated per-staff drafts through the existing guild CAS."""
    if not session.dirty:
        return ""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    async with session.save_lock:
        if not session.dirty:
            return ""
        snapshot = deepcopy(session.project)
        channel_id, active, post_key = session.channel_id, session.active, session.post_key
        prior_version = session.saved_version
        try:
            # Retry only whole-library CAS conflicts caused by unrelated
            # templates, post-history or other staff drafts. Never overwrite
            # this editor owner's newer draft revision.
            for _attempt in range(3):
                source, lib = await _load_library(session.guild_id)
                next_version = draft_snapshot(
                    lib, owner_id=session.owner_id, expected_version=prior_version,
                    project=snapshot, channel_id=channel_id, active=active,
                    post_key=post_key, now=int(time.time()),
                )
                if await _save_library(session.guild_id, source, lib):
                    session.saved_version = next_version
                    if (session.project == snapshot and session.channel_id == channel_id
                        and session.active == active and session.post_key == post_key):
                        session.dirty = False
                    return ""
            return "Autosave collided with another server edit. Changes remain in this open editor."
        except StudioValidationError as exc:
            return str(exc)
        except Exception:
            return "Autosave unavailable. Changes remain only in this open editor."


def _restore_snapshot(session: StudioSession, lib: dict[str, Any]) -> bool:
    saved = lib.get("drafts", {}).get(str(session.owner_id))
    if not isinstance(saved, dict):
        return False
    project = validate_project(saved.get("project") or {})
    version = int(saved.get("version") or 0)
    if version <= 0:
        raise StudioValidationError("Saved draft revision is invalid.")
    channel_id = max(0, int(saved.get("channel_id") or 0))
    active = max(0, min(int(saved.get("active") or 0), len(project["embeds"]) - 1))
    key = str(saved.get("post_key") or "")
    session.project = project
    session.saved_version = version
    session.channel_id = channel_id
    session.active = active
    # A deleted or evicted published post must not become an edit target.
    session.post_key = key if key in lib["published"] else ""
    session.history.clear()
    session.dirty = False
    return True


async def _reload_saved(interaction: discord.Interaction, session: StudioSession) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        _, lib = await _load_library(session.guild_id)
        found = _restore_snapshot(session, lib)
    except (StudioValidationError, ValueError, TypeError):
        return await _say(interaction, "Saved draft could not be restored. Your current editor was not published.")
    except Exception:
        return await _say(interaction, "Saved draft storage is unavailable. No changes were published.")
    await _home(interaction, session, notice="Saved draft restored." if found else "No saved draft found for your account.")


class TemplateNameModal(discord.ui.Modal, title="Save Embed Template"):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(timeout=900)
        self.session = session
        self.name_input = discord.ui.TextInput(label="Template name", placeholder="Rules • Weekly announcement", max_length=60)
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _staff(interaction) or interaction.user.id != self.session.owner_id:
            return await _say(interaction, "You cannot save another editor's template.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        name = str(self.name_input.value or "").strip()
        if not name:
            return await _say(interaction, "A template needs a name.")
        try:
            source, lib = await _load_library(self.session.guild_id)
            if len(lib["templates"]) >= MAX_TEMPLATES:
                return await _say(interaction, "12 templates are saved. Remove an unused one first.")
            key = secrets.token_hex(6)
            lib["templates"][key] = {"name": name, "project": validate_project(self.session.project)}
            applied = await _save_library(self.session.guild_id, source, lib)
        except Exception as exc:
            return await _say(interaction, f"Template not saved: {_error(exc)}")
        if not applied:
            return await _say(interaction, "Templates changed in another session. Open Templates again.")
        await _say(interaction, f"✅ Template **{discord.utils.escape_markdown(name)}** saved for this server.")


class TemplateSelect(discord.ui.Select):
    def __init__(self, session: StudioSession, lib: dict[str, Any]) -> None:
        self.session = session
        choices = [
            discord.SelectOption(label=str(item.get("name") or "Untitled")[:90], value=key)
            for key, item in list(lib["templates"].items())[:25]
        ]
        if not choices:
            choices = [discord.SelectOption(label="No saved templates", value="none")]
        super().__init__(placeholder="Choose a template to load", options=choices, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        key = self.values[0]
        if key == "none":
            return await _say(interaction, "Save your first template from this screen.")
        await interaction.response.defer(ephemeral=True)
        try:
            _source, lib = await _load_library(self.session.guild_id)
            item = lib["templates"].get(key)
            if not item:
                return await _say(interaction, "That template was removed. Refresh this menu.")
            draft = validate_project(item["project"])
        except Exception as exc:
            return await _say(interaction, f"Could not load template: {_error(exc)}")
        self.session.history.append(self.session.project)
        self.session.history = self.session.history[-10:]
        self.session.project = draft
        self.session.dirty = True
        self.session.active = 0
        self.session.post_key = ""
        await _home(interaction, self.session, notice="Template loaded. Nothing posted.")


class TemplateMenu(_StudioView):
    def __init__(self, session: StudioSession, lib: dict[str, Any]) -> None:
        super().__init__(session)
        self.add_item(TemplateSelect(session, lib))

    @discord.ui.button(label="Save Current Draft", emoji="💾", style=discord.ButtonStyle.success, row=1)
    async def save(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(TemplateNameModal(self.session))

    @discord.ui.button(label="Manage Saved Templates", emoji="🗑️", style=discord.ButtonStyle.secondary, row=1)
    async def manage(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _template_deletion(interaction, self.session)

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _templates(interaction: discord.Interaction, session: StudioSession) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        _, lib = await _load_library(session.guild_id)
    except Exception as exc:
        return await _say(interaction, _error(exc))
    await _menu(
        interaction, heading="💾 Reusable Server Templates",
        description=f"{len(lib['templates'])}/{MAX_TEMPLATES} templates. Save this draft, load a previous one or remove an unused template. Your work isn't published by saving.",
        view=TemplateMenu(session, lib),
    )


class DeleteTemplateSelect(discord.ui.Select):
    def __init__(self, session: StudioSession, lib: dict[str, Any]) -> None:
        self.session = session
        choices = [
            discord.SelectOption(label=str(item.get("name") or "Untitled")[:90], value=key)
            for key, item in lib["templates"].items()
        ] or [discord.SelectOption(label="No saved templates", value="none")]
        super().__init__(placeholder="Choose template to REMOVE", options=choices, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        key = self.values[0]
        if key == "none":
            return await _say(interaction, "There is no template to remove.")
        await _menu(interaction, heading="Delete template?", description="This deletes a saved template, not any published message. Confirm to continue.", view=ConfirmDeleteTemplateView(self.session, key))


class DeleteTemplateMenu(_StudioView):
    def __init__(self, session: StudioSession, lib: dict[str, Any]) -> None:
        super().__init__(session)
        self.add_item(DeleteTemplateSelect(session, lib))

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _templates(interaction, self.session)


class ConfirmDeleteTemplateView(_StudioView):
    def __init__(self, session: StudioSession, key: str) -> None:
        super().__init__(session)
        self.key = key
        self.used = False

    @discord.ui.button(label="Delete Saved Template", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if self.used:
            return await _say(interaction, "This request was already submitted.")
        self.used = True
        await interaction.response.defer(ephemeral=True)
        try:
            source, lib = await _load_library(self.session.guild_id)
            if self.key not in lib["templates"]:
                return await _say(interaction, "Template no longer exists.")
            lib["templates"].pop(self.key)
            ok = await _save_library(self.session.guild_id, source, lib)
        except Exception as exc:
            return await _say(interaction, _error(exc))
        if not ok:
            return await _say(interaction, "A different admin updated the library. Please refresh.")
        await _home(interaction, self.session, notice="Saved template removed.")

    @discord.ui.button(label="Cancel", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _templates(interaction, self.session)


async def _template_deletion(interaction: discord.Interaction, session: StudioSession) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        _, lib = await _load_library(session.guild_id)
    except Exception as exc:
        return await _say(interaction, _error(exc))
    await _menu(interaction, heading="Manage Templates", description="Select a template to remove; a second confirmation is required.", view=DeleteTemplateMenu(session, lib))


def _target_channel(interaction: discord.Interaction, session: StudioSession) -> discord.TextChannel | None:
    guild = interaction.guild
    if guild is None or int(guild.id) != session.guild_id:
        return None
    channel = guild.get_channel(session.channel_id)
    return channel if isinstance(channel, discord.TextChannel) else None


def _post_blockers(interaction: discord.Interaction, channel: discord.TextChannel) -> list[str]:
    member = interaction.user
    bot = channel.guild.me
    if not _staff(interaction) or not isinstance(member, discord.Member):
        return ["Manage Server / Administrator"]
    if not isinstance(bot, discord.Member):
        return ["Bot membership unavailable"]
    perms = channel.permissions_for(bot)
    return [
        label for label, permitted in (
            ("View Channel", perms.view_channel),
            ("Send Messages", perms.send_messages),
            ("Embed Links", perms.embed_links),
            ("Read Message History", perms.read_message_history),
        ) if not permitted
    ]


async def _review(interaction: discord.Interaction, session: StudioSession) -> None:
    channel = _target_channel(interaction, session)
    if channel is None:
        return await _say(interaction, "Choose a destination channel in the editor first.")
    blockers = _post_blockers(interaction, channel)
    if blockers:
        return await _say(interaction, "Bot cannot publish in that channel: " + ", ".join(blockers))
    try:
        embeds = render_embeds(session.project)
    except StudioValidationError as exc:
        return await _say(interaction, str(exc))
    status = (
        f"👁️ PRIVATE PREVIEW for {channel.mention}. "
        + ("Update the tracked message" if session.post_key else "Publish a NEW message")
        + " below. The real embeds are shown here. Nothing has been sent."
    )
    if session.project["content"]:
        status += "\nPlain message: " + session.project["content"][:1300]
    kwargs = {"content": status[:1950], "embeds": embeds, "view": StudioReviewView(session), "allowed_mentions": _MENTIONS}
    if interaction.response.is_done():
        await interaction.edit_original_response(**kwargs)
    elif interaction.message is not None:
        await interaction.response.edit_message(**kwargs)
    else:
        await interaction.response.send_message(**kwargs, ephemeral=True)


class StudioReviewView(_StudioView):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session, timeout=300)
        self.sent = False
        # Bind confirmation to exactly what the staff member previewed.
        # Other open editor messages may still mutate the session while this
        # confirmation is visible; a stale preview must never be publishable.
        self.preview_project = deepcopy(session.project)
        self.preview_channel_id = session.channel_id
        self.preview_post_key = session.post_key
        # Link buttons really open their HTTPS target in preview. Role actions
        # are disabled here: a private preview must never grant a public role.
        for link in session.project["links"]:
            self.add_item(discord.ui.Button(label=link["label"], url=link["url"], style=discord.ButtonStyle.link, row=1))
        role_items = session.project.get("role_actions") or []
        if role_items and session.project.get("role_mode") == "dropdown":
            self.add_item(discord.ui.Select(
                placeholder="Role dropdown (active after publishing)",
                options=[discord.SelectOption(label=item["label"], value=str(i)) for i, item in enumerate(role_items)],
                min_values=1, max_values=1, disabled=True, row=2,
            ))
        else:
            for item in role_items:
                self.add_item(discord.ui.Button(label=item["label"], disabled=True, style=discord.ButtonStyle.secondary, row=1))

    @discord.ui.button(label="Publish / Save Edit", emoji="✅", style=discord.ButtonStyle.success, row=0)
    async def publish(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if self.sent:
            return await _say(interaction, "This confirmation has already been used.")
        self.sent = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        if (
            self.session.project != self.preview_project
            or self.session.channel_id != self.preview_channel_id
            or self.session.post_key != self.preview_post_key
        ):
            return await _say(interaction, "The draft changed after your preview. Open Preview again before publishing.")
        editing_existing = bool(self.session.post_key)
        channel = _target_channel(interaction, self.session)
        if channel is None:
            return await _say(interaction, "Destination was changed or removed; nothing posted.")
        blockers = _post_blockers(interaction, channel)
        if blockers:
            return await _say(interaction, "Publishing blocked: " + ", ".join(blockers))
        try:
            from .commands_ext.public_embed_group import _require_safe_embed_action
            if not await _require_safe_embed_action(interaction, action="send public embed"):
                return
            # Re-evaluate the same immutable preview snapshot after the
            # awaited permission/guild preflight, immediately before send.
            if (
                self.session.project != self.preview_project
                or self.session.channel_id != self.preview_channel_id
                or self.session.post_key != self.preview_post_key
            ):
                return await _say(interaction, "This preview is stale. Reopen it; nothing was sent.")
            draft = validate_project(self.preview_project)
            embeds = render_embeds(draft)
            # Role controls need durable server records. Storage is checked
            # BEFORE sending, and the published message is made inert if the
            # subsequent atomic tracking write fails.
            from .embed_studio_roles import member_role_view
            import secrets
            role_items = draft.get("role_actions") or []
            role_token = secrets.token_hex(16) if role_items else ""
            if role_items:
                _source, current_library = await _load_library(self.session.guild_id)
                if not self.session.post_key and len(current_library["published"]) >= 20 and all(
                    isinstance(item, dict) and item.get("role_actions")
                    for item in current_library["published"].values()
                ):
                    return await _say(
                        interaction,
                        "This server already has 20 active role panels tracked. "
                        "Open Edit Posted and remove role actions from an older panel first."
                    )
                from .guild_config import get_guild_config
                from .commands_ext.public_self_roles_group import _profile_cosmetic_role_blocker
                guild_cfg = await get_guild_config(self.session.guild_id, refresh=True)
                for item in role_items:
                    role = channel.guild.get_role(int(item["role_id"]))
                    if not isinstance(role, discord.Role):
                        return await _say(interaction, "An offered role was deleted. Remove it from the draft first.")
                    reason = _profile_cosmetic_role_blocker(channel.guild, role, guild_cfg)
                    if reason:
                        return await _say(interaction, f"An offered role is no longer safe: {reason}")
            action_view = member_role_view(draft, role_token) if role_items else link_view(draft)
            message: discord.Message
            if self.session.post_key:
                source, lib = await _load_library(self.session.guild_id)
                item = lib["published"].get(self.session.post_key)
                if not item or int(item.get("channel_id", 0)) != channel.id:
                    return await _say(interaction, "Tracked post changed or was removed; no edit made.")
                message = await channel.fetch_message(int(item["message_id"]))
                if message.author.id != interaction.client.user.id:
                    return await _say(interaction, "This message is not owned by Dank Shield. Edit rejected.")
                await message.edit(content=draft["content"] or None, embeds=embeds, view=action_view, allowed_mentions=_MENTIONS)
            else:
                message = await channel.send(content=draft["content"] or None, embeds=embeds, view=action_view, allowed_mentions=_MENTIONS)
        except (StudioValidationError, discord.HTTPException, discord.Forbidden) as exc:
            return await _say(interaction, f"❌ Publish failed: {type(exc).__name__}. No further changes attempted.")
        except Exception as exc:
            return await _say(interaction, f"❌ Could not complete publish: {type(exc).__name__}. Verify the destination.")
        self.session.post_key = f"{channel.id}:{message.id}"
        self.session.dirty = True
        tracked = False
        try:
            source, lib = await _load_library(self.session.guild_id)
            lib["published"].pop(self.session.post_key, None)
            lib["published"][self.session.post_key] = {
                "channel_id": str(channel.id), "message_id": str(message.id),
                "by": str(interaction.user.id),
                "role_token": role_token, "role_mode": draft["role_mode"],
                "role_actions": draft["role_actions"],
            }
            from .embed_studio_core import trim_published_history
            try:
                lib["published"] = trim_published_history(lib["published"], max_posts=20)
            except StudioValidationError:
                tracked = False
            else:
                # If the bounded history retained all live role panels, a
                # link-only message may be published but not tracked. Do not
                # report a successful history save for an evicted reference.
                tracked = (
                    self.session.post_key in lib["published"]
                    and await _save_library(self.session.guild_id, source, lib)
                )
        except Exception:
            pass
        if role_items and not tracked:
            # Missing registry means live controls cannot be authorized. Avoid
            # showing unusable buttons, and do not pretend they are persistent.
            try:
                await message.edit(view=link_view(draft), allowed_mentions=_MENTIONS)
            except (discord.Forbidden, discord.HTTPException):
                pass  # Runtime still rejects all untracked or stale buttons.
        autosave_warning = await _autosave(interaction, self.session)
        await _say(
            interaction,
            f"✅ {'Updated' if editing_existing else 'Published'}: {message.jump_url}"
            + ("" if tracked else "\n⚠️ Message sent, but saved-post tracking is unavailable. Keep the message link.")
            + ("\n⚠️ " + autosave_warning if autosave_warning else ""),
        )

    @discord.ui.button(label="Back & Edit", emoji="✏️", style=discord.ButtonStyle.secondary, row=0)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


def _from_message(message: discord.Message) -> dict[str, Any]:
    project = {"version": 1, "content": str(message.content or ""), "embeds": [], "links": []}
    for embed in list(message.embeds)[:10]:
        d = embed.to_dict()
        author = d.get("author") or {}
        footer = d.get("footer") or {}
        project["embeds"].append({
            "title": str(d.get("title") or ""),
            "description": str(d.get("description") or ""),
            "title_url": str(d.get("url") or ""),
            "color": f"{int(d.get('color') or 0x5865F2):06X}",
            "author_name": str(author.get("name") or ""),
            "author_url": str(author.get("url") or ""),
            "author_icon": str(author.get("icon_url") or ""),
            "footer": str(footer.get("text") or ""),
            "footer_icon": str(footer.get("icon_url") or ""),
            "image": str((d.get("image") or {}).get("url") or ""),
            "thumbnail": str((d.get("thumbnail") or {}).get("url") or ""),
            "timestamp": bool(d.get("timestamp")),
            "fields": [
                {"name": f.get("name") or "", "value": f.get("value") or "", "inline": bool(f.get("inline"))}
                for f in d.get("fields", [])
            ],
        })
    for row in message.components:
        for button in getattr(row, "children", []):
            if getattr(button, "url", None) and getattr(button, "label", None):
                project["links"].append({"label": str(button.label), "url": str(button.url)})
    return validate_project(project)


class TrackedPostSelect(discord.ui.Select):
    def __init__(self, session: StudioSession, lib: dict[str, Any]) -> None:
        self.session = session
        options = [
            discord.SelectOption(label=f"#{item['channel_id']} • message {item['message_id']}", value=key)
            for key, item in list(lib["published"].items())[-20:]
        ] or [discord.SelectOption(label="No tracked messages yet", value="none")]
        super().__init__(placeholder="Choose a previously published embed", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        key = self.values[0]
        if key == "none":
            return await _say(interaction, "Publish a message first; it will appear here.")
        await interaction.response.defer(ephemeral=True)
        try:
            _, lib = await _load_library(self.session.guild_id)
            item = lib["published"].get(key)
            if not item:
                return await _say(interaction, "This tracked message no longer exists.")
            channel = interaction.guild.get_channel(int(item["channel_id"]))
            if not isinstance(channel, discord.TextChannel):
                return await _say(interaction, "That channel is not available.")
            if _post_blockers(interaction, channel):
                return await _say(interaction, "Dank Shield cannot safely edit in that channel.")
            message = await channel.fetch_message(int(item["message_id"]))
            if message.author.id != interaction.client.user.id:
                return await _say(interaction, "This is not a Dank Shield message.")
            draft = _from_message(message)
        except Exception as exc:
            return await _say(interaction, f"Could not load this message: {_error(exc)}")
        self.session.history.append(self.session.project)
        self.session.history = self.session.history[-10:]
        draft["role_mode"] = str(item.get("role_mode") or "buttons")
        draft["role_actions"] = deepcopy(item.get("role_actions") or [])
        try:
            draft = validate_project(draft)
        except StudioValidationError:
            return await _say(interaction, "This tracked post has an unsupported role configuration. Ask staff to repair it.")
        self.session.project = draft
        self.session.dirty = True
        self.session.channel_id = int(channel.id)
        self.session.post_key = key
        self.session.active = 0
        await _home(interaction, self.session, notice="Published message loaded. Edits are not live until confirmed.")


class PublishedMenu(_StudioView):
    def __init__(self, session: StudioSession, lib: dict[str, Any]) -> None:
        super().__init__(session)
        self.add_item(TrackedPostSelect(session, lib))

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _published(interaction: discord.Interaction, session: StudioSession) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        _, lib = await _load_library(session.guild_id)
    except Exception as exc:
        return await _say(interaction, _error(exc))
    await _menu(
        interaction, heading="🛠️ Edit a Published Embed",
        description="Select one of the last 20 tracked Dank Shield posts. We'll fetch the actual message, then let you edit a private copy. Publishing applies the changes to that same bot-owned message.",
        view=PublishedMenu(session, lib),
    )


async def open_embed_studio(interaction: discord.Interaction) -> None:
    """Shared entry point; no extra app-command budget and no public changes."""
    if not _staff(interaction):
        return await _say(interaction, "❌ Embed Studio requires Manage Server or Administrator.")
    if interaction.response.is_done():
        # Calls from an already-acknowledged Feature Center create their own
        # private editor; they never alter the public panel.
        pass
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    session = StudioSession(guild_id=int(interaction.guild.id), owner_id=int(interaction.user.id))
    notice = ""
    try:
        _, lib = await _load_library(session.guild_id)
        if _restore_snapshot(session, lib):
            notice = "Recovered your last autosaved draft. Nothing was published."
    except (StudioValidationError, ValueError, TypeError):
        notice = "Saved draft could not be read; starting a new local draft."
    except Exception:
        notice = "Storage unavailable; editing works, but autosave will need to recover."
    await _home(interaction, session, notice=notice)
