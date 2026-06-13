#!/usr/bin/env python3
from pathlib import Path
import py_compile
import sys

ROOT = Path(__file__).resolve().parents[1]

FILES = [
    'stoney_verify/operation_queue.py',
    'stoney_verify/api_new/channel_builder_routes.py',
    'stoney_verify/services/channel_builder_runtime.py',
    'stoney_verify/services/channel_builder_rollback_runtime.py',
    'stoney_verify/startup_guards/guild_operation_queue_guard.py',
    'stoney_verify/startup_guards/channel_builder_api_guard.py',
]

CHECKS = {
    'stoney_verify/startup_guards/guild_operation_queue_guard.py': [
        'channel_builder_api_guard',
        'command_sync_operation_queue_guard',
    ],
    'stoney_verify/api_new/channel_builder_routes.py': [
        'register_channel_builder_routes',
        'submit_channel_builder_job',
        'list_channel_builder_channels',
        'submit_rollback_job',
        'channel_builder_apply_plan',
        'channel_mutation',
        'channel_builder',
    ],
    'stoney_verify/services/channel_builder_runtime.py': [
        'preflight_channel_builder_plan',
        'execute_channel_builder_plan',
        'normalize_channel_builder_items',
        'validate_channel_builder_items',
        'Manage Channels permission',
        'rollback_plan',
    ],
    'stoney_verify/services/channel_builder_rollback_runtime.py': [
        'source_job_rollback_plan',
        'execute_rollback_plan',
        'submit_rollback_job',
        'delete_created_channel',
        'rename_channel',
        'channel_builder_rollback',
    ],
    'stoney_verify/startup_guards/channel_builder_api_guard.py': [
        'channel_builder_routes.register_channel_builder_routes',
        'register_channel_builder_routes(app, server)',
        'channel_builder_routes=true',
    ],
}


def main() -> int:
    for path in FILES:
        target = ROOT / path
        if not target.exists():
            print(f'missing {path}', file=sys.stderr)
            return 1
        try:
            py_compile.compile(str(target), doraise=True)
        except py_compile.PyCompileError as exc:
            print(f'compile failed {path}: {exc}', file=sys.stderr)
            return 1
    for path, snippets in CHECKS.items():
        text = (ROOT / path).read_text(encoding='utf-8')
        for snippet in snippets:
            if snippet not in text:
                print(f'{path} missing {snippet}', file=sys.stderr)
                return 1
    print('Channel Builder queue audit passed')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
