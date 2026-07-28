from __future__ import annotations

from pathlib import Path

root = Path(__file__).resolve().parents[1]

style_path = root / "stoney_verify/profile_signature_style.py"
style = style_path.read_text(encoding="utf-8")
late_definition = (
    'PROFILE_CUSTOM_BACKGROUND_KEY = "profile_signature_custom_background_b64"\n'
    'MEMBER_CUSTOM_BACKGROUND_KEY = "signature_custom_background_b64"\n'
)
if late_definition not in style:
    raise SystemExit("profile_signature_style.py: late member-background definition not found")
style = style.replace(
    late_definition,
    'PROFILE_CUSTOM_BACKGROUND_KEY = "profile_signature_custom_background_b64"\n',
    1,
)
style_marker = "DEFAULT_SERVER_PROFILE_STYLE: dict[str, str] = {\n"
if style_marker not in style:
    raise SystemExit("profile_signature_style.py: default-style marker not found")
style = style.replace(
    style_marker,
    'MEMBER_CUSTOM_BACKGROUND_KEY = "signature_custom_background_b64"\n\n\n' + style_marker,
    1,
)
style_path.write_text(style, encoding="utf-8")

studio_path = root / "stoney_verify/profile_signature_studio.py"
studio = studio_path.read_text(encoding="utf-8")
studio_broken = '''            content=(
                "## 📎 Custom Profile Background
"
                "Use `/dank profile background-upload`, attach the image, and set **server_default** only when editing server defaults.

"
                + profile_background_requirements()
            ),
'''
studio_fixed = '''            content=(
                "## 📎 Custom Profile Background\\n"
                "Use `/dank profile background-upload`, attach the image, and set **server_default** only when editing server defaults.\\n\\n"
                + profile_background_requirements()
            ),
'''
if studio_broken not in studio:
    raise SystemExit("profile_signature_studio.py: broken art-guide literal not found")
studio_path.write_text(studio.replace(studio_broken, studio_fixed, 1), encoding="utf-8")

public_path = root / "stoney_verify/commands_ext/public_profile_cards.py"
public = public_path.read_text(encoding="utf-8")
public_broken = '''            f"{exc}

{profile_background_requirements()}",
'''
public_fixed = '''            f"{exc}\\n\\n{profile_background_requirements()}",
'''
if public_broken not in public:
    raise SystemExit("public_profile_cards.py: broken upload-error literal not found")
public_path.write_text(public.replace(public_broken, public_fixed, 1), encoding="utf-8")

# Theme selection is now intentionally independent from color/background overrides.
theme_test_path = root / "tests/test_profile_signature_theme_application.py"
theme_test = theme_test_path.read_text(encoding="utf-8")
old_theme_tests = '''def test_member_theme_selection_applies_the_complete_theme_look() -> None:
    assert theme_style_updates("420_lobby", member=True) == {
        "signature_theme": "420_lobby",
        "signature_color_mode": "theme",
        "signature_background_mode": "theme",
    }
    assert theme_style_updates("server", member=True) == {
        "signature_theme": "server",
        "signature_color_mode": "server",
        "signature_background_mode": "server",
    }


def test_server_theme_selection_applies_theme_palette_and_background() -> None:
    assert theme_style_updates("cyber_neon", member=False) == {
        SERVER_STYLE_CONFIG_KEYS["theme"]: "cyber_neon",
        SERVER_STYLE_CONFIG_KEYS["color_mode"]: "theme",
        SERVER_STYLE_CONFIG_KEYS["background_mode"]: "theme",
    }
'''
new_theme_tests = '''def test_member_theme_selection_preserves_independent_colors_and_background() -> None:
    assert theme_style_updates("420_lobby", member=True) == {
        "signature_theme": "420_lobby",
    }
    assert theme_style_updates("server", member=True) == {
        "signature_theme": "server",
    }


def test_server_theme_selection_preserves_independent_colors_and_background() -> None:
    assert theme_style_updates("cyber_neon", member=False) == {
        SERVER_STYLE_CONFIG_KEYS["theme"]: "cyber_neon",
    }
'''
if old_theme_tests not in theme_test:
    raise SystemExit("test_profile_signature_theme_application.py: stale theme contract not found")
theme_test_path.write_text(theme_test.replace(old_theme_tests, new_theme_tests, 1), encoding="utf-8")

mixer_test_path = root / "tests/test_profile_signature_color_mixer.py"
mixer_test = mixer_test_path.read_text(encoding="utf-8")
if '        "Custom Art Guide",\n' not in mixer_test:
    marker = '        "Avatar Frame",\n        "Preview",\n'
    if marker not in mixer_test:
        raise SystemExit("test_profile_signature_color_mixer.py: appearance labels marker not found")
    mixer_test = mixer_test.replace(
        marker,
        '        "Avatar Frame",\n        "Custom Art Guide",\n        "Preview",\n',
        1,
    )
mixer_test_path.write_text(mixer_test, encoding="utf-8")

role_test_path = root / "tests/test_profile_role_display_separation.py"
role_test = role_test_path.read_text(encoding="utf-8")
old_role_assertions = '''    assert "spec.content_right - spec.content_x" in RENDERER
    assert "role_width = min(" in RENDERER
    assert "available = spec.content_right - x" in RENDERER
    assert "def _draw_platforms" in RENDERER
'''
new_role_assertions = '''    assert "spec.content_right - spec.content_x" in RENDERER
    assert "role_width = min(" in RENDERER
    assert "def _complete_lines" in RENDERER
    assert "def _draw_complete" in RENDERER
    assert '"   ".join(shared[:2])' not in RENDERER
    assert "def _draw_platforms" in RENDERER
'''
if old_role_assertions not in role_test:
    raise SystemExit("test_profile_role_display_separation.py: stale reserved-zone contract not found")
role_test_path.write_text(role_test.replace(old_role_assertions, new_role_assertions, 1), encoding="utf-8")

runtime_test_path = root / "tests/test_live_profile_card_runtime.py"
runtime_test = runtime_test_path.read_text(encoding="utf-8")
old_settings = '''                    "show_account_dates": False,
                    "show_platforms": False,
'''
new_settings = '''                    "show_account_dates": False,
                    "show_platforms": False,
                    "show_server_branding": False,
'''
if old_settings not in runtime_test:
    raise SystemExit("test_live_profile_card_runtime.py: hidden-field settings marker not found")
runtime_test = runtime_test.replace(old_settings, new_settings, 1)
old_mock = '''            date_labels,
            platform_entries,
        ):
            seen.append((style, server_role_labels, profile_tag_labels, date_labels, platform_entries))
'''
new_mock = '''            date_labels,
            platform_entries,
            show_server_branding,
        ):
            seen.append(
                (
                    style,
                    server_role_labels,
                    profile_tag_labels,
                    date_labels,
                    platform_entries,
                    show_server_branding,
                )
            )
'''
if old_mock not in runtime_test:
    raise SystemExit("test_live_profile_card_runtime.py: renderer mock marker not found")
runtime_test = runtime_test.replace(old_mock, new_mock, 1)
runtime_test = runtime_test.replace(
    '        assert seen and seen[0][1:] == ([], [], [], [])\n',
    '        assert seen and seen[0][1:] == ([], [], [], [], False)\n',
    1,
)
runtime_test_path.write_text(runtime_test, encoding="utf-8")

print("Repaired generated profile source and aligned regression contracts.")
