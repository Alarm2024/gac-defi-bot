// 🪬🧿✝️  PriceService – v17.4
// ─────────────────────────────────────────────────────────────────────────────
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
};

const COINGECKO_ID = {
  ETH:  'ethereum',
  WETH: 'ethereum',
  BNB:  'binancecoin',
  WBNB: 'binancecoin', // ✅ FIX v17.2
  BTC:  'bitcoin',
  WBTC: 'bitcoin', // ✅ FIX v17.4
};

// Kraken uses slightly different pair names
const KRAKEN_PAIR = {
  ETH:  'ETHUSD',
  WETH: 'ETHUSD',
  BNB:  null,     // Kraken doesn't list BNB
  WBNB: null,     // ✅ FIX v17.2 — Kraken doesn't list WBNB either; will skip
  BTC:  'XBTUSD',
  WBTC: 'XBTUSD', // ✅ FIX v17.4
};

// Coinbase product IDs
const COINBASE_ID = {
  ETH:  'ETH-USD',
  WETH: 'ETH-USD',
  BNB:  'BNB-USD',
  WBNB: 'BNB-USD', // ✅ FIX v17.2
  BTC:  'BTC-USD',
  WBTC: 'BTC-USD', // ✅ FIX v17.4
};

const STATIC_FALLBACK = {
  ETH:  1600, // v17.3 FIX — was 3500, badly stale and mismatched against
  WETH: 1600, //   price_client.py's own _STATIC_PRICES (1600). Both
              //   fallback tables now agree on the same last-resort value.
  BNB:  580,
  WBNB: 580,  // ✅ FIX v17.2 — same static fallback as BNB
  BTC:  65000,
  WBTC: 65000, // ✅ FIX v17.4 — same static fallback as BTC
};

const LIVE_SOURCES = new Set(['binance', 'coingecko', 'kraken', 'coinbase']);

// ── CircuitBreaker ────────────────────────────────────────────────────────────

class CircuitBreaker {
  constructor({ threshold = 3, resetMs = 60_000 } = {}) {
    this._threshold = threshold;
    this._resetMs   = resetMs;
    this._failures  = 0;
    this._openedAt  = null;
  }

  get isOpen() {
    if (this._openedAt === null) return false;
    if (Date.now() - this._openedAt >= this._resetMs) {
      this._failures = 0;
      this._openedAt = null;
      return false;
    }
    return true;
  }

  recordSuccess() { this._failures = 0; this._openedAt = null; }

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

  get health() {
    return {
      open      : this.isOpen,
      failures  : this._failures,
      threshold : this._threshold,
      openedAt  : this._openedAt,
      resetsInMs: this._openedAt
        ? Math.max(0, this._resetMs - (Date.now() - this._openedAt))
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
      if (e.isRateLimit) throw e;
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
      cb.recordFailure();
      throw e;
    }
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
    const symbol   = BINANCE_SYMBOL[key];
    const cgId     = COINGECKO_ID[key];
    const krakenPr = KRAKEN_PAIR[key];
    const cbId     = COINBASE_ID[key];

    if (!symbol) throw new Error(`[PriceService] Unsupported asset: "${asset}"`);

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

    // 1. Binance
    if (timeLeft() >= MIN_BUDGET_FOR_SOURCE_MS) {
      try {
        value  = await this._binance(symbol, timeLeft);
        source = 'binance';
        console.info(`[PriceService] ${key} = $${value.toFixed(2)} [binance ✓]`);
      } catch (e) {
        console.warn(`[PriceService] Binance failed for ${key}: ${e.message}`);
      }
    } else {
      console.warn(`[PriceService] Skipping binance for ${key}: budget depleted (${timeLeft()}ms left)`);
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

  async getMultiPrice(assets = ['ETH', 'BNB', 'WETH', 'WBNB']) {
    const results = {};
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