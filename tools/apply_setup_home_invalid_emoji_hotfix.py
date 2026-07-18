from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "stoney_verify/commands_ext/public_setup_recommend.py"
TEST = ROOT / "tests/test_setup_home_button_emoji_static.py"

text = SOURCE.read_text(encoding="utf-8")
old = '''    @discord.ui.button(
        label="More Options",
        emoji="•••",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_home:more_options",
        row=1,
    )'''
new = '''    @discord.ui.button(
        label="More Options",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_home:more_options",
        row=1,
    )'''

if text.count(old) != 1:
    raise SystemExit(f"expected exactly one invalid More Options emoji block, found {text.count(old)}")

text = text.replace(old, new, 1)
SOURCE.write_text(text, encoding="utf-8")

TEST.write_text(
    '''from __future__ import annotations

import ast
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "stoney_verify/commands_ext/public_setup_recommend.py"
TEXT = SOURCE.read_text(encoding="utf-8")


def _button_emojis(class_name: str) -> dict[str, str]:
    tree = ast.parse(TEXT, filename=str(SOURCE))
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == class_name
    ]
    assert len(classes) == 1

    result: dict[str, str] = {}
    for method in classes[0].body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in method.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "button"
            ):
                continue
            for keyword in decorator.keywords:
                if keyword.arg != "emoji":
                    continue
                if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    result[method.name] = keyword.value.value
    return result


def _looks_like_unicode_emoji(value: str) -> bool:
    # Discord rejects punctuation-only strings such as "•••" when supplied
    # through the component emoji field. Require at least one Unicode symbol.
    return any(unicodedata.category(char) in {"So", "Sm"} for char in value)


def test_setup_home_button_emojis_are_valid_unicode_symbols() -> None:
    emojis = _button_emojis("ProductSetupHomeView")
    assert emojis["continue_setup"] == "▶️"
    assert emojis["more_options"] == "⚙️"
    assert all(_looks_like_unicode_emoji(value) for value in emojis.values())
    assert "•••" not in emojis.values()
''',
    encoding="utf-8",
)

for path in (SOURCE, TEST):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")

print("PASS: replaced invalid setup-home button emoji and added regression guard")
