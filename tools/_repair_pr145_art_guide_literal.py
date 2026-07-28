from __future__ import annotations

from pathlib import Path

path = Path(__file__).resolve().parents[1] / "stoney_verify/profile_signature_studio.py"
text = path.read_text(encoding="utf-8")
broken = '''            content=(
                "## 📎 Custom Profile Background
"
                "Use `/dank profile background-upload`, attach the image, and set **server_default** only when editing server defaults.

"
                + profile_background_requirements()
            ),
'''
fixed = '''            content=(
                "## 📎 Custom Profile Background\\n"
                "Use `/dank profile background-upload`, attach the image, and set **server_default** only when editing server defaults.\\n\\n"
                + profile_background_requirements()
            ),
'''
if broken not in text:
    raise SystemExit("profile_signature_studio.py: broken art-guide literal not found")
path.write_text(text.replace(broken, fixed, 1), encoding="utf-8")
print("Repaired generated Custom Art Guide string literals.")
