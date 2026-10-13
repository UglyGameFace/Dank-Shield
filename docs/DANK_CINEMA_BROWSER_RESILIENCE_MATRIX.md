# Dank Cinema: browser settings and playback resilience

Dank Cinema supports **one** signed Theater player across mobile, desktop and
tablet browsers. Browser compatibility is capability-based, never keyed to
Samsung, Opera or Chromium user-agent strings.

## Runtime behavior

| Browser/device condition | Cinema behavior | Observable limit |
|---|---|---|
| System dark mode, browser force-dark | Cinema intentionally uses its own readable dark palette | Browser auto-dark algorithms may still be applied by the browser outside page control; do not apply filters to video |
| System high-contrast / forced-colors | Preserve native outline/readability and avoid contrast transforms on actual video and posters | Forced-color support is OS/browser dependent |
| Reduced-motion preference | Remove decorative motion only | Never alter video timeline or FFmpeg workers |
| Data Saver or 2G/3G and limited hardware hints | Degrade decorative visual quality in Auto mode | Browsers may hide network/memory/CPU hints; don't claim precise bandwidth, streaming bitrate or resources |
| Manual visual High / Standard / Lite | Never overridden by a later system setting event | Visual preference is not video source quality |
| Browser zoom, font size, desktop mode, orientation | Existing responsive CSS, ResizeObserver, visualViewport and stage stability | Must test 200% zoom, font enlargement and unusual aspect ratios |
| Background tabs, screen lock, PiP | Do not poll hidden pages; re-authorize room on resume and synchronize against authoritative clock | Tabs may be suspended, frozen or killed by browser/OS |
| Burst of pageshow, visibilitychange, online | One bounded recovery, not multiple source reloads | Cannot prevent OS discarding entire browser process |
| Autoplay / blocked unmuted audio | Play and Unmute gestures request audio; recovery offered after an actual browser failure | Site cannot bypass browser or OS autoplay policy |
| Battery saver / Opera GX resource limiters | Respect observable throttling and suspend non-essential effects | Website cannot read exact browser limiter settings or override CPU limits |
| Private browsing / blocked cookies | Existing authenticated signed playback fails closed with useful session errors | Cannot force cookies/storage to persist or bypass privacy settings |
| Tracking blocker / extensions, VPN/Private DNS | Show actual HTTP/media errors and reconnect where permitted | Cannot enumerate or override private extensions or VPN |
| Video codec / accelerated decode | Use real browser media capability hints and decoded-first-frame evidence where possible | canPlayType is advisory, does not establish successful playback |
| Fullscreen, PiP and casting | Feature-detect APIs and native state, never show fake controls | Device/browser receiver support varies |
| Old HTML/JS after deployment | Authenticated Theater HTML already sent with Cache-Control private, no-store | Service worker, proxy cache, captive portal and extensions may require independent troubleshooting |

## Automated validation

- CI Python test extracts the **rendered HTML's actual JavaScript functions**
  and runs them under Node against simulated media-query and connection events.
- Test forced color and reduced-motion changes, Save Data toggle, manual
  override durability, offline/hidden visibility, and deduplicated page wake.
- Preserve the existing native browser codec, audio/track selection, runtime
  health, cast feature gates and privacy/authorization tests.
- This is a behavioral regression suite, not a device/browser media farm.

## Real device/browser acceptance still required

On **Samsung Internet, Chrome, Edge, Firefox, Safari/WebKit, Opera, Opera GX,
Brave, Vivaldi** and representative Android/iPhone/tablet/desktop:
- Start actual H.264/AAC MP4 and VP9/Opus WebM, verify frame and audio.
- Test native HEVC where supported and unsupported MKV/HEVC rejection;
  test the correct fallback, not a fake successful status.
- Test AAC fallback with E-AC-3/DTS mixed audio, multiple tracks, English
  preference per user/guild, seeking, pause/resume, subtitles and time drift.
- Test system dark + force dark, forced contrast, 200% text zoom, reduced motion,
  data saver and device energy constraints.
- Test hidden tab + device lock + page restoration; preserve correct room
  host/viewer authority and progress.
- Test fullscreen, PiP and casting with real devices when available.
- Test private mode and content blockers; signed media must fail closed.
- Record rendered first frame, audible output, start latency, seek recovery,
  stable A/V sync, CPU/RAM and network costs. Do not certify just because
  the CSS renders or canPlayType returns probably.

**Rollout:** User currently tests on Android/Samsung Browser. Automated JS tests
are not equivalent to full Opera/Opera GX or Safari/Firefox audio/video tests.
Do not claim universal codec/DRM/hardware compatibility and do not enable
unbounded FFmpeg video encoding on the same Discloud bot.
