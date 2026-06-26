// 🪬🧿✝️  GARDEN ANGEL v17.1 – STRATEGIST (LIVE CROSS-DEX)
// ─────────────────────────────────────────────────────────────────────────────
// v17.1 FIX over v17.0:
//
//   FIX — Engine address env key mismatch.
//     v17.0 _validateDecision() read: this.env.ARBITRAGE_ENGINE
//     But index.js and wrangler.toml use: ARBITRAGE_ENGINE_CONTRACT
//     The mismatch meant validation always failed with "ARBITRAGE_ENGINE address
//     not set", blocking every BUY even when the contract is correctly configured.
//
//     v17.1 checks both keys in order:
//       env.ARBITRAGE_ENGINE ?? env.ARBITRAGE_ENGINE_CONTRACT ?? chainCfg.engineAddress
//
//   All other v17.0 logic is unchanged — see v17.0 header for full change log.
// ─────────────────────────────────────────────────────────────────────────────

import {
  CFG,
  KV_KEYS,
  UNISWAP_ROUTER_ABI,
  ARBITRAGE_CONFIG,
} from '../config/constants.js';
import {
  encodeFunctionData,
  decodeFunctionResult,
  parseUnits,
  isAddress,
} from 'viem';

// ── ArbitrageEngine ABI ───────────────────────────────────────────────────────
// ⚠️  This must match your deployed ArbitrageEngine.sol exactly.

const ARBITRAGE_ENGINE_ABI = [
  {
    name            : 'executeArbitrage',
    type            : 'function',
    stateMutability : 'nonpayable',
    inputs          : [
      { name: 'flashLoanToken',  type: 'address'   },
      { name: 'flashLoanAmount', type: 'uint256'   },
      { name: 'buyRouter',       type: 'address'   },
      { name: 'sellRouter',      type: 'address'   },
      { name: 'buyPath',         type: 'address[]' },
      { name: 'sellPath',        type: 'address[]' },
      { name: 'baseOutMin',      type: 'uint256'   },
      { name: 'stableOutMin',    type: 'uint256'   },
      { name: 'deadline',        type: 'uint256'   },
    ],
    outputs: [],
  },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

function safeNumber(v) {
  const n = parseFloat(v);
  return isNaN(n) || !isFinite(n) ? 0 : n;
}

function bigintSafe(s) {
  try { return BigInt(s ?? '0'); } catch { return 0n; }
}

// ── StrategistModule ──────────────────────────────────────────────────────────

export class StrategistModule {
  constructor(env, kv, priceService, blockchainService, gasOracleService = null) {
    this.env        = env;
    this.kv         = kv;
    this.price      = priceService;
    this.blockchain = blockchainService;
    this.gasOracle  = gasOracleService;
    this.log        = console;

    this.routerAddress = env.UNISWAP_ROUTER || ARBITRAGE_CONFIG.ETH?.routers?.UNISWAP;
    this.WETH          = ARBITRAGE_CONFIG.ETH?.base?.address;
    this.USDC          = ARBITRAGE_CONFIG.ETH?.stable?.address;
  }

  // ── Live on-chain quote ───────────────────────────────────────────────────

  async _quoteOut(chain, router, amountIn, path) {
    if (!amountIn || amountIn === 0n) {
      throw new Error(`_quoteOut called with zero amountIn for ${chain}`);
    }

    const data = encodeFunctionData({
      abi          : UNISWAP_ROUTER_ABI,
      functionName : 'getAmountsOut',
      args         : [amountIn, path],
    });

    const raw = await this.blockchain.call(chain, router, data);

    if (!raw || raw === '0x') {
      throw new Error(`Empty eth_call response from router ${router} on ${chain}`);
    }

    const amounts = decodeFunctionResult({
      abi          : UNISWAP_ROUTER_ABI,
      functionName : 'getAmountsOut',
      data         : raw,
    });

    const out = amounts[amounts.length - 1];
    if (out === undefined || out === 0n) {
      throw new Error(`Router returned zero output for path ${path.join('→')} on ${chain}`);
    }

    return out;
  }

  // ── Build calldata for ArbitrageEngine.executeArbitrage() ─────────────────

  _buildExecuteCalldata(chainKey, decision) {
    const chainCfg = ARBITRAGE_CONFIG[chainKey];
    const deadline = BigInt(Math.floor(Date.now() / 1000) + 300);

    const calldata = encodeFunctionData({
      abi          : ARBITRAGE_ENGINE_ABI,
      functionName : 'executeArbitrage',
      args         : [
        chainCfg.stable.address,
        bigintSafe(decision.loanAmountUnits),
        decision.buyRouter,
        decision.sellRouter,
        [chainCfg.stable.address, chainCfg.base.address],
        [chainCfg.base.address,   chainCfg.stable.address],
        bigintSafe(decision.baseOutMin),
        bigintSafe(decision.stableOutMin),
        deadline,
      ],
    });

    const executeParams = {
      flashLoanToken  : chainCfg.stable.address,
      flashLoanAmount : bigintSafe(decision.loanAmountUnits).toString(),
      buyRouter       : decision.buyRouter,
      sellRouter      : decision.sellRouter,
      buyPath         : [chainCfg.stable.address, chainCfg.base.address],
      sellPath        : [chainCfg.base.address,   chainCfg.stable.address],
      baseOutMin      : bigintSafe(decision.baseOutMin).toString(),
      stableOutMin    : bigintSafe(decision.stableOutMin).toString(),
      deadline        : deadline.toString(),
      deadlineISO     : new Date(Number(deadline) * 1000).toISOString(),
    };

    return { calldata, executeParams, abi: ARBITRAGE_ENGINE_ABI };
  }

  // ── Validate decision ─────────────────────────────────────────────────────

  _validateDecision(chainKey, decision) {
    const errors   = [];
    const warnings = [];
    const chainCfg = ARBITRAGE_CONFIG[chainKey];

    if (!decision.buyRouter || !isAddress(decision.buyRouter)) {
      errors.push(`buyRouter "${decision.buyRouter}" is not a valid address`);
    }
    if (!decision.sellRouter || !isAddress(decision.sellRouter)) {
      errors.push(`sellRouter "${decision.sellRouter}" is not a valid address`);
    }
    if (decision.buyRouter === decision.sellRouter) {
      errors.push('buyRouter and sellRouter are the same address');
    }

    const loanAmt   = bigintSafe(decision.loanAmountUnits);
    const baseMin   = bigintSafe(decision.baseOutMin);
    const stableMin = bigintSafe(decision.stableOutMin);

    if (loanAmt   === 0n) errors.push('loanAmountUnits is 0');
    if (baseMin   === 0n) errors.push('baseOutMin is 0 — buy-leg quote likely failed');
    if (stableMin === 0n) errors.push('stableOutMin is 0 — sell-leg quote likely failed');

    if (safeNumber(decision.grossReturn) <= 0) {
      errors.push(`grossReturn is non-positive ($${safeNumber(decision.grossReturn).toFixed(4)})`);
    }

    const baseAsset = chainCfg?.base?.symbol ?? decision.base;
    if (!this.price.isLive(baseAsset)) {
      const src = this.price.getPriceSource(baseAsset);
      errors.push(
        `${baseAsset} price source is "${src ?? 'unknown'}" (not live). ` +
        `BUY blocked — check Binance/CoinGecko/Kraken/Coinbase connectivity.`
      );
    }

    // ── FIX: check both ARBITRAGE_ENGINE and ARBITRAGE_ENGINE_CONTRACT ────
    const engineAddr =
      this.env.ARBITRAGE_ENGINE ??
      this.env.ARBITRAGE_ENGINE_CONTRACT ??
      chainCfg?.engineAddress;

    if (!engineAddr || !isAddress(engineAddr)) {
      errors.push(
        `ArbitrageEngine address not set or invalid. ` +
        `Set env.ARBITRAGE_ENGINE_CONTRACT in wrangler.toml secrets.`
      );
    }

    return {
      valid        : errors.length === 0,
      errors,
      warnings,
      engineAddress: engineAddr ?? null,
      priceSource  : this.price.getPriceSource(baseAsset),
      priceLive    : this.price.isLive(baseAsset),
    };
  }

  // ── Scan a single chain ───────────────────────────────────────────────────

  async _scanChain(chainKey) {
    const chainCfg = ARBITRAGE_CONFIG[chainKey];
    if (!chainCfg) throw new Error(`No arbitrage config for chain "${chainKey}"`);

    const { stable, base, routers, gasPriceAsset } = chainCfg;
    const routerNames = Object.keys(routers);

    if (routerNames.length < 2) {
      return {
        chain      : chainKey, signal: 'HOLD',
        reason     : `Only ${routerNames.length} DEX router on ${chainKey} — need ≥2`,
        grossReturn: 0, loanFee: 0, gasCostUSD: 0, netAfterFee: 0, spread: 0,
        stable     : stable.symbol, base: base.symbol,
        ts         : new Date().toISOString(),
      };
    }

    const loanAmountUnits = parseUnits(String(CFG.LOAN_AMOUNT_USD), stable.decimals);
    let best = null;
    const pairLog = [];

    for (const buyName of routerNames) {
      for (const sellName of routerNames) {
        if (buyName === sellName) continue;
        const label = `${buyName}→${sellName}`;
        try {
          const baseOut   = await this._quoteOut(
            chainKey, routers[buyName], loanAmountUnits, [stable.address, base.address]
          );
          const stableOut = await this._quoteOut(
            chainKey, routers[sellName], baseOut, [base.address, stable.address]
          );
          pairLog.push({ pair: label, stableOut: stableOut.toString(), ok: true });
          if (!best || stableOut > best.stableOut) {
            best = { buyOn: buyName, sellOn: sellName, baseOut, stableOut };
          }
        } catch (e) {
          pairLog.push({ pair: label, error: e.message, ok: false });
          this.log.warn(`[Strategist] ${chainKey} quote ${label} failed: ${e.message}`);
        }
      }
    }

    if (!best) {
      return {
        chain      : chainKey, signal: 'HOLD',
        reason     : 'All on-chain quotes failed — check RPC health and router addresses',
        pairLog,
        grossReturn: 0, loanFee: 0, gasCostUSD: 0, netAfterFee: 0, spread: 0,
        stable     : stable.symbol, base: base.symbol,
        ts         : new Date().toISOString(),
      };
    }

    const diffUnits   = best.stableOut - loanAmountUnits;
    const grossReturn = Number(diffUnits) / (10 ** stable.decimals);
    const loanFeePct  = safeNumber(CFG.FLASH_LOAN_FEE_PCT ?? 0.09) / 100;
    const loanFee     = CFG.LOAN_AMOUNT_USD * loanFeePct;
    const spread      = grossReturn / CFG.LOAN_AMOUNT_USD;

    let gasCostUSD = 0;
    if (this.gasOracle) {
      try {
        const nativePrice = await this.price.getPrice(gasPriceAsset ?? 'ETH');
        gasCostUSD = await this.gasOracle.estimateGasCostUSD(
          nativePrice, chainKey, CFG.ARB_GAS_UNITS ?? 900_000n
        );
      } catch (e) {
        this.log.warn(`[Strategist] gas estimate failed for ${chainKey}: ${e.message}`);
      }
    }

    const netAfterFee  = grossReturn - loanFee - gasCostUSD;
    const minProfit    = safeNumber(this.env.MIN_NET_PROFIT_USD ?? CFG.MIN_NET_PROFIT_USD ?? 10);

    const slipBps      = BigInt(CFG.SLIPPAGE_BPS ?? 50);
    const baseOutMin   = best.baseOut   - (best.baseOut   * slipBps) / 10_000n;
    const stableOutMin = best.stableOut - (best.stableOut * slipBps) / 10_000n;

    const decision = {
      chain          : chainKey,
      buyOn          : best.buyOn,
      sellOn         : best.sellOn,
      buyRouter      : routers[best.buyOn],
      sellRouter     : routers[best.sellOn],
      stable         : stable.symbol,
      base           : base.symbol,
      loanAmount     : CFG.LOAN_AMOUNT_USD,
      loanAmountUnits: loanAmountUnits.toString(),
      baseOut        : best.baseOut.toString(),
      baseOutMin     : baseOutMin.toString(),
      stableOut      : best.stableOut.toString(),
      stableOutMin   : stableOutMin.toString(),
      grossReturn,
      loanFee,
      gasCostUSD,
      netAfterFee,
      minProfit,
      spread,
      pairLog,
      ts             : new Date().toISOString(),
    };

    const validation = this._validateDecision(chainKey, decision);
    decision.validation = validation;

    if (netAfterFee >= minProfit && validation.valid) {
      decision.signal = 'BUY';
      decision.reason = null;
      try {
        const { calldata, executeParams } = this._buildExecuteCalldata(chainKey, decision);
        decision.calldata      = calldata;
        decision.executeParams = executeParams;
        decision.engineAddress = validation.engineAddress;
      } catch (e) {
        decision.signal       = 'HOLD';
        decision.calldata     = null;
        decision.calldataError = e.message;
        decision.reason       = `Calldata build failed: ${e.message}`;
        this.log.error(`[Strategist] calldata build failed for ${chainKey}: ${e.message}`);
      }

    } else if (netAfterFee >= minProfit && !validation.valid) {
      decision.signal = 'HOLD_VALIDATION_FAILED';
      decision.reason = `Profitable ($${netAfterFee.toFixed(2)}) but blocked: ${validation.errors.join('; ')}`;
      this.log.warn(`[Strategist] ${chainKey} BUY blocked by validation: ${decision.reason}`);

    } else {
      decision.signal = 'HOLD';
      decision.reason = `net $${netAfterFee.toFixed(2)} < min $${minProfit.toFixed(2)}`;
    }

    return decision;
  }

  // ── Public: scan chains, return best decision ─────────────────────────────

  async decide(chainsOrLegacyPriceData = Object.keys(ARBITRAGE_CONFIG), _legacyGasData = null) {
    const chains = Array.isArray(chainsOrLegacyPriceData)
      ? chainsOrLegacyPriceData
      : Object.keys(ARBITRAGE_CONFIG);

    if (chains.length === 0) throw new Error('[Strategist] No chains to scan');

    const results = [];
    for (const c of chains) {
      try {
        results.push(await this._scanChain(c));
      } catch (e) {
        this.log.warn(`[Strategist] _scanChain(${c}) threw: ${e.message}`);
        results.push({
          chain: c, signal: 'ERROR', reason: e.message,
          grossReturn: 0, netAfterFee: 0, ts: new Date().toISOString(),
        });
      }
    }

    if (!results.length) throw new Error('[Strategist] No chains scanned successfully');

    results.sort((a, b) => safeNumber(b.netAfterFee) - safeNumber(a.netAfterFee));
    const decision = { ...results[0], allChains: results };

    await this.kv.putJSON(KV_KEYS.LAST_DECISION, decision);
    this.log.info('[Strategist] decision', {
      chain      : decision.chain,
      signal     : decision.signal,
      netAfterFee: safeNumber(decision.netAfterFee).toFixed(2),
      priceSource: this.price.getPriceSource(decision.base),
      hasCalldata: !!decision.calldata,
    });

    return decision;
  }

  async analyze(_input = {}, chains) {
    return this.decide(chains);
  }

  async getLastDecision() {
    try {
      return await this.kv.getJSON(KV_KEYS.LAST_DECISION);
    } catch {
      return null;
    }
  }
}
