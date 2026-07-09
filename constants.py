"""
constants.py — Garden Angel single configuration gate (Python / HF Space)
──────────────────────────────────────────────────────────────────────────────
Python mirror of src/config/constants.js. One place to SEE EVERYTHING the bot
is configured with: chains, token/router/pool addresses, tuning knobs, ABIs,
and which environment variables drive the secret/endpoint config.

SECURITY MODEL — read this before trusting the file
  • The only hardcoded 0x-addresses here are CANONICAL, publicly-known
    infrastructure (WETH/WBNB/USDC/USDT, the Uniswap/Sushi/Pancake/Biswap
    routers, the Aave V3 pools). They are safe to keep in source.
  • Every address the bot can SEND FUNDS TO — the arbitrage engine, the gas
    paymaster, and the payout/sweep destination — is read from an environment
    variable and defaults to None/"". Nothing that moves money is hardcoded.
  • Secret VALUES (private keys, passwords, tokens, api keys) are NEVER printed
    by summary(); only whether they are set. Do not add code that logs them.

Usage
    from constants import (
        CHAIN_REGISTRY, ARBITRAGE_CONFIG, CFG, KV_KEYS,
        SECRET_ENV_KEYS, ENDPOINT_ENV_KEYS, FUND_ROUTING_ENV_KEYS,
        summary, validate,
    )
    print(summary())            # human-readable config dump (secrets redacted)
    for w in validate():
        print("⚠️", w)

Run it directly to print the gate:
    python constants.py
"""

from __future__ import annotations

import os

# ─────────────────────────────────────────────────────────────────────────────
# Chain registry — mirror of CHAIN_REGISTRY in constants.js
#   rpc_env_key / fallback_rpc_env_key name the env vars that hold the RPC URLs.
# ─────────────────────────────────────────────────────────────────────────────
CHAIN_REGISTRY: dict[str, dict] = {
    "ETH": {
        "id": 1,
        "name": "Ethereum",
        "rpc_env_key": "ETH_RPC_PRIMARY",
        "fallback_rpc_env_key": "ETH_RPC_SECONDARY",
        "explorer_base": "https://etherscan.io/tx",
        "gas_symbol": "ETH",
        "price_key": "ETHUSDT",
    },
    "BSC": {
        "id": 56,
        "name": "BSC",
        "rpc_env_key": "BSC_RPC_PRIMARY",
        "fallback_rpc_env_key": "BSC_RPC_SECONDARY",
        "explorer_base": "https://bscscan.com/tx",
        "gas_symbol": "BNB",
        "price_key": "BNBUSDT",
    },
    "UNICHAIN": {
        "id": 130,
        "name": "Unichain",
        "rpc_env_key": "UNICHAIN_RPC_URL",
        "fallback_rpc_env_key": None,
        "explorer_base": "https://uniscan.xyz/tx",
        "gas_symbol": "ETH",
        "price_key": "ETHUSDT",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Arbitrage config — mirror of ARBITRAGE_CONFIG in constants.js
#   engine_address is intentionally None: always supply it via
#   ARBITRAGE_ENGINE_CONTRACT / ARBITRAGE_ENGINE_CONTRACT_BSC so a payout
#   target never lives in source.
# ─────────────────────────────────────────────────────────────────────────────
ARBITRAGE_CONFIG: dict[str, dict] = {
    "ETH": {
        "stable": {"symbol": "USDC", "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "decimals": 6},
        "base":   {"symbol": "WETH", "address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "decimals": 18},
        "routers": {
            "UNISWAP":   "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",  # Uniswap V2 Router02
            "SUSHISWAP": "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F",  # SushiSwap Router
        },
        "engine_env_key": "ARBITRAGE_ENGINE_CONTRACT",
        "engine_address": None,   # resolved from env at runtime — see PAYOUT/engine_address()
        "gas_price_asset": "ETH",
    },
    "BSC": {
        "stable": {"symbol": "USDT", "address": "0x55d398326f99059fF775485246999027B3197955", "decimals": 18},
        "base":   {"symbol": "WBNB", "address": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", "decimals": 18},
        "routers": {
            "PANCAKESWAP": "0x10ED43C718714eb63d5aA57B78B54704E256024E",  # PancakeSwap V2
            "BISWAP":      "0x3a6d8cA21D1CF76F653A67577FA0D27453350dD8",  # Biswap
        },
        "engine_env_key": "ARBITRAGE_ENGINE_CONTRACT_BSC",
        "engine_address": None,
        "gas_price_asset": "BNB",
    },
}

# Aave V3 pool addresses (public infrastructure) — mirror of blockchain.js.
AAVE_V3_POOL: dict[str, str | None] = {
    "ETH": "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
    "BSC": "0x6807dc923806fE8Fd134338EABCA509979a7e0cB",
    "UNICHAIN": None,
}

# ─────────────────────────────────────────────────────────────────────────────
# KV / ledger key registry — mirror of KV_KEYS in constants.js (kept for parity
# and visibility; the Python service persists to SQLite, not Cloudflare KV).
# ─────────────────────────────────────────────────────────────────────────────
KV_KEYS: dict[str, str] = {
    "BOT_STATE": "bot_state",
    "TRADE_LOG": "trade_log",
    "FAILED_LOG": "failed_attempts",
    "CIRCUIT": "circuit_breaker",
    "CIRCUIT_STATE": "circuit:state",
    "GAS_READINGS": "gas_readings",
    "LAST_DECISION": "last_decision",
    "TELEGRAM_CHAT": "TELEGRAM_CHAT_ID",
    "GAS_DEBT": "gas_debt",
    "GROSS_PROFIT": "gross_profit",
    "TOTAL_LOAN_FEES": "total_loan_fees",
    "GHOST_MODE": "ghost_mode",
    "MINT_MODE": "mode:mint",
}

# ─────────────────────────────────────────────────────────────────────────────
# Bot-wide tuning — mirror of CFG in constants.js.
#   (JS BigInt gas units become plain ints here.)
# ─────────────────────────────────────────────────────────────────────────────
CFG: dict[str, float | int] = {
    # Flash loan
    "LOAN_AMOUNT_USD": 100_000,
    "MIN_LOAN_USD": 10_000,
    "MAX_LOAN_USD": 1_000_000,
    "FLASH_LOAN_FEE_PCT": 0.09,      # Aave V3 = 0.09%
    "MIN_NET_PROFIT_USD": 10,        # minimum net to trigger BUY

    # Gas
    "GAS_LIMIT_GWEI": 80,
    "GAS_UNITS": 500_000,
    "ARB_GAS_UNITS": 900_000,

    # Execution
    "SLIPPAGE_BPS": 50,              # 0.5% slippage tolerance

    # Circuit breaker
    "CIRCUIT_FAIL_LIMIT": 10,
    "CIRCUIT_WINDOW_MS": 5 * 60 * 1000,
    "CIRCUIT_RESET_MS": 3_600_000,

    # Logging
    "LOG_RING": 50,
    "GAS_RING": 10,

    # Fees
    "ADMIN_FEE_PCT": 0,
    "AUTO_RECOVERY_FAIL_LIMIT": 3,
}

# ─────────────────────────────────────────────────────────────────────────────
# ABIs — plain Python (web3.py-compatible) mirrors of constants.js.
# ─────────────────────────────────────────────────────────────────────────────
ARBITRAGE_ENGINE_ABI: list[dict] = [
    {
        "inputs": [
            {"name": "asset", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "params", "type": "bytes"},
        ],
        "name": "executeArbitrage",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "assets", "type": "address[]"},
            {"name": "amounts", "type": "uint256[]"},
            {"name": "premiums", "type": "uint256[]"},
            {"name": "initiator", "type": "address"},
            {"name": "params", "type": "bytes"},
        ],
        "name": "executeOperation",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

GAS_PAYMASTER_ABI: list[dict] = [
    {
        "inputs": [
            {"name": "_grossProfit", "type": "uint256"},
            {"name": "_gasCost", "type": "uint256"},
            {"name": "_loanFee", "type": "uint256"},
        ],
        "name": "recordTrade",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "recipient", "type": "address"}],
        "name": "payout",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "getLedger",
        "outputs": [
            {"name": "", "type": "uint256"},
            {"name": "", "type": "uint256"},
            {"name": "", "type": "uint256"},
            {"name": "", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

UNISWAP_ROUTER_ABI: list[dict] = [
    {
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForTokens",
        "outputs": [{"name": "", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path", "type": "address[]"},
        ],
        "name": "getAmountsOut",
        "outputs": [{"name": "", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Environment-driven config (the "gate")
#   These functions read os.environ every call, so a Space restart that injects
#   new secrets is picked up without editing this file. Nothing is cached and
#   no secret VALUE is ever returned by the summary helpers.
# ─────────────────────────────────────────────────────────────────────────────

# Names of the env vars that hold secrets. summary() only reports set/unset,
# never the value.
SECRET_ENV_KEYS: tuple[str, ...] = (
    "PRIVATE_KEY",           # Worker signing key
    "PAYOUT_PRIVATE_KEY",    # Python sweep signing key
    "PAYOUT_PRIVATE_KEY_ETH",
    "PAYOUT_PASSWORD",
    "TELEGRAM_BOT_TOKEN",
    "ORACLE_API_KEY",
)

# Names of the env vars that hold public endpoints / addresses / tunables.
ENDPOINT_ENV_KEYS: tuple[str, ...] = (
    "ORACLE_URL",
    "ORACLE_MIRROR_URL",
    "ETH_RPC_PRIMARY", "ETH_RPC_SECONDARY", "ETH_RPC_URL",
    "BSC_RPC_PRIMARY", "BSC_RPC_SECONDARY", "BSC_RPC_URL",
    "UNICHAIN_RPC_URL",
    "TELEGRAM_CHAT_ID", "TELEGRAM_LOG_CHANNEL_ID",
)

# Env vars that name where funds can go / which contracts are used.
# These are what you MUST verify point at addresses you control.
FUND_ROUTING_ENV_KEYS: tuple[str, ...] = (
    "PAYOUT_WALLET",                    # Python sweep destination (cold wallet)
    "PAYOUT_WALLET_ETH",
    "PAYOUT_RECIPIENT",                 # Worker GasPaymaster.payout() recipient
    "FLASH_ARBITRAGE_CONTRACT_ADDRESS", # HF-Space FlashArbitrageV2 (funds withdraw to its owner)
    "ARBITRAGE_ENGINE_CONTRACT",        # Worker arbitrage engine
    "ARBITRAGE_ENGINE_CONTRACT_BSC",
    "GAS_PAYMASTER_CONTRACT",           # Worker gas paymaster
    "PAYOUT_CHAIN",                     # BSC (default) | ETH
)


def env(key: str, default: str = "") -> str:
    """Read a single env var, stripped."""
    return os.getenv(key, default).strip()


def engine_address(chain: str) -> str | None:
    """Resolve the arbitrage-engine contract address for a chain from env."""
    cfg = ARBITRAGE_CONFIG.get(chain.upper())
    if not cfg:
        return None
    return os.getenv(cfg["engine_env_key"], "").strip() or None


def rpc_url(chain: str) -> str | None:
    """Resolve the RPC URL for a chain: primary env key, then fallback."""
    cfg = CHAIN_REGISTRY.get(chain.upper())
    if not cfg:
        return None
    for k in (cfg["rpc_env_key"], cfg["fallback_rpc_env_key"]):
        if k and os.getenv(k, "").strip():
            return os.getenv(k).strip()
    return None


def _redact(key: str) -> str:
    return "✅ set" if os.getenv(key, "").strip() else "❌ unset"


def validate() -> list[str]:
    """Return human-readable warnings for missing/likely-misconfigured env.
    Does not raise and never echoes a secret value."""
    warnings: list[str] = []

    # Chain-aware: an ETH deployment (PAYOUT_CHAIN=ETH + ALLOW_ETH=1) uses the
    # *_ETH wallet/key vars, so checking the BSC names would false-positive.
    payout_chain = env("PAYOUT_CHAIN", "BSC").upper()
    is_eth = payout_chain == "ETH" and env("ALLOW_ETH", "0") == "1"
    wallet_key = "PAYOUT_WALLET_ETH" if is_eth else "PAYOUT_WALLET"
    pk_key = "PAYOUT_PRIVATE_KEY_ETH" if is_eth else "PAYOUT_PRIVATE_KEY"

    if not env(wallet_key):
        warnings.append(f"{wallet_key} unset — on-chain sweeps will no-op.")
    if not env(pk_key):
        warnings.append(f"{pk_key} unset — payouts are accounting-only.")
    if not env("PAYOUT_PASSWORD"):
        warnings.append("PAYOUT_PASSWORD unset — /payout command disabled.")
    if not env("TELEGRAM_BOT_TOKEN"):
        warnings.append("TELEGRAM_BOT_TOKEN unset — Telegram control disabled.")
    if not env("ORACLE_URL"):
        warnings.append("ORACLE_URL unset — price oracle source unavailable.")
    if payout_chain != "BSC" and env("ALLOW_ETH", "0") != "1":
        warnings.append("PAYOUT_CHAIN != BSC but ALLOW_ETH != 1 — Python payout hard-locked to BSC.")
    return warnings


def summary() -> str:
    """Human-readable dump of the full configuration gate. Secret VALUES are
    never included — only whether each secret is set."""
    lines: list[str] = []
    lines.append("🪬🧿✝️  Garden Angel — configuration gate")
    lines.append("=" * 60)

    lines.append("\n[Chains]")
    for name, c in CHAIN_REGISTRY.items():
        lines.append(f"  {name:<9} id={c['id']:<4} rpc={rpc_url(name) or '(public default)'}")

    lines.append("\n[Arbitrage pairs]")
    for name, c in ARBITRAGE_CONFIG.items():
        routers = " ↔ ".join(c["routers"].keys())
        lines.append(f"  {name:<4} {c['base']['symbol']}/{c['stable']['symbol']}  DEXes: {routers}")
        lines.append(f"       engine({c['engine_env_key']}): {engine_address(name) or '❌ NOT SET'}")

    lines.append("\n[Secrets] (value never shown)")
    for k in SECRET_ENV_KEYS:
        lines.append(f"  {k:<24} {_redact(k)}")

    lines.append("\n[Fund routing] (verify these are addresses YOU control)")
    for k in FUND_ROUTING_ENV_KEYS:
        val = env(k)
        shown = val if val else "❌ unset"
        lines.append(f"  {k:<32} {shown}")

    lines.append("\n[Tuning]")
    for k in ("LOAN_AMOUNT_USD", "MIN_NET_PROFIT_USD", "FLASH_LOAN_FEE_PCT",
              "SLIPPAGE_BPS", "GAS_LIMIT_GWEI", "ADMIN_FEE_PCT"):
        lines.append(f"  {k:<20} {CFG[k]}")

    warns = validate()
    lines.append("\n[Warnings]")
    lines.extend(f"  ⚠️ {w}" for w in warns) if warns else lines.append("  none")

    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())
