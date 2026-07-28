from __future__ import annotations

from pathlib import Path

root = Path(__file__).resolve().parents[1]

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

print("Repaired generated profile Studio/Public string literals.")
