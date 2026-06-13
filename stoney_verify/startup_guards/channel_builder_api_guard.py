from __future__ import annotations

"""Compatibility bridge for Channel Builder structured API routes.

The real route handlers live in stoney_verify.api_new.channel_builder_routes.
This guard only ensures older startup paths still get Channel Builder routes if
api_new.server has not yet been updated to register them directly.
"""

import builtins
import sys
from typing import Any

import discord
from aiohttp import web

_ORIGINAL_IMPORT = builtins.__import__


def _log(message: str) -> None:
    try:
        print(f"🧱 channel_builder_api_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ channel_builder_api_guard {message}")
    except Exception:
        pass


def _patch_server_module(server: Any) -> None:
    if getattr(server, "_CHANNEL_BUILDER_API_GUARD_PATCHED", False):
        return

    original_start_api = getattr(server, "start_api", None)
    if not callable(original_start_api):
        _warn("server.start_api missing; cannot add routes")
        return

    async def start_api_with_channel_builder(bot_instance: discord.Client):
        if getattr(server, "_API_RUNNER", None) is not None:
            print("⚠️ New structured Bot API already running; skipping duplicate start.")
            return

        from stoney_verify.api_new.channel_builder_routes import register_channel_builder_routes

        server._validate_api_startup_config()
        app = web.Application(middlewares=[server._auth_middleware])

        app.router.add_get("/health", server.health)
        app.router.add_post("/ticket/create", server.create_ticket)
        app.router.add_post("/ticket/close", server.close_ticket)
        app.router.add_post("/ticket/delete", server.delete_ticket)
        app.router.add_post("/ticket/reopen", server.reopen_ticket_endpoint)
        app.router.add_post("/ticket/assign", server.assign_ticket_endpoint)
        if getattr(server, "unclaim_ticket", None) is not None:
            app.router.add_post("/ticket/unclaim", server.unclaim_ticket_endpoint)
        if getattr(server, "transfer_ticket", None) is not None:
            app.router.add_post("/ticket/transfer", server.transfer_ticket_endpoint)

        app.router.add_get("/tickets/queue", server.get_ticket_queue)
        app.router.add_post("/tickets/queue", server.get_ticket_queue)
        app.router.add_get("/tickets/unclaimed", server.get_unclaimed_tickets)
        app.router.add_post("/tickets/unclaimed", server.get_unclaimed_tickets)
        app.router.add_get("/tickets/claimed", server.get_claimed_tickets)
        app.router.add_post("/tickets/claimed", server.get_claimed_tickets)
        app.router.add_get("/tickets/my-claimed", server.get_my_claimed_tickets)
        app.router.add_post("/tickets/my-claimed", server.get_my_claimed_tickets)
        app.router.add_post("/tickets/sync-active", server.sync_active_tickets)
        app.router.add_post("/tickets/sync-one", server.sync_one_ticket)

        app.router.add_post("/members/sync", server.force_member_sync)
        app.router.add_post("/members/reconcile", server.reconcile_departed)
        app.router.add_post("/members/role-sync", server.role_member_sync)

        register_channel_builder_routes(app, server)

        runner = web.AppRunner(app)
        await runner.setup()
        bind_host = server._api_bind_host()
        bind_port = server._api_bind_port()
        site = web.TCPSite(runner, bind_host, bind_port)
        await site.start()
        server._API_RUNNER = runner
        server._API_SITE = site
        print(
            f"🌐 New structured Bot API started on {bind_host}:{bind_port} "
            f"(auth_required={server._should_require_api_auth()}, channel_builder_routes=true)"
        )

    setattr(start_api_with_channel_builder, "_channel_builder_api_original", original_start_api)
    server.start_api = start_api_with_channel_builder
    server._CHANNEL_BUILDER_API_GUARD_PATCHED = True
    _log("patched structured API startup to call channel_builder_routes.register_channel_builder_routes")


def _maybe_patch_loaded() -> None:
    try:
        server = sys.modules.get("stoney_verify.api_new.server")
        if server is not None:
            _patch_server_module(server)
    except Exception as e:
        _warn(f"patch loaded server failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.api_new.server" or name.endswith(".api_new.server"):
            target = sys.modules.get("stoney_verify.api_new.server") or sys.modules.get(name)
            if target is not None:
                _patch_server_module(target)
        _maybe_patch_loaded()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


def install() -> bool:
    if getattr(builtins, "_stoney_channel_builder_api_import_hook", False):
        _maybe_patch_loaded()
        return True
    try:
        builtins.__import__ = _safe_import
        setattr(builtins, "_stoney_channel_builder_api_import_hook", True)
        _maybe_patch_loaded()
        _log("loaded; Channel Builder route bridge active")
        return True
    except Exception as e:
        _warn(f"install failed: {e!r}")
        return False


install()

__all__ = ["install"]
