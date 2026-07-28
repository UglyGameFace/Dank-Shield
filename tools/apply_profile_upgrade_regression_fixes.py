from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_required(path: str, replacements: tuple[tuple[str, str], ...]) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    for old, new in replacements:
        if old not in text:
            raise RuntimeError(f"{path}: required text was not found: {old!r}")
        text = text.replace(old, new)
    target.write_text(text, encoding="utf-8")


def main() -> None:
    replace_once(
        "tests/test_live_profile_card_runtime.py",
        "        assert seen and seen[0][1:] == ([], [], [])\n",
        "        assert seen and seen[0][1:] == ([], [], [], [])\n",
    )

    replace_once(
        "tests/test_profile_platform_privacy_preview_ux.py",
        '''def test_every_platform_detail_uses_explicit_public_private_language():
    for platform in profile_signature_studio.PLATFORM_SPECS:
        private_view = profile_signature_studio.PlatformDetailView(
            author_id=42,
            platform=platform,
            entry={"username": "player", "shared": False},
        )
        public_view = profile_signature_studio.PlatformDetailView(
            author_id=42,
            platform=platform,
            entry={"username": "player", "shared": True},
        )
        private_button = next(child for child in private_view.children if child.label == "Make Public")
        public_button = next(child for child in public_view.children if child.label == "Make Private")
        assert private_button.label == "Make Public"
        assert private_button.disabled is False
        assert public_button.label == "Make Private"
        assert public_button.disabled is False


def test_unsaved_platform_cannot_be_published_before_username_exists():
    view = profile_signature_studio.PlatformDetailView(author_id=42, platform="steam", entry={})
    button = next(child for child in view.children if child.label == "Make Public")
    assert button.label == "Make Public"
    assert button.disabled is True
''',
        '''def test_every_platform_detail_uses_explicit_display_modes_and_private_control():
    for platform, spec in profile_signature_studio.PLATFORM_SPECS.items():
        private_view = profile_signature_studio.PlatformDetailView(
            author_id=42,
            platform=platform,
            entry={"username": "player", "shared": False, "mode": "username"},
        )
        public_view = profile_signature_studio.PlatformDetailView(
            author_id=42,
            platform=platform,
            entry={"username": "player", "shared": True, "mode": "username"},
        )
        private_labels = {child.label: child for child in private_view.children if child.label}
        public_labels = {child.label: child for child in public_view.children if child.label}
        assert "Show Username" in private_labels
        assert private_labels["Show Username"].disabled is False
        assert "Logo Only" in private_labels
        assert private_labels["Logo Only"].disabled is False
        assert private_labels["Make Private"].disabled is True
        assert public_labels["Show Username"].style == discord.ButtonStyle.success
        assert public_labels["Make Private"].disabled is False
        if spec.supports_url:
            assert "Show Link" in private_labels


def test_unsaved_platform_allows_logo_only_without_username_or_link():
    view = profile_signature_studio.PlatformDetailView(author_id=42, platform="steam", entry={})
    buttons = {child.label: child for child in view.children if child.label}
    assert buttons["Show Link"].disabled is True
    assert buttons["Show Username"].disabled is True
    assert buttons["Logo Only"].disabled is False
    assert buttons["Make Private"].disabled is True
''',
    )

    replace_once(
        "tests/test_profile_role_display_separation.py",
        '''def test_live_banner_uses_wide_layout_real_logo_cache_and_server_branding() -> None:
    assert "SIGNATURE_WIDTH = 1400" in RENDERER
    assert "SIGNATURE_HEIGHT = 340" in RENDERER
    assert "_PLATFORM_LOGO_CACHE" in RENDERER
    assert "_download_platform_logo" in RENDERER
    assert "platform_entries" in RENDERER
    assert "guild_icon_bytes" in RENDERER
    assert "Logo only" not in RENDERER.split("def _draw_logo_box", 1)[0]
''',
        '''def test_live_banner_uses_wide_layout_bundled_logos_and_server_branding() -> None:
    assert "SIGNATURE_WIDTH = 1400" in RENDERER
    assert "SIGNATURE_HEIGHT = 340" in RENDERER
    assert "PLATFORM_LOGO_DIR" in RENDERER
    assert "_PLATFORM_LOGO_BYTES_CACHE" in RENDERER
    assert "_bundled_platform_logo_bytes" in RENDERER
    assert "platform_entries" in RENDERER
    assert "guild_icon_bytes" in RENDERER
    assert "cdn.discordapp.com" not in RENDERER
''',
    )

    replace_required(
        "stoney_verify/commands_ext/public_self_roles_group.py",
        (
            ('title="🧩 Add Server Roles / Cosmetics"', 'title="🧩 Add Profile Tags & Cosmetics"'),
            ('label="Server Roles / Cosmetics"', 'label="Profile Tags & Cosmetics"'),
            ('label="Browse / Add Server Roles"', 'label="Browse / Add Profile Tags"'),
            ('label="Remove Role / Cosmetic"', 'label="Remove Profile Tag"'),
            ('title="➖ Remove Roles / Cosmetics"', 'title="➖ Remove Profile Tags"'),
            ('description="Remove from Profile Builder roles/cosmetics"', 'description="Remove from Profile Tags & Cosmetics"'),
            ('No server role/cosmetics are available yet.', 'No profile tags/cosmetics are available yet.'),
            ('Choose your server role/cosmetics…', 'Choose your profile tags/cosmetics…'),
            ('role/cosmetic limit reached', 'profile tag limit reached'),
            ('No role/cosmetic changes needed.', 'No profile tag changes needed.'),
            ('is already a server role/cosmetic.', 'is already a profile tag/cosmetic.'),
            ('as a server role/cosmetic.', 'as a profile tag/cosmetic.'),
        ),
    )

    replace_required(
        "stoney_verify/startup_guards/profile_role_editor_guard.py",
        (
            ('PROFILE_ROLES_COSMETICS_LABEL = "Server Roles / Cosmetics"', 'PROFILE_ROLES_COSMETICS_LABEL = "Profile Tags & Cosmetics"'),
            ('"""Make the profile role/cosmetic button obvious to normal users."""', '"""Make the Profile Tags & Cosmetics button obvious to normal users."""'),
            ('These are profile/server roles/cosmetics members can choose for themselves.', 'These are optional profile tags/cosmetics members can choose for themselves.'),
            ('Use **Server Roles / Cosmetics** to pick optional server roles offered through the Profile Builder.', 'Use **Profile Tags & Cosmetics** to pick optional self-selected tags and cosmetics.'),
        ),
    )

    replace_required(
        "tools/test_profile_cosmetic_roles_static.py",
        (
            ('label="Profile Roles / Cosmetics"', 'label="Profile Tags & Cosmetics"'),
            ('label="Server Roles / Cosmetics"', 'label="Profile Tags & Cosmetics"'),
        ),
    )

    replace_required(
        "tools/test_profile_role_editor_guard_static.py",
        (
            ('assert "Server Roles / Cosmetics" in PROFILE', 'assert "Server Roles / Cosmetics" not in PROFILE'),
            ('assert "Profile Roles / Cosmetics" in PROFILE', 'assert "Profile Tags & Cosmetics" in PROFILE'),
            ('assert "Browse / Add Server Roles" in PROFILE', 'assert "Browse / Add Profile Tags" in PROFILE'),
            ('assert "Add Server Roles / Cosmetics" in PROFILE', 'assert "Add Profile Tags & Cosmetics" in PROFILE'),
            ('assert "Remove Role / Cosmetic" in PROFILE', 'assert "Remove Profile Tag" in PROFILE'),
            ('assert "Server Roles / Cosmetics" in GUARD', 'assert "Server Roles / Cosmetics" not in GUARD'),
            ('assert "These are profile/server roles/cosmetics" in GUARD', 'assert "These are optional profile tags/cosmetics" in GUARD'),
            ('assert "Profile Roles / Cosmetics" in GUARD', 'assert "Profile Tags & Cosmetics" in GUARD'),
        ),
    )

    Path(__file__).unlink()
    print("Corrected profile upgrade contracts and separated Profile Tags from Server Roles wording.")


if __name__ == "__main__":
    main()
