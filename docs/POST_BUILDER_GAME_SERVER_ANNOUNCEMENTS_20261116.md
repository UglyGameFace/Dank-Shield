# Post Builder: interaction timing and gaming announcement layouts

## Why this change

The Android Discord screenshot from November 16, 2026 shows a private Post Builder using the old Feature Showcase layout. The **Look & Images** action produced a visible `Dank Shield didn't respond in time` message. There was no matching callback traceback in the supplied evidence, so this is a **timing mitigation**, not a claim that all possible runtime failures are resolved.

Before this change, `_menu` and `_home` entered the autosave/render path before acknowledging a component interaction. Navigation now acknowledges immediately using Discord's deferred message-update response. Modal and command origins defer ephemerally; already-acknowledged interactions are not acknowledged a second time. Autosave retains its exact owner/version CAS, protected drafts and fail-closed error notice. If a private panel has actually expired after 15 minutes, a stale component cannot be revived by a callback; the editor footer directs staff to reopen Post Builder, which restores autosaved drafts.

## Gaming-specific layouts

The existing six editable community presets remain. Six new, text-first and mobile-friendly themes are available from **Pick a Layout**:

- Minecraft Server Relaunch: comeback headline, address, supported editions, actual server features and joining instructions
- Modded Adventure: featured mods, progression and installation/setup requirements
- Multiplayer Game Night: date/time, platform, voice location and RSVP
- Tournament / Challenge: format, confirmed times/prizes, participation rules
- Server Update & Patch Notes: new features, confirmed fixes, compatibility and next plans
- Players Wanted: game/community pitch, supported platforms, how to join

Each theme uses a distinct embed accent color, visually grouped fields, a clear headline and a footer. No invented images, player counts, invites, scheduled times or automatic role pings. Every theme is validated by the existing Discord limits, remains private until Preview & Post confirmation, and supports existing Undo, saved templates, brand styles, media and link buttons.

**Look & Images → Game Server Details** opens a four-input mobile form to edit address, editions/platforms, features/mods and join instructions without opening Extra Text Sections. Existing non-game fields and artwork are preserved; the same bounded `StudioSession.apply` validates everything. Existing values longer than the quick modal's limits are left intact rather than truncated.

## Example: The 420 Lobby Minecraft comeback

The user supplied the public server address `23.109.125.116:20229` and named Cobblemon. They previously identified Geyser/Floodgate and GrimAC. To create their private draft:

1. Pick **Minecraft • Server Relaunch**.
2. Open **Look & Images → Game Server Details** and set the address to `23.109.125.116:20229`.
3. List **Cobblemon** and only other currently deployed mods/plugins. Describe Java/Bedrock compatibility only after checking client versions, IP/port and Floodgate/Geyser endpoint; do not assume the Bedrock port matches the Java port.
4. Update the headline to `💚 THE 420 LOBBY MINECRAFT IS BACK`, add a gameplay screenshot or server banner, and preview on Android.
5. Select the announcements channel and explicitly confirm the post. No announcement is published by this patch.

## Validation and rollout

Run the exact-head Toke, Embed Studio, Mobile Panel, full Python suite, SQL and public command contracts before merging. Post-merge GitHub checks and Discloud live SHA require independent verification. In production, test Look & Images, Pick a Layout, Game Server Details, modal submit, Undo, Save/Restore, and Preview & Post using a fresh and a recovered private editor. An expired private panel still must be reopened.

No new worker, database migration, persistent ViewStore patch, command root, external service or environment setting.
