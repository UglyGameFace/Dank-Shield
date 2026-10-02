from __future__ import annotations

"""Signed synchronized web player for Dank Shield Movie Night."""

import hashlib
import hmac
import html
import json
import os
import time
from typing import Any, Optional
from urllib.parse import urlencode

from aiohttp import web

from stoney_verify.movie_night import MovieNightRoom, get_movie_night_manager
from stoney_verify.torrent_streaming import get_torrent_manager


def _secret() -> str:
    return str(os.getenv("DANK_TORRENT_STREAM_SECRET", "") or "").strip()


def _public_base() -> str:
    return str(os.getenv("DANK_MEDIA_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")


def _signature(room_id: str, user_id: int, expires: int) -> str:
    key = _secret().encode("utf-8")
    payload = f"movie-night:{room_id}:{int(user_id)}:{int(expires)}".encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def movie_night_watch_url(
    room_id: str,
    user_id: int,
    *,
    ttl_seconds: int = 6 * 60 * 60,
) -> str:
    base = _public_base()
    secret = _secret()
    if not base or not secret or not room_id or int(user_id) <= 0:
        return ""
    expires = int(time.time()) + max(300, min(int(ttl_seconds), 21600))
    query = urlencode(
        {
            "uid": int(user_id),
            "exp": expires,
            "sig": _signature(str(room_id), int(user_id), expires),
        }
    )
    return f"{base}/movie/{room_id}/watch?{query}"


def _validate_access(
    room_id: str,
    user_id: str,
    expires: str,
    signature: str,
) -> Optional[int]:
    secret = _secret()
    if not secret:
        return None
    try:
        uid = int(str(user_id or "").strip())
        exp = int(str(expires or "").strip())
    except Exception:
        return None
    now = int(time.time())
    if uid <= 0 or exp < now or exp > now + 21660:
        return None
    expected = _signature(str(room_id), uid, exp)
    if not signature or not hmac.compare_digest(expected, str(signature)):
        return None
    return uid


def _request_identity(request: web.Request) -> tuple[str, Optional[int]]:
    room_id = str(request.match_info.get("room_id", "") or "")
    uid = _validate_access(
        room_id,
        str(request.query.get("uid", "") or ""),
        str(request.query.get("exp", "") or ""),
        str(request.query.get("sig", "") or ""),
    )
    return room_id, uid


async def _room_and_user(
    request: web.Request,
) -> tuple[MovieNightRoom, int]:
    room_id, uid = _request_identity(request)
    if uid is None:
        raise web.HTTPUnauthorized(text="Invalid or expired Movie Night link.")
    room = get_movie_night_manager().get(room_id)
    if room is None:
        raise web.HTTPNotFound(text="Movie Night room not found.")
    return room, uid


def _open_vote(room: MovieNightRoom) -> Optional[dict[str, Any]]:
    unresolved = [vote for vote in room.votes.values() if not vote.resolved]
    if not unresolved:
        return None
    vote = max(unresolved, key=lambda item: float(item.created_at))
    manager = get_movie_night_manager()
    return {
        "vote_id": vote.vote_id,
        "action": vote.action,
        "yes": len(vote.yes & manager.active_viewers(room)),
        "no": len(vote.no & manager.active_viewers(room)),
        "required_yes": manager.required_yes_votes(room),
        "expires_at_monotonic": vote.expires_at,
    }


async def _state_payload(room: MovieNightRoom, user_id: int) -> dict[str, Any]:
    movie_manager = get_movie_night_manager()
    torrent_manager = get_torrent_manager()
    session = await torrent_manager.get(room.stream_token) if room.stream_token else None

    candidate = room.candidates.get(room.current_candidate_id) if room.current_candidate_id else None
    variant = (
        candidate.variants.get(room.current_variant_id)
        if candidate is not None and room.current_variant_id
        else None
    )

    title = candidate.title if candidate is not None else (
        session.release_metadata.get("title")
        if session is not None and isinstance(session.release_metadata, dict)
        else ""
    )
    source = ""
    if variant is not None and isinstance(variant.metadata, dict):
        release = variant.metadata.get("release_name")
        if isinstance(release, dict):
            source = str(release.get("source") or "")

    stream_url = torrent_manager.stream_url(session, ttl_seconds=21600) if session is not None else ""
    torrent_status = torrent_manager.status(session) if session is not None else {}
    viewer = room.viewers.get(int(user_id))
    sync_ready = bool(
        int(user_id) == int(room.host_id)
        or (viewer is not None and viewer.sync_ready)
    )
    sync_status = (
        "host"
        if int(user_id) == int(room.host_id)
        else "synced"
        if sync_ready
        else "joining"
    )
    buffer_quorum = movie_manager.buffer_quorum_viewers(room)

    return {
        "ok": True,
        "room_id": room.room_id,
        "title": str(title or "Movie Night"),
        "release_source": source,
        "state": room.playback_state,
        "position_seconds": round(room.current_position(), 3),
        "is_host": int(user_id) == int(room.host_id),
        "host_active": movie_manager.host_active(room),
        "viewer_count": len(movie_manager.active_viewers(room)),
        "buffer_quorum_count": len(buffer_quorum),
        "sync_status": sync_status,
        "sync_ready": sync_ready,
        "sync_target_position": round(room.current_position(), 3),
        "stream_token": room.stream_token,
        "stream_url": stream_url,
        "torrent": {
            "name": torrent_status.get("name", ""),
            "size": torrent_status.get("size", 0),
            "downloaded": torrent_status.get("downloaded", 0),
            "progress": torrent_status.get("progress", 0.0),
            "download_rate": torrent_status.get("download_rate", 0),
            "peers": torrent_status.get("peers", 0),
            "seeds": torrent_status.get("seeds", 0),
            "buffer": torrent_status.get("buffer", {}),
        },
        "open_vote": _open_vote(room),
        "ended": bool(room.ended),
    }


async def movie_night_state(request: web.Request) -> web.Response:
    room, uid = await _room_and_user(request)
    return web.json_response(await _state_payload(room, uid))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, float(value))
    except Exception:
        return float(default)


async def movie_night_heartbeat(request: web.Request) -> web.Response:
    room, uid = await _room_and_user(request)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    position = _float(payload.get("position_seconds"))
    duration = _float(payload.get("duration_seconds"))
    buffered = max(position, _float(payload.get("buffered_until_seconds"), position))
    paused = bool(payload.get("paused", True))

    torrent_manager = get_torrent_manager()
    movie_manager = get_movie_night_manager()
    session = await torrent_manager.get(room.stream_token) if room.stream_token else None
    viewer_before = room.viewers.get(int(uid))
    joining = bool(
        session is not None
        and int(uid) != int(room.host_id)
        and (viewer_before is None or not viewer_before.sync_ready)
    )

    byte_position = 0
    buffered_byte = 0
    sync_buffer_target_seconds = 0.0
    if session is not None and duration > 0:
        byte_position = int(min(1.0, position / duration) * session.file_size)
        buffered_byte = int(min(1.0, buffered / duration) * session.file_size)

        if joining:
            target_seconds = max(0.0, room.current_position())
            target_byte = int(
                min(1.0, target_seconds / duration) * session.file_size
            )
            target_end = min(
                session.file_size - 1,
                target_byte + 1024 * 1024 - 1,
            )
            plan = torrent_manager.prepare_playback_request(
                session,
                target_byte,
                target_end,
            )
            sync_buffer_target_seconds = min(
                15.0,
                max(8.0, float(getattr(plan, "target_seconds", 8.0) or 8.0)),
            )
            prioritize_end = min(
                session.file_size - 1,
                target_byte + max(
                    1024 * 1024,
                    int(getattr(plan, "target_bytes", 0) or 0),
                ),
            )
            torrent_manager.prioritize_range(
                session,
                target_byte,
                prioritize_end,
                readahead_bytes=max(
                    1024 * 1024,
                    int(getattr(plan, "target_bytes", 0) or 0),
                ),
            )

    movie_manager.heartbeat(
        room.room_id,
        user_id=uid,
        position_seconds=position,
        byte_position=byte_position,
        buffered_until_byte=buffered_byte,
        paused=paused,
        buffered_until_seconds=buffered,
        media_duration_seconds=duration,
        sync_buffer_target_seconds=sync_buffer_target_seconds,
    )

    if session is not None:
        corridor = movie_manager.group_buffer_corridor(room.room_id)
        if corridor is not None:
            start, weakest_buffer_end, leader = corridor
            request_end = min(
                session.file_size - 1,
                max(start, weakest_buffer_end, leader),
            )
            plan = torrent_manager.prepare_playback_request(
                session,
                start,
                min(session.file_size - 1, start + 1024 * 1024 - 1),
            )
            target_end = min(
                session.file_size - 1,
                max(request_end, start + int(plan.target_bytes)),
            )
            torrent_manager.prioritize_range(
                session,
                start,
                target_end,
                readahead_bytes=plan.target_bytes,
            )

    return web.json_response(await _state_payload(room, uid))


async def movie_night_action(request: web.Request) -> web.Response:
    room, uid = await _room_and_user(request)
    if int(uid) != int(room.host_id):
        raise web.HTTPForbidden(text="Only the active Movie Night host controls playback.")

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    action = str(payload.get("action") or "").strip().lower()
    if action not in {"pause", "resume", "seek", "end"}:
        raise web.HTTPBadRequest(text="Unsupported Movie Night playback action.")

    action_payload: dict[str, Any] = {}
    if action == "seek":
        action_payload["seconds"] = _float(payload.get("seconds"))

    manager = get_movie_night_manager()
    manager.join_room(room.room_id, user_id=uid)
    manager.apply_host_action(
        room.room_id,
        host_id=uid,
        action=action,
        payload=action_payload,
    )
    return web.json_response(await _state_payload(room, uid))


def _watch_html(room_id: str, uid: int, query: str) -> str:
    safe_room = html.escape(room_id, quote=True)
    boot = json.dumps(
        {
            "roomId": room_id,
            "uid": uid,
            "query": query,
        },
        separators=(",", ":"),
    ).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>Dank Shield Movie Night</title>
<style>
:root {{ font-family: system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:#f5f7fb; background:#090b10; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; min-height:100vh; background:radial-gradient(circle at top,#252b3b 0,#10131b 38%,#090b10 72%); }}
main {{ width:min(1100px,100%); margin:auto; padding:18px; }}
header {{ display:flex; gap:12px; align-items:center; justify-content:space-between; flex-wrap:wrap; margin-bottom:14px; }}
h1 {{ font-size:1.15rem; margin:0; }}
.badge {{ background:#1d2330; border:1px solid #343d51; padding:7px 10px; border-radius:999px; font-size:.82rem; }}
.card {{ background:rgba(18,22,31,.92); border:1px solid #2d3546; border-radius:18px; padding:14px; box-shadow:0 18px 60px rgba(0,0,0,.35); }}
video {{ display:block; width:100%; max-height:72vh; background:#000; border-radius:12px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:8px; margin-top:12px; }}
.stat {{ background:#111620; border-radius:12px; padding:10px; min-height:58px; }}
.stat b {{ display:block; font-size:.75rem; color:#98a2b8; margin-bottom:4px; }}
.controls {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }}
button {{ border:1px solid #39445a; background:#20283a; color:#fff; padding:10px 14px; border-radius:11px; font-weight:700; }}
button:disabled {{ opacity:.45; }}
#sync {{ background:#315bd8; }}
#notice {{ margin-top:10px; color:#bec6d7; min-height:1.5em; }}
small {{ color:#8994aa; }}
</style>
</head>
<body>
<main>
<header>
  <div><h1>🎬 Dank Shield Movie Night</h1><small>Room {safe_room}</small></div>
  <span class="badge" id="role">Connecting…</span>
</header>
<section class="card">
  <video id="video" controls playsinline preload="metadata"></video>
  <div class="controls">
    <button id="sync">Tap to Sync</button>
    <button id="play" disabled>Play</button>
    <button id="pause" disabled>Pause</button>
  </div>
  <div id="notice"></div>
  <div class="grid">
    <div class="stat"><b>Title</b><span id="title">Movie Night</span></div>
    <div class="stat"><b>Room</b><span id="state">—</span></div>
    <div class="stat"><b>Viewers</b><span id="viewers">0</span></div>
    <div class="stat"><b>Torrent</b><span id="progress">0%</span></div>
    <div class="stat"><b>Seeds / Peers</b><span id="peers">0 / 0</span></div>
    <div class="stat"><b>Buffer target</b><span id="buffer">—</span></div>
  </div>
</section>
</main>
<script>
const BOOT={boot};
const video=document.getElementById("video");
const notice=document.getElementById("notice");
let lastToken="";
let remoteApply=false;
let lastState=null;

function api(path) {{ return path+"?"+BOOT.query; }}
async function jsonFetch(path, options={{}}) {{
  const response=await fetch(api(path), {{
    cache:"no-store",
    headers:{{"Content-Type":"application/json"}},
    ...options
  }});
  if(!response.ok) throw new Error(await response.text());
  return response.json();
}}
function bufferedEnd() {{
  if(!video.buffered || !video.buffered.length) return video.currentTime || 0;
  return video.buffered.end(video.buffered.length-1);
}}
function fmtRate(n) {{
  if(!n) return "0 B/s";
  const u=["B/s","KiB/s","MiB/s","GiB/s"]; let x=n,i=0;
  while(x>=1024&&i<u.length-1){{x/=1024;i++;}}
  return x.toFixed(i?1:0)+" "+u[i];
}}
async function applyState(s) {{
  lastState=s;
  document.getElementById("title").textContent=(s.title||"Movie Night")+(s.release_source?" • "+s.release_source:"");
  document.getElementById("state").textContent=s.state||"—";
  document.getElementById("viewers").textContent=String(s.viewer_count||0);
  document.getElementById("role").textContent=
    s.is_host?"Host":(s.sync_status==="joining"?"Joining…":"Synced Viewer");
  const t=s.torrent||{{}};
  document.getElementById("progress").textContent=((t.progress||0)*100).toFixed(1)+"% • "+fmtRate(t.download_rate||0);
  document.getElementById("peers").textContent=String(t.seeds||0)+" / "+String(t.peers||0);
  const b=t.buffer||{{}};
  document.getElementById("buffer").textContent=b.target_seconds?Number(b.target_seconds).toFixed(0)+"s":"adaptive";

  document.getElementById("play").disabled=!s.is_host;
  document.getElementById("pause").disabled=!s.is_host;

  if(s.stream_token && s.stream_url && s.stream_token!==lastToken) {{
    lastToken=s.stream_token;
    video.src=s.stream_url;
    video.load();
  }}
  if(!s.stream_url) {{
    notice.textContent="Waiting for the host to choose media.";
    return;
  }}

  const target=Number(s.position_seconds||0);
  const drift=Math.abs((video.currentTime||0)-target);
  remoteApply=true;
  try {{
    if(drift>1.75 && Number.isFinite(target)) video.currentTime=target;
    if(s.sync_status==="joining") {{
      if(!video.paused) video.pause();
      notice.textContent=
        "Joining Movie Night… buffering around "+Math.floor(target/60)+":"+
        String(Math.floor(target%60)).padStart(2,"0")+
        " without pausing the room.";
    }} else {{
      if((s.state==="paused" || s.state==="buffering") && !video.paused) video.pause();
      if(s.state==="buffering") notice.textContent="Buffering the group for smoother playback…";
      if(s.state==="playing" && video.paused) {{
        try {{ await video.play(); notice.textContent=""; }}
        catch(_) {{ notice.textContent="Tap Sync once to allow synchronized playback."; }}
      }}
    }}
    if(s.ended) {{
      video.pause();
      notice.textContent="Movie Night has ended.";
    }}
  }} finally {{
    setTimeout(()=>{{remoteApply=false;}},150);
  }}

  if(s.open_vote && s.sync_status!=="joining") {{
    notice.textContent="Vote open: "+s.open_vote.action+" • "+s.open_vote.yes+"/"+s.open_vote.required_yes+" yes. Open /movie in Discord to vote.";
  }}
}}
async function poll() {{
  try {{ await applyState(await jsonFetch("/movie/"+BOOT.roomId+"/state")); }}
  catch(err) {{ notice.textContent="Sync error: "+String(err.message||err); }}
}}
async function heartbeat() {{
  try {{
    await jsonFetch("/movie/"+BOOT.roomId+"/heartbeat", {{
      method:"POST",
      body:JSON.stringify({{
        position_seconds:video.currentTime||0,
        duration_seconds:Number.isFinite(video.duration)?video.duration:0,
        buffered_until_seconds:bufferedEnd(),
        paused:video.paused
      }})
    }});
  }} catch(_) {{}}
}}
async function hostAction(action, extra={{}}) {{
  if(!lastState || !lastState.is_host || remoteApply) return;
  try {{
    await applyState(await jsonFetch("/movie/"+BOOT.roomId+"/action", {{
      method:"POST",
      body:JSON.stringify({{action,...extra}})
    }}));
  }} catch(err) {{ notice.textContent="Control error: "+String(err.message||err); }}
}}
document.getElementById("sync").onclick=async()=>{{
  if(lastState) {{
    if(Math.abs((video.currentTime||0)-Number(lastState.position_seconds||0))>0.5)
      video.currentTime=Number(lastState.position_seconds||0);
    if(lastState.state==="playing") {{
      try {{ await video.play(); notice.textContent="Synced."; }} catch(_) {{}}
    }}
  }}
}};
document.getElementById("play").onclick=()=>hostAction("resume");
document.getElementById("pause").onclick=()=>hostAction("pause");
video.addEventListener("play",()=>{{ if(!remoteApply && lastState?.is_host) hostAction("resume"); }});
video.addEventListener("pause",()=>{{ if(!remoteApply && lastState?.is_host) hostAction("pause"); }});
video.addEventListener("seeked",()=>{{ if(!remoteApply && lastState?.is_host) hostAction("seek",{{seconds:video.currentTime||0}}); }});
video.addEventListener("error",()=>{{
  if(lastState?.stream_url) {{
    notice.textContent="Buffering source pieces… retrying.";
    setTimeout(()=>{{ video.src=lastState.stream_url; video.load(); }},2500);
  }}
}});
poll();
setInterval(poll,2000);
setInterval(heartbeat,3000);
</script>
</body>
</html>"""


async def movie_night_watch(request: web.Request) -> web.Response:
    room, uid = await _room_and_user(request)
    query = urlencode(
        {
            "uid": str(request.query.get("uid", "") or ""),
            "exp": str(request.query.get("exp", "") or ""),
            "sig": str(request.query.get("sig", "") or ""),
        }
    )
    return web.Response(
        text=_watch_html(room.room_id, uid, query),
        content_type="text/html",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'self'; "
                "script-src 'unsafe-inline'; "
                "style-src 'unsafe-inline'; "
                "media-src 'self'; "
                "connect-src 'self'; "
                "img-src 'none'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            ),
        },
    )


def register_movie_night_public_routes(app: web.Application) -> None:
    app.router.add_get("/movie/{room_id}/watch", movie_night_watch)
    app.router.add_get("/movie/{room_id}/state", movie_night_state)
    app.router.add_post("/movie/{room_id}/heartbeat", movie_night_heartbeat)
    app.router.add_post("/movie/{room_id}/action", movie_night_action)


__all__ = [
    "movie_night_watch_url",
    "register_movie_night_public_routes",
]
