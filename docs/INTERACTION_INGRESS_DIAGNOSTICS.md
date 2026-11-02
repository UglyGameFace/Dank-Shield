# Optional Discord interaction-ingress diagnostics

`DANK_INTERACTION_INGRESS_TRACE=true` enables **temporary, privacy-safe** diagnostics for Discord interactions that time out or never reach the bot's command handlers. The default is **off**.

At bot startup, the existing process-health owner attaches supported `on_socket_event_type` and `on_interaction` listeners. The native command tree also records the supported `interaction_check` stage. Compare `RAW_INTERACTION_CREATE`, `INTERACTION_INGRESS`, and `tree_interaction_check` markers to distinguish gateway receipt, parsed dispatch, and command-tree entry.

Only bounded interaction type and command names are logged. The diagnostics deliberately omit user and guild IDs, message contents, command arguments, and component custom IDs. They do not change command routing, permission decisions, or handler results. The startup listener setting requires a bot restart to enable or disable. Leave it disabled in normal production operation.

These three markers cannot establish whether the handler completed or whether Discord delivered the final response. Missing stages are a debugging clue, not proof of an individual user's behavior.

The historical branch `fix/trace-application-command-ingress-20261007` used unconditional per-interaction logging with user IDs and component custom IDs. This replacement intentionally preserves its gateway/parsed/tree diagnostic purpose without those disclosure and overhead risks.
