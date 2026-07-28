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

    Path(__file__).unlink()
    print("Corrected profile upgrade regression contracts.")


if __name__ == "__main__":
    main()
