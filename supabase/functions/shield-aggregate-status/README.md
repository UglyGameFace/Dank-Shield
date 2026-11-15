# Shield read-only Dank Shield database bridge

This Supabase Edge Function exposes **only aggregate data** about the The 420 Lobby guild to the private Shield Control Center on Cloudflare. It does not serve ticket bodies, Discord member details, permissions, or administrative actions.

The function has `verify_jwt=false` because it has its own, mandatory Ed25519 request authentication. The raw public key is checked in and non-secret. Cloudflare's corresponding PKCS8 private signing key MUST be stored only as the encrypted `DANK_SHIELD_SIGNING_KEY` Pages production secret. No signing key or service-role key may be committed.

Each Cloudflare request signs `GET\n/shield-aggregate-status\n<13-digit epoch-ms>\n<32-hex nonce>`. The endpoint refuses missing/invalid signatures and timestamps outside ±60 seconds. Responses are no-store. Supabase's built-in `SUPABASE_SERVICE_ROLE_KEY` is used *inside the Edge Function only*. The browser and Cloudflare do not receive it.

The snapshots are **database evidence, not live bot health**. Discloud's localhost structured API stays inaccessible; no moderating or ticket-changing operations are connected. Maximum 1000 rows per source; `truncated` and `atLeast` report limitations.

To rotate the key, generate a new Ed25519 pair, change the checked-in public key, redeploy the function, then replace the Cloudflare encrypted signing key. Do not deploy one side with the other unavailable except during a maintenance window.
