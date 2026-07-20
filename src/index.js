// 🪬🧿✝  GARDEN ANGEL — RELAY v18.3 (keyless, passive JSON relay + Telegram hop)
// ─────────────────────────────────────────────────────────────────────────────
// This Worker is a PASSIVE DATA RELAY only:
//   • /health           → JSON status
//   • /prices           → read-only price JSON (X-API-Key gated if ORACLE_API_KEY is set)
//   • /status           → GET: last status snapshot pushed by the AWS bot (public,
//                          read-only). POST: bot pushes its own snapshot (Bearer-gated
//                          by RELAY_AUTH_TOKEN). See v18.2 note below.
//   • /command          → dashboard→bot remote control (toggle ghost/mint, trigger a
//                          hunt). Both GET and POST Bearer-gated by RELAY_AUTH_TOKEN —
//                          unlike /status this can change bot behavior. See v18.3 note.
//   • /bot<token>/<method> → dumb pass-through to api.telegram.org/bot<token>/<method>
//                          (Bearer-gated by RELAY_AUTH_TOKEN — see below)
//   • *                 → clean JSON 404 (kills the old "Bot Live" plain-text banner)
//
// NO wallet key. NO signing. NO payout. NO cron auto-trader.
// After deploying, delete PRIVATE_KEY / PAYOUT_PASSWORD / PAYOUT_RECIPIENT
// secrets from the Cloudflare dashboard — nothing here reads them.
//
// v18.1 — RESTORES Telegram forwarding, removed in the v18.0 rewrite (see
// "route not found" 404s in production logs — the Python bot's
// TELEGRAM_PROXY_URL(S) still points TELEGRAM_PROXY_URL/TELEGRAM_PROXY_URLS
// at this Worker's own domain expecting it to relay sendMessage/getUpdates/
// getMe, but v18.0's router had no route for any of that at all, so every
// call 404'd — 100% of the time, not flaky). This adds back a MINIMAL,
// keyless pass-through: the caller supplies their own bot token in the URL
// path exactly as they would calling Telegram directly, this Worker stores
// none of it, and simply forwards method/body/response byte-for-byte. Gated
// by the same RELAY_AUTH_TOKEN bearer secret telegram_client.py and
// report_channel.py already send to every proxy route (see
// telegram_client.py's _route_headers()) so this can't be used as an open
// relay for arbitrary third-party bot tokens — set it once with
// `wrangler secret put RELAY_AUTH_TOKEN` to the SAME value already
// configured for the Python bot.
//
// v18.2 — adds /status: the AWS-deployed bot (post-migration, see
// Garden-Angel-Terminal's bot.py _status_heartbeat_loop) POSTs a compact
// status snapshot here every ~30s; the Hugging Face Space's DASHBOARD_ONLY
// dashboard GETs it back so it can show real numbers again instead of a
// permanent "runs on a separate deployment" placeholder. Stored in the
// existing BOT_KV namespace under a single fixed key — last-write-wins,
// no history kept.
//
// v18.3 — adds /command, the reverse direction of /status: the Hugging
// Face dashboard's buttons (Ghost Mode, Mint Mode, Start Hunt Scan) POST a
// command here when clicked; the AWS bot polls (GET) every ~10s, runs the
// exact same CommandHandlers method its own Telegram commands already use,
// and the command is deleted on read so it can never double-fire. Gated by
// RELAY_AUTH_TOKEN on both sides — this one is never public.
// ─────────────────────────────────────────────────────────────────────────────

import { PriceService } from './services/price.js';

function jsonResponse(obj, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json', ...extraHeaders },
  });
}

// Constant-time string comparison for the RELAY_AUTH_TOKEN bearer check —
// a plain `!==` returns as soon as it finds the first differing character,
// which leaks (via response timing) how many leading characters a guess got
// right. Length is checked up front (rather than folded into the loop) so a
// caller sending a huge Authorization header can't force the XOR loop to run
// proportional to their input size — worst case here is bounded by the
// token's own (short, fixed) length, not an attacker-controlled one.
// Leaking whether the length matches isn't a practical risk for a
// high-entropy secret token.
function _timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let mismatch = 0;
  for (let i = 0; i < a.length; i++) {
    mismatch |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return mismatch === 0;
}

export default {

  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // ── /health ───────────────────────────────────────────────────────────────
    if (url.pathname === '/health') {
      return jsonResponse({ status: 'ok', version: '18.3', ts: new Date().toISOString() });
    }

    // ── /prices — read-only price JSON ─────────────────────────────────────────
    // Returns { "prices": { "ASSET": { price, source, critical, change24h } } }
    // — the exact shape price_client.py's _try_oracle_url() expects.
    if (url.pathname === '/prices') {
      const assetsParam = url.searchParams.get('assets') || '';
      const assets = assetsParam.split(',').map(a => a.trim().toUpperCase()).filter(Boolean);

      if (!assets.length) {
        return jsonResponse({ error: 'missing assets param' }, 400);
      }

      const apiKey = request.headers.get('X-API-Key');
      if (env.ORACLE_API_KEY && apiKey !== env.ORACLE_API_KEY) {
        return jsonResponse({ error: 'unauthorized' }, 401);
      }

      try {
        const price   = new PriceService();
        const results = await price.getMultiPrice(assets);

        const prices = {};
        for (const asset of assets) {
          const entry = results[`${asset}USDT`];
          if (entry) {
            prices[asset] = {
              price     : entry.price,
              source    : entry.source ?? 'worker',
              critical  : entry.critical ?? false,
              change24h : entry.change24h ?? null,
            };
          }
        }

        return jsonResponse({ prices });
      } catch (e) {
        console.error('/prices error:', e.message);
        return jsonResponse({ error: e.message }, 500);
      }
    }

    // ── /status — DASHBOARD_ONLY status relay (v18.2) ──────────────────────────
    // The AWS-deployed bot POSTs its own live status here every ~30s (gated by
    // RELAY_AUTH_TOKEN, same secret as the /bot Telegram relay below); the
    // Hugging Face Space's dashboard (running DASHBOARD_ONLY, no in-process
    // bot of its own) GETs it back to show real numbers instead of a
    // permanent placeholder. Read side is intentionally public/keyless — this
    // is non-sensitive operational data (uptime, scan counts, mode flags),
    // never wallet keys or trade details, matching /prices' own
    // read-is-public posture.
    if (url.pathname === '/status') {
      if (!env.BOT_KV) {
        return jsonResponse({ ok: false, description: 'KV storage not configured' }, 503);
      }
      if (request.method === 'POST') {
        if (!env.RELAY_AUTH_TOKEN) {
          return jsonResponse({ ok: false, description: 'relay auth not configured' }, 503);
        }
        const authHeader = request.headers.get('Authorization') || '';
        const bearer = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : '';
        if (!_timingSafeEqual(bearer, env.RELAY_AUTH_TOKEN)) {
          return jsonResponse({ ok: false, description: 'unauthorized' }, 401);
        }
        let body;
        try {
          body = await request.json();
        } catch (e) {
          return jsonResponse({ ok: false, description: 'invalid JSON body' }, 400);
        }
        if (typeof body !== 'object' || body === null || Array.isArray(body)) {
          return jsonResponse({ ok: false, description: 'JSON body must be a plain object' }, 400);
        }
        const record = { ...body, received_at: Math.floor(Date.now() / 1000) };
        await env.BOT_KV.put('garden_angel_status', JSON.stringify(record));
        return jsonResponse({ ok: true });
      }

      if (request.method === 'GET') {
        const raw = await env.BOT_KV.get('garden_angel_status');
        if (!raw) {
          return jsonResponse({ available: false });
        }
        let record;
        try {
          record = JSON.parse(raw);
        } catch (e) {
          return jsonResponse({ available: false });
        }
        return jsonResponse({ available: true, ...record });
      }

      return jsonResponse({ ok: false, description: 'method not allowed' }, 405);
    }

    // ── /command — DASHBOARD_ONLY remote control relay (v18.3) ──────────────────
    // The Hugging Face Space's dashboard POSTs a control command here when one
    // of its buttons is clicked (toggle ghost/mint mode, trigger a hunt scan)
    // — it has no in-process bot to act on those clicks directly now that the
    // bot runs standalone on AWS. The AWS bot polls (GET) every ~10s, executes
    // at most one pending command, and it's deleted immediately so it can
    // never run twice. Unlike /status, this can change bot behavior, so BOTH
    // directions require RELAY_AUTH_TOKEN — it is never public.
    if (url.pathname === '/command') {
      if (!env.BOT_KV) {
        return jsonResponse({ ok: false, description: 'KV storage not configured' }, 503);
      }
      if (!env.RELAY_AUTH_TOKEN) {
        return jsonResponse({ ok: false, description: 'relay auth not configured' }, 503);
      }
      const authHeader = request.headers.get('Authorization') || '';
      const bearer = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : '';
      if (!_timingSafeEqual(bearer, env.RELAY_AUTH_TOKEN)) {
        return jsonResponse({ ok: false, description: 'unauthorized' }, 401);
      }

      if (request.method === 'POST') {
        let body;
        try {
          body = await request.json();
        } catch (e) {
          return jsonResponse({ ok: false, description: 'invalid JSON body' }, 400);
        }
        if (typeof body !== 'object' || body === null || Array.isArray(body) || typeof body.action !== 'string') {
          return jsonResponse({ ok: false, description: 'JSON body must be an object with a string "action"' }, 400);
        }
        const record = { action: body.action, queued_at: Math.floor(Date.now() / 1000) };
        await env.BOT_KV.put('garden_angel_command', JSON.stringify(record));
        return jsonResponse({ ok: true });
      }

      if (request.method === 'GET') {
        const raw = await env.BOT_KV.get('garden_angel_command');
        if (!raw) {
          return jsonResponse({ available: false });
        }
        // Consume immediately so a command never runs twice.
        await env.BOT_KV.delete('garden_angel_command');
        let record;
        try {
          record = JSON.parse(raw);
        } catch (e) {
          return jsonResponse({ available: false });
        }
        return jsonResponse({ available: true, ...record });
      }

      return jsonResponse({ ok: false, description: 'method not allowed' }, 405);
    }

    // ── /bot<token>/<method> — Telegram Bot API relay (v18.1) ──────────────────
    // Dumb pass-through to https://api.telegram.org/bot<token>/<method>: same
    // HTTP method, same body, same query string, response returned unchanged.
    // No token is read from or stored in Worker secrets/KV — the caller's own
    // bot token travels only in the URL path, exactly as it would calling
    // Telegram directly, so this stays "keyless" from the Worker's own
    // perspective. Gated by RELAY_AUTH_TOKEN so this can't become an open
    // relay for anyone else's bot token.
    // Trailing slash tolerated (e.g. "/sendMessage/") — our own client never
    // sends one (see telegram_client.py's _build_url()), but a stray slash
    // shouldn't be a hard 404.
    const botMatch = url.pathname.match(/^\/bot([^/]+)\/([^/]+)\/?$/);
    if (botMatch) {
      if (!env.RELAY_AUTH_TOKEN) {
        return jsonResponse({ ok: false, description: 'relay auth not configured' }, 503);
      }
      const authHeader = request.headers.get('Authorization') || '';
      const bearer = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : '';
      if (!_timingSafeEqual(bearer, env.RELAY_AUTH_TOKEN)) {
        return jsonResponse({ ok: false, description: 'unauthorized' }, 401);
      }

      const [, botToken, method] = botMatch;
      const upstreamUrl = `https://api.telegram.org/bot${botToken}/${method}${url.search}`;

      try {
        const init = { method: request.method };
        if (request.method !== 'GET' && request.method !== 'HEAD') {
          // Stream the body straight through (request.body) instead of
          // buffering it into memory first — sendDocument can carry a real
          // file, and arrayBuffer()/text() would hold the whole thing in
          // Worker memory for no benefit. duplex: 'half' is required by the
          // fetch spec whenever the body is a ReadableStream — only set it
          // (and body) when there IS a stream; a bodyless POST (e.g.
          // Content-Length: 0) has request.body === null, and some fetch
          // implementations are picky about pairing a null body with duplex.
          init.headers = { 'Content-Type': request.headers.get('Content-Type') || 'application/json' };
          if (request.body) {
            init.body = request.body;
            init.duplex = 'half';
          }
        }
        const upstream = await fetch(upstreamUrl, init);
        const responseHeaders = {
          'Content-Type': upstream.headers.get('Content-Type') || 'application/json',
        };
        // Propagate Telegram's own rate-limit signal (429 responses also
        // carry retry_after in the JSON body, which telegram_client.py
        // already reads — this header is a cheap extra for anything that
        // only looks at headers).
        const retryAfter = upstream.headers.get('Retry-After');
        if (retryAfter) responseHeaders['Retry-After'] = retryAfter;
        // Stream the response back too, rather than buffering it with
        // .text() — same OOM/latency reasoning as the request side.
        return new Response(upstream.body, {
          status: upstream.status,
          headers: responseHeaders,
        });
      } catch (e) {
        // Same envelope shape Telegram itself uses for a failed call, so
        // telegram_client.py's non-JSON/route-failure handling (which
        // already expects {"ok": false, "description": ...}) treats an
        // upstream network hiccup the same way it treats any other
        // retryable route problem.
        return jsonResponse({ ok: false, description: `${e.name}: ${e.message}` }, 502);
      }
    }

    // ── Catch-all — clean JSON 404 (replaces the plain-text "Bot Live" banner
    // that produced "Expecting value: line 1 column 1 (char 0)" downstream) ─────
    return jsonResponse({
      ok      : false,
      service : 'garden-angel-relay',
      version : '18.3',
      error   : 'route not found',
      path    : url.pathname,
      ts      : new Date().toISOString(),
    }, 404);
  },
};
