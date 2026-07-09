// 🪬🧿✝️  GARDEN ANGEL v17.1 – EXECUTOR (SHARED ASSET REGISTRY)
// ─────────────────────────────────────────────────────────────────────────────
// v17.1 changelog (this revision, on top of v17.0's decimal scaling fix):
//   - BTCB GAP FIX: v17.0's local ASSET_DECIMALS map had only 5 entries,
//     all ETH-mainnet addresses (WETH/WBTC/USDC/USDT/DAI) — BTCB
//     (0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c, BSC, 18 decimals) was
//     never added, even though scanner_4.py had already been emitting it
//     correctly for the PancakeSwap/Biswap BTCB pair. Depending on the
//     caller, this gap didn't always produce the intended fail-closed
//     "No known decimals" error — some paths could reach startArbitrage's
//     simulation with the amount already set upstream, surfacing instead
//     as a generic "Execution reverted for an unknown reason" revert with
//     no diagnostic value.
//   - STRUCTURAL FIX: ASSET_DECIMALS is no longer maintained locally in
//     this file. resolveDecimals() now comes from ../shared/assets.js,
//     which is the single JS-side source of truth, mirrored (by hand, for
//     now) in ../shared/assets.py for the Python side. Run
//     `node shared/check_asset_sync.js` before every deploy — it fails
//     loudly if the two registries disagree on any address.
//   - This does NOT fully solve cross-language drift (both files are still
//     hand-authored), but it turns a silent, expensive on-chain revert into
//     either (a) a clear fail-closed JS error naming the missing asset, or
//     (b) a pre-deploy CI failure — instead of a mystery revert discovered
//     hours later in production logs.
//
// v17.0 changelog (previous revision, on top of v16.2's route-shape normalization):
//   - AMOUNT SCALING FIX: previous versions hardcoded `usdRef * 1e18` for
//     every asset. That's correct for 18-decimal tokens (WETH, DAI) but
//     silently wrong by a factor of 10^10 for 8-decimal tokens (WBTC) and
//     10^12 for 6-decimal tokens (USDC/USDT) — a scaling error that size,
//     if it ever reached broadcast, would misorder the trade catastrophically.
//   - ASSET_DECIMALS is a static map mirroring contract_manager.py's
//     AssetRegistry._STATIC. Kept in sync manually for now — if these two
//     lists drift, that's a real cross-language consistency bug to watch
//     for, not something this file alone can guarantee.
//   - Unknown assets now FAIL CLOSED (throw) rather than assuming 18
//     decimals, for the same reason the router/threshold fields fail
//     closed below: a wrong guess here is a silent, expensive error.
// ─────────────────────────────────────────────────────────────────────────────

import { encodeFunctionData } from 'viem';
import { CFG, KV_KEYS, CHAIN_REGISTRY, ARBITRAGE_ENGINE_ABI } from '../config/constants.js';
import { resolveDecimals } from '../shared/assets.js';

// ── Helpers ───────────────────────────────────────────────────────────────────

function safeNumber(v) {
  const n = parseFloat(v);
  return isNaN(n) || !isFinite(n) ? 0 : n;
}

// v17.1 FIX — ASSET_DECIMALS used to be a local, hand-maintained map here
// (5 entries, ETH-mainnet addresses only). It was missing BTCB
// (0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c, BSC, 18 decimals), which
// scanner_4.py had already been correctly emitting for BSC WBTC/BTCB pairs.
// resolveDecimals() is now imported from ../shared/assets.js, the single
// source of truth also mirrored in shared/assets.py. Run
// `node shared/check_asset_sync.js` before every deploy to catch drift
// between the two languages before it reaches production. See
// shared/assets.js's header comment for the full incident writeup.

// Fallback key names ── if KV_KEYS constants.js exports are undefined (the
// most common cause of the "all-zeros" ledger bug), we use explicit strings.
const LKEYS = {
  GROSS : KV_KEYS?.GROSS_PROFIT    ?? 'ledger:gross_profit',
  FEES  : KV_KEYS?.TOTAL_LOAN_FEES ?? 'ledger:total_loan_fees',
  GAS   : KV_KEYS?.GAS_DEBT        ?? 'ledger:gas_debt',
};

// ── Dual-path KV read ─────────────────────────────────────────────────────────
export async function kvRead(env, kvSvc, key) {
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

  try {
    if (typeof kvSvc?.putJSON === 'function') {
      await kvSvc.putJSON(key, value);
      console.log(`[KV] service PUT "${key}" = ${serialized}`);
      wrote = true;
    }
  } catch (e) {
    console.warn(`[KV] service PUT "${key}" threw:`, e.message);
  }

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

  async executeSignal(rawDecision, chainKeyArg) {
    // ── DEFENSIVE NORMALIZATION (carried over from v16.2) ─────────────────────
    const chainKey = chainKeyArg ?? rawDecision.chain ?? rawDecision.chainKey;

    const decision = {
      ...rawDecision,
      signal     : rawDecision.signal      ?? rawDecision.baseAsset ?? null,
      grossReturn: rawDecision.grossReturn ?? rawDecision.callerGrossReturn ?? 0,
      loanFee    : rawDecision.loanFee     ?? rawDecision.callerLoanFee     ?? 0,
      netAfterFee: rawDecision.netAfterFee ?? rawDecision.callerNetProfit   ?? null,
      loanAmount : rawDecision.loanAmount  ?? rawDecision.amount ?? undefined,
      amountIn   : rawDecision.amountIn ?? undefined,
    };

    // asset must be resolved BEFORE amountIn scaling — decimals depend on it.
    const asset = decision.asset ?? '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2'; // WETH default

    if (decision.amountIn === undefined) {
      const usdRef = safeNumber(rawDecision.amount ?? CFG?.LOAN_AMOUNT_USD ?? 50000);
      const assetDecimals = resolveDecimals(asset); // throws on unknown asset — see fail-closed note above
      decision.amountIn = Math.floor(usdRef * (10 ** assetDecimals));
    }

    if (chainKeyArg === undefined || rawDecision.grossReturn === undefined) {
      console.warn(
        '[Executor] executeSignal received route-shaped input — using defensive field aliasing',
        { resolvedChain: chainKey, hadExplicitChainKey: chainKeyArg !== undefined }
      );
    }

    console.info('⚡ Executor start', { chain: chainKey, signal: decision.signal, asset });

    // ── DRY_RUN: still posts a synthetic ledger entry so /debug is non-zero ──
    if (this.env.DRY_RUN === 'true') {
      console.warn('DRY_RUN=true – posting synthetic ledger entry');
      const cur = await readLedger(this.env, this.kv);
      await writeLedger(this.env, this.kv,
        cur.gross    + safeNumber(decision.grossReturn),
        cur.loanFees + safeNumber(decision.loanFee),
        cur.gasDebt  + 5.00
      );
      return { executed: false, reason: 'dry_run', txHash: null };
    }

    // ── Guards ────────────────────────────────────────────────────────────────
    if (!this.env.PRIVATE_KEY?.startsWith('0x'))
      throw new Error('PRIVATE_KEY missing or malformed');
    const contract = this.env.ARBITRAGE_ENGINE_CONTRACT;
    if (!contract) throw new Error('ARBITRAGE_ENGINE_CONTRACT not set');

    const chainDef = CHAIN_REGISTRY[chainKey] ?? CHAIN_REGISTRY.ETH;
    if (!Number.isFinite(decision.amountIn)) {
      throw new Error(`startArbitrage: amountIn is not a finite number (got: ${decision.amountIn}) — upstream caller likely sent NaN/undefined instead of a real scaled amount`);
    }
    const amount   = BigInt(decision.amountIn);

    // ── startArbitrage — 9-arg entry point on FlashArbitrageV2.sol ─────────────
    // v17.1 FIX: this used to build an 8-arg call with a single
    // `intermediateToken` address, encoded against ARBITRAGE_ENGINE_ABI —
    // which never actually declared a `startArbitrage` entry at all (only
    // executeArbitrage/executeOperation). The real deployed contract's
    // interface (confirmed against contract_manager.py's ABI, the Python
    // side's source of truth) takes pathBuy/pathSell as full address[]
    // swap paths, not a single intermediate token — 9 args, not 8.
    // Calling the real contract with the wrong function selector and/or
    // a malformed array arg is consistent with the "reverted for an
    // unknown reason" — no decodable reason — error seen in production.
    //
    // FAIL CLOSED, deliberately NOT backward-compatible with a single
    // intermediateToken field: reconstructing a 2-hop path from one
    // token address would be a guess with real funds behind it — the
    // same category of risk contract_manager.py's docstring already
    // flags for slippage thresholds ("should not be guessed at here").
    // If the caller still sends the old shape, this throws a clear,
    // diagnosable error instead of silently inventing a route.
    const required = [
      'routerBuy', 'routerSell', 'pathBuy', 'pathSell',
      'minIntermediateOut', 'minFinalOut', 'minProfit',
    ];
    const missing = required.filter(k => decision[k] === undefined || decision[k] === null);
    if (missing.length) {
      const legacyHint = decision.intermediateToken !== undefined
        ? ' NOTE: payload has legacy `intermediateToken` but no `pathBuy`/`pathSell` — ' +
          'the caller needs to send full swap-path arrays now; a single intermediate ' +
          'token is no longer accepted (see v17.1 changelog above for why).'
        : '';
      throw new Error(
        `startArbitrage payload incomplete — missing: ${missing.join(', ')}. ` +
        `Route/threshold data must be sent by the caller; no default is safe for these fields.${legacyHint}`
      );
    }
    if (!Array.isArray(decision.pathBuy) || decision.pathBuy.length < 2) {
      throw new Error(`startArbitrage: pathBuy must be an address[] with at least 2 hops, got: ${JSON.stringify(decision.pathBuy)}`);
    }
    if (!Array.isArray(decision.pathSell) || decision.pathSell.length < 2) {
      throw new Error(`startArbitrage: pathSell must be an address[] with at least 2 hops, got: ${JSON.stringify(decision.pathSell)}`);
    }

    const routerBuy           = decision.routerBuy;
    const routerSell          = decision.routerSell;
    const pathBuy             = decision.pathBuy;
    const pathSell            = decision.pathSell;
    for (const [k, v] of [
      ['minIntermediateOut', decision.minIntermediateOut],
      ['minFinalOut', decision.minFinalOut],
      ['minProfit', decision.minProfit],
    ]) {
      if (!Number.isFinite(Number(v))) {
        throw new Error(`startArbitrage: ${k} is not a finite number (got: ${v}) — upstream caller likely sent NaN/undefined instead of a real threshold`);
      }
    }
    const minIntermediateOut  = BigInt(decision.minIntermediateOut);
    const minFinalOut         = BigInt(decision.minFinalOut);
    const minProfit           = BigInt(decision.minProfit);

    const callData = encodeFunctionData({
      abi: ARBITRAGE_ENGINE_ABI,
      functionName: 'startArbitrage',
      args: [
        asset, amount,
        routerBuy, routerSell, pathBuy, pathSell,
        minIntermediateOut, minFinalOut, minProfit,
      ],
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

    // ── LEDGER UPDATE ──────────────────────────────────────────────────────────
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