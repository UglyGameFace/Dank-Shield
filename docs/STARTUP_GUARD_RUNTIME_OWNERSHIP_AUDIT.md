# Startup Guard Runtime Ownership Audit

Base examined: `85cdac93bcb6fdd47243819ff8ea1a8ca4eada65`

This document records the runtime-ownership evidence for the dead bulk startup-loader finding. It separates three different things that the old architecture blurred together:

1. a module being listed in `startup_guards/__init__.py`;
2. a module actually being imported by a production owner;
3. a module mutating runtime state when imported.

Those are not equivalent. The historical bulk list is not a boot contract and must never be used to infer that a guard is live.

## Classification rules

- **Explicit boot owner**: imported directly by `main.py`.
- **Host owner**: imported by Python host hooks (`sitecustomize.py` / `usercustomize.py`).
- **Transitive live owner**: imported by an explicit boot/host owner.
- **Feature owner**: imported by the canonical module that owns a live product path.
- **Historical bulk only**: present in the old bulk-loader inventory but the bulk loader is not called at normal boot. This classification alone is not permission to delete the file; a dedicated module audit is still required before deletion.
- **Test/tool only**: current evidence shows validation/reference usage, not a production installer.

## Confirmed production boot ownership

| Module | Ownership path | Runtime effect / risk | Duplicate risk if bulk-loaded again | Disposition |
| --- | --- | --- | --- | --- |
| `startup_guards.process_health` | imported by `startup_guards/__init__.py` whenever the package loads | installs process/import/signal/ready health safety at import time | high: import-hook/listener ownership | **keep live**; package side effect preserved in this finding |
| `startup_guards.discord_api_safety` | direct `main.py` import | wraps selected Discord API operations with retry/serialization safety | high: Discord API method wrappers | **keep live** |
| `startup_guards.command_safety` | direct `main.py` import | command-tree safety and sync budget ownership | high: `CommandTree` wrappers | **keep live** |
| `startup_guards.command_scope_dedupe` | direct `main.py` import | production command-scope cleanup / ready listener | high: command-tree and listener ownership | **keep live** |
| `startup_guards.public_server_env_id_guard` | direct `main.py` import | prevents public runtime from consuming deployment-level guild/role/channel IDs | medium: config mutation/normalization | **keep live** |
| `startup_guards.guild_config_runtime_validator` | direct `main.py` import | validates discovered runtime guild config and purges invalid saved IDs | medium: guild-config wrapper | **keep live** |
| `startup_guards.interaction_action_lock_guard` | direct `main.py` import | wraps Discord UI scheduled interaction execution to suppress duplicate action races | high: discord.py UI monkey patch | **keep live** |
| `startup_guards.auto_shard` | transitive import from `command_safety` | env-gated `commands.Bot` to `AutoShardedBot` substitution; disabled by default | high if enabled twice/out of order | **keep live through current owner**; separate future migration recommended |
| `startup_guards.global_command_sync` | transitive import from `command_safety` | wraps `CommandTree.sync` to block unsafe large global syncs | high: stacked sync wrappers | **keep live through current owner** |

The direct `main.py` set is the production boot contract. Nothing in the historical bulk inventory may silently extend it.

## Confirmed host-auto-import ownership

| Module | Ownership path | Runtime effect / risk | Disposition |
| --- | --- | --- | --- |
| `startup_guards.runtime_safety` | `sitecustomize.py` calls `load_runtime_safety()` | installs/chains an import hook and patches selected runtime modules as they load | **keep live** for this finding; separate monkey-patch migration audit required |
| `startup_guards.public_startup_scope` | imported transitively by `runtime_safety` | installs/chains an import hook around public startup/sync scope | **keep live through current owner** |
| `startup_guards.basic_verification_mode_guard` | imported/applied by `sitecustomize.py` | installs Basic Verify listener/panel/sync compatibility ownership | **keep live**; verification-integrity behavior already separately validated |
| `startup_guards.id_verify_allowlist_guard` | imported/applied by `basic_verification_mode_guard` | fail-closed legacy ID-verify compatibility and canonical ticket-flow bridge | **keep live through current owner** |
| `startup_guards.unverified_ticket_panel_flow` | imported by `id_verify_allowlist_guard` | supplies canonical ID-ticket compatibility flow used by the allowlist owner | **keep live through current owner** |
| `startup_guards.panel_menu_retry_guard` | dynamically imported by `usercustomize.py` | attempts to wrap ticket panel using removed `public_ticket_panel_clean_hardening` state | **retire**: current dependency was intentionally removed by DS-BACKLOG-027, so this hook cannot install working behavior |

`sitecustomize.py` also contains an obsolete compatibility alias for `load_all_startup_guards`. It does not call the loader and has no valid production purpose once the loader API is retired.

## Confirmed feature-owned startup-guard imports

These modules are not evidence that a bulk startup-loader is needed. Their canonical feature owner imports them deliberately.

| Guard/helper | Confirmed owner | What the owner uses it for | Disposition |
| --- | --- | --- | --- |
| `member_lifecycle_router_guard` | `commands_ext/public_member_lifecycle_runtime.py` | authoritative welcome/exit lifecycle listeners and `/dank member-logs` route | **keep live through feature owner** |
| `ticket_category_setup_guard` | `commands_ext/public_setup_compact.py` | managed ticket-category selection/runtime loader ownership | **keep live through feature owner** |
| `ticket_forms_foundation_guard` | `commands_ext/public_ticket_panel_clean.py` | ticket intake form parsing/behavior integrated with the canonical clean panel | **keep live through feature owner**; separate migration away from patching may be worthwhile later |
| `verification_idle_kick_feature` | `commands_ext/public_setup_solid.py` | verification idle-kick setup/status behavior | **keep feature-owned** |
| `setup_service_modes` | `commands_ext/public_spam_group.py` | compatibility helpers for the native Spam Guard setup UI | **keep compatibility helper**; tests confirm it does not replace canonical setup builders |
| `fresh_join_role_recovery` | `members_new/join_removal_safety.py` | fail-closed fresh-join role recovery on removal safety path | **keep feature-owned** |
| `setup_permission_repair_guard` | `setup_permission_repair_services.py` | compatibility helper functions consumed by the canonical repair service | **keep feature-owned until dedicated migration** |
| `invite_shield_sanitize_shared` | `services/invite_cleanup_service.py`, `invite_policy_engine.py` | shared invite-code/guild sanitization helpers | **keep feature-owned** |

## Channel Builder ownership correction

The previous architecture note claiming Channel Builder routes were available only through `channel_builder_api_guard` is obsolete.

Current production path:

`stoney_verify.app` -> `api_new.server.start_api(bot)` -> `register_channel_builder_routes(app, sys.modules[__name__])`

`api_new/server.py` imports `register_channel_builder_routes` directly. `channel_builder_api_guard.py` is already removed and `tools/audit_channel_builder_queue.py` explicitly requires the old bridge/patcher files to remain absent.

Current feature-owned Channel Builder guard/helper chain:

| Module | Owner | Runtime effect | Disposition |
| --- | --- | --- | --- |
| `channel_builder_full_font_catalog_guard` | `api_new/channel_builder_routes.py` | extends the Channel Builder runtime font catalog | **keep feature-owned** |
| `channel_font_rename_queue_guard` | loaded by the full-font owner | queue-backed preview/apply/undo rename flow | **keep feature-owned** |
| `channel_font_menu_clarity_guard` | loaded by the full-font owner | patches wording/view presentation for the font flow | **keep feature-owned for now**; separate migration to canonical UI owner later |
| `setup_channel_font_mode_guard` | used by Channel Builder routes and font helpers | loads/saves channel font options and renders font-mode setup | **keep feature-owned** |

A full Channel Builder redesign is out of scope for this loader-retirement change.

## Historical bulk inventory: activation status by family

Every module below was present in the old `_STARTUP_GUARDS` tuple. The normal boot path does **not** iterate that tuple. Therefore the tuple itself provides no production ownership. Modules already identified above as independently live remain live through their explicit owners; all others remain untouched until a dedicated importer/behavior audit proves a safe disposition.

| Family | Historical modules | Independent live evidence in this audit | Bulk-loader side-effect risk | Recommendation |
| --- | --- | --- | --- | --- |
| Core/process/command runtime | `embed_literal_newline_guard`, `process_health`, `command_safety`, `global_interaction_trace_guard`, `interaction_action_lock_guard`, `slash_command_cleanup`, `runtime_safety`, `public_startup_scope`, `event_safety`, `shard_safety`, `job_dedupe` | confirmed live: `process_health`, `command_safety`, `interaction_action_lock_guard`, `runtime_safety`, `public_startup_scope`; `embed_literal_newline_guard` has test/tool references but no confirmed production importer; `slash_command_cleanup` is referenced by audits/other dormant guards rather than normal boot | very high: discord.py wrappers, import hooks, command-tree mutation, listeners | keep confirmed live owners; **leave others dormant / separate audit** |
| Command-surface/product compatibility | `public_verify_admin_command_skip`, `automod_public_guard`, `protection_center_command_guard`, `embed_builder_command_guard`, `share_router_guard`, `setup_overview_command_guard`, `dank_shield_branding_guard`, `production_command_surface_guard` | no bulk activation; canonical public command surface is registered by `commands_ext` and its explicit profile | high: command registration/pruning/response rewriting | **leave dormant / separate audit**; never rescue by bulk loading |
| Schema/config/queue bootstrap | `auto_schema_bootstrap`, `ticket_category_schema_bootstrap_guard`, `operation_queue_schema_guard`, `guild_operation_queue_guard`, `guild_config_write_safety`, `public_no_env_runtime_config` | schema modules are imported by tests/tools and some feature code as manifests/helpers; Supabase migrations remain the sole schema mutation authority | high: historical schema/config/queue mutation paths | retain only helper/manifests needed by current owners; **never bulk-activate**; separate consolidation audit |
| Spam/invite protection compatibility | `spam_guard_invite_hard_block`, `spam_guard_default_state_guard`, `spam_guard_invite_override_options`, `discord_invite_blocker_runtime_guard`, `invite_live_enforcer_guard`, `protection_invite_target_precedence_guard`, `protection_center_invite_controls_guard`, `protection_center_clear_categories_guard`, `protection_center_invite_simple_flow_guard` | current invite policy/cleanup paths import specific shared helpers directly; no evidence makes the whole historical chain a boot requirement | high: message deletion, invite policy, command/UI mutation, listeners | **leave dormant unless directly feature-owned**; separate protection audit before deletion |
| Member lifecycle / role / modlog compatibility | `member_lifecycle_router_guard`, `member_lifecycle_verify_runtime_hardening`, `member_lifecycle_audit_context_guard`, `profile_role_editor_guard`, `self_roles_command_guard`, `modlog_probot_parity_guard`, `vc_join_leave_modlog_labels_guard`, `setup_permission_repair_modlog_silence_guard`, `invite_intent_safety`, `alt_identity_link_safety`, `member_join_removal_safety`, `guild_role_order_guard`, `stoney_verify.members_new.role_state_compat_guard`, `setup_role_safety`, `stoney_verify.commands_ext.public_moderation_command_guard`, `member_activity_notices_db_safety`, `member_update_modlog`, `resource_modlog_coverage` | `member_lifecycle_router_guard` is explicitly feature-owned; other current role/member behavior has canonical owners elsewhere and must be evaluated one by one before deletion | high: listener duplication, role mutation, modlog duplication, compatibility monkey patches | keep confirmed feature owner; **leave remainder dormant / separate audit** |
| Verification compatibility | `basic_verify_panel_auto_refresh_guard`, `unverified_ticket_panel_flow`, `unverified_legacy_panel_patch_disable` | `unverified_ticket_panel_flow` is confirmed live transitively through `id_verify_allowlist_guard`; Basic Verify native runtime is otherwise owned by public verify modules | high: verification authorization/UI/listener behavior | keep confirmed live path; **leave historical extras dormant / separate verification compatibility audit** |
| Ticket runtime compatibility | `stoney_verify.tickets_new.guild_config_ticket_guard`, `stoney_verify.tickets_new.creation_category_guard`, `stoney_verify.tickets_new.channel_panel_repair`, `stoney_verify.tickets_new.category_enforcer`, `stoney_verify.tickets_new.sync_native_guard`, `stoney_verify.tickets_new.sync_alias_guard`, `stoney_verify.api_new.guild_config_guard`, `stoney_verify.tickets_new.panel_creation_guard_runtime`, `ticket_panel_doctor_command`, `ticket_panel_doctor_production_wording`, `ticket_forms_foundation_guard`, `ticket_category_setup_guard`, `ticket_action_lock_guard`, `stoney_verify.panel_bootstrap_runtime` | `ticket_forms_foundation_guard` and `ticket_category_setup_guard` are confirmed feature-owned; core `tickets_new` modules are also imported by the live app/ticket runtime independently of the bulk list | high: ticket creation/panel/sync/lock ownership and duplicate interactions | keep explicit owners; **leave compatibility entries dormant unless independently imported**; no mass deletion in this PR |
| VC compatibility | `vc_request_setup_clarity`, `vc_setup_one_press_fix`, `vc_per_guild_access_fix`, `vc_accept_claim_guard` | no bulk activation; current VC behavior must be traced in its own audit before removal | medium/high: UI/callback/claim behavior | **leave dormant / separate audit** |
| Setup compatibility | `setup_category_modal_compat`, `protection_pack_manual_import_guard`, `protection_import_button_patch` | no bulk activation; setup uses canonical `commands_ext`/`setup_ui` paths, with selected helpers imported directly where required | medium/high: setup UI/callback ownership | **leave dormant / separate audit** |

## Proven dead host path eligible for removal in this task

`usercustomize.py` -> `panel_menu_retry_guard` -> `public_ticket_panel_clean_hardening`

The final dependency was intentionally removed by DS-BACKLOG-027 after its stale-menu, duplicate-interaction, confirm-lock, and preflight behavior moved into the canonical ticket-panel owner. Existing ticket audits require the removed module to stay absent. `panel_menu_retry_guard.apply()` therefore cannot install today and the host hook silently swallows the failure. This is a proven dead path rather than a merely suspicious file.

Disposition:

- remove the `usercustomize.py` dynamic import/apply block;
- retire `panel_menu_retry_guard.py` after final reference verification;
- keep the canonical ticket-panel owner unchanged.

## Loader disposition

The correct outcome for this finding is **formal retirement of executable bulk loading**, not reactivation.

- Preserve the old list only as clearly documented inert historical metadata while downstream audits/tests are migrated.
- Remove `load_startup_guards()` / `load_all_startup_guards()` and loader-only `_LOADED` / `_ERRORS` state.
- Remove the obsolete loader compatibility alias from `sitecustomize.py`.
- Make startup diagnostics report the explicit boot-owner contract using already-loaded module state only.
- Diagnostics must never import missing guards as a repair action.
- Do not add replacement startup guards or host hooks.

## Separate follow-up debt

This finding intentionally does not collapse the remaining live monkey-patch/import-hook architecture. `process_health`, `runtime_safety`, `public_startup_scope`, command-tree wrappers, and feature-owned guard helpers need dedicated migrations into canonical owners if they are to be removed. Their existence is real runtime debt, but combining that migration with bulk-loader retirement would violate the one-subsystem change boundary and greatly increase regression risk.
