// 🪬🧿✝  GARDEN ANGEL — RELAY v18.1 (keyless, passive JSON relay + Telegram hop)
// ─────────────────────────────────────────────────────────────────────────────
// This Worker is a PASSIVE DATA RELAY only:
//   • /health           → JSON status
//   • /prices           → read-only price JSON (X-API-Key gated if ORACLE_API_KEY is set)
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
// ─────────────────────────────────────────────────────────────────────────────

import { PriceService } from './services/price.js';

function jsonResponse(obj, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json', ...extraHeaders },
  });
}

export default {

  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // ── /health ───────────────────────────────────────────────────────────────
    if (url.pathname === '/health') {
      return jsonResponse({ status: 'ok', version: '18.1', ts: new Date().toISOString() });
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

    // ── /bot<token>/<method> — Telegram Bot API relay (v18.1) ──────────────────
    // Dumb pass-through to https://api.telegram.org/bot<token>/<method>: same
    // HTTP method, same body, same query string, response returned unchanged.
    // No token is read from or stored in Worker secrets/KV — the caller's own
    // bot token travels only in the URL path, exactly as it would calling
    // Telegram directly, so this stays "keyless" from the Worker's own
    // perspective. Gated by RELAY_AUTH_TOKEN so this can't become an open
    // relay for anyone else's bot token.
    const botMatch = url.pathname.match(/^\/bot([^/]+)\/([^/]+)$/);
    if (botMatch) {
      if (!env.RELAY_AUTH_TOKEN) {
        return jsonResponse({ ok: false, description: 'relay auth not configured' }, 503);
      }
      const authHeader = request.headers.get('Authorization') || '';
      const bearer = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : '';
      if (bearer !== env.RELAY_AUTH_TOKEN) {
        return jsonResponse({ ok: false, description: 'unauthorized' }, 401);
      }

      const [, botToken, method] = botMatch;
      const upstreamUrl = `https://api.telegram.org/bot${botToken}/${method}${url.search}`;

      try {
        const init = { method: request.method };
        if (request.method !== 'GET' && request.method !== 'HEAD') {
          // arrayBuffer, not text — sendDocument's body is multipart with
          // raw file bytes; decoding/re-encoding it as text would corrupt
          // anything non-UTF-8. Forwarding the original Content-Type header
          // (multipart boundary included) alongside the raw bytes keeps
          // both JSON and multipart calls intact.
          init.headers = { 'Content-Type': request.headers.get('Content-Type') || 'application/json' };
          init.body = await request.arrayBuffer();
        }
        const upstream = await fetch(upstreamUrl, init);
        const body = await upstream.text();
        return new Response(body, {
          status: upstream.status,
          headers: { 'Content-Type': upstream.headers.get('Content-Type') || 'application/json' },
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
      version : '18.1',
      error   : 'route not found',
      path    : url.pathname,
      ts      : new Date().toISOString(),
    }, 404);
  },
};
