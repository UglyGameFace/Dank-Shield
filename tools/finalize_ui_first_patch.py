from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "stoney_verify/profile_card_service.py",
    '''def effective_preferences(
    user_preferences: Optional[Mapping[str, Any]],
    guild_settings: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    global_values = normalize_preferences(user_preferences)
    local = dict(guild_settings or {})
    resolved = dict(global_values)
    for key in DEFAULT_PROFILE_PREFERENCES:
        resolved[key] = bool(global_values[key]) and bool(local.get(key, True))
    return resolved
''',
    '''def effective_preferences(
    user_preferences: Optional[Mapping[str, Any]],
    guild_settings: Optional[Mapping[str, Any]],
) -> dict[str, bool]:
    global_values = normalize_preferences(user_preferences)
    local = dict(guild_settings or {})
    return {
        key: bool(global_values[key]) and bool(local.get(key, True))
        for key in DEFAULT_PROFILE_PREFERENCES
    }
''',
)
replace_once(
    "stoney_verify/profile_card_service.py",
    '''async def get_effective_profile_settings(guild_id: int, user_id: int) -> dict[str, Any]:
    user_row, guild_row = await asyncio.gather(
        get_profile_user(user_id),
        get_profile_guild_settings(guild_id, user_id),
    )
    return {
        "preferences": effective_preferences(user_row.get("preferences"), guild_row.get("settings")),
        "platforms": dict(user_row.get("platforms") or {}),
    }
''',
    '''async def get_effective_profile_settings(guild_id: int, user_id: int) -> dict[str, Any]:
    user_row, guild_row = await asyncio.gather(
        get_profile_user(user_id),
        get_profile_guild_settings(guild_id, user_id),
    )
    preferences = normalize_preferences(user_row.get("preferences"))
    preferences.update(
        effective_preferences(
            user_row.get("preferences"),
            guild_row.get("settings"),
        )
    )
    return {
        "preferences": preferences,
        "platforms": dict(user_row.get("platforms") or {}),
    }
''',
)
replace_once(
    "tests/test_live_profile_card_final_safety.py",
    '''class FakePermissions:
    view_channel = True
    send_messages = True
    embed_links = True
    read_message_history = True
''',
    '''class FakePermissions:
    view_channel = True
    send_messages = True
    embed_links = True
    read_message_history = True
    attach_files = True
''',
)
replace_once(
    "tests/test_live_profile_card_integration_contract.py",
    '''    assert 'if suffix == "privacy"' in EXISTING_PROFILE
    assert EXISTING_PROFILE.count('label="Privacy & Platforms"') >= 2
    assert "invalidate_member_live_cards" in EXISTING_PROFILE
''',
    '''    assert 'if suffix == "privacy"' in EXISTING_PROFILE
    assert EXISTING_PROFILE.count('label="Signature Settings"') >= 2
    assert "open_profile_signature_studio" in EXISTING_PROFILE
    assert "invalidate_member_live_cards" in EXISTING_PROFILE
''',
)
replace_once(
    "tests/test_setup_aio_navigation_behavior.py",
    '''        "Server Design",
        "Member Profiles & Live Cards",
        "Backups & History",
''',
    '''        "Server Design",
        "Welcome & Join",
        "Profile Signatures",
        "Backups & History",
''',
)
