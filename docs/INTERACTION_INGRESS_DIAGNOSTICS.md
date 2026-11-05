# Optional Discord interaction-ingress diagnostics

`DANK_INTERACTION_INGRESS_TRACE=true` enables **temporary, privacy-safe** diagnostics for Discord interactions that time out or never reach the bot's command handlers. The default is **off**.

At bot startup, the existing process-health owner attaches supported `on_socket_event_type` and `on_interaction` listeners. The native command tree also records the supported `interaction_check` stage. Compare `RAW_INTERACTION_CREATE`, `INTERACTION_INGRESS`, and `tree_interaction_check` markers to distinguish gateway receipt, parsed dispatch, and command-tree entry.

Only bounded interaction type and command names are logged. The diagnostics deliberately omit user and guild IDs, message contents, command arguments, and component custom IDs. They do not change command routing, permission decisions, or handler results. The startup listener setting requires a bot restart to enable or disable. Leave it disabled in normal production operation.

These three markers cannot establish whether the handler completed or whether Discord delivered the final response. Missing stages are a debugging clue, not proof of an individual user's behavior.

The optional trace also checks acknowledgement at 1.5 and 2.5 seconds, measured
against the interaction's creation time, so dispatch delays count toward the deadline.
`INTERACTION_ACK_LATE` includes structural command names such as
`dank.birthdays.setup`, the time since the observer started, and interaction age.
Arguments and component/modal contents are excluded. At most 64 acknowledgement
observers run concurrently. These observers do not defer, retry or execute actions.

The existing guild-config adapter also emits `CONFIG_DB_SLOW` for operations taking
at least 1.5 seconds, capped at one warning per 30 seconds. `queue_ms` measures time
waiting for an executor thread; `work_ms` includes the existing database operation
and its bounded storage retries. Compare these with `EVENT_LOOP_LAG` and
acknowledgement traces before attributing a timeout to PostgreSQL or thread contention.
This diagnostic does not change pool sizes or connection credentials.

The November 5 stabilization removes synchronous token-store and VC persistence
calls from the affected verification callbacks, token issuance, startup timer reload
and VC sweeper. Birthday and welcome storage paths acknowledge explicitly before I/O.
Date/template modals open immediately from the already-loaded panel snapshot; their
submit callbacks acknowledge separately. Full Discord/Discloud acceptance still needs
the deployed release fingerprint and a real command/button/modal canary.

The historical branch `fix/trace-application-command-ingress-20261007` used unconditional per-interaction logging with user IDs and component custom IDs. This replacement intentionally preserves its gateway/parsed/tree diagnostic purpose without those disclosure and overhead risks.
