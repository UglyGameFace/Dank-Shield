#!/usr/bin/env python3
from pathlib import Path
import py_compile
import sys

ROOT = Path(__file__).resolve().parents[1]

FILES = [
    'stoney_verify/operation_queue.py',
    'stoney_verify/services/channel_builder_runtime.py',
    'stoney_verify/services/channel_builder_rollback_runtime.py',
    'stoney_verify/startup_guards/guild_operation_queue_guard.py',
    'stoney_verify/startup_guards/channel_builder_api_guard.py',
    'stoney_verify/startup_guards/channel_builder_runtime_service_guard.py',
    'stoney_verify/startup_guards/channel_builder_rollback_api_guard.py',
    'stoney_verify/startup_guards/channel_builder_rollback_runtime_service_guard.py',
]

CHECKS = {
    'stoney_verify/startup_guards/guild_operation_queue_guard.py': [
        'channel_builder_api_guard',
        'channel_builder_runtime_service_guard',
        'channel_builder_rollback_api_guard',
        'channel_builder_rollback_runtime_service_guard',
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
    'stoney_verify/startup_guards/channel_builder_runtime_service_guard.py': [
        'channel_builder_runtime',
        '_execute_channel_builder_plan',
        '_normalize_items',
        'list_channel_builder_channels',
    ],
    'stoney_verify/startup_guards/channel_builder_rollback_runtime_service_guard.py': [
        'channel_builder_rollback_runtime',
        'submit_channel_builder_rollback_job',
        '_execute_rollback_plan',
        '_source_job_rollback_plan',
    ],
    'stoney_verify/startup_guards/channel_builder_api_guard.py': [
        'channel_builder_apply_plan',
        'channel_mutation',
        'channel_builder',
        'rollback_plan',
        'list_channel_builder_channels',
    ],
    'stoney_verify/startup_guards/channel_builder_rollback_api_guard.py': [
        'channel_builder_rollback',
        'source_job_id',
        'delete_created_channel',
        'rename_channel',
        'channel_mutation',
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
