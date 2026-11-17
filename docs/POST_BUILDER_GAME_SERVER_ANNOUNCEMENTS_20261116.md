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

## Discord movie nights and regular community events

The original gaming layouts were missing obvious Discord social formats. The same unified **Pick a Layout** dropdown now includes eight additional event designs, 20 total (below Discord's 25-option maximum):

- **Movie Night:** film pick, permitted watch method, venue, time, and RSVP
- **Live Watch Party:** stream/esports/event info, meeting place and participation
- **Trivia Night:** topics, rules, teams, time, and play location
- **Karaoke:** song queue, microphone/voice rules, time and channel
- **Community Hangout:** casual voice lounge, topics, and newcomer guidance
- **Giveaway:** real prize and eligibility, entry steps, and close time
- **AMA / Q&A:** hosts, subjects, question instructions and schedule
- **Creative Contest:** theme, allowed submissions, judging, and deadline

Each design has its own title, accent, short promotion, organizer label, four usable fields, and footer. These are **editable announcements**, not automatic Discord Scheduled Events, giveaway winners, music playback, or movie streams. Staff must fill in a real time and location, and provide only watch methods or media access they have permission to use.

The shared **Look & Images → Event Details** form edits the four things members need to know: **when, where, what is happening, and how to join**. It updates corresponding event-specific fields rather than adding duplicates, and doesn't erase extra fields, existing art, URLs, or saved copy. Use **Write Message** for the event headline and custom description; use **Add Pictures** for permitted artwork, and **Add Buttons** to link a legitimate event/RSVP page. Publication still requires the existing explicit preview and confirmation.

To build The 420 Lobby movie night, select **Movie Night**, open **Event Details**, fill in the actual movie title/rating, date/time/timezone, authorized watch location and participation instructions, add a poster image if licensed for use, then preview and confirm in announcements. Do not promise simultaneous streaming access without verifying the platform and viewing rights.
