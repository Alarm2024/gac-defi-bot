# Garden Angel — Go-Live Runbook (read fully before risking funds)

This is an honest checklist, not a "it's safe now" certificate. The code changes
in this PR remove real bugs and tighten control, but several go-live
prerequisites are things **only you** can do, and a few residual risks cannot be
engineered away. Do the DRY-RUN pass first, every time.

## Straight talk on the current state

- **"Exclusive control" is not true yet.** Your price oracle, the `/execute`
  endpoint, and the Telegram proxy still run on third-party Workers
  (`elghaly.workers.dev`). Until you deploy the Worker under your own account
  and point `ORACLE_URL` at it, a third party sits in your pricing and
  execution path. This is the #1 blocker and only you can close it.
- **The arbitrage contract is a framework, not a finished product.**
  `FlashArbitrageV2.sol` says so itself and marks several addresses "VERIFY
  before use". Profitable flash-loan arbitrage after fees + gas + slippage is
  rare and highly competitive; treat "+$X spread" scan hits as unproven until a
  real tx confirms a real profit.
- **I cannot verify the wallets are yours.** The signing wallet
  (`0x535151bD…`) and `PAYOUT_WALLET` (`0x2C256C78d7…`) are env-set (good), but
  only you can confirm you hold both private keys.

Because of the above, do **not** run live with meaningful funds until you have
personally completed the checklist and watched a DRY-RUN behave correctly.

## Payout architecture (after this PR)

Funds move in exactly **one** place: the Hugging Face `PayoutManager`
(`/payout`, `/sweep`), which sweeps the hot wallet to `PAYOUT_WALLET` and logs
every sweep to your Telegram log channel. The Cloudflare Worker's `/payout` is
now **report-only** — it never signs or broadcasts a fund-moving transaction.
This matches "payout only from Hugging Face" and removes the Worker key as a
fund-moving path.

## Prerequisite checklist (you)

1. **Deploy the Worker to YOUR Cloudflare account.**
   - Fill `wrangler.toml` → `kv_namespaces[0].id` with your real `BOT_KV`
     namespace id (`npx wrangler kv namespace list`). The current placeholder is
     why the build fails after the entry-point fix.
   - `npx wrangler secret put` each secret (see `.env.example`).
   - Point the Hugging Face `ORACLE_URL` at your Worker's domain, not
     `garden-angel-production.elghaly.workers.dev`.
2. **Be the deployer/owner of `FlashArbitrageV2`.** Zero-arg constructor, so
   deploy it yourself on BSC; confirm `owner()` == your signing wallet. Set
   `FLASH_ARBITRAGE_CONTRACT_ADDRESS` to that address.
3. **Verify wallets.** Confirm you control `PAYOUT_WALLET` and the signing
   wallet. Fund the **signing** wallet with a little BNB for gas — not
   `PAYOUT_WALLET`.
4. **Rotate the Telegram bot token** (it transited third-party proxies).
5. **Set the config gate.** Run `python constants.py` in the Space — every line
   under `[Fund routing]` must show an address you recognize, and `[Warnings]`
   should be empty.

## Fix required in the Space: `contract_manager.py` withdraw ABI

The deployed `FlashArbitrageV2.withdraw(address token)` takes **one** argument
and sends the full balance to `owner`. `contract_manager.py` currently declares
and calls a **two**-argument `withdraw(token, amount)`, so `/withdraw` reverts
(selector mismatch) and you cannot recover stuck tokens. Apply both edits, then
verify against the actual deployed contract on BscScan before relying on it.

In the `FLASH_ARBITRAGE_V2_ABI` JSON, replace the withdraw entry:

```json
{"inputs": [{"internalType": "address", "name": "token", "type": "address"}],
 "name": "withdraw", "outputs": [], "stateMutability": "nonpayable", "type": "function"}
```

And change the method to match (one argument):

```python
def withdraw(self, token: str) -> str:
    """Withdraw the full balance of `token` to the contract owner.
    Matches the deployed 1-arg withdraw(address)."""
    fn = self.contract.functions.withdraw(Web3.to_checksum_address(token))
    return self.client.send(fn)
```

## DRY-RUN first (do this every deploy)

1. Set `DRY_RUN=true` (Worker) and `PAYOUT_DRY_RUN=true` (Space). Restart the
   Space so it picks up the secrets (a saved secret does not hot-reload).
2. Confirm startup log shows: your oracle reachable, price feed live, executor
   ready on the correct signing address, `payout_ready=✅`.
3. Trigger `/hunt` and `/sweep ghost` — confirm the simulated sweep targets
   `PAYOUT_WALLET` and the amounts look right, with **no** real broadcast.
4. Only then, with a **small** test balance, flip `PAYOUT_DRY_RUN=false` and do
   one `/sweep live`. Verify the tx on BscScan lands in `PAYOUT_WALLET`.
5. Watch several full scan cycles confirm real, profitable executions before
   scaling any amount up.

## Residual risks you are accepting by going live

- Real-money loss from failed/reverting arbitrage (gas burned) or from
  unprofitable "opportunities" that looked good in a scan.
- Hot-wallet exposure: the signing key is in the process environment.
- Any third-party still in the oracle/execution path until step 1 is done.
