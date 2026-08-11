#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch(path: str, replacements: list[tuple[str, str]]) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    original = text
    for old, new in replacements:
        if old not in text:
            if new in text:
                continue
            raise RuntimeError(f"{path}: missing exact patch anchor: {old[:120]!r}")
        text = text.replace(old, new, 1)
    if text != original:
        target.write_text(text, encoding="utf-8")
        print(f"patched {path}")
    else:
        print(f"already patched {path}")


def main() -> int:
    patch(
        "stoney_verify/api_new/server.py",
        [
            (
                "from .channel_builder_routes import register_channel_builder_routes\n",
                "from .channel_builder_routes import register_channel_builder_routes\n"
                "from .queued_handlers import queued_api_handler\n"
                "from ..operation_queue import operation_queue_health_summary\n",
            ),
            (
                "        bind_port=_api_bind_port(),\n    )\n",
                "        bind_port=_api_bind_port(),\n"
                "        operation_queue=operation_queue_health_summary(),\n"
                "    )\n",
            ),
            ('    app.router.add_post("/ticket/create", create_ticket)\n', '    app.router.add_post("/ticket/create", queued_api_handler(sys.modules[__name__], "create_ticket", create_ticket))\n'),
            ('    app.router.add_post("/ticket/close", close_ticket)\n', '    app.router.add_post("/ticket/close", queued_api_handler(sys.modules[__name__], "close_ticket", close_ticket))\n'),
            ('    app.router.add_post("/ticket/delete", delete_ticket)\n', '    app.router.add_post("/ticket/delete", queued_api_handler(sys.modules[__name__], "delete_ticket", delete_ticket))\n'),
            ('    app.router.add_post("/ticket/reopen", reopen_ticket_endpoint)\n', '    app.router.add_post("/ticket/reopen", queued_api_handler(sys.modules[__name__], "reopen_ticket_endpoint", reopen_ticket_endpoint))\n'),
            ('    app.router.add_post("/ticket/assign", assign_ticket_endpoint)\n', '    app.router.add_post("/ticket/assign", queued_api_handler(sys.modules[__name__], "assign_ticket_endpoint", assign_ticket_endpoint))\n'),
            ('        app.router.add_post("/ticket/unclaim", unclaim_ticket_endpoint)\n', '        app.router.add_post("/ticket/unclaim", queued_api_handler(sys.modules[__name__], "unclaim_ticket_endpoint", unclaim_ticket_endpoint))\n'),
            ('        app.router.add_post("/ticket/transfer", transfer_ticket_endpoint)\n', '        app.router.add_post("/ticket/transfer", queued_api_handler(sys.modules[__name__], "transfer_ticket_endpoint", transfer_ticket_endpoint))\n'),
            ('    app.router.add_post("/tickets/sync-active", sync_active_tickets)\n', '    app.router.add_post("/tickets/sync-active", queued_api_handler(sys.modules[__name__], "sync_active_tickets", sync_active_tickets))\n'),
            ('    app.router.add_post("/tickets/sync-one", sync_one_ticket)\n', '    app.router.add_post("/tickets/sync-one", queued_api_handler(sys.modules[__name__], "sync_one_ticket", sync_one_ticket))\n'),
            ('    app.router.add_post("/members/sync", force_member_sync)\n', '    app.router.add_post("/members/sync", queued_api_handler(sys.modules[__name__], "force_member_sync", force_member_sync))\n'),
            ('    app.router.add_post("/members/reconcile", reconcile_departed)\n', '    app.router.add_post("/members/reconcile", queued_api_handler(sys.modules[__name__], "reconcile_departed", reconcile_departed))\n'),
            ('    app.router.add_post("/members/role-sync", role_member_sync)\n', '    app.router.add_post("/members/role-sync", queued_api_handler(sys.modules[__name__], "role_member_sync", role_member_sync))\n'),
        ],
    )

    patch(
        "stoney_verify/startup_guards/__init__.py",
        [
            ('    "stoney_verify.startup_guards.operation_queue_persistence_retry_guard",\n', ''),
            ('    "stoney_verify.startup_guards.channel_builder_runtime_exports_guard",\n', ''),
            ('    "stoney_verify.startup_guards.api_operation_queue_guard",\n', ''),
        ],
    )

    patch(
        "stoney_verify/commands_ext/public_members_cleanup_group.py",
        [
            (
                "    MemberCleanupResult,\n    execute_member_cleanup,\n    validate_member_cleanup,\n",
                "    MemberCleanupResult,\n"
                "    actor_can_use_no_confirm,\n"
                "    execute_member_cleanup,\n"
                "    finalize_cleanup_run,\n"
                "    validate_member_cleanup,\n",
            ),
            (
                ") -> tuple[list[str], list[str], list[str]]:\n    removed: list[str] = []\n    blocked: list[str] = []\n    failed: list[str] = []\n    if interaction.guild is None:\n        return removed, blocked, [\"Guild missing while processing queue.\"]\n",
                ") -> tuple[list[str], list[str], list[str], list[MemberCleanupResult]]:\n"
                "    removed: list[str] = []\n"
                "    blocked: list[str] = []\n"
                "    failed: list[str] = []\n"
                "    results: list[MemberCleanupResult] = []\n"
                "    if interaction.guild is None:\n"
                "        return removed, blocked, [\"Guild missing while processing queue.\"], results\n",
            ),
            (
                "        result: MemberCleanupResult = await execute_member_cleanup(interaction.guild, request)\n        line = f\"**{_safe_name(result.target_display_name)}** (`{result.target_user_id}`) — {result.status}\"\n",
                "        result: MemberCleanupResult = await execute_member_cleanup(interaction.guild, request)\n"
                "        results.append(result)\n"
                "        line = f\"**{_safe_name(result.target_display_name)}** (`{result.target_user_id}`) — {result.status}\"\n",
            ),
            (
                "    return removed, blocked, failed\n\n\ndef _queue_result_embed",
                "    return removed, blocked, failed, results\n\n\ndef _queue_result_embed",
            ),
            (
                "        removed, blocked, failed = await _process_queue_items(interaction, items=self.items, reason=self.reason)\n        await interaction.edit_original_response(embed=_queue_result_embed(removed=removed, blocked=blocked, failed=failed, title=self.result_title), view=self)\n",
                "        removed, blocked, failed, results = await _process_queue_items(interaction, items=self.items, reason=self.reason)\n"
                "        await finalize_cleanup_run(\n"
                "            interaction.guild, actor_user_id=self.actor_user_id,\n"
                "            mode=\"purge_all\" if \"Purge-All\" in self.result_title else \"cleanup_queue\",\n"
                "            inactive_days=int(self.items[0].inactive_days if self.items else 90),\n"
                "            reason=self.reason, results=results,\n"
                "        )\n"
                "        await interaction.edit_original_response(embed=_queue_result_embed(removed=removed, blocked=blocked, failed=failed, title=self.result_title), view=self)\n",
            ),
            (
                "    settings = await get_cleanup_settings(int(interaction.guild.id))\n    safe_limit = max(1, min(int(limit or settings.default_queue_limit or _QUEUE_DEFAULT_LIMIT), _QUEUE_MAX_LIMIT))\n",
                "    settings = await get_cleanup_settings(int(interaction.guild.id))\n"
                "    no_confirm_allowed, no_confirm_reason = await actor_can_use_no_confirm(interaction.user)\n"
                "    require_confirmation = bool(settings.require_queue_confirmation or not no_confirm_allowed)\n"
                "    safe_limit = max(1, min(int(limit or settings.default_queue_limit or _QUEUE_DEFAULT_LIMIT), _QUEUE_MAX_LIMIT))\n",
            ),
            (
                "        f\"{'This is a confirmation screen. Nothing has happened yet.' if settings.require_queue_confirmation else 'Auto-process mode is enabled. Processing starts from this message.'}\\n\\n\"\n",
                "        f\"{'This is a confirmation screen. Nothing has happened yet.' if require_confirmation else 'Authorized auto-process mode is enabled. Processing starts from this message.'}\\n\\n\"\n",
            ),
            (
                "    if True:  # Safety invariant: mass cleanup always requires confirmation.\n        body += \"\\n\\nPress **Confirm Queue** to process these members one by one with final safety checks. Press **Cancel** to do nothing.\"\n",
                "    if not settings.require_queue_confirmation and not no_confirm_allowed:\n"
                "        body += f\"\\n\\n⚠️ Saved no-confirm mode was ignored for this actor: {no_confirm_reason}\"\n"
                "    if require_confirmation:\n"
                "        body += \"\\n\\nPress **Confirm Queue** to process these members one by one with final safety checks. Press **Cancel** to do nothing.\"\n",
            ),
            (
                "    removed, blocked, failed = await _process_queue_items(interaction, items=queued, reason=reason)\n    await interaction.edit_original_response(embed=_queue_result_embed(removed=removed, blocked=blocked, failed=failed))\n\n\n@members_group.command(name=\"purge-all\"",
                "    removed, blocked, failed, results = await _process_queue_items(interaction, items=queued, reason=reason)\n"
                "    await finalize_cleanup_run(interaction.guild, actor_user_id=int(interaction.user.id), mode=\"cleanup_queue\", inactive_days=int(report.options.inactive_days), reason=reason, results=results, skipped=len(skipped) + len(validation_blocked))\n"
                "    await interaction.edit_original_response(embed=_queue_result_embed(removed=removed, blocked=blocked, failed=failed))\n\n\n@members_group.command(name=\"purge-all\"",
            ),
            (
                "    settings = await get_cleanup_settings(int(interaction.guild.id))\n\n    await interaction.response.defer(ephemeral=True, thinking=True)\n    report, queued, skipped, validation_blocked = await _build_purge_all_preview(\n",
                "    settings = await get_cleanup_settings(int(interaction.guild.id))\n"
                "    no_confirm_allowed, no_confirm_reason = await actor_can_use_no_confirm(interaction.user)\n"
                "    require_confirmation = bool(settings.require_queue_confirmation or not no_confirm_allowed)\n\n"
                "    await interaction.response.defer(ephemeral=True, thinking=True)\n"
                "    report, queued, skipped, validation_blocked = await _build_purge_all_preview(\n",
            ),
            (
                "    if True:  # Safety invariant: mass cleanup always requires confirmation.\n        body += \"\\n\\nPress **Confirm Queue** to purge exactly these eligible members. Press **Cancel** to do nothing.\"\n",
                "    if not settings.require_queue_confirmation and not no_confirm_allowed:\n"
                "        body += f\"\\n\\n⚠️ Saved no-confirm mode was ignored for this actor: {no_confirm_reason}\"\n"
                "    if require_confirmation:\n"
                "        body += \"\\n\\nPress **Confirm Queue** to purge exactly these eligible members. Press **Cancel** to do nothing.\"\n",
            ),
            (
                "    removed, blocked, failed = await _process_queue_items(interaction, items=queued, reason=reason)\n    await interaction.edit_original_response(embed=_queue_result_embed(removed=removed, blocked=blocked, failed=failed, title=\"🧹 Purge-All Result\"))\n",
                "    removed, blocked, failed, results = await _process_queue_items(interaction, items=queued, reason=reason)\n"
                "    await finalize_cleanup_run(interaction.guild, actor_user_id=int(interaction.user.id), mode=\"purge_all\", inactive_days=safe_days, reason=reason, results=results, skipped=len(skipped) + len(validation_blocked))\n"
                "    await interaction.edit_original_response(embed=_queue_result_embed(removed=removed, blocked=blocked, failed=failed, title=\"🧹 Purge-All Result\"))\n",
            ),
            (
                "    changed = any(value is not None for value in (require_queue_confirmation, allow_low_confidence_queue, default_queue_limit))\n    if changed:\n",
                "    changed = any(value is not None for value in (require_queue_confirmation, allow_low_confidence_queue, default_queue_limit))\n"
                "    if require_queue_confirmation is False:\n"
                "        allowed, why = await actor_can_use_no_confirm(interaction.user)\n"
                "        if not allowed:\n"
                "            return await reply_once(interaction, {\"content\": f\"❌ {why}\", \"ephemeral\": True})\n"
                "    if changed:\n",
            ),
        ],
    )

    print("DS-BACKLOG-027 exact owner-file patch complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
