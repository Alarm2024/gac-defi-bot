// 🪬🧿✝️  Constants – v17.0
// ─────────────────────────────────────────────────────────────────────────────
// v17 FIXES over v16.1:
//
//   FIX 1 — ARBITRAGE_CONFIG was completely absent.
//     The StrategistModule imports ARBITRAGE_CONFIG from this file, so its
//     absence caused "Cannot read properties of undefined" on every chain scan.
//     Added ETH and BSC configs with verified token addresses and router pairs.
//
//   FIX 2 — CHAIN_REGISTRY RPC env key names updated to match wrangler.toml.
//     v16 used ETH_RPC_URL / FALLBACK_ETH_RPC but the worker env sets
//     ETH_RPC_PRIMARY / ETH_RPC_SECONDARY. The mismatch caused the
//     "No RPC URL set for ETH" warning and public-default fallback on every call.
//
//   FIX 3 — Missing KV_KEYS entries (MINT_MODE, CIRCUIT_STATE).
//     index.js references K.MINT_MODE and K.CIRCUIT_STATE but they weren't
//     exported, causing silent undefined key collisions.
//
//   FIX 4 — Missing CFG entries used by strategist + gasOracle:
//     MIN_NET_PROFIT_USD, FLASH_LOAN_FEE_PCT, SLIPPAGE_BPS, ARB_GAS_UNITS.
// ─────────────────────────────────────────────────────────────────────────────

import { mainnet, bsc } from 'viem/chains';

// ── Custom chain: Unichain ────────────────────────────────────────────────────

export const UNICHAIN_MAINNET = {
  id: 130,
  name: 'Unichain',
  nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
  rpcUrls: {
    default: { http: ['https://mainnet.unichain.org'] },
    public : { http: ['https://mainnet.unichain.org'] },
  },
  blockExplorers: { default: { name: 'Uniscan', url: 'https://uniscan.xyz' } },
};

// ── Chain registry ─────────────────────────────────────────────────────────────
// FIX: rpcEnvKey / fallbackRpcEnvKey now match the wrangler.toml var names.
//      Old: ETH_RPC_URL / FALLBACK_ETH_RPC  → New: ETH_RPC_PRIMARY / ETH_RPC_SECONDARY

export const CHAIN_REGISTRY = {
  ETH: {
    id: 1, name: 'Ethereum', viemChain: mainnet,
    rpcEnvKey        : 'ETH_RPC_PRIMARY',        // was 'ETH_RPC_URL'
    fallbackRpcEnvKey: 'ETH_RPC_SECONDARY',       // was 'FALLBACK_ETH_RPC'
    explorerBase: 'https://etherscan.io/tx', gasSymbol: 'ETH', priceKey: 'ETHUSDT',
  },
  BSC: {
    id: 56, name: 'BSC', viemChain: bsc,
    rpcEnvKey        : 'BSC_RPC_PRIMARY',         // was 'BSC_RPC_URL'
    fallbackRpcEnvKey: 'BSC_RPC_SECONDARY',        // was 'FALLBACK_BSC_RPC'
    explorerBase: 'https://bscscan.com/tx', gasSymbol: 'BNB', priceKey: 'BNBUSDT',
  },
  UNICHAIN: {
    id: 130, name: 'Unichain', viemChain: UNICHAIN_MAINNET,
    rpcEnvKey        : 'UNICHAIN_RPC_URL',
    fallbackRpcEnvKey: null,
    explorerBase: 'https://uniscan.xyz/tx', gasSymbol: 'ETH', priceKey: 'ETHUSDT',
  },
};

// ── Arbitrage config ───────────────────────────────────────────────────────────
// FIX: This object was completely missing in v16.1.
//      StrategistModule imports and depends on it for every chain scan.
//
// ⚠️  engineAddress: set to null here — always supply via env.ARBITRAGE_ENGINE_CONTRACT
//     so the address stays in secrets and not in source code.

export const ARBITRAGE_CONFIG = {
  ETH: {
    stable: {
      symbol  : 'USDC',
      address : '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
      decimals: 6,
    },
    base: {
      symbol  : 'WETH',
      address : '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
      decimals: 18,
    },
    routers: {
      UNISWAP  : '0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D',   // Uniswap V2 Router02
      SUSHISWAP: '0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F',   // SushiSwap Router
    },
    engineAddress: null,   // override via env.ARBITRAGE_ENGINE_CONTRACT
    gasPriceAsset: 'ETH',
  },

  BSC: {
    stable: {
      symbol  : 'USDT',
      address : '0x55d398326f99059fF775485246999027B3197955',   // BSC-USDT (18 dec)
      decimals: 18,
    },
    base: {
      symbol  : 'WBNB',
      address : '0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c',
      decimals: 18,
    },
    routers: {
      PANCAKESWAP: '0x10ED43C718714eb63d5aA57B78B54704E256024E',  // PancakeSwap V2
      BISWAP     : '0x3a6d8cA21D1CF76F653A67577FA0D27453350dD8',  // Biswap
    },
    engineAddress: null,   // override via env.ARBITRAGE_ENGINE_CONTRACT_BSC
    gasPriceAsset: 'BNB',
  },
};

// ── KV key registry ───────────────────────────────────────────────────────────
// FIX: Added MINT_MODE and CIRCUIT_STATE which index.js references via K.MINT_MODE
//      and K.CIRCUIT_STATE but weren't defined here.

export const KV_KEYS = {
  BOT_STATE      : 'bot_state',
  TRADE_LOG      : 'trade_log',
  FAILED_LOG     : 'failed_attempts',
  CIRCUIT        : 'circuit_breaker',
  CIRCUIT_STATE  : 'circuit:state',          // NEW — used by /circuit command
  GAS_READINGS   : 'gas_readings',
  LAST_DECISION  : 'last_decision',
  TELEGRAM_CHAT  : 'TELEGRAM_CHAT_ID',
  GAS_DEBT       : 'gas_debt',
  GROSS_PROFIT   : 'gross_profit',
  TOTAL_LOAN_FEES: 'total_loan_fees',
  GHOST_MODE     : 'ghost_mode',
  MINT_MODE      : 'mode:mint',               // NEW — used by /mintmode command
};

// ── Bot-wide config ───────────────────────────────────────────────────────────
// FIX: Added MIN_NET_PROFIT_USD, FLASH_LOAN_FEE_PCT, SLIPPAGE_BPS, ARB_GAS_UNITS
//      which strategist.js and gasOracle.js read but were absent.

export const CFG = {
  // Flash loan
  LOAN_AMOUNT_USD        : 100_000,
  MIN_LOAN_USD           : 10_000,
  MAX_LOAN_USD           : 1_000_000,
  FLASH_LOAN_FEE_PCT     : 0.09,             // NEW — Aave V3 = 0.09%
  MIN_NET_PROFIT_USD     : 10,               // NEW — minimum net to trigger BUY

  // Gas
  GAS_LIMIT_GWEI         : 80,
  GAS_UNITS              : 500_000n,
  ARB_GAS_UNITS          : 900_000n,         // NEW — gas estimate for arb tx

  // Execution
  SLIPPAGE_BPS           : 50,               // NEW — 0.5% slippage tolerance

  // Circuit breaker
  CIRCUIT_FAIL_LIMIT     : 10,
  CIRCUIT_WINDOW_MS      : 5 * 60 * 1000,
  CIRCUIT_RESET_MS       : 3_600_000,

  // Logging
  LOG_RING               : 50,
  GAS_RING               : 10,

  // Fees
  ADMIN_FEE_PCT          : 0,
  AUTO_RECOVERY_FAIL_LIMIT: 3,
};

// ── ABIs ──────────────────────────────────────────────────────────────────────

export const ARBITRAGE_ENGINE_ABI = [
  {
    inputs: [
      { name: 'asset',  type: 'address' },
      { name: 'amount', type: 'uint256' },
      { name: 'params', type: 'bytes'   },
    ],
    name: 'executeArbitrage',
    outputs: [],
    stateMutability: 'nonpayable',
    type: 'function',
  },
  {
    inputs: [
      { name: 'assets',    type: 'address[]' },
      { name: 'amounts',   type: 'uint256[]' },
      { name: 'premiums',  type: 'uint256[]' },
      { name: 'initiator', type: 'address'   },
      { name: 'params',    type: 'bytes'     },
    ],
    name: 'executeOperation',
    outputs: [{ name: '', type: 'bool' }],
    stateMutability: 'nonpayable',
    type: 'function',
  },
  // v17.1 FIX — startArbitrage was completely absent from this ABI, even
  // though executor.js called encodeFunctionData({ abi: ARBITRAGE_ENGINE_ABI,
  // functionName: 'startArbitrage', ... }) directly against it. Added here
  // to match contract_manager.py's FLASH_ARBITRAGE_V2_ABI exactly (the
  // Python side's source of truth for the real deployed contract) —
  // 9 args, with pathBuy/pathSell as full address[] swap paths rather
  // than a single intermediate token. See executor.js's v17.1 changelog
  // for the full incident writeup on why this mismatch matters.
  {
    inputs: [
      { name: 'asset',               type: 'address'   },
      { name: 'amount',              type: 'uint256'   },
      { name: 'routerBuy',           type: 'address'   },
      { name: 'routerSell',          type: 'address'   },
      { name: 'pathBuy',             type: 'address[]' },
      { name: 'pathSell',            type: 'address[]' },
      { name: 'minIntermediateOut',  type: 'uint256'   },
      { name: 'minFinalOut',         type: 'uint256'   },
      { name: 'minProfit',           type: 'uint256'   },
    ],
    name: 'startArbitrage',
    outputs: [],
    stateMutability: 'nonpayable',
    type: 'function',
  },
  // v17.1 — also added for completeness/future use: quoteRoundTrip, matching
  // contract_manager.py's quote_round_trip(). Not currently called from
  // executor.js, but useful if/when a pre-broadcast quote check is wired in.
  {
    inputs: [
      { name: 'routerBuy',  type: 'address'   },
      { name: 'routerSell', type: 'address'   },
      { name: 'pathBuy',    type: 'address[]' },
      { name: 'pathSell',   type: 'address[]' },
      { name: 'amount',     type: 'uint256'   },
    ],
    name: 'quoteRoundTrip',
    outputs: [
      { name: 'intermediateOut', type: 'uint256' },
      { name: 'finalOut',        type: 'uint256' },
    ],
    stateMutability: 'view',
    type: 'function',
  },
];

export const GAS_PAYMASTER_ABI = [
  {
    inputs: [
      { name: '_grossProfit', type: 'uint256' },
      { name: '_gasCost',     type: 'uint256' },
      { name: '_loanFee',     type: 'uint256' },
    ],
    name: 'recordTrade',
    outputs: [],
    stateMutability: 'nonpayable',
    type: 'function',
  },
  {
    inputs: [{ name: 'recipient', type: 'address' }],
    name: 'payout',
    outputs: [],
    stateMutability: 'nonpayable',
    type: 'function',
  },
  {
    inputs: [],
    name: 'getLedger',
    outputs: [
      { name: '', type: 'uint256' },
      { name: '', type: 'uint256' },
      { name: '', type: 'uint256' },
      { name: '', type: 'uint256' },
    ],
    stateMutability: 'view',
    type: 'function',
  },
];

// FIX (v16.1 carry-over): amountIn is the first param — was absent in v16.0.
export const UNISWAP_ROUTER_ABI = [
  {
    inputs: [
      { name: 'amountIn',     type: 'uint256'   },
      { name: 'amountOutMin', type: 'uint256'   },
      { name: 'path',         type: 'address[]' },
      { name: 'to',           type: 'address'   },
      { name: 'deadline',     type: 'uint256'   },
    ],
    name: 'swapExactTokensForTokens',
    outputs: [{ name: '', type: 'uint256[]' }],
    stateMutability: 'nonpayable',
    type: 'function',
  },
  {
    inputs: [
      { name: 'amountIn', type: 'uint256'   },
      { name: 'path',     type: 'address[]' },
    ],
    name: 'getAmountsOut',
    outputs: [{ name: '', type: 'uint256[]' }],
    stateMutability: 'view',
    type: 'function',
  },
]