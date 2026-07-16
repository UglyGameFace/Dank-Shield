from __future__ import annotations

from pathlib import Path

def _custom_setup_opens_service_picker(source: str) -> bool:
    """Verify the owned Custom Setup runtime route."""
    import ast

    tree = ast.parse(source)

    choice_view = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "SetupTypeChoiceView"
        ),
        None,
    )

    if choice_view is None:
        return False

    save_method = next(
        (
            node
            for node in choice_view.body
            if isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            )
            and node.name == "_save_and_show"
        ),
        None,
    )

    if save_method is None:
        return False

    def called_name(call: ast.Call) -> str | None:
        if isinstance(call.func, ast.Name):
            return call.func.id
        if isinstance(call.func, ast.Attribute):
            return call.func.attr
        return None

    for branch in ast.walk(save_method):
        if not isinstance(branch, ast.If):
            continue

        values = {
            node.value
            for node in ast.walk(branch.test)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
        }

        if "custom_setup" not in values:
            continue

        for statement in branch.body:
            for child in ast.walk(statement):
                if (
                    isinstance(child, ast.Call)
                    and called_name(child)
                    == "_open_custom_service_picker"
                ):
                    return True

    return False


ROOT = Path(__file__).resolve().parents[1]

solid = (ROOT / "stoney_verify/commands_ext/public_setup_solid.py").read_text(errors="ignore")
flow = (ROOT / "stoney_verify/startup_guards/unverified_ticket_panel_flow.py").read_text(errors="ignore")
fresh = (ROOT / "stoney_verify/commands_ext/public_setup_fresh_choice.py").read_text(errors="ignore")

failures: list[str] = []

for marker in (
    "stoney_solid:dashboard_custom_setup",
    "_open_custom_service_picker",
):
    if marker not in solid:
        failures.append(f"setup home missing custom setup marker: {marker}")

if not _custom_setup_opens_service_picker(fresh):
    failures.append("fresh choice custom setup does not open the custom service picker")

for marker in (
    "def _should_auto_route_unverified_ticket",
    "reason=basic_verify_or_no_advanced_verify",
    "public ticket click allowed through normal support path",
    "skipped verification UI post in ticket",
):
    if marker not in flow:
        failures.append(f"unverified support ticket gate missing marker: {marker}")

if failures:
    print("FAIL custom setup/basic verify ticket gate")
    for item in failures:
        print(" -", item)
    raise SystemExit(1)

print("PASS custom setup/basic verify ticket gate")
