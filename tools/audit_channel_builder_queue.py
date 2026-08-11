#!/usr/bin/env python3
from pathlib import Path
import py_compile
import sys

ROOT = Path(__file__).resolve().parents[1]

FILES = [
    "stoney_verify/operation_queue.py",
    "stoney_verify/api_new/server.py",
    "stoney_verify/api_new/channel_builder_routes.py",
    "stoney_verify/services/channel_builder_runtime.py",
    "stoney_verify/services/channel_builder_execution.py",
    "stoney_verify/services/channel_builder_rollback_runtime.py",
    "stoney_verify/startup_guards/guild_operation_queue_guard.py",
]

REMOVED_FILES = [
    "stoney_verify/startup_guards/channel_builder_api_guard.py",
    "stoney_verify/startup_guards/channel_builder_runtime_exports_guard.py",
    "stoney_verify/startup_guards/channel_builder_rollback_api_guard.py",
    "stoney_verify/startup_guards/channel_builder_runtime_service_guard.py",
    "stoney_verify/startup_guards/channel_builder_rollback_runtime_service_guard.py",
    "tools/patch_channel_builder_server_routes.py",
    ".github/workflows/channel-builder-server-direct-registration.yml",
]

CHECKS = {
    "stoney_verify/api_new/server.py": [
        "from .channel_builder_routes import register_channel_builder_routes",
        "register_channel_builder_routes(app, sys.modules[__name__])",
    ],
    "stoney_verify/api_new/channel_builder_routes.py": [
        "from ..services.channel_builder_execution import",
        "register_channel_builder_routes",
        "submit_channel_builder_job",
        "preflight_channel_builder_job",
        "/channel-builder/preflight",
        "queueable=ok",
        "Channel Builder preflight failed",
        "submit_operation",
        "get_operation_job_persistent",
        "cancel_operation_job",
        "/operation/{job_id}/cancel",
        "channel_builder_apply_plan",
        "channel_mutation",
        "channel_builder",
    ],
    "stoney_verify/services/channel_builder_execution.py": [
        "preflight_channel_builder_plan",
        "execute_channel_builder_plan",
        "with_retry",
        "Manage Channels",
        "rollback_plan",
        "delete_created_channel",
        "rename_channel",
        "rollback_available",
    ],
    "stoney_verify/services/channel_builder_rollback_runtime.py": [
        "get_operation_job_persistent",
        "source_job_rollback_plan",
        "execute_rollback_plan",
        "submit_rollback_job",
        "delete_created_channel",
        "rename_channel",
        "channel_builder_rollback",
        "with_retry",
    ],
    "stoney_verify/operation_queue.py": [
        "reconcile_startup",
        "get_operation_job_persistent",
        "cancel_operation_job",
        "DANK_OPERATION_QUEUE_MAX_GLOBAL",
        "DANK_OPERATION_QUEUE_MAX_PER_GUILD",
        "DANK_OPERATION_QUEUE_MAX_PER_TYPE",
        "failure_rate",
        "with_retry",
    ],
    "stoney_verify/startup_guards/guild_operation_queue_guard.py": [
        "ensure_operation_queue_started_background",
        "command_sync_operation_queue_guard",
    ],
}

FORBIDDEN_STARTUP_REFS = [
    "channel_builder_api_guard",
    "channel_builder_runtime_exports_guard",
    "channel_builder_rollback_api_guard",
]


def main() -> int:
    for path in REMOVED_FILES:
        if (ROOT / path).exists():
            print(f"obsolete file still exists {path}", file=sys.stderr)
            return 1

    startup_init = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")
    for marker in FORBIDDEN_STARTUP_REFS:
        if marker in startup_init:
            print(f"startup still references obsolete shim {marker}", file=sys.stderr)
            return 1

    for path in FILES:
        target = ROOT / path
        if not target.exists():
            print(f"missing {path}", file=sys.stderr)
            return 1
        if path.endswith(".py"):
            try:
                py_compile.compile(str(target), doraise=True)
            except py_compile.PyCompileError as exc:
                print(f"compile failed {path}: {exc}", file=sys.stderr)
                return 1

    for path, snippets in CHECKS.items():
        text = (ROOT / path).read_text(encoding="utf-8")
        for snippet in snippets:
            if snippet not in text:
                print(f"{path} missing {snippet}", file=sys.stderr)
                return 1

    print("Channel Builder direct-registration/queue/rollback audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
