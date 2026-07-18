from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]

GUARD = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "setup_home_authority_guard.py"
)

REGISTRY = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "__init__.py"
)

SOLID = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_solid.py"
)

FRESH = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_fresh_choice.py"
)

STARTUP_GUARDS = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
)


def test_obsolete_authority_guard_is_deleted() -> None:
    assert not GUARD.exists()


def test_authority_guard_is_not_registered() -> None:
    source = REGISTRY.read_text(
        encoding="utf-8"
    )

    assert "setup_home_authority_guard" not in source


def test_dead_solid_owner_alias_is_removed() -> None:
    source = SOLID.read_text(
        encoding="utf-8"
    )

    assert "_DANK_SOLID_HOME_OWNER" not in source


def test_fresh_choice_no_longer_claims_solid_owns_home() -> None:
    source = FRESH.read_text(
        encoding="utf-8"
    )

    assert (
        "public_setup_solid owns /dank setup"
        not in source
    )
    assert (
        "DANK_ENABLE_LEGACY_SETUP_CHOICE_HOME"
        not in source
    )
    assert "def _patch(" not in source
    assert (
        "solid._build_main_setup_payload ="
        not in source
    )
    assert (
        "recovery._ORIGINAL_BUILD_MAIN ="
        not in source
    )
    assert "FreshChoiceHomeView =" not in source
    assert "FreshServerChoiceView =" not in source
    assert "guided setup choices ready" in source



def test_no_startup_guard_assigns_setup_home_payload() -> None:
    offenders: list[str] = []

    for path in sorted(
        STARTUP_GUARDS.glob("*.py")
    ):
        source = path.read_text(
            encoding="utf-8"
        )

        try:
            tree = ast.parse(
                source,
                filename=str(path),
            )
        except SyntaxError as exc:
            raise AssertionError(
                f"{path} does not parse: {exc}"
            ) from exc

        for node in ast.walk(tree):
            targets: list[ast.expr] = []

            if isinstance(node, ast.Assign):
                targets.extend(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets.append(node.target)
            elif isinstance(node, ast.AugAssign):
                targets.append(node.target)

            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr
                    == "_build_main_setup_payload"
                ):
                    offenders.append(
                        f"{path.name}:{node.lineno}"
                    )

        if (
            'setattr(solid, "_build_main_setup_payload"'
            in source
        ):
            offenders.append(
                f"{path.name}:dynamic-setattr"
            )

    assert offenders == []


def test_normal_import_order_keeps_guided_home() -> None:
    script = textwrap.dedent(
        """
        from __future__ import annotations

        import importlib
        import os
        import sys


        os.environ.pop(
            "DANK_ENABLE_LEGACY_SETUP_CHOICE_HOME",
            None,
        )

        authority_name = (
            "stoney_verify.startup_guards."
            "setup_home_authority_guard"
        )

        solid = importlib.import_module(
            "stoney_verify.commands_ext."
            "public_setup_solid"
        )
        recommend = importlib.import_module(
            "stoney_verify.commands_ext."
            "public_setup_recommend"
        )
        recovery = importlib.import_module(
            "stoney_verify.commands_ext."
            "public_setup_recovery"
        )
        fresh = importlib.import_module(
            "stoney_verify.commands_ext."
            "public_setup_fresh_choice"
        )

        assert authority_name not in sys.modules

        assert (
            solid._build_main_setup_payload
            is recovery._build_main_with_recovery
        )

        assert (
            recovery._ORIGINAL_BUILD_MAIN
            is recommend._product_main_setup_payload
        )

        before_home = solid._build_main_setup_payload
        before_original = recovery._ORIGINAL_BUILD_MAIN

        assert not hasattr(fresh, "_patch")

        importlib.reload(fresh)

        assert (
            solid._build_main_setup_payload
            is before_home
        )

        assert (
            recovery._ORIGINAL_BUILD_MAIN
            is before_original
        )

        assert (
            recovery._ORIGINAL_BUILD_MAIN
            is recommend._product_main_setup_payload
        )
        """
    )

    env = os.environ.copy()
    env.pop(
        "DANK_ENABLE_LEGACY_SETUP_CHOICE_HOME",
        None,
    )

    existing_pythonpath = env.get(
        "PYTHONPATH",
        "",
    )

    env["PYTHONPATH"] = (
        str(ROOT)
        if not existing_pythonpath
        else str(ROOT)
        + os.pathsep
        + existing_pythonpath
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, (
        "isolated import-order regression failed\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
