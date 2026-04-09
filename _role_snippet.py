
@app_commands.command(name="set_stoner", description="Set or remove the STONER role for a member (staff only)")
@app_commands.describe(member="Member to modify", enabled="Whether the member should have the STONER role")
async def set_stoner(interaction: discord.Interaction, member: discord.Member, enabled: bool):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    rid = int(STONER_ROLE_ID or 0)
    if rid <= 0:
        return await interaction.response.send_message("⚠️ STONER_ROLE_ID is not configured.", ephemeral=True)

    role = interaction.guild.get_role(rid)
    if not role:
        return await interaction.response.send_message("⚠️ Stoner role not found (check STONER_ROLE_ID).", ephemeral=True)

    me = interaction.guild.me
    if not me or not me.guild_permissions.manage_roles:
        return await interaction.response.send_message("❌ I need **Manage Roles** permission.", ephemeral=True)
    try:
        if me.top_role <= role:
            return await interaction.response.send_message(
                "❌ I can't manage that role (role hierarchy). Move my bot role above it.",
                ephemeral=True,
            )
    except Exception:
        pass

    try:
        if enabled:
            await member.add_roles(role, reason=f"set_stoner by {interaction.user} ({interaction.user.id})")
        else:
            await member.remove_roles(role, reason=f"set_stoner by {interaction.user} ({interaction.user.id})")

        await interaction.response.send_message(
            f"✅ Updated stoner role for {member.mention}: **{'ON' if enabled else 'OFF'}**",
            ephemeral=True,
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Forbidden: I couldn't modify roles (check permissions/hierarchy).",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)


@app_commands.command(name="fix_roles", description="Fix Verified/Resident/Stoner roles in one go (staff only)")
@app_commands.describe(
    member="Member to modify",
    verified="Give/remove VERIFIED role",
    resident="Give/remove RESIDENT role",
    stoner="Optional: give/remove STONER role",
)
async def fix_roles(
    interaction: discord.Interaction,
    member: discord.Member,
    verified: bool,
    resident: bool,
    stoner: bool | None = None,
):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    me = interaction.guild.me
    if not me or not me.guild_permissions.manage_roles:
        return await interaction.response.send_message("❌ I need **Manage Roles** permission.", ephemeral=True)

    def _get_role(role_id: int, name: str):
        if role_id <= 0:
            return None, f"{name}_ROLE_ID not configured"
        r = interaction.guild.get_role(role_id)
        if not r:
            return None, f"{name} role not found"
        try:
            if me and me.top_role <= r:
                return None, f"Can't manage {name} (role hierarchy)"
        except Exception:
            pass
        return r, None

    vr, verr = _get_role(int(VERIFIED_ROLE_ID or 0), "VERIFIED")
    rr, rerr = _get_role(int(RESIDENT_ROLE_ID or 0), "RESIDENT")
    sr, serr = (None, None)
    if stoner is not None:
        sr, serr = _get_role(int(STONER_ROLE_ID or 0), "STONER")

    errors = [e for e in (verr, rerr, serr) if e]
    if errors:
        return await interaction.response.send_message("⚠️ Can't run fix_roles: " + "; ".join(errors), ephemeral=True)

    changed: list[str] = []

    try:
        if vr:
            if verified and vr not in member.roles:
                await member.add_roles(vr, reason=f"fix_roles verified by {interaction.user} ({interaction.user.id})")
                changed.append("VERIFIED=ON")
            if (not verified) and vr in member.roles:
                await member.remove_roles(vr, reason=f"fix_roles verified by {interaction.user} ({interaction.user.id})")
                changed.append("VERIFIED=OFF")

        if rr:
            if resident and rr not in member.roles:
                await member.add_roles(rr, reason=f"fix_roles resident by {interaction.user} ({interaction.user.id})")
                changed.append("RESIDENT=ON")
            if (not resident) and rr in member.roles:
                await member.remove_roles(rr, reason=f"fix_roles resident by {interaction.user} ({interaction.user.id})")
                changed.append("RESIDENT=OFF")

        if sr and stoner is not None:
            if stoner and sr not in member.roles:
                await member.add_roles(sr, reason=f"fix_roles stoner by {interaction.user} ({interaction.user.id})")
                changed.append("STONER=ON")
            if (not stoner) and sr in member.roles:
                await member.remove_roles(sr, reason=f"fix_roles stoner by {interaction.user} ({interaction.user.id})")
                changed.append("STONER=OFF")

        if not changed:
            msg = "✅ No changes needed."
        else:
            msg = "✅ Applied: " + ", ".join(changed)

        await interaction.response.send_message(f"{msg} ({member.mention})", ephemeral=True)

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Forbidden: I couldn't modify roles (check permissions/hierarchy).",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)

