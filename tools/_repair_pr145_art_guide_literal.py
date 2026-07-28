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

print("Repaired generated profile constants and string literals.")
