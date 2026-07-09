from __future__ import annotations

"""Use the centralized Dank picker for Profile Role Builder role adding.

The previous manager used DankRoleSelect, which is still Discord's native role
entity selector underneath. That forces staff to search for role names and can
hide large role lists. Profile Role Builder should instead use DankMultiPickerView
pages built from server roles, with next/previous buttons.

Run from repo root:
    python tools/apply_profile_role_centralized_picker_cleanup.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "stoney_verify/commands_ext/public_self_roles_group.py"

OLD_IMPORT = "from stoney_verify.ui.picker import DankChoice, DankMultiPickerView, DankRoleSelect"
NEW_IMPORT = "from stoney_verify.ui.picker import DankChoice, DankMultiPickerView"

INSERT_AFTER = '''async def _handle_profile_cosmetics_remove_pick(interaction: discord.Interaction, values: list[str]) -> None:
    guild = interaction.guild
    if guild is None:
        return await _reply(interaction, "This only works inside the server.", ok=False)

    await _ack_profile_action(interaction)

    selected = {int(value) for value in values if str(value).isdigit()}
    if not selected:
        return await _reply(interaction, "No cosmetic roles selected for removal.", ok=False)

    role_ids, _config = await _profile_cosmetic_role_ids(guild)
    remaining = [role_id for role_id in role_ids if int(role_id) not in selected]
    await _save_profile_cosmetic_role_ids(guild, remaining)

    removed_labels = []
    for role_id in selected:
        role = guild.get_role(int(role_id))
        removed_labels.append(role.mention if isinstance(role, discord.Role) else f"`{role_id}`")

    await _reply(interaction, "Removed from cosmetic allowlist: " + ", ".join(removed_labels), ok=True)


'''

HELPER_BLOCK = '''PROFILE_ROLE_PICKER_PAGE_SIZE = 20


def _profile_role_picker_candidates(guild: discord.Guild) -> list[discord.Role]:
    """Server roles shown in the centralized Profile Role Builder picker.

    We intentionally show all normal roles that are below the bot and not
    managed/default. Sensitive/access roles can still be blocked after selection
    with the normal safety explanation, but staff can browse names instead of
    relying on Discord's native role search UI.
    """

    roles: list[discord.Role] = []
    me = guild.me
    for role in reversed(list(getattr(guild, "roles", []) or [])):
        if not isinstance(role, discord.Role):
            continue
        try:
            if role.is_default() or role.managed:
                continue
            if isinstance(me, discord.Member) and role >= me.top_role and not me.guild_permissions.administrator:
                continue
        except Exception:
            continue
        roles.append(role)
    return roles


def _profile_role_picker_choices(guild: discord.Guild, *, page: int = 0) -> tuple[list[DankChoice], int, int, int]:
    roles = _profile_role_picker_candidates(guild)
    total = len(roles)
    pages = max(1, (total + PROFILE_ROLE_PICKER_PAGE_SIZE - 1) // PROFILE_ROLE_PICKER_PAGE_SIZE)
    page = max(0, min(int(page or 0), pages - 1))
    start = page * PROFILE_ROLE_PICKER_PAGE_SIZE
    chunk = roles[start:start + PROFILE_ROLE_PICKER_PAGE_SIZE]
    choices = [
        DankChoice(
            label=str(role.name or "Role")[:100],
            value=str(int(role.id)),
            description=f"Role {start + index + 1} of {total}",
            emoji="🧩",
        )
        for index, role in enumerate(chunk)
    ]
    return choices, page, pages, total


def _profile_role_picker_embed(guild: discord.Guild, *, page: int = 0) -> discord.Embed:
    choices, page, pages, total = _profile_role_picker_choices(guild, page=page)
    embed = discord.Embed(
        title="🧩 Add Server Roles / Cosmetics",
        description=(
            "Browse existing server roles with the Dank Shield picker. "
            "No Discord search box needed. Pick one or more safe roles from this page."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    if choices:
        embed.add_field(
            name=f"Roles page {page + 1}/{pages}",
            value="\n".join(f"• `{choice.label}`" for choice in choices)[:1024],
            inline=False,
        )
    else:
        embed.add_field(name="Roles", value="No eligible server roles are visible to the bot.", inline=False)
    embed.add_field(
        name="Safety",
        value="Staff/access/verification/moderation roles are blocked when selected. Only profile-safe roles are saved.",
        inline=False,
    )
    embed.set_footer(text=f"{total} browsable role(s) • centralized Dank picker")
    return embed


async def _handle_profile_role_add_picker(interaction: discord.Interaction, values: list[str]) -> None:
    guild = interaction.guild
    if guild is None:
        return await _reply(interaction, "This only works inside the server.", ok=False)

    await _ack_profile_action(interaction)

    role_ids, config = await _profile_cosmetic_role_ids(guild)
    added: list[str] = []
    skipped: list[str] = []

    for value in values:
        if not str(value).isdigit():
            continue
        role = guild.get_role(int(value))
        if not isinstance(role, discord.Role):
            skipped.append(f"`{value}` no longer exists")
            continue
        blocker = _profile_cosmetic_role_blocker(guild, role, config)
        if blocker:
            skipped.append(f"{role.mention}: {blocker}")
            continue
        if int(role.id) in role_ids:
            skipped.append(f"{role.mention}: already added")
            continue
        if len(role_ids) >= PROFILE_COSMETIC_MAX_ROLES:
            skipped.append(f"{role.mention}: role/cosmetic limit reached")
            continue
        role_ids.append(int(role.id))
        added.append(role.mention)

    if added:
        await _save_profile_cosmetic_role_ids(guild, role_ids)

    lines: list[str] = []
    if added:
        lines.append("Added: " + ", ".join(added))
    if skipped:
        lines.append("Skipped:\n" + "\n".join(f"• {item}" for item in skipped[:8]))
    await _reply(interaction, "\n".join(lines) if lines else "No role/cosmetic changes needed.", ok=bool(added))


class ProfileRoleAddPickerView(DankMultiPickerView):
    def __init__(self, *, author_id: int, guild: discord.Guild, page: int = 0) -> None:
        choices, page, pages, _total = _profile_role_picker_choices(guild, page=page)
        super().__init__(
            author_id=author_id,
            choices=choices,
            on_pick=_handle_profile_role_add_picker,
            custom_id=f"{PROFILE_PREFIX}builder:role_add_picker:{page}",
            placeholder=f"Choose roles from page {page + 1}/{pages}…",
            min_values=0,
            max_values=len(choices),
            include_cancel=False,
            allow_anyone=False,
        )
        self.guild_id = int(guild.id)
        self.page = int(page)
        self.pages = int(pages)
        self.add_item(ProfileRolePickerPageButton(delta=-1, disabled=self.page <= 0, row=1))
        self.add_item(ProfileRolePickerPageButton(delta=1, disabled=self.page >= self.pages - 1, row=1))
        self.add_item(ProfileRolePickerBackButton(row=2))


class ProfileRolePickerPageButton(discord.ui.Button):
    def __init__(self, *, delta: int, disabled: bool, row: int) -> None:
        self.delta = int(delta)
        super().__init__(
            label="Previous Roles" if self.delta < 0 else "Next Roles",
            emoji="⬅️" if self.delta < 0 else "➡️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"{PROFILE_PREFIX}builder:role_picker_page:{'prev' if self.delta < 0 else 'next'}",
            row=row,
            disabled=bool(disabled),
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if not isinstance(view, ProfileRoleAddPickerView):
            return await _reply(interaction, "Picker expired. Reopen Profile Roles / Cosmetics.", ok=False)
        if not await view.interaction_check(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside the server.", ok=False)
        page = max(0, min(view.page + self.delta, view.pages - 1))
        await interaction.response.edit_message(
            embed=_profile_role_picker_embed(guild, page=page),
            view=ProfileRoleAddPickerView(author_id=view.author_id, guild=guild, page=page),
            allowed_mentions=discord.AllowedMentions.none(),
        )


class ProfileRolePickerBackButton(discord.ui.Button):
    def __init__(self, *, row: int) -> None:
        super().__init__(
            label="Back to Role Manager",
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"{PROFILE_PREFIX}builder:role_picker_back",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if isinstance(view, ProfileRoleAddPickerView) and not await view.interaction_check(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside the server.", ok=False)
        await interaction.response.edit_message(
            embed=await _profile_cosmetic_manager_embed(guild),
            view=ProfileCosmeticRoleManagerView(author_id=int(interaction.user.id)),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def _open_profile_role_add_picker(interaction: discord.Interaction, *, page: int = 0) -> None:
    guild = interaction.guild
    if guild is None:
        return await _reply(interaction, "This only works inside the server.", ok=False)
    await interaction.response.edit_message(
        embed=_profile_role_picker_embed(guild, page=page),
        view=ProfileRoleAddPickerView(author_id=int(interaction.user.id), guild=guild, page=page),
        allowed_mentions=discord.AllowedMentions.none(),
    )


'''

OLD_MANAGER_INIT = '''class ProfileCosmeticRoleManagerView(discord.ui.View):
    def __init__(self, *, author_id: int) -> None:
        super().__init__(timeout=300)
        self.author_id = int(author_id)
        self.add_item(
            DankRoleSelect(
                author_id=self.author_id,
                on_pick=self._add_role,
                placeholder="Add an existing server role / cosmetic…",
                row=0,
                allow_anyone=False,
            )
        )
'''

NEW_MANAGER_INIT = '''class ProfileCosmeticRoleManagerView(discord.ui.View):
    def __init__(self, *, author_id: int) -> None:
        super().__init__(timeout=300)
        self.author_id = int(author_id)

    @discord.ui.button(label="Browse / Add Server Roles", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="dank:profile:v1:builder:cosmetics_add_browser", row=0)
    async def browse_add_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_profile_role_add_picker(interaction, page=0)
'''


def replace_required(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        print(f"✅ patched {label}")
        return text.replace(old, new)
    if new in text:
        print(f"✅ already patched {label}")
        return text
    raise SystemExit(f"Could not find target block for {label}")


def main() -> None:
    text = PROFILE.read_text(encoding="utf-8")
    text = text.replace(OLD_IMPORT, NEW_IMPORT)
    text = replace_required(text, INSERT_AFTER, INSERT_AFTER + HELPER_BLOCK, "profile role centralized picker helpers")
    text = replace_required(text, OLD_MANAGER_INIT, NEW_MANAGER_INIT, "ProfileCosmeticRoleManagerView native role select removal")

    forbidden = (
        "DankRoleSelect(",
        "from stoney_verify.ui.picker import DankChoice, DankMultiPickerView, DankRoleSelect",
        "placeholder=\"Add an existing server role / cosmetic…\"",
    )
    remaining = [token for token in forbidden if token in text]
    if remaining:
        raise SystemExit("Native role picker remnants remain: " + ", ".join(remaining))

    required = (
        "ProfileRoleAddPickerView(DankMultiPickerView)",
        "Browse / Add Server Roles",
        "Previous Roles",
        "Next Roles",
        "Back to Role Manager",
        "_profile_role_picker_candidates",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit("Missing centralized profile picker tokens: " + ", ".join(missing))

    PROFILE.write_text(text, encoding="utf-8")
    print("✅ Profile Role Builder now uses centralized paged picker")


if __name__ == "__main__":
    main()
