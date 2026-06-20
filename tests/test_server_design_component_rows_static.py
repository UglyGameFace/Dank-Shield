from pathlib import Path
import re


SOURCE = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()


def _class_blocks(source: str):
    matches = list(re.finditer(r"^class\s+(\w+)\((?:[^\n]*discord\.ui\.View[^\n]*)\):", source, re.M))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        yield match.group(1), source[match.start():end]


def test_no_dank_design_view_has_decorator_row_overflow():
    failures = []

    for class_name, block in _class_blocks(SOURCE):
        rows = {}

        for button in re.finditer(r"@discord\.ui\.button\([^\n]*row=(\d+)[^\n]*\)", block):
            row = int(button.group(1))
            rows[row] = rows.get(row, 0) + 1

        for select in re.finditer(r"@discord\.ui\.select\([^\n]*row=(\d+)[^\n]*\)", block):
            row = int(select.group(1))
            rows[row] = rows.get(row, 0) + 5

        bad = {row: width for row, width in rows.items() if row < 0 or row > 4 or width > 5}
        if bad:
            failures.append((class_name, bad))

    assert not failures, failures


def test_exact_format_has_only_five_row_four_buttons():
    start = SOURCE.find("class ExactFormatEditorView")
    assert start != -1
    end = SOURCE.find("\ndef ExactFormatEditorViewFactory", start)
    assert end != -1
    block = SOURCE[start:end]

    row4_buttons = re.findall(r"@discord\.ui\.button\([^\n]*row=4[^\n]*\)", block)
    assert len(row4_buttons) <= 5, row4_buttons
    assert 'custom_id="dank_design:exact_save"' not in block
    assert 'custom_id="dank_design:exact_save_preview"' in block
