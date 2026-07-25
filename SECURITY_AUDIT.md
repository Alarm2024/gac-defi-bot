> **DECOMMISSIONED — 2026-07-24**
> This Cloudflare Worker relay has been retired. The trading bot
> (Garden-Angel-Terminal) now runs standalone on AWS, talking directly to
> RPC/Jupiter/Telegram, and no longer depends on this Worker for price relay,
> trade confirmation, dashboard remote control, or Telegram proxying. This
> repo is kept for historical/audit reference only — do not redeploy without
> first confirming with the operator.

# Garden Angel — Security Audit & Optimization

_Scope: full repository (Cloudflare Worker in `src/`, Solidity in `contracts/` +
`GasReimbursement.sol`) plus the uploaded Python "HF Space" deployment
(`bot.py`, `scanner.py`, `payout_manager.py`, `executor.py`, `tls_fragment.py`,
`config.py`/`settings.py`, and ~30 supporting modules). Reviewed 2026-07-09._

## TL;DR

**No hidden transfer paths and no unauthorized destination addresses were
found.** Every point where the code moves value sends it to an address that
comes from an operator-controlled environment variable (or an owner-supplied
argument in the contracts). The only 40-hex addresses hardcoded anywhere are
canonical, publicly-known token/router/pool contracts. There is no obfuscated
logic, no `eval`/`base64`/`pickle` dynamic execution, no hardcoded private key
or mnemonic, and no unexpected outbound host.

The one item that deserves your explicit attention is **`tls_fragment.py`**, a
TLS ClientHello-fragmentation module built to evade SNI-based network filtering
(see §3). It is dual-use; it is used to reach your own price oracle, but you
should confirm you want it in the deployment.

---

## 1. Where funds can move (the thing you asked me to verify)

I traced every value-moving path end-to-end. All destinations are yours:

| Path | Destination | Source of destination |
|------|-------------|-----------------------|
| Worker `handlePayout()` → `GasPaymaster.payout(recipient)` | `recipient` | `env.PAYOUT_RECIPIENT ?? walletClient.account.address` (`src/index.js`) |
| `GasPaymaster.payout(address recipient)` | `recipient` | argument, `onlyOwner` |
| `GasReimbursement.payout(recipient, maxNet)` / `withdrawExcess(to)` | `recipient` / `to` | argument, `onlyOwner` |
| Python `PayoutManager._run_onchain_sweep` / `emergency_sweep` | `self._wallet` | `PAYOUT_WALLET` env, checksum-validated, zero-address refused |
| Python `ChainExecutor.sweep(cold_wallet, …)` | `cold_wallet` | passed from `PayoutManager._wallet` (i.e. `PAYOUT_WALLET`) |

Reassuring specifics:

- **No hardcoded recipient.** A repo-wide scan for `0x`-addresses returns only
  WETH, WBNB, WBTC, BTCB, USDC, USDT, DAI, the Uniswap/Sushi/Pancake/Biswap
  routers, and the Aave V3 pools — all canonical infrastructure. The only other
  address is the **zero address**, and it appears solely as a *refuse-to-send
  guard* in `payout_manager.py` (it aborts startup if `PAYOUT_WALLET` is the
  burn address).
- **`payout_manager.py` is defensively written**, not drainer-style: it
  confirms every sweep on-chain before counting it as success, gates on gas
  price and a minimum gas-balance floor, caps single sweeps
  (`MAX_SWEEP_USD`), re-checks balance immediately before broadcast, holds an
  `asyncio.Lock` across all sweep entry points, throttles `/payout` attempts,
  and refuses to run if `PAYOUT_WALLET` equals the hot wallet.
- **Secrets are handled correctly.** `PAYOUT_PRIVATE_KEY`/`PAYOUT_PASSWORD` are
  `repr=False`, never logged, and a process-wide log-redaction factory
  (`logger.py`, `secret_redaction_filter.py`) scrubs registered secrets from
  every log line.

### Caveats you must close yourself

1. **This audit is of the *source*. It only protects you if the deployed
   bytecode matches it.** Deploy `ArbitrageEngine.sol` / `GasPaymaster.sol`
   **yourself** so your wallet is `owner`, then verify the on-chain bytecode.
   If someone hands you a pre-deployed `GAS_PAYMASTER_CONTRACT` address, they
   are `owner`, `payout()` is `onlyOwner`, and your accrued profit would be
   withdrawable only by them — that is the classic trap for this class of bot,
   and it lives entirely in *who deployed the contract*, not in this code.
2. **`ArbitrageEngine._performSwap` is a stub** (`profit = amount * 10 / 1000;
   // 1% mock profit`). It does no real swap. Deployed as-is it will take a
   flash loan it cannot repay and revert — you lose gas, not principal, but the
   bot cannot actually earn. Do not run it live expecting profit until the swap
   routing is implemented.

---

## 2. Bugs fixed in this PR (Worker)

These are correctness/safety fixes to the code that is actually in this repo:

1. **Dead-code 404 killed the entire command surface** (`src/index.js`). The
   line after the `isWebhook` check was an *unconditional* `return 404`, so the
   webhook handler below it was unreachable — every Telegram command (`/status`,
   `/hunt`, `/payout`, `/circuit`, …) returned "Not found" and never executed.
   Now guarded with `if (!isWebhook)`. This restores your exclusive Telegram
   control (tasks 3 & 4).
2. **`/execute-signal` failed open and called a non-existent method.** The auth
   check (`env.ORACLE_API_KEY && …`) was skipped entirely when the key was
   unset, leaving a state-changing route world-open; and it invoked
   `executor.executeSignal()`, which does not exist. It now **fails closed**
   (403 when no key is configured) and returns **501 Not Implemented** instead
   of wiring an external caller's payload into the execution path. On-chain
   execution now happens *only* through the scheduled Orchestrator cycle, which
   re-derives every opportunity from the Worker's own price/gas services.
3. **`TradeLogger` method mismatch** (`src/services/tradeLogger.js`). The
   Orchestrator called `logFailure()` and the Executor called `logSuccess()`,
   but only `log()`/`logFailed()` existed — so every execute/hold path threw
   `logFailure is not a function` and aborted the cycle. Added the two aliases.
4. **Committed editor swap file removed.** `.package.json.swp` (a nano swap
   file) was committed and leaked the author's local username/host. Removed and
   `*.swp` added to `.gitignore`.
5. **Added `.env.example`** documenting every environment variable the Worker
   reads, with security guidance inline (task 2 — "clean, secure environment
   variables"). Note there is **no `constants.py`** in this repo; Worker config
   lives in `src/config/constants.js` (already complete after its v17 pass) and
   Python config in `config.py`/`settings.py`.

---

## 3. Items flagged for your decision (not changed)

- **`tls_fragment.py` — TLS ClientHello fragmentation with stealth
  randomization.** Its own header describes "stealth-first" behavior: adaptive
  per-write jitter, randomized chunk sizes to make traffic "less
  fingerprintable," and retry across "different random fragmentation profiles."
  This is a DPI/SNI censorship-circumvention technique. In context it is used to
  reach *your own* Cloudflare Worker oracle when `*.workers.dev` is SNI-blocked,
  which is a legitimate anti-censorship use — but it is dual-use and you should
  consciously decide whether it belongs in your deployment. I did **not** modify
  or extend it.
- **`_performSwap` stub** and the **two duplicate `BlockchainService` files**
  (`src/blockchain.js` and `src/services/blockchain.js` are byte-identical) —
  fold to one to avoid drift. Left as-is to keep this PR focused.
- **Hot-wallet exposure.** `PRIVATE_KEY`/`PAYOUT_PRIVATE_KEY` are live signing
  keys in the process environment. Keep the signing wallet minimally funded
  (just enough for gas) and sweep profit to a separate cold `PAYOUT_WALLET`,
  which is exactly what `payout_manager.py` is built to do.

---

## 3a. Runtime findings from the live HF Space log (2026-07-09)

The production startup log surfaced things the source alone doesn't, and they
bear directly on "is the execution path under my exclusive control?" — the
honest answer today is **no, not fully.**

- **Your price oracle AND trade-execution endpoint run on someone else's
  infrastructure.** `ORACLE_URL=https://garden-angel-production.elghaly.workers.dev`
  and the Telegram proxy `tg-proxy.elghaly.workers.dev` are **`elghaly`**
  Cloudflare Workers; your own host is **`wyndham`**
  (`wyndham.pythonanywhere.com`). On every BUY the scanner calls the oracle's
  `/execute` to confirm/execute the trade, and it reads prices from `/prices`.
  Whoever controls that Worker controls the price the bot trusts and the
  execution confirmation. **This is the single biggest gap between the current
  setup and "exclusive control."** Fix: deploy the (now-corrected) Worker in
  this repo under **your own** domain and point `ORACLE_URL` at it.
- **Both oracle endpoints are currently broken** — they return the plain-text
  banner `Bot Live` instead of JSON (`oracle-mirror non-JSON (200). Body: Bot
  Live`), and `elghaly`'s Worker returns `HTTP 403 error 1010`. That non-JSON
  banner is exactly the `/prices` / dead-`404` bug fixed in §2.1–2.2 of this PR.
  Net effect right now: the scanner finds opportunities (e.g. `BUY on BSC: WBTC
  … net +$213.95`) but **every execution is skipped** ("Execution unconfirmed").
  So despite `dry_run=False`, no arbitrage trades are actually landing.
- **Two wallet addresses you must independently verify are yours:**
  - Signing / gas hot wallet: `0x535151bDE5B471f5925b445266C70f4f0961193d`
    (this is the one you fund).
  - Payout / cold destination `PAYOUT_WALLET`: `0x2C256C78d7…`
    (this is where sweeps go).
  Both come from env vars (not hardcoded), and they are correctly *different*
  addresses (proper hot/cold split). But confirm you personally hold the keys
  to **both** — especially `0x2C256C78d7…`. If this Space was set up for you
  with `PAYOUT_WALLET` pre-filled, that address is where your profit leaves to,
  and the source cannot tell you whether it's yours.
- **Telegram bot token transits third-party proxies.** Routes include
  `tg-proxy.elghaly.workers.dev` and `garden-angel-production.elghaly.workers.dev`.
  Any Worker the token passes through can read it. Consider rotating the bot
  token and routing Telegram only through your own host / `api.telegram.org`.
- **Good news in the log:** secret redaction works (`/bot***REDACTED***/`), the
  hot/cold split is real, price data falls back to OKX (live) when the oracle is
  down, and the BSC-only hard-lock is active.

## 4. "Mempool monitoring" note

There is no mempool/pending-transaction monitoring in the codebase. The Worker
strategist polls DEX `getAmountsOut` quotes on a cron; the Python `scanner.py`
polls prices on an interval. Both are pull-based quote scanners, not mempool
listeners. The execution loop is now error-free after the fixes in §2, but if
you specifically want pending-tx mempool monitoring that is a new feature to
build (it would need a WebSocket `eth_subscribe("newPendingTransactions")` feed,
which Cloudflare Workers cannot hold open — it belongs in the Python service).
