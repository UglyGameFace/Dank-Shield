# Soundboard Studio (Discord native)

Dank Shield provides a mobile-friendly workflow for creating native **server Soundboard sounds**.
It uses Discord's Soundboard API, not a simulated music bot or an extra voice connection.

## Upload from Android, iOS, or desktop

1. In the target server, run `/dank upload`.
2. Choose **Soundboard Clip** for `asset`.
3. Attach an MP3, OGG, WAV, M4A, AAC, FLAC, OPUS, MP4, MOV, or WEBM file.
4. Optionally attach a second and third file. Set `trim_start` to start each clip
   later in the recording; it applies to the whole batch (0–300 seconds).
5. Tap **Preview audio**, optionally **Edit** the name/emoji/playback volume,
   then **Publish clip** or **Publish all**.

No sound is uploaded until the publish action. The review is ephemeral and
expires in 10 minutes. Prepared sound bytes are discarded on timeout or cancel.

## Browse and manage

Open `/dank home` → **Voice & Accessibility** → **Soundboard Studio**,
or use Find Feature and search for *soundboard*.

The Studio browses Discord's actual server sound list, with pagination and
refresh. Choose a sound to edit name, emoji, volume, or delete it. Deleting
requires a separate confirmation. Permission changes are checked again at
each sensitive action.

## Permissions

- Uploading requires the user's **Create Expressions** permission, and
  the bot's **Create Expressions** permission.
- Editing or deleting requires both the user's and bot's
  **Manage Expressions** permission.
- The Studio is server-only and every interaction is owner/guild scoped.
- Audit-log reasons are provided to Discord, permitting Dank Shield's
  existing self-action authorization and AntiNuke provenance tracking.
- Server owners control these permissions through native Discord roles.

## Audio safety and constraints

Discord's native Soundboard accepts at most **5.2 seconds** and **512 KB**
for each MP3/OGG sound. Dank Shield encodes a 5-second mono MP3 at 64kbps,
normalizes loudness, and checks output size before presenting a preview.

- Input is capped at **8 MiB per file**, **three files per interaction**.
- It runs with at most two active FFmpeg conversions, 20-second process
  timeouts, and a short-lived temporary output file.
- The input is streamed via a pipe and FFmpeg's permitted input protocols
  are restricted to `pipe`, preventing nested file and network reads.
- No external URL download, user-supplied FFmpeg arguments, cross-server
  catalog, permanent asset storage, or separate audio database.
- Existing voice moderation, Share Router, AntiNuke, and app-command
  names remain unchanged.

## Deployment verification

The code assumes the Discloud `APT=canvas, ffmpeg` package remains installed.
After the PR passes checks and lands on main, verify one mobile upload,
a longer file with `trim_start`, a three-file batch, an invalid/corrupt
file, permission denial, manager edit/delete, and the AntiNuke audit log
on the running Discloud bot. A passing CI job is not proof of live deployment.
