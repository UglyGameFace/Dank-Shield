from __future__ import annotations

"""Make Dank Design Choose Missing Icons batch-safe.

Idempotent version:
- If the old "too many rows" dead-end exists, remove it.
- If the flow is already patched, verify it and continue.
- Never fails just because the old block is already gone.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    ROOT / "stoney_verify/commands_ext/public_design_studio.py",
    ROOT / "tools/apply_p0_int_design_style_change_native_guard.py",
]

FORBIDDEN = (
    "Too many missing-emoji rows for one modal",
    "design.style_change.fix_missing.too_many",
    "fix the rest from Channel Editor",
)

REQUIRED = (
    "batch = missing[:5]",
    "StyleChangeFixMissingEmojiModal(items=batch",
    "opens them in batches of 5",
)


def patch_line_walker(path: Path) -> int:
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    i = 0
    patched = 0

    while i < len(lines):
        line = lines[i]

        if line.strip() == "if len(missing) > 5:":
            indent = line[: len(line) - len(line.lstrip())]

            j = i + 1
            found_modal = False
            while j < len(lines):
                if "StyleChangeFixMissingEmojiModal(items=missing" in lines[j]:
                    found_modal = True
                    j += 1
                    while j < len(lines) and lines[j].strip() == ")":
                        j += 1
                    break
                j += 1

            if found_modal:
                out.extend([
                    f"{indent}# Discord modals support at most 5 text inputs. Open the first batch,",
                    f"{indent}# then rebuild the preview after submit so this button can handle",
                    f"{indent}# the next unresolved batch instead of dead-ending the flow.",
                    f"{indent}batch = missing[:5]",
                    f'{indent}separator_id = _safe_str(pending.get("separator_id"), "none")',
                    f"{indent}await interaction.response.send_modal(",
                    f"{indent}    StyleChangeFixMissingEmojiModal(items=batch, separator_id=separator_id)",
                    f"{indent})",
                ])
                i = j
                patched += 1
                continue

        out.append(line)
        i += 1

    text = "\n".join(out) + "\n"
    text = text.replace(
        'f"• **{len(missing_emoji)} missing emoji** — use **Choose Missing Icons** or leave them skipped."',
        'f"• **{len(missing_emoji)} missing emoji** — use **Choose Missing Icons**; it opens them in batches of 5."',
    )

    path.write_text(text, encoding="utf-8")
    return patched


def verify_public() -> None:
    public = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")

    remaining = [token for token in FORBIDDEN if token in public]
    if remaining:
        raise SystemExit("Missing-icons dead-end still remains: " + ", ".join(remaining))

    missing = [token for token in REQUIRED if token not in public]
    if missing:
        raise SystemExit("Missing-icons batch flow missing: " + ", ".join(missing))


def main() -> None:
    for path in TARGETS:
        if not path.exists():
            continue

        before = path.read_text(encoding="utf-8")
        patched = patch_line_walker(path)
        after = path.read_text(encoding="utf-8")

        if patched:
            print(f"✅ patched {path.relative_to(ROOT)} blocks={patched}")
        elif before != after:
            print(f"✅ refreshed wording in {path.relative_to(ROOT)}")
        else:
            print(f"✅ already batch-safe: {path.relative_to(ROOT)}")

    verify_public()
    print("✅ Dank Design missing-icons flow verified batch-safe")


if __name__ == "__main__":
    main()
