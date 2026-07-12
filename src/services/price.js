// 🪬🧿✝️  PriceService – v17.7
// ─────────────────────────────────────────────────────────────────────────────
// v17.7 FIX — XRP not mapped, shipped proactively THIS time alongside the
//   Python side (scanner.py v18.15 / price_client.py v2.16) instead of
//   after the fact — CAKE's v17.6 fix above only landed once someone
//   noticed the static-$2.00-guess symptom in production logs, hours
//   after scanner.py's CAKE pair had already been live and blind to a
//   real price. Same pattern as v17.6: real token (Binance-Peg XRP,
//   independently cross-checked against CoinGecko/OKX/Uniswap/OKLink,
//   not just this Worker's own BscScan lookup — that address showed a
//   "displayed name does not match contract's Name function" warning,
//   confirmed as a known cosmetic quirk of BSC's early-2020 Binance-Peg
//   contract template rather than a red flag). CoinGecko id is "ripple";
//   Kraken lists XRP too.
//
// v17.7 FIXES (review + one correction to that review) — three CAKE
//   adjustments, in the order they actually happened:
//   1. COINBASE_ID.CAKE was 'CAKE-USD' — an automated reviewer (Gemini)
//      claimed Coinbase doesn't list CAKE for trading and this was set
//      to null on that claim.
//   2. KRAKEN_PAIR.CAKE was null on the wrong assumption Kraken doesn't
//      list it — verified it does (kraken.com/prices/pancakeswap,
//      pro.kraken.com/app/trade/cake-eur) and wired it to 'CAKEUSD'.
//   3. CORRECTION — a live `wrangler tail` of the deployed Worker (still
//      running the pre-#1 code) showed "CAKE = $1.38 [coinbase ✓]" —
//      direct production evidence Coinbase's CAKE-USD DOES work,
//      contradicting step 1's claim. Reverted back to 'CAKE-USD'.
//      Lesson: an automated review's confident claim still needs
//      checking against real evidence, same bar as any other change in
//      this file — it isn't automatically more trustworthy than a
//      memory-sourced guess would have been.
//
// v17.6 FIX — CAKE not mapped → getPrice('CAKE') throws "Unsupported asset"
//   immediately (no source is even attempted). Same root cause as v17.4's
//   WBTC gap and v17.2's WBNB gap below: the Python bot's scanner.py added
//   a new BSC scan pair (CAKE/USDT, v18.13) but this Worker's own lookup
//   tables were never updated to match, so /prices?assets=...CAKE... always
//   silently dropped CAKE from the response — confirmed in production via
//   the Python side's own logs: the oracle mirror consistently returned
//   "3/4 assets" (never CAKE), forcing every scan to fall through OKX/
//   CoinGecko/Binance (themselves separately rate-limited/geo-blocked) all
//   the way to a static $2.00 guess. CAKE is a real token (PancakeSwap's
//   own), not wrapped/pegged to anything already mapped here, so it gets
//   its own entries rather than an alias — CoinGecko id is
//   "pancakeswap-token" (NOT "cake", a different, unrelated coingecko id).
//   Kraken doesn't list CAKE, same treatment as BNB/WBNB above (null,
//   skipped cleanly). STATIC_FALLBACK ($2.00) matches price_client.py's
//   own last-resort value on the Python side, same cross-fallback
//   agreement principle v17.3 already established for ETH/WETH.
//
// v17.5 FIX — HTTP 451 (Binance geo-block, permanent for this Worker's IP
//   range/jurisdiction) was falling into the generic error path, so every
//   getPrice() call retried it 3x (800ms/1600ms backoff) via withRetry, and
//   the CircuitBreaker needed 3 of THOSE full retry-cycles (threshold=3)
//   before finally suspending Binance for 60s — up to 9 real doomed requests
//   burned per open/close cycle. 451 means "will never succeed from here,"
//   not "try again shortly" — it now short-circuits exactly like the
//   existing 429 handling: _fetchJSON tags it isPermanentBlock, withRetry
//   rethrows immediately with zero extra attempts, and _withSource calls the
//   new CircuitBreaker.forceOpen() to suspend Binance for 30 minutes instead
//   of the normal 60s transient-failure window. Directly cuts /prices
//   latency on every cycle this fires, since that wasted time was inline in
//   the same request price_client.py's Oracle tier was waiting on.
//
// v17.4 FIX — WBTC not mapped → getPrice('WBTC') throws "Unsupported asset"
//   immediately (no source is even attempted). Same root cause and same fix
//   as v17.2's WBNB gap below: the BSC-side scan pairs use the wrapped/pegged
//   symbol (WBTC, i.e. BTCB on BSC) while every lookup table here only had
//   the unwrapped BTC. Mirrors the WETH→ETH / WBNB→BNB aliasing pattern —
//   WBTC tracks the same USD price as BTC, so it's aliased to BTC's entries
//   across BINANCE_SYMBOL/COINGECKO_ID/KRAKEN_PAIR/COINBASE_ID/STATIC_FALLBACK.
//
// v17.3 FIX — STATIC_FALLBACK had ETH/WETH = 3500, badly stale and mismatched
//   against price_client.py's own last-resort table (_STATIC_PRICES = 1600).
//   The mismatch meant the two fallback layers of the same oracle chain could
//   disagree by 2x depending on which one served a given /prices request —
//   confusing at best, and dangerous for anything computing spread/profit off
//   it. Both fallback tables now agree: ETH/WETH = 1600.
//
// v17.2 FIX over v17.1  (one targeted change):
//
//   FIX — WBNB not mapped → price source "unknown" → BSC BUY always blocked.
//
//     The BSC chain config uses `base: 'WBNB'` (Wrapped BNB).
//     v17.1 had BNB in every lookup table but not WBNB, so:
//       priceService.getPrice('WBNB')   → throws  "Unsupported asset: WBNB"
//       priceService.isLive('WBNB')     → false
//       priceService.getPriceSource('WBNB') → null
//
//     The strategist's _validateDecision() reads source === null → adds error:
//       "WBNB price source is 'unknown' (not live). BUY blocked."
//
//     This validation error is BLOCKING — it fires on every BSC cycle regardless
//     of whether the trade would be profitable, so no BSC BUY can ever execute.
//
//     Fix: mirror the WETH → ETH aliasing pattern for WBNB → BNB.
//     WBNB IS BNB with an ERC-20 wrapper; they track the same price.
//
// All other logic is identical to v17.1 (User-Agent header, 4-source waterfall,
// Kraken + Coinbase fallback sources, auto-healing circuit breakers).
// ─────────────────────────────────────────────────────────────────────────────

const BINANCE_SYMBOL = {
  ETH:  'ETHUSDT',
  WETH: 'ETHUSDT',
  BNB:  'BNBUSDT',
  WBNB: 'BNBUSDT', // ✅ FIX v17.2 — WBNB is BNB wrapped; same Binance price feed
  BTC:  'BTCUSDT',
  WBTC: 'BTCUSDT', // ✅ FIX v17.4 — WBTC is BTC wrapped/pegged; same Binance price feed
  CAKE: 'CAKEUSDT', // ✅ FIX v17.6 — real, listed Binance pair (not an alias)
  XRP:  'XRPUSDT',  // ✅ FIX v17.7 — real, listed Binance pair
  MATIC:'MATICUSDT', // v18.0 — real, listed Binance pair (still 451-blocked from this Worker's IP, same as every symbol here; kept for completeness + the OKX/Coinbase/Kraken fallbacks below actually serve it)
};

// v18.0 — OKX instrument ids ({BASE}-USDT). NEW primary source: OKX's public
// REST API is NOT geo-blocked from this Worker's Cloudflare edge (Binance is,
// permanently — HTTP 451) and is not as aggressively rate-limited as
// CoinGecko's free tier (HTTP 429). Mirrors the Python side's _OKX_BASE
// (price_client.py v2.18) — only symbols confirmed listed on OKX spot.
const OKX_INST = {
  ETH:  'ETH-USDT',  WETH: 'ETH-USDT',
  BNB:  'BNB-USDT',  WBNB: 'BNB-USDT',
  BTC:  'BTC-USDT',  WBTC: 'BTC-USDT',
  CAKE: 'CAKE-USDT',
  XRP:  'XRP-USDT',
  GMT:  'GMT-USDT',
  FLOKI:'FLOKI-USDT',
  WOO:  'WOO-USDT',
  SOLV: 'SOLV-USDT',
  MATIC:'MATIC-USDT',
};

const COINGECKO_ID = {
  ETH:  'ethereum',
  WETH: 'ethereum',
  BNB:  'binancecoin',
  WBNB: 'binancecoin', // ✅ FIX v17.2
  BTC:  'bitcoin',
  WBTC: 'bitcoin', // ✅ FIX v17.4
  CAKE: 'pancakeswap-token', // ✅ FIX v17.6 — "cake" is a different, unrelated id
  XRP:  'ripple', // ✅ FIX v17.7
  // v18.0 — operator token expansion (2026-07-12). These IDs are mirrored
  //   verbatim from the Python side's already-in-production _CG_IDS
  //   (price_client.py v2.18) so the two halves of the same oracle can
  //   never disagree on which CoinGecko id a symbol maps to. Without these,
  //   getPrice() threw "Unsupported asset" for every one of them (see the
  //   gate fix in getPrice below) and the scanner ran BLIND on these pairs.
  //   SYRUP has no CoinGecko id — the scanner prices it from its own live
  //   pool, so it is intentionally absent here (not an oversight).
  GMT:    'stepn',
  SYN:    'synapse-2',
  BIFI:   'beefy-finance',
  TWT:    'trust-wallet-token',
  ALPACA: 'alpaca-finance',
  DEGO:   'dego-finance',
  LINA:   'linear',
  FLOKI:  'floki',
  BANANA: 'apeswap-finance',
  MATIC:  'matic-network',
  WOO:    'woo-network',
  SOLV:   'solv-protocol',
  LIT:    'litentry',
};

// Kraken uses slightly different pair names
const KRAKEN_PAIR = {
  ETH:  'ETHUSD',
  WETH: 'ETHUSD',
  BNB:  null,     // Kraken doesn't list BNB
  WBNB: null,     // ✅ FIX v17.2 — Kraken doesn't list WBNB either; will skip
  BTC:  'XBTUSD',
  WBTC: 'XBTUSD', // ✅ FIX v17.4
  CAKE: 'CAKEUSD', // ✅ FIX v17.7 (review) — v17.6 wrongly assumed Kraken
                   //   doesn't list CAKE; verified via kraken.com/prices/
                   //   pancakeswap and pro.kraken.com/app/trade/cake-eur —
                   //   it's a real, tradable spot pair. Wiring it in only
                   //   adds another live fallback source, no downside.
  XRP:  'XRPUSD', // ✅ FIX v17.7 — Kraken does list XRP
  MATIC:'MATICUSD', // v18.0 — Kraken lists MATIC (Polygon)
};

// Coinbase product IDs
const COINBASE_ID = {
  ETH:  'ETH-USD',
  WETH: 'ETH-USD',
  BNB:  'BNB-USD',
  WBNB: 'BNB-USD', // ✅ FIX v17.2
  BTC:  'BTC-USD',
  WBTC: 'BTC-USD', // ✅ FIX v17.4
  CAKE: 'CAKE-USD', // ✅ FIX v17.7 (correction) — an automated review
                     //   claimed Coinbase doesn't list CAKE and this was
                     //   briefly set to null; a live `wrangler tail` of
                     //   the deployed Worker then showed
                     //   "CAKE = $1.38 [coinbase ✓]" — direct production
                     //   evidence the pair works, contradicting that
                     //   claim. Reverted. Lesson: verify automated review
                     //   findings against real evidence before trusting
                     //   confident-sounding claims, same bar as any
                     //   other change here.
  XRP:  'XRP-USD',  // ✅ FIX v17.7 — real, listed Coinbase product
  MATIC:'MATIC-USD', // v18.0 — real, listed Coinbase product
  WOO:  'WOO-USD',   // v18.0 — real, listed Coinbase product
};

const STATIC_FALLBACK = {
  ETH:  1600, // v17.3 FIX — was 3500, badly stale and mismatched against
  WETH: 1600, //   price_client.py's own _STATIC_PRICES (1600). Both
              //   fallback tables now agree on the same last-resort value.
  BNB:  580,
  WBNB: 580,  // ✅ FIX v17.2 — same static fallback as BNB
  BTC:  65000,
  WBTC: 65000, // ✅ FIX v17.4 — same static fallback as BTC
  CAKE: 2.00,  // ✅ FIX v17.6 — matches price_client.py's own last-resort value
  XRP:  1.50,  // ✅ FIX v17.7 — matches price_client.py's own last-resort value
  MATIC:0.50,  // v18.0 — rough last-resort only
  // NOTE — the other v18.0 alt tokens (GMT/SYN/BIFI/TWT/ALPACA/DEGO/LINA/
  //   FLOKI/BANANA/WOO/SOLV/LIT) deliberately have NO static fallback: a
  //   fabricated static price on a thin alt is worse than an honest miss,
  //   because it can feed a bad spread/profit calc. When every live source
  //   fails for one of these, getPrice() returns "failed" and the scanner
  //   falls back to its own live pool-implied price instead.
};

// v18.0 — OKX added as a live source (unblocked from this Worker, unlike
// Binance's permanent 451). Order here doesn't imply priority; the waterfall
// in getPrice does.
const LIVE_SOURCES = new Set(['okx', 'binance', 'coingecko', 'kraken', 'coinbase']);

// How long to suspend a source after a DEFINITIVE, non-retryable failure
// (e.g. HTTP 451 geo-block) — much longer than the 60-90s window used for
// ordinary transient failures, since a jurisdiction/IP-range block won't
// clear itself on that timescale from the same Cloudflare edge location.
const PERMANENT_BLOCK_RESET_MS = 30 * 60_000; // 30 min

// ── CircuitBreaker ────────────────────────────────────────────────────────────

class CircuitBreaker {
  constructor({ threshold = 3, resetMs = 60_000 } = {}) {
    this._threshold = threshold;
    this._resetMs   = resetMs;
    this._failures  = 0;
    this._openedAt  = null;
    this._resetOverrideMs = null; // v17.5 — per-open override for forceOpen()
  }

  get isOpen() {
    if (this._openedAt === null) return false;
    const resetMs = this._resetOverrideMs ?? this._resetMs;
    if (Date.now() - this._openedAt >= resetMs) {
      this._failures = 0;
      this._openedAt = null;
      this._resetOverrideMs = null;
      return false;
    }
    return true;
  }

  recordSuccess() {
    this._failures = 0;
    this._openedAt = null;
    this._resetOverrideMs = null;
  }

  recordFailure() {
    this._failures += 1;
    if (this._failures >= this._threshold && this._openedAt === null) {
      this._openedAt = Date.now();
      console.warn(
        `[CircuitBreaker] OPEN — source suspended for ${this._resetMs / 1000}s ` +
        `after ${this._failures} consecutive failures`
      );
    }
  }

  // v17.5 — NEW: for definitive, non-retryable failures (e.g. HTTP 451)
  // where the normal failure-streak threshold would waste several more
  // doomed requests before finally opening. Opens immediately, with its
  // own (typically much longer) reset window.
  forceOpen(resetMs) {
    this._failures = this._threshold;
    this._openedAt = Date.now();
    this._resetOverrideMs = resetMs;
    console.warn(
      `[CircuitBreaker] FORCE-OPEN — suspended for ${resetMs / 1000}s ` +
      `(non-retryable failure, e.g. geo-block)`
    );
  }

  get health() {
    const resetMs = this._resetOverrideMs ?? this._resetMs;
    return {
      open      : this.isOpen,
      failures  : this._failures,
      threshold : this._threshold,
      openedAt  : this._openedAt,
      resetsInMs: this._openedAt
        ? Math.max(0, resetMs - (Date.now() - this._openedAt))
        : null,
    };
  }
}

// ── Retry with exponential back-off ──────────────────────────────────────────

async function withRetry(fn, { attempts = 2, baseDelayMs = 400 } = {}, timeLeftFn) {
  let lastErr;
  for (let i = 0; i < attempts; i++) {
    // Short-circuit before starting an attempt if the global deadline is already gone
    if (timeLeftFn && timeLeftFn() <= 0) {
      throw new Error('[PriceService] Deadline exceeded before retry attempt');
    }
    try {
      return await fn();
    } catch (e) {
      lastErr = e;
      // v17.5 — a permanent block (451) is exactly like a rate-limit signal
      // in that retrying it here is pointless; skip straight to the caller
      // so _withSource can force-open the circuit instead of burning
      // `attempts` doomed requests first.
      if (e.isRateLimit || e.isPermanentBlock) throw e;
      if (i < attempts - 1) {
        let delay = baseDelayMs * 2 ** i;
        if (timeLeftFn) {
          const remaining = timeLeftFn();
          if (remaining <= 0) {
            throw new Error('[PriceService] Deadline exceeded during retry backoff');
          }
          // Cap backoff sleep so it never overruns the remaining global budget
          delay = Math.min(delay, remaining);
        }
        await new Promise(r => setTimeout(r, delay));
      }
    }
  }
  throw lastErr;
}

// ── PriceService ──────────────────────────────────────────────────────────────

export class PriceService {
  constructor({ ttlMs = 30_000, timeoutMs = 3_000 } = {}) {
    this._cache   = new Map();
    this._ttl     = ttlMs;
    this._timeout = timeoutMs;

    this._cb = {
      okx      : new CircuitBreaker({ threshold: 3, resetMs: 60_000 }), // v18.0
      binance  : new CircuitBreaker({ threshold: 3, resetMs: 60_000 }),
      coingecko: new CircuitBreaker({ threshold: 3, resetMs: 90_000 }),
      kraken   : new CircuitBreaker({ threshold: 3, resetMs: 60_000 }),
      coinbase : new CircuitBreaker({ threshold: 3, resetMs: 60_000 }),
    };
  }

  // ── User-Agent on every fetch (v17.1 fix — prevents Binance/CoinGecko 403) ─

  async _fetchJSON(url, timeoutOverride) {
    const timeoutMs = timeoutOverride !== undefined ? timeoutOverride : this._timeout;
    const ctrl  = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(url, {
        signal : ctrl.signal,
        headers: {
          'Accept'    : 'application/json',
          'User-Agent': 'Mozilla/5.0 (compatible; GardenAngel/17.2; +https://gardenangel.io)',
        },
      });

      if (res.status === 429) {
        const retryAfterSec = parseInt(res.headers.get('Retry-After') ?? '10', 10);
        const err = new Error(`HTTP 429 rate-limited; Retry-After ${retryAfterSec}s at ${url}`);
        err.isRateLimit  = true;
        err.retryAfterMs = retryAfterSec * 1_000;
        throw err;
      }

      // v17.5 — HTTP 451 = "Unavailable For Legal Reasons," i.e. a
      // jurisdiction/IP-range block. This will not resolve on a retry
      // timescale (seconds) — flag it so withRetry/_withSource skip
      // straight to a long suspension instead of burning attempts.
      if (res.status === 451) {
        const err = new Error(`HTTP 451 (geo-blocked) at ${url}`);
        err.isPermanentBlock = true;
        throw err;
      }

      if (!res.ok) throw new Error(`HTTP ${res.status} at ${url}`);
      return await res.json();
    } catch (e) {
      if (e.name === 'AbortError') throw new Error(`Timeout (>${timeoutMs}ms) at ${url}`);
      throw e;
    } finally {
      clearTimeout(timer);
    }
  }

  async _withSource(name, fn, timeLeftFn) {
    const cb = this._cb[name];
    if (cb.isOpen) {
      throw new Error(
        `[PriceService] ${name} circuit OPEN — ` +
        `resets in ${(cb.health.resetsInMs / 1000).toFixed(0)}s`
      );
    }
    try {
      const result = await withRetry(async () => {
        if (timeLeftFn && timeLeftFn() <= 0) {
          throw new Error(`[PriceService] No budget left to initiate fetch for ${name}`);
        }
        try {
          return await fn();
        } catch (e) {
          if (e.isRateLimit) {
            let waitMs = Math.min(e.retryAfterMs ?? 10_000, 15_000);
            if (timeLeftFn) {
              const remaining = timeLeftFn();
              if (remaining < waitMs) {
                throw new Error(
                  `[PriceService] Aborting ${name}: rate-limit wait (${waitMs}ms) ` +
                  `exceeds remaining budget (${remaining}ms)`
                );
              }
              waitMs = Math.min(waitMs, remaining);
            }
            console.warn(`[PriceService] ${name} rate-limited — waiting ${waitMs}ms`);
            await new Promise(r => setTimeout(r, waitMs));
            if (timeLeftFn && timeLeftFn() <= 0) {
              throw new Error(`[PriceService] Deadline exceeded after rate-limit wait for ${name}`);
            }
            return await fn();
          }
          throw e;
        }
      }, { attempts: 3, baseDelayMs: 800 }, timeLeftFn);
      cb.recordSuccess();
      return result;
    } catch (e) {
      // v17.5 — a permanent block skips the normal 3-strikes streak
      // entirely and force-opens with a much longer window; everything
      // else keeps the original transient-failure behavior.
      if (e.isPermanentBlock) {
        cb.forceOpen(PERMANENT_BLOCK_RESET_MS);
      } else {
        cb.recordFailure();
      }
      throw e;
    }
  }

  // ── Source 0: OKX (v18.0 — primary; unblocked from this Worker) ────────────

  async _okx(instId, timeLeftFn) {
    return this._withSource('okx', async () => {
      const perCallTimeout = timeLeftFn ? Math.min(this._timeout, timeLeftFn()) : this._timeout;
      const data = await this._fetchJSON(
        `https://www.okx.com/api/v5/market/ticker?instId=${instId}`,
        perCallTimeout
      );
      // OKX envelope: { code: "0", data: [ { last: "1234.5", ... } ] }
      if (data.code !== '0') throw new Error(`OKX error code ${data.code} for ${instId}: ${data.msg ?? ''}`);
      const p = parseFloat(data.data?.[0]?.last);
      if (!p || !isFinite(p)) throw new Error(`OKX bad price for ${instId}: ${JSON.stringify(data.data?.[0])}`);
      return p;
    }, timeLeftFn);
  }

  // ── Source 1: Binance ─────────────────────────────────────────────────────

  async _binance(symbol, timeLeftFn) {
    return this._withSource('binance', async () => {
      const perCallTimeout = timeLeftFn ? Math.min(this._timeout, timeLeftFn()) : this._timeout;
      const data = await this._fetchJSON(
        `https://api.binance.com/api/v3/ticker/price?symbol=${symbol}`,
        perCallTimeout
      );
      const p = parseFloat(data.price);
      if (!p || !isFinite(p)) throw new Error(`Binance bad price for ${symbol}: ${JSON.stringify(data)}`);
      return p;
    }, timeLeftFn);
  }

  // ── Source 2: CoinGecko ───────────────────────────────────────────────────

  async _coingecko(id, timeLeftFn) {
    return this._withSource('coingecko', async () => {
      const perCallTimeout = timeLeftFn ? Math.min(this._timeout, timeLeftFn()) : this._timeout;
      const data = await this._fetchJSON(
        `https://api.coingecko.com/api/v3/simple/price?ids=${id}&vs_currencies=usd&precision=2`,
        perCallTimeout
      );
      const p = parseFloat(data[id]?.usd);
      if (!p || !isFinite(p)) throw new Error(`CoinGecko bad price for ${id}: ${JSON.stringify(data)}`);
      return p;
    }, timeLeftFn);
  }

  // ── Source 3: Kraken ──────────────────────────────────────────────────────

  async _kraken(pair, timeLeftFn) {
    return this._withSource('kraken', async () => {
      const perCallTimeout = timeLeftFn ? Math.min(this._timeout, timeLeftFn()) : this._timeout;
      const data = await this._fetchJSON(
        `https://api.kraken.com/0/public/Ticker?pair=${pair}`,
        perCallTimeout
      );
      if (data.error?.length) throw new Error(`Kraken error: ${data.error.join(', ')}`);
      const result = data.result?.[pair] ?? data.result?.[Object.keys(data.result ?? {})[0]];
      const p = parseFloat(result?.c?.[0]);
      if (!p || !isFinite(p)) throw new Error(`Kraken bad price for ${pair}: ${JSON.stringify(result)}`);
      return p;
    }, timeLeftFn);
  }

  // ── Source 4: Coinbase ────────────────────────────────────────────────────

  async _coinbase(productId, timeLeftFn) {
    return this._withSource('coinbase', async () => {
      const perCallTimeout = timeLeftFn ? Math.min(this._timeout, timeLeftFn()) : this._timeout;
      const data = await this._fetchJSON(
        `https://api.coinbase.com/v2/prices/${productId}/spot`,
        perCallTimeout
      );
      const p = parseFloat(data.data?.amount);
      if (!p || !isFinite(p)) throw new Error(`Coinbase bad price for ${productId}: ${JSON.stringify(data)}`);
      return p;
    }, timeLeftFn);
  }

  // ── Core: getPrice ────────────────────────────────────────────────────────

  async getPrice(asset) {
    const key      = (asset ?? '').toUpperCase();
    const okxInst  = OKX_INST[key];        // v18.0
    const symbol   = BINANCE_SYMBOL[key];
    const cgId     = COINGECKO_ID[key];
    const krakenPr = KRAKEN_PAIR[key];
    const cbId     = COINBASE_ID[key];

    // v18.0 FIX — was `if (!symbol) throw` which rejected every asset that
    // lacked a *Binance* symbol, i.e. all the new alt tokens (GMT, ALPACA,
    // DEGO, TWT, SYN, BIFI, LINA, FLOKI, BANANA, WOO, SOLV, LIT). That was
    // THE root cause of the "Unsupported asset" storm in production — the
    // asset was priceable via OKX/CoinGecko, but this early gate threw
    // before any source was even tried. Now we reject only when NO source
    // maps the asset at all.
    if (!okxInst && !symbol && !cgId && !krakenPr && !cbId) {
      throw new Error(`[PriceService] Unsupported asset: "${asset}"`);
    }

    const hit = this._cache.get(key);
    if (hit && Date.now() - hit.ts < this._ttl) return hit.value;

    // Hard wall-clock budget for the whole waterfall (all 4 sources +
    // their retries combined). Aligned to the true 10s gateway ceiling
    // (Wyndham), with a small safety margin so we always have time to
    // fall through to the static fallback and return before the gateway
    // drops the connection. Without this, a single asset's sequential
    // Binance→CoinGecko→Kraken→Coinbase fallback — each with its own
    // timeout and up to `attempts` retries — could take 30-60s+ before
    // ever reaching static fallback, which is what was hanging /prices.
    const MAX_TIMEOUT_MS = 10_000; // Wyndham's strict 10s gateway ceiling
    const SAFETY_MARGIN_MS = 500;  // leave headroom to return before the gateway cuts us off
    const MIN_BUDGET_FOR_SOURCE_MS = 600; // don't bother starting a source with less than this left
    const deadline = Date.now() + (MAX_TIMEOUT_MS - SAFETY_MARGIN_MS);
    const timeLeft = () => deadline - Date.now();

    let value, source;

    // 0. OKX — v18.0 primary. Unblocked from this Worker (Binance is 451),
    //    lighter rate limits than CoinGecko, and covers the majors + several
    //    of the new alts. Tried FIRST so the common case never even touches
    //    the geo-blocked / rate-limited sources below.
    if (okxInst && timeLeft() >= MIN_BUDGET_FOR_SOURCE_MS) {
      try {
        value  = await this._okx(okxInst, timeLeft);
        source = 'okx';
        console.info(`[PriceService] ${key} = $${value.toFixed(2)} [okx ✓]`);
      } catch (e) {
        console.warn(`[PriceService] OKX failed for ${key}: ${e.message}`);
      }
    }

    // 1. Binance — kept for completeness but expected to 451 from this
    //    Worker's IP range; only runs if OKX didn't already resolve.
    if (value === undefined && symbol && timeLeft() >= MIN_BUDGET_FOR_SOURCE_MS) {
      try {
        value  = await this._binance(symbol, timeLeft);
        source = 'binance';
        console.info(`[PriceService] ${key} = $${value.toFixed(2)} [binance ✓]`);
      } catch (e) {
        console.warn(`[PriceService] Binance failed for ${key}: ${e.message}`);
      }
    }

    // 2. CoinGecko
    if (value === undefined && cgId && timeLeft() >= MIN_BUDGET_FOR_SOURCE_MS) {
      try {
        value  = await this._coingecko(cgId, timeLeft);
        source = 'coingecko';
        console.info(`[PriceService] ${key} = $${value.toFixed(2)} [coingecko ✓]`);
      } catch (e) {
        console.warn(`[PriceService] CoinGecko failed for ${key}: ${e.message}`);
      }
    } else if (value === undefined && cgId) {
      console.warn(`[PriceService] Skipping coingecko for ${key}: budget depleted (${timeLeft()}ms left)`);
    }

    // 3. Kraken — null pair means this asset isn't listed, skip silently
    if (value === undefined && krakenPr && timeLeft() >= MIN_BUDGET_FOR_SOURCE_MS) {
      try {
        value  = await this._kraken(krakenPr, timeLeft);
        source = 'kraken';
        console.info(`[PriceService] ${key} = $${value.toFixed(2)} [kraken ✓]`);
      } catch (e) {
        console.warn(`[PriceService] Kraken failed for ${key}: ${e.message}`);
      }
    } else if (value === undefined && krakenPr) {
      console.warn(`[PriceService] Skipping kraken for ${key}: budget depleted (${timeLeft()}ms left)`);
    }

    // 4. Coinbase
    if (value === undefined && cbId && timeLeft() >= MIN_BUDGET_FOR_SOURCE_MS) {
      try {
        value  = await this._coinbase(cbId, timeLeft);
        source = 'coinbase';
        console.info(`[PriceService] ${key} = $${value.toFixed(2)} [coinbase ✓]`);
      } catch (e) {
        console.warn(`[PriceService] Coinbase failed for ${key}: ${e.message}`);
      }
    } else if (value === undefined && cbId) {
      console.warn(`[PriceService] Skipping coinbase for ${key}: budget depleted (${timeLeft()}ms left)`);
    }

    // 5. Static fallback — last resort
    if (value === undefined) {
      const fb = STATIC_FALLBACK[key];
      if (fb !== undefined) {
        value  = fb;
        source = 'static';
        console.error(
          `[PriceService] ⛔ STATIC FALLBACK for ${key} = $${fb}. ` +
          `ALL live sources failed (Binance, CoinGecko, Kraken, Coinbase). ` +
          `Circuit health: ${JSON.stringify(this.getCircuitHealth())}`
        );
      }
    }

    if (value === undefined || !isFinite(value)) {
      throw new Error(
        `[PriceService] Price completely unavailable for "${asset}" — all sources failed`
      );
    }

    this._cache.set(key, { value, ts: Date.now(), source });
    return value;
  }

  // ── Live-price guard ──────────────────────────────────────────────────────

  isLive(asset) {
    const hit = this._cache.get((asset ?? '').toUpperCase());
    if (!hit) return false;
    if (Date.now() - hit.ts >= this._ttl) return false;
    return LIVE_SOURCES.has(hit.source);
  }

  getPriceSource(asset) {
    return this._cache.get((asset ?? '').toUpperCase())?.source ?? null;
  }

  // ── Legacy convenience methods ────────────────────────────────────────────
  async getETHPrice()  { return this.getPrice('ETH');  }
  async getBNBPrice()  { return this.getPrice('BNB');  }
  async getWETHPrice() { return this.getPrice('WETH'); }
  async getWBNBPrice() { return this.getPrice('WBNB'); } // ✅ FIX v17.2 — new helper

  // ── Parallel batch fetch ──────────────────────────────────────────────────

  // v18.0 — batched CoinGecko warm-up. CoinGecko's free tier rate-limits
  // hard (HTTP 429), and the old getMultiPrice fired ONE CoinGecko request
  // PER asset — a 15-asset scan every few seconds = a 429 storm that
  // knocked the source out for 90s+ and forced static fallbacks. CoinGecko's
  // simple/price endpoint accepts a comma-separated id list, so one request
  // prices them all. We warm the cache here (source 'coingecko'); getPrice()
  // then returns those as cache hits without re-fetching. Best-effort: any
  // failure (429/timeout) is swallowed and the normal per-asset waterfall
  // (OKX-first) still runs for whatever isn't warmed.
  async _warmCoinGeckoBatch(assets, timeLeftFn) {
    const wanted = [];
    const idByKey = {};
    for (const a of assets) {
      const key = (a ?? '').toUpperCase();
      const id  = COINGECKO_ID[key];
      if (!id) continue;
      const hit = this._cache.get(key);
      if (hit && Date.now() - hit.ts < this._ttl) continue; // already fresh
      idByKey[key] = id;
      wanted.push(id);
    }
    if (!wanted.length) return;
    if (this._cb.coingecko.isOpen) return; // circuit already open — don't bother
    const ids = [...new Set(wanted)].join(',');
    try {
      await this._withSource('coingecko', async () => {
        const perCallTimeout = timeLeftFn ? Math.min(this._timeout, timeLeftFn()) : this._timeout;
        const data = await this._fetchJSON(
          `https://api.coingecko.com/api/v3/simple/price?ids=${ids}&vs_currencies=usd&precision=6`,
          perCallTimeout
        );
        let warmed = 0;
        for (const [key, id] of Object.entries(idByKey)) {
          // Optional-chain `data` itself: a malformed/null batch response
          // must skip cleanly, not throw a TypeError that aborts the warm-up.
          const p = parseFloat(data?.[id]?.usd);
          if (p && isFinite(p)) {
            this._cache.set(key, { value: p, ts: Date.now(), source: 'coingecko' });
            warmed++;
          }
        }
        console.info(`[PriceService] CoinGecko batch warmed ${warmed}/${Object.keys(idByKey).length} assets in 1 request`);
        return warmed;
      }, timeLeftFn);
    } catch (e) {
      console.warn(`[PriceService] CoinGecko batch warm-up skipped: ${e.message}`);
    }
  }

  async getMultiPrice(assets = ['ETH', 'BNB', 'WETH', 'WBNB']) {
    const results = {};
    // v18.0 — one batched CoinGecko call up front instead of N separate ones.
    const BATCH_BUDGET_MS = 4_000;
    const batchDeadline = Date.now() + BATCH_BUDGET_MS;
    await this._warmCoinGeckoBatch(assets, () => batchDeadline - Date.now());
    await Promise.allSettled(
      assets.map(async (a) => {
        const key = a.toUpperCase();
        try {
          const price  = await this.getPrice(key);
          const source = this.getPriceSource(key);
          results[`${key}USDT`] = { price, source, asset: key, live: LIVE_SOURCES.has(source) };
        } catch (e) {
          console.error(`[PriceService] getMultiPrice failed for ${key}: ${e.message}`);
          results[`${key}USDT`] = { price: 0, source: 'failed', error: e.message, asset: key, live: false };
        }
      })
    );
    return results;
  }

  // ── Diagnostics ───────────────────────────────────────────────────────────

  getCircuitHealth() {
    return {
      okx      : this._cb.okx.health,   // v18.0 — primary source; must appear in diagnostics
      binance  : this._cb.binance.health,
      coingecko: this._cb.coingecko.health,
      kraken   : this._cb.kraken.health,
      coinbase : this._cb.coinbase.health,
    };
  }

  getSourceHealth() {
    const cache = {};
    for (const [k, v] of this._cache.entries()) {
      const ageMs = Date.now() - v.ts;
      cache[k] = {
        price  : v.value,
        source : v.source,
        ageMs,
        ageStr : `${(ageMs / 1000).toFixed(1)}s`,
        expired: ageMs >= this._ttl,
        live   : LIVE_SOURCES.has(v.source),
      };
    }
    return {
      circuits : this.getCircuitHealth(),
      cache,
      config   : { ttlMs: this._ttl, timeoutMs: this._timeout },
      ts       : new Date().toISOString(),
    };
  }

  invalidate(asset)   { this._cache.delete((asset ?? '').toUpperCase()); }
  invalidateAll()     { this._cache.clear(); }
}
