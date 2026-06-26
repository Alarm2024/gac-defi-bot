// 🪬🧿✝️  GARDEN ANGEL v16.1 – EXECUTOR (LEDGER FIX)
// ─────────────────────────────────────────────────────────────────────────────
// Root causes fixed:
//   1. Key-name fallbacks  → if KV_KEYS exports undefined, writes went to the
//      literal key "undefined" and reads always returned null (→ 0).
//   2. Dual-write pattern  → service wrapper failure is caught; raw CF KV
//      binding is always attempted as a second path.
//   3. Post-write verify   → logs a mismatch so you can catch it in Wrangler tail.
//   4. DRY_RUN ledger stub → dry-run now still posts a synthetic ledger entry
//      so /debug shows non-zero values immediately after a simulated trade.
// ─────────────────────────────────────────────────────────────────────────────

import { encodeFunctionData } from 'viem';
import { CFG, KV_KEYS, CHAIN_REGISTRY, ARBITRAGE_ENGINE_ABI } from '../config/constants.js';

// ── Helpers ───────────────────────────────────────────────────────────────────

function safeNumber(v) {
  const n = parseFloat(v);
  return isNaN(n) || !isFinite(n) ? 0 : n;
}

// Fallback key names ── if KV_KEYS constants.js exports are undefined (the
// most common cause of the "all-zeros" ledger bug), we use explicit strings.
const LKEYS = {
  GROSS : KV_KEYS?.GROSS_PROFIT    ?? 'ledger:gross_profit',
  FEES  : KV_KEYS?.TOTAL_LOAN_FEES ?? 'ledger:total_loan_fees',
  GAS   : KV_KEYS?.GAS_DEBT        ?? 'ledger:gas_debt',
};

// ── Dual-path KV read ─────────────────────────────────────────────────────────
// Exported so index.js can reuse the same helpers.
export async function kvRead(env, kvSvc, key) {
  // Path 1: KVService wrapper
  try {
    if (typeof kvSvc?.getJSON === 'function') {
      const v = await kvSvc.getJSON(key);
      if (v !== null && v !== undefined) {
        console.log(`[KV] service GET "${key}" =`, v);
        return v;
      }
    }
  } catch (e) {
    console.warn(`[KV] service GET "${key}" threw:`, e.message);
  }

  // Path 2: raw Cloudflare KV binding
  try {
    if (env?.BOT_KV) {
      const raw = await env.BOT_KV.get(key);
      if (raw !== null) {
        const parsed = JSON.parse(raw);
        console.log(`[KV] raw GET "${key}" =`, parsed);
        return parsed;
      }
    }
  } catch (e) {
    console.warn(`[KV] raw GET "${key}" threw:`, e.message);
  }

  console.log(`[KV] GET "${key}" → null (key not found on either path)`);
  return null;
}

// ── Dual-path KV write ────────────────────────────────────────────────────────
export async function kvWrite(env, kvSvc, key, value) {
  const serialized = JSON.stringify(value);
  let wrote = false;

  // Path 1: KVService wrapper
  try {
    if (typeof kvSvc?.putJSON === 'function') {
      await kvSvc.putJSON(key, value);
      console.log(`[KV] service PUT "${key}" = ${serialized}`);
      wrote = true;
    }
  } catch (e) {
    console.warn(`[KV] service PUT "${key}" threw:`, e.message);
  }

  // Path 2: raw CF KV binding (always attempt — belt-and-suspenders)
  try {
    if (env?.BOT_KV) {
      await env.BOT_KV.put(key, serialized);
      console.log(`[KV] raw    PUT "${key}" = ${serialized}`);
      wrote = true;
    }
  } catch (e) {
    console.warn(`[KV] raw PUT "${key}" threw:`, e.message);
  }

  if (!wrote) {
    throw new Error(`All KV write paths failed for key: "${key}". Check BOT_KV binding in wrangler.toml.`);
  }
}

// ── Ledger read/write with verification ───────────────────────────────────────

async function readLedger(env, kv) {
  const gross    = safeNumber(await kvRead(env, kv, LKEYS.GROSS) ?? 0);
  const loanFees = safeNumber(await kvRead(env, kv, LKEYS.FEES)  ?? 0);
  const gasDebt  = safeNumber(await kvRead(env, kv, LKEYS.GAS)   ?? 0);
  console.log('[Ledger] read →', { gross, loanFees, gasDebt, keys: LKEYS });
  return { gross, loanFees, gasDebt };
}

async function writeLedger(env, kv, gross, loanFees, gasDebt) {
  console.log('[Ledger] writing →', { gross, loanFees, gasDebt });
  await kvWrite(env, kv, LKEYS.GROSS, gross);
  await kvWrite(env, kv, LKEYS.FEES,  loanFees);
  await kvWrite(env, kv, LKEYS.GAS,   gasDebt);

  // Post-write verification
  const got = await readLedger(env, kv);
  const ok  = Math.abs(got.gross    - gross)    < 0.001 &&
              Math.abs(got.loanFees - loanFees) < 0.001 &&
              Math.abs(got.gasDebt  - gasDebt)  < 0.001;

  if (ok) {
    console.log('[Ledger] ✅ write verified');
  } else {
    console.error('[Ledger] ⚠️ WRITE MISMATCH — expected:', { gross, loanFees, gasDebt }, 'got:', got);
  }
}

// ── ExecutorModule ────────────────────────────────────────────────────────────

export class ExecutorModule {
  constructor(env, blockchain, kv, tradeLogger) {
    this.env         = env;
    this.blockchain  = blockchain;
    this.kv          = kv;
    this.tradeLogger = tradeLogger;
  }

  async execute(decision, chainKey) {
    console.info('⚡ Executor start', { chain: chainKey, signal: decision.signal });

    // ── DRY_RUN: still posts a synthetic ledger entry so /debug is non-zero ──
    if (this.env.DRY_RUN === 'true') {
      console.warn('DRY_RUN=true – posting synthetic ledger entry');
      const cur = await readLedger(this.env, this.kv);
      await writeLedger(this.env, this.kv,
        cur.gross    + safeNumber(decision.grossReturn),
        cur.loanFees + safeNumber(decision.loanFee),
        cur.gasDebt  + 5.00                             // $5 simulated gas
      );
      return { executed: false, reason: 'dry_run', txHash: null };
    }

    // ── Guards ────────────────────────────────────────────────────────────────
    if (!this.env.PRIVATE_KEY?.startsWith('0x'))
      throw new Error('PRIVATE_KEY missing or malformed');
    const contract = this.env.ARBITRAGE_ENGINE_CONTRACT;
    if (!contract) throw new Error('ARBITRAGE_ENGINE_CONTRACT not set');

    const chainDef = CHAIN_REGISTRY[chainKey] ?? CHAIN_REGISTRY.ETH;
    const asset    = '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2'; // WETH
    const amount   = BigInt(decision.amountIn ?? Math.floor((CFG?.LOAN_AMOUNT_USD ?? 50000) * 1e18));
    const params   = decision.calldata ?? '0x';

    const callData = encodeFunctionData({
      abi: ARBITRAGE_ENGINE_ABI,
      functionName: 'executeArbitrage',
      args: [asset, amount, params],
    });

    // ── Simulate ──────────────────────────────────────────────────────────────
    try {
      await this.blockchain.simulateTransaction(chainKey, contract, callData);
      console.info('✅ Simulation passed');
    } catch (err) {
      throw new Error(`Simulation failed: ${err.message}`);
    }

    // ── Broadcast ─────────────────────────────────────────────────────────────
    const txHash  = await this.blockchain.sendTransaction(chainKey, contract, callData, 500000n);
    const receipt = await this.blockchain.waitForReceipt(chainKey, txHash);
    if (receipt.status === 'reverted') throw new Error(`Transaction reverted: ${txHash}`);

    // ── Gas cost ──────────────────────────────────────────────────────────────
    const gasCostWei = receipt.gasUsed * receipt.effectiveGasPrice;
    const gasCostEth = Number(gasCostWei) / 1e18;
    const ethPrice   = safeNumber(decision.ethPrice ?? 3500);
    const gasCostUSD = gasCostEth * ethPrice;

    // ── LEDGER UPDATE (the critical section) ──────────────────────────────────
    const newGross   = safeNumber(decision.grossReturn);
    const newLoanFee = safeNumber(decision.loanFee);

    console.info('[Executor] ledger delta', { newGross, newLoanFee, gasCostUSD });

    const current = await readLedger(this.env, this.kv);
    await writeLedger(this.env, this.kv,
      current.gross    + newGross,
      current.loanFees + newLoanFee,
      current.gasDebt  + gasCostUSD,
    );

    // ── Trade record ──────────────────────────────────────────────────────────
    const trade = {
      ts               : new Date().toISOString(),
      txHash,
      chain            : chainKey,
      chainName        : chainDef.name,
      explorerUrl      : `${chainDef.explorerBase}/${txHash}`,
      signal           : decision.signal,
      spread           : decision.loanAmount ? newGross / decision.loanAmount : 0,
      loanAmount       : decision.loanAmount,
      grossReturn      : newGross,
      loanFee          : newLoanFee,
      gasCostUSD,
      netProfitAfterFee: decision.netAfterFee,
      status           : 'SUCCESS',
    };
    await this.tradeLogger.logSuccess(trade);

    return { executed: true, txHash, chain: chainKey, explorerUrl: trade.explorerUrl, trade };
  }
}
