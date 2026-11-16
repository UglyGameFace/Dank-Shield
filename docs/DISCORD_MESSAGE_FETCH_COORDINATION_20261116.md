# Discord message-fetch coordination (2026-11-16)

After resource-pressure PR #851 merged into `main` at `04730f44d94d30672312641220ea7c4dcb7bad43`, the next optimization is to consolidate repeated live message GETs.

The existing canonical `startup_guards.discord_api_safety.fetch_message_with_api_safety` already coalesces simultaneous fetches for the same channel/message and serializes different message IDs in a channel. This change makes the active native owners for profile cards, Smart Text, welcome posts, LFG boards, trusted advert cleanup, Toke attendance, spam controls, ticket webhook refresh and the durable Share Router queue consume that same implementation.

No new monkey patches, listeners, queues, workers or second Discord limiter. No broad message caching, because ownership and moderation flows must observe current Discord state. Errors, timeouts and existing member-authorization checks remain with their current owner.

Verification: exact PR-head CI and a real Discloud canary should report fewer repeated single-message 429s and `message_fetch_safety_snapshot().coalesced` increasing if concurrent fetches overlap. No guarantee of eliminating every Discord API limit; aggregate rate limits also apply to other routes. Issue #384 remains open until actual current production 429 rates are measured.
