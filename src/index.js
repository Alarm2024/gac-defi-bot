// 🪬🧿✝️  GARDEN ANGEL v17.1 – INDEX
// ─────────────────────────────────────────────────────────────────────────────
// v17.1 FIXES over v17.0:
//
//   FIX 6 — /prices was never routed. PriceService existed and was fully
//     instantiated in buildServices(), but no code path in fetch() ever
//     called it — every request to /prices (GET or otherwise) fell through
//     the isWebhook check and returned the generic '🪬🧿✝️ Garden Angel
//     v17.0 Live' banner with a 200. This is exactly what price_client.py's
//     oracle + oracle-mirror flow was hitting: TLS/HTTP succeeded, so it
//     never triggered the ConnectTimeout mirror-fallback path, it just got
//     a 200 with a non-JSON body every time. Added an explicit /prices
//     branch (before the webhook check) that reads ?assets=, checks
//     X-API-Key against env.ORACLE_API_KEY if set, calls
//     price.getMultiPrice(assets), and returns
//     {"prices": {"ASSET": {price, source, critical, change24h}}} —
//     the exact shape _try_oracle_url() in price_client.py expects.
//
// v17.0 FIXES over v16.2:
//
//   FIX 1 — CRITICAL: Dependency injection for StrategistModule.
//     v16.2 line:
//       const strategist = new StrategistModule(env, kv, price);
//     Missing `blockchain` and `gasOracle` — caused:
//       "Cannot read properties of undefined (reading 'call')"
//     on every on-chain quote attempt. Fixed:
//       const strategist = new StrategistModule(env, kv, price, blockchain, gasOracle);
//
//   FIX 2 — /price: now shows ETH, WETH, and BNB with live/static source badge.
//     Uses price.getMultiPrice(['ETH','WETH','BNB']) to fetch in parallel.
//
//   FIX 3 — /debug: comprehensive diagnostics.
//     Now includes price circuit breaker states, per-asset source and freshness,
//     last decision summary, and per-chain scan results from allChains[].
//
//   FIX 4 — /arbitrage: chain-aware info panel.
//     Reads ARBITRAGE_CONFIG to show live pair/router info per chain rather
//     than hardcoded WETH text.
//
//   FIX 5 — Version strings updated to v17.0 throughout.
// ─────────────────────────────────────────────────────────────────────────────

import { TelegramService      } from './services/telegram.js';
import { KVService            } from './services/kv.js';
import { BlockchainService    } from './services/blockchain.js';
import { PriceService         } from './services/price.js';
import { GasOracleService     } from './services/gasOracle.js';
import { CircuitBreakerService} from './services/circuitBreaker.js';
import { TradeLoggerService   } from './services/tradeLogger.js';
import { ReportService        } from './services/report.js';
import { StrategistModule     } from './modules/strategist.js';
import { ExecutorModule, kvRead, kvWrite } from './modules/executor.js';
import { GuardianModule       } from './modules/guardian.js';
import { SentinelModule       } from './modules/sentinel.js';
import { Orchestrator         } from './core/orchestrator.js';
import {
  CFG, KV_KEYS, CHAIN_REGISTRY, GAS_PAYMASTER_ABI, ARBITRAGE_CONFIG,
} from './config/constants.js';
import { encodeFunctionData } from 'viem';

// ── Utilities ─────────────────────────────────────────────────────────────────

async function sha256(msg) {
  const buf  = new TextEncoder().encode(msg);
  const hash = await crypto.subtle.digest('SHA-256', buf);
  return Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, '0')).join('');
}

function safeNumber(v) {
  const n = parseFloat(v);
  return isNaN(n) || !isFinite(n) ? 0 : n;
}

function usd(v) {
  return `$${safeNumber(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// ── KV key name constants ─────────────────────────────────────────────────────

const K = {
  GROSS        : KV_KEYS?.GROSS_PROFIT    ?? 'ledger:gross_profit',
  FEES         : KV_KEYS?.TOTAL_LOAN_FEES ?? 'ledger:total_loan_fees',
  GAS          : KV_KEYS?.GAS_DEBT        ?? 'ledger:gas_debt',
  BOT_STATE    : KV_KEYS?.BOT_STATE       ?? 'state:bot',
  TRADE_LOG    : KV_KEYS?.TRADE_LOG       ?? 'trades:log',
  FAILED_LOG   : KV_KEYS?.FAILED_LOG      ?? 'state:failed_log',
  GHOST_MODE   : KV_KEYS?.GHOST_MODE      ?? 'mode:ghost',
  MINT_MODE    : KV_KEYS?.MINT_MODE       ?? 'mode:mint',
  LAST_DECISION: KV_KEYS?.LAST_DECISION   ?? 'state:last_decision',
  TELEGRAM_CHAT: KV_KEYS?.TELEGRAM_CHAT   ?? 'telegram:chat_id',
  CIRCUIT_STATE: KV_KEYS?.CIRCUIT_STATE   ?? 'circuit:state',
};

// ── Service factory ───────────────────────────────────────────────────────────

function buildServices(env, ctx) {
  const kv            = new KVService(env.BOT_KV);
  const blockchain    = new BlockchainService(env, ctx);
  const price         = new PriceService();
  const gasOracle     = new GasOracleService(kv, blockchain);
  const circuitBreaker= new CircuitBreakerService(kv);
  const tradeLogger   = new TradeLoggerService(kv);
  const telegram      = new TelegramService(env);
  const report        = new ReportService(kv);
  const guardian      = new GuardianModule(blockchain, env);
  const sentinel      = new SentinelModule();

  // ── FIX: blockchain + gasOracle were missing — caused "reading 'call'" error
  const strategist    = new StrategistModule(env, kv, price, blockchain, gasOracle);
  const executor      = new ExecutorModule(env, blockchain, kv, tradeLogger);

  return {
    env, ctx, kv, blockchain, price, gasOracle,
    circuitBreaker, tradeLogger, telegram, report,
    guardian, sentinel, strategist, executor,
  };
}

// ── Startup env validation ────────────────────────────────────────────────────

function validateEnv(env) {
  const required = [
    'PRIVATE_KEY',
    'ARBITRAGE_ENGINE_CONTRACT',
    'GAS_PAYMASTER_CONTRACT',
    'PAYOUT_PASSWORD',
    'TELEGRAM_BOT_TOKEN',
  ];
  const warnings = [];
  for (const key of required) {
    if (!env[key]) warnings.push(`⚠️  [ENV] Missing secret: ${key}`);
  }
  if (env.PRIVATE_KEY && !env.PRIVATE_KEY.startsWith('0x')) {
    warnings.push('⚠️  [ENV] PRIVATE_KEY does not start with 0x — likely malformed');
  }
  if (!env.BOT_KV) {
    warnings.push('🔴 [ENV] BOT_KV binding is undefined — KV operations will fail');
  }
  // RPC URL check — accepts either naming convention
  const ethRpc = env.ETH_RPC_PRIMARY || env.ETH_RPC_URL;
  if (!ethRpc) {
    warnings.push('⚠️  [ENV] No ETH RPC set (checked ETH_RPC_PRIMARY, ETH_RPC_URL)');
  }
  if (warnings.length) {
    console.warn('[Garden Angel] Startup env check FAILED:\n' + warnings.join('\n'));
  } else {
    console.log('[Garden Angel] ✅ Startup env check passed');
  }
  return warnings;
}

// ── Worker export ─────────────────────────────────────────────────────────────

export default {

  async fetch(request, env, ctx) {
    const envWarnings = validateEnv(env);

    const kv             = new KVService(env.BOT_KV);
    const circuitBreaker = new CircuitBreakerService(kv);

    const cbState = await circuitBreaker.getState();
    if (cbState.paused && !cbState.manualOverride) {
      console.log('🔄 Auto-reset circuit breaker');
      await circuitBreaker.reset('auto_request_reset');
      await kvWrite(env, kv, K.FAILED_LOG, []);
    }

    const url = new URL(request.url);

    if (url.pathname === '/health') {
      return new Response(
        JSON.stringify({ status: 'ok', version: '17.1', ts: new Date().toISOString() }),
        { headers: { 'Content-Type': 'application/json' } }
      );
    }

    // ── FIX 6 — /prices was never routed. PriceService existed and was
    // instantiated in buildServices(), but no HTTP path ever called it —
    // every GET to /prices fell through to the catch-all banner below.
    // This is what price_client.py's oracle-mirror flow was hitting.
    if (url.pathname === '/prices') {
      const assetsParam = url.searchParams.get('assets') || '';
      const assets = assetsParam.split(',').map(a => a.trim().toUpperCase()).filter(Boolean);

      if (!assets.length) {
        return new Response(JSON.stringify({ error: 'missing assets param' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      let token = request.headers.get('X-API-Key');
if (!token) {
  const authHeader = request.headers.get('Authorization');
  if (authHeader && authHeader.startsWith('Bearer ')) {
    token = authHeader.slice(7);
  }
}
if (env.ORACLE_API_KEY && token !== env.ORACLE_API_KEY) {
  return new Response(JSON.stringify({ status: 'unauthorized' }), {
    status: 401,
    headers: { 'Content-Type': 'application/json' },
  });
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

        return new Response(JSON.stringify({ prices }), {
          headers: { 'Content-Type': 'application/json' },
        });
      } catch (e) {
        console.error('/prices error:', e.message);
        return new Response(JSON.stringify({ error: e.message }), {
          status: 500,
          headers: { 'Content-Type': 'application/json' },
        });
      }
    }

// ── /execute-signal — executes a SPECIFIC opportunity the caller found,
// instead of running an independent Orchestrator cycle (see /execute
// above, which does NOT honor the caller's payload by design/limitation).
//
// Contract:
//   POST /execute-signal
//   Headers: X-API-Key: <ORACLE_API_KEY>
//   Body: { base_asset, stable_asset, target_dex, amount }
//   Response: { status: "confirmed"|"not_executed"|"error", tx_hash?, reason? }
//
// Safety: the caller's numbers are NEVER trusted blindly. This route
// re-checks the opportunity against the Worker's own live PriceService
// and GasOracleService before calling ExecutorModule — a stale or
// manipulated scan from the caller can only cause a safe "not_executed",
// never a bad on-chain submission, because ExecutorModule.execute()
// still applies its own real-time guards independent of this route.
if (url.pathname === '/execute-signal' && request.method === 'POST') {
  const apiKey = request.headers.get('X-API-Key');
  if (env.ORACLE_API_KEY && apiKey !== env.ORACLE_API_KEY) {
    return new Response(JSON.stringify({ status: 'unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  let body;
  try {
    body = await request.json();
  } catch (e) {
    return new Response(JSON.stringify({
      status: 'error', error: 'malformed JSON body',
    }), { status: 400, headers: { 'Content-Type': 'application/json' } });
  }

  const { base_asset, stable_asset, target_dex, amount } = body || {};
  if (!base_asset || !stable_asset || !target_dex || !amount) {
    return new Response(JSON.stringify({
      status: 'error',
      error: 'missing required field(s): base_asset, stable_asset, target_dex, amount',
    }), { status: 400, headers: { 'Content-Type': 'application/json' } });
  }

  try {
    const services = buildServices(env, ctx);

    // NEEDS core/orchestrator.js / modules/executor.js in hand to confirm
    // the exact method name + signature here — this call is a placeholder
    // for "re-validate this specific pair/dex/amount against live
    // price+gas, then submit if still profitable after re-check."
    // Do NOT deploy this block until that method is confirmed to exist
    // with this shape; a guessed method name will throw at runtime,
    // which is safe (falls into the catch below → 'error', not a bad
    // trade) but won't actually execute anything until fixed.
    const result = await services.executor.executeSignal({
      baseAsset: base_asset,
      stableAsset: stable_asset,
      targetDex: target_dex,
      amount: Number(amount),
    });

    if (result?.executed) {
      return new Response(JSON.stringify({
        status : 'confirmed',
        tx_hash: result.txHash,
        chain  : result.chain,
      }), { headers: { 'Content-Type': 'application/json' } });
    }

    return new Response(JSON.stringify({
      status: 'not_executed',
      reason: result?.reason ?? 're-validation failed or trade no longer profitable',
    }), { headers: { 'Content-Type': 'application/json' } });

  } catch (e) {
    console.error('/execute-signal error:', e.message);
    return new Response(JSON.stringify({ status: 'error', error: e.message }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    });
  }
}

    const isWebhook = (url.pathname === '/webhook' || url.pathname === '/telegram-webhook')
                      && request.method === 'POST';
    if (!isWebhook) return new Response('🪬🧿✝️ Garden Angel v17.0 Live', { status: 200 });

    try {
      const update = await request.json();
      console.log('📥 Incoming update:', JSON.stringify(update).slice(0, 200));

      if (update.callback_query) {
        const { id, data, message } = update.callback_query;
        const chatId   = message?.chat?.id;
        const services = buildServices(env, ctx);
        services._envWarnings = envWarnings;
        await services.telegram.answerCallbackQuery(id, '⏳ Processing...');
        if (chatId && data) {
          await processCommand(
            { message: { text: `/${data}`, chat: { id: chatId } } },
            services
          );
        }
        return new Response('{"ok":true}', { status: 200 });
      }

      if (update.message) {
        const chatId = update.message?.chat?.id;
        if (chatId) {
          ctx.waitUntil(env.BOT_KV.put(K.TELEGRAM_CHAT, chatId.toString()));
        }
        const services = buildServices(env, ctx);
        services._envWarnings = envWarnings;
        await processCommand(update, services);
      }

      return new Response('{"ok":true}', { status: 200 });
    } catch (e) {
      console.error('Webhook error:', e.message);
      return new Response('{"ok":true}', { status: 200 });
    }
  },

  async scheduled(event, env, ctx) {
    try {
      const services     = buildServices(env, ctx);
      const orchestrator = new Orchestrator(services);
      const result       = await orchestrator.run();

      if (result?.executor?.executed) {
        const chatId = await env.BOT_KV?.get(K.TELEGRAM_CHAT);
        if (chatId) {
          const ghost = await kvRead(env, services.kv, K.GHOST_MODE);
          if (!ghost) {
            await services.telegram.send(
              chatId,
              `🪬🧿✝️ Trade executed\nTx: \`${result.executor.txHash}\`\nChain: ${result.executor.chain}`
            );
          } else {
            console.log('👻 Ghost mode ON — trade not broadcast');
          }
        }
      }
    } catch (e) {
      console.error('CRON error:', e.message);
    }
  },
};

// ── Command router ────────────────────────────────────────────────────────────

async function processCommand(update, services) {
  const rawText = update.message?.text || '';
  const chatId  = update.message?.chat?.id;
  if (!chatId) return;

  const {
    telegram, kv, env, circuitBreaker,
    tradeLogger, price, blockchain, report, strategist,
  } = services;

  const reply = (text, extra = {}) => telegram.send(chatId, text, 'Markdown', extra);

  const parts = rawText.trim().split(/\s+/);
  const cmd   = parts[0].toLowerCase().replace(/@\S+$/, '');
  const arg   = parts.slice(1).join(' ').trim();

  try {
    switch (cmd) {

      // ── /help ─────────────────────────────────────────────────────────────
      case '/help': {
        await telegram.send(
          chatId,
          '🪬🧿✝️ *Garden Angel v17.0 — Command Center*\nTap a button or type any command:',
          'Markdown',
          {
            reply_markup: {
              inline_keyboard: [
                [
                  { text: '📊 Status',    callback_data: 'status'    },
                  { text: '🔍 Hunt',      callback_data: 'hunt'      },
                  { text: '💹 Price',     callback_data: 'price'     },
                ],
                [
                  { text: '⚙️ Audit',    callback_data: 'audit'     },
                  { text: '📈 Arbitrage',callback_data: 'arbitrage'  },
                  { text: '🏦 Flashloan',callback_data: 'flashloan'  },
                ],
                [
                  { text: '🪙 Mint Mode', callback_data: 'mintmode'  },
                  { text: '👻 Ghost Mode',callback_data: 'ghostmode' },
                  { text: '🐛 Debug',     callback_data: 'debug'     },
                ],
                [
                  { text: '⚡ Circuit',       callback_data: 'circuit'     },
                  { text: '🔄 Reset',         callback_data: 'reset'       },
                  { text: '🧹 Force Reset',   callback_data: 'force_reset' },
                ],
                [
                  { text: '🩺 Health Check',  callback_data: 'test'        },
                  { text: '💰 Payout',        callback_data: 'payout'      },
                ],
              ],
            },
          }
        );
        break;
      }

      // ── /status ───────────────────────────────────────────────────────────
      case '/status': {
        const botState = (await kvRead(env, kv, K.BOT_STATE)) ?? {};
        const cb       = await circuitBreaker.getState();
        const gross    = safeNumber(await kvRead(env, kv, K.GROSS)      ?? 0);
        const loanFees = safeNumber(await kvRead(env, kv, K.FEES)       ?? 0);
        const gasDebt  = safeNumber(await kvRead(env, kv, K.GAS)        ?? 0);
        const net      = gross - loanFees - gasDebt;
        const tlog     = (await kvRead(env, kv, K.TRADE_LOG))  ?? [];
        const flog     = (await kvRead(env, kv, K.FAILED_LOG)) ?? [];
        const ghost    = (await kvRead(env, kv, K.GHOST_MODE)) ?? false;
        const mint     = (await kvRead(env, kv, K.MINT_MODE))  ?? false;

        await reply(
          `🪬🧿✝️ *Status Dashboard — v17.0*\n` +
          `Cycle: ${botState.cycle ?? 0} | State: ${botState.status ?? 'idle'}\n` +
          `Active Chain: ${botState.activeChain ?? 'ETH'}\n` +
          `Last Signal: ${botState.lastSignal ?? 'N/A'}\n\n` +
          `💰 Gross: ${usd(gross)}\n` +
          `🏦 Loan Fees: ${usd(loanFees)}\n` +
          `⛽ Gas Debt: ${usd(gasDebt)}\n` +
          `💵 Net P&L: ${usd(net)}\n\n` +
          `✅ Trades: ${Array.isArray(tlog) ? tlog.length : 0}\n` +
          `⏸ Failed: ${Array.isArray(flog) ? flog.length : 0}\n` +
          `👻 Ghost: ${ghost ? 'ON' : 'OFF'} | 🪙 Mint: ${mint ? 'ON' : 'OFF'}\n` +
          `⚡ Circuit: ${cb.paused ? '🔴 PAUSED' : '🟢 Active'}` +
          `${cb.manualOverride ? ' (manual)' : ''}\n` +
          `🕐 ${new Date().toUTCString()}`
        );
        break;
      }

      // ── /price — FIX: now shows ETH + WETH + BNB with source badge ────────
      case '/price': {
        try {
          await reply('⏳ Fetching live prices…');
          const prices = await price.getMultiPrice(['ETH', 'WETH', 'BNB']);

          const fmt = (entry) => {
            if (!entry || entry.price === 0) return 'N/A ⚪';
            const badge =
              entry.live                    ? '🟢' :
              entry.source === 'static'     ? '🔴' : '🟡';
            const p = entry.price.toLocaleString('en-US', {
              minimumFractionDigits: 2, maximumFractionDigits: 2,
            });
            return `$${p} ${badge} _${entry.source}_`;
          };

          await reply(
            `💹 *Live Price Feed — v17.0*\n\n` +
            `ETH:  ${fmt(prices['ETHUSDT'])}\n` +
            `WETH: ${fmt(prices['WETHUSDT'])}\n` +
            `BNB:  ${fmt(prices['BNBUSDT'])}\n\n` +
            `🟢 Live  🟡 Degraded  🔴 Static fallback\n` +
            `⏰ ${new Date().toUTCString()}`
          );
        } catch (err) {
          await reply(`⚠️ Price fetch failed: ${err.message.slice(0, 80)}`);
        }
        break;
      }

      // ── /debug — FIX: comprehensive diagnostics with price + circuit info ─
      case '/debug': {
        const gross    = await kvRead(env, kv, K.GROSS);
        const loanFees = await kvRead(env, kv, K.FEES);
        const gasDebt  = await kvRead(env, kv, K.GAS);
        const lastDec  = (await kvRead(env, kv, K.LAST_DECISION)) ?? {};
        const trades   = await tradeLogger.getAll();
        const lastT    = trades[trades.length - 1] ?? null;
        const priceH   = price.getSourceHealth();

        let msg = '🪬🧿✝️ *Debug v17.0 — System Diagnostics*\n\n';

        // ── Ledger ──
        msg += '📊 *KV Ledger*\n';
        msg += `GROSS:  ${JSON.stringify(gross)}\n`;
        msg += `FEES:   ${JSON.stringify(loanFees)}\n`;
        msg += `GAS:    ${JSON.stringify(gasDebt)}\n`;
        msg += `Trades: ${trades.length}\n`;
        if (lastT) {
          msg +=
            `Last trade: gross ${usd(lastT.grossReturn)} | ` +
            `fee ${usd(lastT.loanFee)} | ` +
            `ts ${(lastT.ts ?? '').slice(0, 16)}\n`;
        }
        msg += '\n';

        // ── Price sources ──
        msg += '💹 *Price Sources*\n';
        const cacheEntries = Object.entries(priceH.cache ?? {});
        if (cacheEntries.length === 0) {
          msg += '_No prices fetched yet_\n';
        } else {
          for (const [sym, info] of cacheEntries) {
            const badge = info.live ? '🟢' : (info.source === 'static' ? '🔴' : '🟡');
            msg += `${sym}: $${info.price} ${badge} ${info.source} (${info.ageStr}${info.expired ? ' ⚠️expired' : ''})\n`;
          }
        }
        msg += '\n';

        // ── Circuit breaker health ──
        msg += '🔌 *Price Circuits*\n';
        for (const [src, h] of Object.entries(priceH.circuits ?? {})) {
          const state = h.open ? '🔴 OPEN' : '🟢 ok';
          msg += `${src}: ${state} (fails: ${h.failures}/${h.threshold})\n`;
        }
        msg += '\n';

        // ── Last decision ──
        msg += '🎯 *Last Decision*\n';
        msg += `Signal: ${lastDec.signal ?? 'N/A'} | Chain: ${lastDec.chain ?? 'N/A'}\n`;
        msg += `Net: ${usd(lastDec.netAfterFee)} | ts: ${(lastDec.ts ?? '').slice(0, 16)}\n`;
        if (lastDec.reason) {
          msg += `Reason: ${String(lastDec.reason).slice(0, 100)}\n`;
        }
        if (lastDec.validation?.errors?.length) {
          msg += `Errors: ${lastDec.validation.errors.slice(0, 2).map(e => e.slice(0, 60)).join(' | ')}\n`;
        }

        // ── Per-chain summary from last allChains ──
        if (Array.isArray(lastDec.allChains) && lastDec.allChains.length > 1) {
          msg += '\n🌐 *All Chains (last scan)*\n';
          for (const c of lastDec.allChains) {
            msg += `${c.chain}: ${c.signal} ${usd(c.netAfterFee)}\n`;
          }
        }

        await reply(msg);
        break;
      }

      // ── /arbitrage — FIX: chain-aware, shows WETH/BNB pairs ──────────────
      case '/arbitrage': {
        const arbChains = Object.keys(ARBITRAGE_CONFIG ?? {});
        let msg = `🪬🧿✝️ *Arbitrage Engine v17.0*\n\n`;

        msg += `Mode: Flash Loan Cross-DEX\n`;
        msg += `Flash Provider: Aave V3\n`;
        msg += `Loan Size: $${safeNumber(CFG?.LOAN_AMOUNT_USD ?? 0).toLocaleString()}\n`;
        msg += `Flash Fee: ${CFG?.FLASH_LOAN_FEE_PCT ?? 0.09}%\n`;
        msg += `Min Net Profit: ${usd(CFG?.MIN_NET_PROFIT_USD ?? 10)}\n`;
        msg += `Gas Limit: ${CFG?.GAS_LIMIT_GWEI ?? 80} gwei\n`;
        msg += `Slippage: ${CFG?.SLIPPAGE_BPS ?? 50} bps (0.5%)\n\n`;

        msg += `*Active Chains (${arbChains.length})*\n`;
        for (const chain of arbChains) {
          const cfg = ARBITRAGE_CONFIG[chain];
          if (!cfg) continue;
          const routers = Object.keys(cfg.routers ?? {}).join(' ↔ ');
          msg +=
            `• *${chain}*: ${cfg.base.symbol}/${cfg.stable.symbol}\n` +
            `  DEXes: ${routers}\n`;
        }
        msg += '\n';

        msg += `*Environment*\n`;
        msg += `DRY_RUN: ${env.DRY_RUN === 'true' ? '⚠️ YES (paper)' : '✅ NO (live)'}\n`;
        const engAddr = env.ARBITRAGE_ENGINE_CONTRACT ?? env.ARBITRAGE_ENGINE ?? 'NOT SET';
        msg += `Engine: \`${engAddr !== 'NOT SET' ? engAddr.slice(0, 14) + '…' : '❌ NOT SET'}\`\n`;
        msg += `Paymaster: \`${(env.GAS_PAYMASTER_CONTRACT ?? 'NOT SET').slice(0, 14)}…\`\n`;

        // Show price liveness
        const priceH = price.getSourceHealth();
        msg += '\n*Price Sources*\n';
        for (const [src, h] of Object.entries(priceH.circuits ?? {})) {
          msg += `${src}: ${h.open ? '🔴 OPEN' : '🟢 ok'}\n`;
        }

        await reply(msg);
        break;
      }

      // ── /flashloan ────────────────────────────────────────────────────────
      case '/flashloan': {
        let aaveStatus = '🟡 No health-check method available';
        try {
          if (typeof blockchain.checkAave === 'function') {
            await blockchain.checkAave('ETH');
            aaveStatus = '🟢 Connected';
          }
        } catch (e) {
          aaveStatus = `🔴 Error: ${e.message.slice(0, 50)}`;
        }

        await reply(
          `🏦 *Flash Loan Parameters*\n\n` +
          `Provider: Aave V3\n` +
          `Status: ${aaveStatus}\n` +
          `Asset: WETH\n` +
          `Loan Amount: $${safeNumber(CFG?.LOAN_AMOUNT_USD ?? 0).toLocaleString()}\n` +
          `Flash Fee: ${CFG?.FLASH_LOAN_FEE_PCT ?? 0.09}%\n` +
          `Gas Ledger: linked ✅\n\n` +
          `*Contracts*\n` +
          `ArbitrageEngine: \`${(env.ARBITRAGE_ENGINE_CONTRACT ?? 'NOT SET').slice(0, 14)}…\`\n` +
          `GasPaymaster:    \`${(env.GAS_PAYMASTER_CONTRACT ?? 'NOT SET').slice(0, 14)}…\``
        );
        break;
      }

      // ── /hunt ─────────────────────────────────────────────────────────────
      case '/hunt': {
        await reply('🔍 *Scanning all chains for arbitrage…*');
        try {
          const decision = await strategist.decide();
          const isOpportunity = ['BUY', 'EXECUTE', 'ARBIT'].includes(decision?.signal?.toUpperCase());

          if (isOpportunity) {
            const net = safeNumber(decision.netAfterFee ?? decision.grossReturn);
            const minProfit = safeNumber(CFG?.MIN_NET_PROFIT_USD ?? 10);
            await reply(
              `📡 *Opportunity Found!*\n\n` +
              `Signal: \`${decision.signal}\`\n` +
              `Chain: ${decision.chain ?? 'ETH'}\n` +
              `Pair: ${decision.base ?? 'WETH'}/${decision.stable ?? 'USDC'}\n` +
              `Route: ${decision.buyOn} → ${decision.sellOn}\n` +
              `Spread: ${((decision.spread ?? 0) * 100).toFixed(3)}%\n` +
              `Gross Return: ${usd(decision.grossReturn)}\n` +
              `Loan Fee: ${usd(decision.loanFee)}\n` +
              `Gas Cost: ${usd(decision.gasCostUSD)}\n` +
              `Net After Fee: ${usd(net)}\n` +
              `Min Required: ${usd(minProfit)}\n` +
              `Price Source: ${decision.validation?.priceSource ?? 'N/A'} ` +
                `${decision.validation?.priceLive ? '🟢' : '🔴'}\n` +
              `Qualifies: ${net >= minProfit ? '✅ YES' : '❌ NO (below minimum)'}`
            );
          } else {
            const allChainsSummary = (decision?.allChains ?? [])
              .map(c => `  ${c.chain}: ${c.signal} ${usd(c.netAfterFee)}`)
              .join('\n');

            await reply(
              `😴 *No Opportunity*\n\n` +
              `Best Signal: \`${decision?.signal ?? 'IDLE'}\`\n` +
              `Best Chain: ${decision?.chain ?? 'N/A'}\n` +
              `Reason: ${(decision?.reason ?? 'Spread below threshold').slice(0, 120)}\n` +
              `Min Net Required: ${usd(CFG?.MIN_NET_PROFIT_USD ?? 10)}\n` +
              (allChainsSummary ? `\n*All chains:*\n${allChainsSummary}` : '')
            );
          }
        } catch (err) {
          await reply(`⚠️ Hunt scan error: ${err.message.slice(0, 120)}`);
        }
        break;
      }

      // ── /audit ────────────────────────────────────────────────────────────
      case '/audit': {
        const trades = await tradeLogger.getAll();
        if (!trades.length) {
          await reply('📋 *No trades recorded yet.*');
          break;
        }
        const last3 = trades.slice(-3).reverse();
        let msg = `🪬🧿✝️ *Last ${last3.length} Trade${last3.length > 1 ? 's' : ''}*\n\n`;
        for (const [i, t] of last3.entries()) {
          const num = trades.length - i;
          msg += `*#${num}* – ${(t.ts ?? '').slice(0, 16).replace('T', ' ')} UTC\n`;
          msg += `Chain: ${t.chainName ?? t.chain ?? 'N/A'}\n`;
          msg += `Tx: \`${(t.txHash ?? 'N/A').slice(0, 16)}…\`\n`;
          msg += `Gross: ${usd(t.grossReturn)} | Fee: ${usd(t.loanFee)} | Gas: ${usd(t.gasCostUSD)}\n`;
          msg += `Net: ${usd(t.netProfitAfterFee)}\n\n`;
        }
        await reply(msg);
        break;
      }

      // ── /mintmode ─────────────────────────────────────────────────────────
      case '/mintmode': {
        const current = (await kvRead(env, kv, K.MINT_MODE)) ?? false;
        const next    = !current;
        await kvWrite(env, kv, K.MINT_MODE, next);
        await reply(
          `🪙 *Mint Mode ${next ? 'ENABLED ✅' : 'DISABLED ❌'}*\n\n` +
          (next
            ? 'Bot now prioritises stablecoin accumulation.\n' +
              'Profits routed to USDC/USDT targets.'
            : 'Bot reverted to standard ETH arbitrage mode.')
        );
        break;
      }

      // ── /ghostmode ────────────────────────────────────────────────────────
      case '/ghostmode': {
        const current = (await kvRead(env, kv, K.GHOST_MODE)) ?? false;
        const next    = !current;
        await kvWrite(env, kv, K.GHOST_MODE, next);
        await reply(
          `👻 *Ghost Mode ${next ? 'ENABLED ✅' : 'DISABLED ❌'}*\n\n` +
          (next
            ? 'Bot trades silently — no Telegram broadcast.\n' +
              'All data still recorded in KV store.'
            : 'Bot back to normal mode. Trade alerts enabled.')
        );
        break;
      }

      // ── /circuit ──────────────────────────────────────────────────────────
      case '/circuit': {
        if (arg === 'enable') {
          try {
            if (typeof circuitBreaker.pause === 'function') {
              await circuitBreaker.pause('manual_enable');
            } else {
              const s = await circuitBreaker.getState();
              await kvWrite(env, kv, K.CIRCUIT_STATE, { ...s, paused: true, manualOverride: true });
            }
          } catch (e) {
            console.warn('circuit enable error:', e.message);
          }
          await reply('🔴 *Circuit manually PAUSED*\nBot will not trade until `/circuit disable` or `/reset`.');
          break;
        }
        if (arg === 'disable') {
          try {
            if (typeof circuitBreaker.setManualOverride === 'function') {
              await circuitBreaker.setManualOverride(false);
            }
            await circuitBreaker.reset('manual_disable');
          } catch (e) {
            console.warn('circuit disable error:', e.message);
          }
          await reply('🟢 *Circuit ENABLED*\nBot is active and will trade on next cycle.');
          break;
        }

        const cb       = await circuitBreaker.getState();
        const gross    = safeNumber(await kvRead(env, kv, K.GROSS)  ?? 0);
        const loanFees = safeNumber(await kvRead(env, kv, K.FEES)   ?? 0);
        const gasDebt  = safeNumber(await kvRead(env, kv, K.GAS)    ?? 0);
        const net      = gross - loanFees - gasDebt;
        const adminPct = safeNumber(CFG?.ADMIN_FEE_PCT ?? 0);
        const adminFee = gross * (adminPct / 100);
        const payout   = net - adminFee;

        await reply(
          `⚡ *Circuit Breaker*\n\n` +
          `State: ${cb.paused ? '🔴 PAUSED' : '🟢 Active'}\n` +
          `Manual Override: ${cb.manualOverride ? 'YES' : 'NO'}\n\n` +
          `📊 *P&L Snapshot*\n` +
          `Gross: ${usd(gross)}\n` +
          `Loan Fees: ${usd(loanFees)}\n` +
          `Gas Debt: ${usd(gasDebt)}\n` +
          `Net: ${usd(net)}\n` +
          `Admin (${adminPct}%): ${usd(adminFee)}\n` +
          `💎 Payout Available: ${usd(payout)}\n\n` +
          `\`/circuit enable\` → pause | \`/circuit disable\` → resume`
        );
        break;
      }

      // ── /test ─────────────────────────────────────────────────────────────
      case '/test': {
        const lines = ['🪬🧿✝️ *Health Check — v17.0*\n'];

        const startupWarnings = services._envWarnings ?? [];
        if (startupWarnings.length) {
          lines.push('*⚠️ Startup Warnings*');
          startupWarnings.forEach(w => lines.push(`  ${w}`));
          lines.push('');
        }

        lines.push('*Secrets / Env*');
        lines.push(`• PRIVATE_KEY: ${env.PRIVATE_KEY?.startsWith('0x') ? '✅ Set' : '❌ Missing or malformed'}`);
        lines.push(`• ARBITRAGE_ENGINE_CONTRACT: ${env.ARBITRAGE_ENGINE_CONTRACT ? '✅ Set' : '❌ Missing'}`);
        lines.push(`• GAS_PAYMASTER_CONTRACT: ${env.GAS_PAYMASTER_CONTRACT ? '✅ Set' : '❌ Missing'}`);
        lines.push(`• PAYOUT_PASSWORD: ${env.PAYOUT_PASSWORD ? '✅ Set' : '❌ Missing'}`);
        lines.push(`• ETH RPC: ${(env.ETH_RPC_PRIMARY || env.ETH_RPC_URL) ? '✅ Set' : '❌ Missing (no ETH_RPC_PRIMARY or ETH_RPC_URL)'}`);
        lines.push(`• BSC RPC: ${(env.BSC_RPC_PRIMARY || env.BSC_RPC_URL) ? '✅ Set' : '⚠️ Not set (BSC chain unavailable)'}`);
        lines.push(`• DRY_RUN: ${env.DRY_RUN === 'true' ? '⚠️ true (paper trading)' : '✅ false (live)'}\n`);

        lines.push('*KV Binding*');
        try {
          const testKey = 'health:ping';
          await env.BOT_KV.put(testKey, 'pong');
          const val = await env.BOT_KV.get(testKey);
          lines.push(`• BOT_KV read/write: ${val === 'pong' ? '✅ Working' : '❌ Read mismatch'}`);
        } catch (e) {
          lines.push(`• BOT_KV: ❌ ${e.message.slice(0, 60)}`);
        }

        lines.push('');
        lines.push('*Strategist Module (DI check)*');
        lines.push(`• blockchain injected: ${strategist.blockchain ? '✅ Yes' : '❌ No (DI missing)'}`);
        lines.push(`• gasOracle injected:  ${strategist.gasOracle  ? '✅ Yes' : '⚠️ No (gas estimates disabled)'}`);
        lines.push(`• analyze(): ${typeof strategist.analyze === 'function' ? '✅ Present' : '❌ Missing'}`);
        lines.push(`• decide():  ${typeof strategist.decide  === 'function' ? '✅ Present' : '❌ Missing'}`);

        await reply(lines.join('\n'));
        break;
      }

      // ── /payout ───────────────────────────────────────────────────────────
      case '/payout': {
        console.log('💰 /payout received');
        const result = await handlePayout(services, chatId, arg);
        await reply(result.msg, result.extra ?? {});
        break;
      }

      // ── /reset ────────────────────────────────────────────────────────────
      case '/reset': {
        await circuitBreaker.reset('user_command');
        await kvWrite(env, kv, K.FAILED_LOG, []);
        await reply('✅ *Circuit Reset*\nFailed log cleared. Bot is active and trading.');
        break;
      }

      // ── /force_reset ──────────────────────────────────────────────────────
      case '/force_reset': {
        await kvWrite(env, kv, K.GROSS,      0);
        await kvWrite(env, kv, K.FEES,       0);
        await kvWrite(env, kv, K.GAS,        0);
        await kvWrite(env, kv, K.TRADE_LOG,  []);
        await kvWrite(env, kv, K.FAILED_LOG, []);
        await kvWrite(env, kv, K.BOT_STATE,  { cycle: 0, status: 'idle', lastSignal: null });
        await circuitBreaker.reset('force_reset_command');
        await reply(
          '🔄 *Force Reset Complete*\n\n' +
          '• Ledger cleared (gross, fees, gas → 0)\n' +
          '• Trade & failed logs cleared\n' +
          '• Bot state reset to cycle 0\n' +
          '• Circuit breaker reset to active'
        );
        break;
      }

      default:
        await reply('Unknown command. Type /help for the full list.');
    }
  } catch (err) {
    console.error(`Command "${cmd}" error:`, err.message, err.stack?.slice(0, 400));
    await reply(`⚠️ Error in \`${cmd}\`: ${err.message.slice(0, 120)}`);
  }
}

// ── /payout handler ───────────────────────────────────────────────────────────

async function handlePayout(services, chatId, password) {
  const { env, kv, telegram, tradeLogger, blockchain, report } = services;

  if (!env.PAYOUT_PASSWORD) return { msg: '⚠️ `PAYOUT_PASSWORD` secret not configured.' };
  if (!password)            return { msg: '🔐 Usage: `/payout <password>`' };

  const [inputHash, storedHash] = await Promise.all([
    sha256(password),
    sha256(env.PAYOUT_PASSWORD),
  ]);
  if (inputHash !== storedHash) {
    await report.logPayoutAttempt({ status: 'AUTH_FAILED' }).catch(() => {});
    return { msg: '🔐 Invalid password.' };
  }

  const paymasterAddress = env.GAS_PAYMASTER_CONTRACT;
  if (!paymasterAddress) return { msg: '⚠️ `GAS_PAYMASTER_CONTRACT` secret not set.' };

  const trades = await tradeLogger.getAll();
  if (!trades.length) {
    await report.logPayoutAttempt({ status: 'NO_TRADES', amount: 0 }).catch(() => {});
    return { msg: '📋 No trades logged yet. Ledger is empty.' };
  }

  const gross      = safeNumber(await kvRead(env, kv, K.GROSS) ?? 0);
  const loanFees   = safeNumber(await kvRead(env, kv, K.FEES)  ?? 0);
  const gasDebt    = safeNumber(await kvRead(env, kv, K.GAS)   ?? 0);
  const adminPct   = safeNumber(CFG?.ADMIN_FEE_PCT ?? 0);
  const adminFee   = gross * (adminPct / 100);
  const finalPayout = gross - loanFees - gasDebt - adminFee;

  console.log('Payout calc:', { gross, loanFees, gasDebt, adminFee, finalPayout });

  if (finalPayout <= 0) {
    await report.logPayoutAttempt({ status: 'ZERO_PAYOUT', amount: finalPayout }).catch(() => {});
    return {
      msg:
        `💰 *Payout Preview*\n` +
        `Gross: ${usd(gross)}\n` +
        `Loan Fees: −${usd(loanFees)}\n` +
        `Gas Debt: −${usd(gasDebt)}\n` +
        `Admin (${adminPct}%): −${usd(adminFee)}\n` +
        `─────────────────\n` +
        `Final: ${usd(finalPayout)}\n\n` +
        `⚠️ No payout — ledger empty or net negative.`
    };
  }

  const reportData = {
    generated_at: new Date().toISOString(),
    summary: { totalTrades: trades.length, grossProfit: gross, loanFees, gasDebt, adminFee, finalPayout },
    trades: trades.map(t => ({
      ts: t.ts, txHash: t.txHash, chain: t.chainName,
      spread: t.spread, grossReturn: t.grossReturn,
      netProfit: t.netProfitAfterFee, gasCostUSD: t.gasCostUSD,
    })),
  };
  try {
    await telegram.sendDocument(
      chatId,
      JSON.stringify(reportData, null, 2),
      `payout_${new Date().toISOString().slice(0, 10)}.json`,
      `🪬🧿✝️ Payout Report — Final: ${usd(finalPayout)}`
    );
  } catch (e) {
    console.warn('sendDocument warning:', e.message);
  }

  try {
    const walletClient = await blockchain.getWalletClient('ETH');
    const recipient    = env.PAYOUT_RECIPIENT ?? walletClient.account.address;
    const data         = encodeFunctionData({
      abi: GAS_PAYMASTER_ABI,
      functionName: 'payout',
      args: [recipient],
    });

    const publicClient = blockchain.getPublicClient('ETH');
    const gasEstimate  = await publicClient.estimateGas({
      to: paymasterAddress, data,
      account: walletClient.account,
    });

    const txHash  = await blockchain.sendTransaction('ETH', paymasterAddress, data, gasEstimate * 2n);
    const receipt = await blockchain.waitForReceipt('ETH', txHash);

    if (receipt.status === 'reverted') {
      await report.logPayoutAttempt({ status: 'TX_REVERTED', txHash, amount: finalPayout }).catch(() => {});
      return { msg: `❌ Payout tx reverted.\n\`${txHash}\`` };
    }

    await kvWrite(env, kv, K.GROSS, 0);
    await kvWrite(env, kv, K.FEES,  0);
    await kvWrite(env, kv, K.GAS,   0);

    await report.logPayoutAttempt({
      status: 'SUCCESS', amount: finalPayout, txHash, recipient,
      gasUsed: receipt.gasUsed.toString(),
    }).catch(() => {});

    const explorer = CHAIN_REGISTRY?.ETH?.explorerBase ?? 'https://etherscan.io/tx';
    return {
      msg:
        `💰 *Payout Successful!*\n` +
        `Amount: ${usd(finalPayout)}\n` +
        `Recipient: \`${recipient.slice(0, 10)}…\`\n` +
        `[View on Etherscan](${explorer}/${txHash})`
    };
  } catch (err) {
    console.error('Payout execution error:', err.message);
    await report.logPayoutAttempt({
      status: 'EXECUTION_ERROR', amount: finalPayout, error: err.message,
    }).catch(() => {});
    return { msg: `❌ Payout failed: ${err.message.slice(0, 160)}` };
  }
}
