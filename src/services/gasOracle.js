// 🪬🧿✝️  GasOracleService – v16.2
// ─────────────────────────────────────────────────────────────────────────────
// Fixes applied:
//   1. _fromChain() — reads real gas price via viem publicClient.getGasPrice().
//   2. _fromEtherscan() — free-tier fallback (ProposeGasPrice, no API key).
//   3. isGasAcceptable() — pre-flight check used by executor before sending txs.
//   4. estimateGasCostUSD() — accurate USD cost given current Gwei + ETH price.
//   5. recordReading() — appends to KV ring buffer (GAS_RING_SIZE entries).
// ─────────────────────────────────────────────────────────────────────────────

import { KV_KEYS, CFG } from '../config/constants.js';

const GAS_RING_SIZE = CFG?.GAS_RING ?? 10;

// Conservative hard-coded fallback (used only when ALL fetch sources fail).
const FALLBACK_GWEI = 20n;   // 20 gwei

export class GasOracleService {
  constructor(kv, blockchain) {
    this.kv         = kv;
    this.blockchain = blockchain;
    this._timeout   = 4_000;   // 4-second abort timeout per fetch
  }

  // ── Source: on-chain via viem ─────────────────────────────────────────────
  // Most accurate — reads the actual pending base fee from the node.

  async _fromChain(chain = 'ETH') {
    const client   = this.blockchain.getPublicClient(chain);
    const gasPrice = await client.getGasPrice();    // bigint in wei
    const gwei     = Number(gasPrice) / 1e9;
    if (!gwei || !isFinite(gwei)) throw new Error('Bad gas price from RPC');
    return { wei: gasPrice, gwei };
  }

  // ── Source: Etherscan gas tracker (free, no key needed) ───────────────────

  async _fromEtherscan() {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this._timeout);
    try {
      const res = await fetch(
        'https://api.etherscan.io/api?module=gastracker&action=gasoracle',
        { signal: controller.signal, headers: { Accept: 'application/json' } }
      );
      if (!res.ok) throw new Error(`Etherscan HTTP ${res.status}`);
      const data = await res.json();
      // ProposeGasPrice is the "average" tier — safe for most arb txs
      const gwei = parseFloat(data.result?.ProposeGasPrice);
      if (!gwei || !isFinite(gwei)) throw new Error(`Bad Etherscan gas data: ${JSON.stringify(data.result)}`);
      return { wei: BigInt(Math.ceil(gwei * 1e9)), gwei };
    } finally {
      clearTimeout(timer);
    }
  }

  // ── Public: current gas price ─────────────────────────────────────────────

  async getGasPrice(chain = 'ETH') {
    // 1. On-chain via viem (most accurate)
    try {
      return await this._fromChain(chain);
    } catch (e) {
      console.warn(`[GasOracle] Chain fetch failed (${chain}): ${e.message}`);
    }

    // 2. Etherscan public gas tracker
    try {
      return await this._fromEtherscan();
    } catch (e) {
      console.warn(`[GasOracle] Etherscan fallback failed: ${e.message}`);
    }

    // 3. Hard fallback — log a critical warning
    console.error(
      `[GasOracle] ⚠️  CRITICAL: All gas sources failed. ` +
      `Using static fallback ${FALLBACK_GWEI} gwei — profit estimates will be approximate.`
    );
    return { wei: FALLBACK_GWEI * 1_000_000_000n, gwei: Number(FALLBACK_GWEI) };
  }

  // ── Public: pre-flight gas acceptability check ────────────────────────────
  // Call this before every execution to ensure the network isn't congested.
  // limitGwei defaults to CFG.GAS_LIMIT_GWEI (80 in current config).

  async isGasAcceptable(chain = 'ETH', limitGwei = CFG?.GAS_LIMIT_GWEI ?? 80) {
    const { gwei, wei } = await this.getGasPrice(chain);
    const acceptable    = gwei <= limitGwei;
    if (!acceptable) {
      console.warn(
        `[GasOracle] Gas too high: ${gwei.toFixed(1)} gwei > limit ${limitGwei} gwei`
      );
    }
    return { acceptable, gwei, wei, limitGwei };
  }

  // ── Public: estimated gas cost in USD ─────────────────────────────────────
  // gasUnits defaults to CFG.GAS_UNITS (500,000 in current config).
  // Returns a number in USD (e.g. 14.78).

  async estimateGasCostUSD(ethPriceUSD, chain = 'ETH', gasUnits = CFG?.GAS_UNITS ?? 500_000n) {
    const { gwei } = await this.getGasPrice(chain);
    // gas cost in ETH = (gwei per unit × units) / 1e9
    const ethCost  = (gwei * Number(gasUnits)) / 1e9;
    const usdCost  = ethCost * ethPriceUSD;
    console.log(
      `[GasOracle] Estimated gas cost: ${gwei.toFixed(1)} gwei × ${Number(gasUnits).toLocaleString()} units ` +
      `= ${ethCost.toFixed(6)} ETH = $${usdCost.toFixed(2)}`
    );
    return usdCost;
  }

  // ── Public: persist reading to KV ring buffer ─────────────────────────────
  // Keeps the last GAS_RING_SIZE readings for trend analysis / alerting.

  async recordReading(chain = 'ETH') {
    const reading  = await this.getGasPrice(chain);
    const key      = KV_KEYS?.GAS_READINGS ?? 'gas_readings';
    try {
      const existing = (await this.kv.getJSON(key)) ?? [];
      const updated  = [
        ...existing,
        { gwei: reading.gwei, ts: Date.now(), chain },
      ].slice(-GAS_RING_SIZE);
      await this.kv.putJSON(key, updated);
    } catch (e) {
      console.warn('[GasOracle] Failed to record KV reading:', e.message);
    }
    return reading;
  }
}
