from __future__ import annotations

"""Make Dank Design Choose Missing Icons batch-safe.

Discord modals can only contain up to 5 text inputs. The old flow detected more
than 5 missing-icon rows and dead-ended with an error telling the user to apply
safe rows and hunt the rest from Channel Editor.

This patch changes the behavior to open the modal for the first 5 unresolved
missing-icon rows. After submit, the preview rebuilds; if more missing-icon rows
remain, the button is still available and opens the next batch.

Run from repo root:
    python tools/apply_dank_design_missing_icons_batch_fix.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ROOT / "stoney_verify/commands_ext/public_design_studio.py",
    ROOT / "tools/apply_p0_int_design_style_change_native_guard.py",
)

OLD_BLOCK = '''            if len(missing) > 5:
                await safe_send_interaction(
                    interaction,
                    content="Too many missing-emoji rows for one modal. Use **Apply Safe Ones Only** to apply safe rows first, then fix the rest from Channel Editor.",
                    ephemeral=True,
                    action_name="design.style_change.fix_missing.too_many",
                )
                return
            separator_id = _safe_str(pending.get("separator_id"), "none")
            await interaction.response.send_modal(
                StyleChangeFixMissingEmojiModal(items=missing, separator_id=separator_id)
            )
'''

NEW_BLOCK = '''            # Discord modals support at most 5 text inputs. Open the first batch,
            # then rebuild the preview after submit so the same button can handle
            # the next unresolved batch instead of dead-ending the flow.
            batch = missing[:5]
            separator_id = _safe_str(pending.get("separator_id"), "none")
            await interaction.response.send_modal(
                StyleChangeFixMissingEmojiModal(items=batch, separator_id=separator_id)
            )
'''

OLD_HELP = 'f"• **{len(missing_emoji)} missing emoji** — use **Choose Missing Icons** or leave them skipped."'
NEW_HELP = 'f"• **{len(missing_emoji)} missing emoji** — use **Choose Missing Icons**; it opens them in batches of 5."'

OLD_HOW_TO_FIX = '"How to fix",\n            value="\\n".join(_style_change_issue_lines(items))[:1024],'
NEW_HOW_TO_FIX = '"How to fix",\n            value="\\n".join(_style_change_issue_lines(items))[:1024],'


def patch_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text
    if OLD_BLOCK in text:
        text = text.replace(OLD_BLOCK, NEW_BLOCK)
    elif NEW_BLOCK in text:
        pass
    else:
        raise SystemExit(f"Could not find missing-icons too-many block in {path.relative_to(ROOT)}")

    text = text.replace(OLD_HELP, NEW_HELP)
    if text != original:
        path.write_text(text, encoding="utf-8")
        print(f"✅ patched {path.relative_to(ROOT)}")
        return True
    print(f"✅ already patched {path.relative_to(ROOT)}")
    return False


def main() -> None:
    for path in TARGETS:
        if path.exists():
            patch_file(path)

    public = TARGETS[0].read_text(encoding="utf-8")
    forbidden = (
        "Too many missing-emoji rows for one modal",
        "design.style_change.fix_missing.too_many",
        "fix the rest from Channel Editor",
    )
    remaining = [token for token in forbidden if token in public]
    if remaining:
        raise SystemExit("Missing-icons dead-end still remains in public design source: " + ", ".join(remaining))

    required = (
        "batch = missing[:5]",
        "StyleChangeFixMissingEmojiModal(items=batch",
        "opens them in batches of 5",
    )
    missing = [token for token in required if token not in public]
    if missing:
        raise SystemExit("Missing-icons batch fix missing expected tokens: " + ", ".join(missing))

    print("✅ Dank Design missing-icons flow now batches instead of erroring")


if __name__ == "__main__":
    main()
