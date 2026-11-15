// Read-only aggregated Dank Shield guild snapshot for Shield Control Center.
// This endpoint NEVER accepts a guild ID from the request or serves member/ticket content.
// Authenticate using short-lived Ed25519 signatures from the Cloudflare Shield server.
const PUBLIC_KEY_B64U = "U2OMgZuaVXAC3sNHnVITp7aED6WwSTjOtQReG7auOAw";
const GUILD_ID = "1514374173517152418";
const BOT_ID = "1458458091468226630";
// The bot normally writes every two minutes. Treat >= 6 minutes as stale.

function b64decode(s: string): Uint8Array {
  const raw = atob(s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "="));
  return Uint8Array.from(raw, x => x.charCodeAt(0));
}
function reply(status: number, data: Record<string, unknown>): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {"content-type":"application/json; charset=utf-8","cache-control":"private, no-store","x-content-type-options":"nosniff"}
  });
}
async function authorized(req: Request): Promise<boolean> {
  if (req.method !== "GET") return false;
  const ts = req.headers.get("x-shield-timestamp") || "";
  const nonce = req.headers.get("x-shield-nonce") || "";
  const signed = req.headers.get("x-shield-signature") || "";
  if (!/^\d{13}$/.test(ts) || !/^[a-f0-9]{32}$/.test(nonce) || !/^[A-Za-z0-9_-]{86}$/.test(signed)) return false;
  if (Math.abs(Date.now() - Number(ts)) > 60_000) return false;
  const key = await crypto.subtle.importKey("raw", b64decode(PUBLIC_KEY_B64U), {name:"Ed25519"}, false, ["verify"]);
  const message = new TextEncoder().encode("GET\n/shield-aggregate-status\n"+ts+"\n"+nonce);
  try { return await crypto.subtle.verify("Ed25519", key, b64decode(signed), message); }
  catch { return false; }
}
async function rows(table: string, params: URLSearchParams): Promise<Array<Record<string, unknown>>> {
  const base = Deno.env.get("SUPABASE_URL");
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!base || !key) throw Error("Missing server-side service credentials");
  const target = new URL("/rest/v1/"+table,base);
  target.search=params.toString();
  const result = await fetch(target, {
    method:"GET",
    headers:{"apikey":key,"Authorization":"Bearer "+key,"Accept":"application/json"},
    signal:AbortSignal.timeout(4500)
  });
  if (!result.ok) throw Error("Database read unavailable");
  const data: unknown = await result.json();
  if (!Array.isArray(data)) throw Error("Unexpected database response");
  return data as Array<Record<string, unknown>>;
}
Deno.serve(async (request: Request) => {
  if (request.method !== "GET") return reply(405,{ok:false,error:"method_not_allowed"});
  if (!await authorized(request)) return reply(401,{ok:false,error:"unauthorized"});
  const now = new Date();
  const cutoff = new Date(now.getTime() - 86_400_000).toISOString();
  const guild = "eq."+GUILD_ID;
  try {
    const [configs,tickets,activity,heartbeats] = await Promise.all([
      rows("guild_configs",new URLSearchParams({select:"guild_name,updated_at",guild_id:guild,limit:"1"})),
      rows("tickets",new URLSearchParams({select:"status",guild_id:guild,limit:"1000"})),
      rows("activity_feed_events",new URLSearchParams({select:"created_at",guild_id:guild,created_at:"gte."+cutoff,order:"created_at.desc",limit:"1000"})),
      rows("bot_status_heartbeats",new URLSearchParams({select:"status,last_seen_at,guild_count",bot_id:"eq."+BOT_ID,limit:"1"}))
    ]);
    if (configs.length !== 1) return reply(503,{ok:false,error:"guild_unavailable"});
    const ticketCounts: Record<string, number> = {open:0,claimed:0,closed:0,other:0};
    for (const ticket of tickets) {
      const status=String(ticket.status||"").toLowerCase();
      if (status==="open") ticketCounts.open++;
      else if (status==="claimed") ticketCounts.claimed++;
      else if (status==="closed") ticketCounts.closed++;
      else ticketCounts.other++;
    }
    const raw = heartbeats[0] || null;
    const seenAt = typeof raw?.last_seen_at === "string" ? raw.last_seen_at : null;
    const age = seenAt ? now.getTime() - Date.parse(seenAt) : Number.POSITIVE_INFINITY;
    const fresh = Number.isFinite(age) && age >= 0 && age < 360_000;
    const runtime = {
      status:!seenAt ? "not_reported" : !fresh ? "stale" : raw?.status === "online" ? "heartbeat_recent" : "reported_not_online",
      lastSeenAt:seenAt,
      guildCount:Number.isInteger(raw?.guild_count) && (raw.guild_count as number)>=0 ? raw.guild_count : null,
      evidence:"Supabase heartbeat (nominal interval 120 seconds); fresh evidence is not a live HTTP health probe."
    };
    return reply(200,{
      ok:true,source:"dank-shield-supabase",kind:"database_snapshot",guild:"The 420 Lobby",
      capturedAt:now.toISOString(),
      configUpdatedAt:configs[0].updated_at||null,
      tickets:{...ticketCounts,atLeast:tickets.length,truncated:tickets.length>=1000},
      activity24h:{atLeast:activity.length,truncated:activity.length>=1000,latestAt:activity[0]?.created_at||null},
      runtime
    });
  } catch {
    return reply(503,{ok:false,error:"source_unavailable"});
  }
});
