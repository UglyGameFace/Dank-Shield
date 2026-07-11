from pathlib import Path


BASIC = Path(
    "stoney_verify/verification_new/basic_verify.py"
).read_text(encoding="utf-8")

PUBLIC = Path(
    "stoney_verify/commands_ext/public_verify_basic_panel.py"
).read_text(encoding="utf-8")

APP = Path(
    "stoney_verify/app.py"
).read_text(encoding="utf-8")


def test_basic_verify_installs_fixed_persistent_view():
    assert "def install_basic_verify_runtime(" in BASIC
    assert "add_view(BasicVerifyView())" in BASIC
    assert "_RUNTIME_VIEW_REGISTERED = True" in BASIC


def test_basic_verify_installs_delayed_fallback_listener():
    assert "async def _basic_verify_fallback_listener(" in BASIC
    assert "await asyncio.sleep(0.15)" in BASIC
    assert "if interaction.response.is_done():" in BASIC
    assert (
        'add_listener(\n'
        '                _basic_verify_fallback_listener,\n'
        '                "on_interaction",'
        in BASIC
    )


def test_fallback_only_handles_basic_verify_custom_id():
    start = BASIC.index(
        "async def _basic_verify_fallback_listener("
    )
    end = BASIC.index(
        "def basic_verify_runtime_status(",
        start,
    )
    block = BASIC[start:end]

    assert "BASIC_VERIFY_CUSTOM_ID" in block
    assert "maybe_handle_basic_verify_interaction" in block


def test_app_installs_basic_verify_before_login():
    assert (
        "install_basic_verify_runtime as "
        "_install_basic_verify_runtime"
        in APP
    )
    assert (
        "_install_basic_verify_runtime(bot, strict=True)"
        in APP
    )
    assert (
        APP.index("_install_basic_verify_runtime(bot, strict=True)")
        < APP.index("def run()")
    )


def test_command_module_delegates_to_native_runtime():
    assert "install_basic_verify_runtime" in PUBLIC
    assert "register_basic_verify_runtime" not in PUBLIC
    assert "maybe_handle_basic_verify_interaction" not in PUBLIC
    assert '@bot.listen("on_interaction")' not in PUBLIC
    assert "_LISTENER_ATTACHED" not in PUBLIC


def test_backward_compatible_registration_name_remains():
    assert "def register_basic_verify_runtime(" in BASIC
    assert (
        "return install_basic_verify_runtime("
        in BASIC
    )
