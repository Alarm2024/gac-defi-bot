// 🪬🧿✝  GARDEN ANGEL — RELAY v18.0 (keyless, passive JSON relay)
// ─────────────────────────────────────────────────────────────────────────────
// This Worker is a PASSIVE DATA RELAY only:
//   • /health  → JSON status
//   • /prices  → read-only price JSON (X-API-Key gated if ORACLE_API_KEY is set)
//   • *        → clean JSON 404 (kills the old "Bot Live" plain-text banner)
//
// NO wallet key. NO signing. NO Telegram. NO payout. NO cron auto-trader.
// After deploying, delete PRIVATE_KEY / PAYOUT_PASSWORD / PAYOUT_RECIPIENT
// secrets from the Cloudflare dashboard — nothing here reads them.
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
      return jsonResponse({ status: 'ok', version: '18.0', ts: new Date().toISOString() });
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

    // ── Catch-all — clean JSON 404 (replaces the plain-text "Bot Live" banner
    // that produced "Expecting value: line 1 column 1 (char 0)" downstream) ─────
    return jsonResponse({
      ok      : false,
      service : 'garden-angel-relay',
      version : '18.0',
      error   : 'route not found',
      path    : url.pathname,
      ts      : new Date().toISOString(),
    }, 404);
  },
};
