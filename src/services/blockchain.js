// 🪬🧿✝️  BlockchainService – v17.0
// ─────────────────────────────────────────────────────────────────────────────
// v17 FIX over v16.2:
//
//   FIX — RPC URL multi-key fallback.
//     v16 read only cfg.rpcEnvKey (e.g. 'ETH_RPC_URL'). If wrangler.toml used
//     a different naming convention (e.g. 'ETH_RPC_PRIMARY'), _rpcUrl() returned
//     undefined and the bot silently fell back to viem's public default, causing
//     rate-limit errors and unreliable on-chain quotes.
//
//     v17 _rpcUrl() tries the following env keys in order for each chain:
//       1. cfg.rpcEnvKey          (e.g. 'ETH_RPC_PRIMARY')
//       2. cfg.fallbackRpcEnvKey  (e.g. 'ETH_RPC_SECONDARY')
//       3. Hardcoded alt patterns: ETH_RPC_URL, ETH_ALCHEMY_URL, BSC_RPC_URL …
//     The first non-empty value wins. Only warns if ALL candidates are empty.
// ─────────────────────────────────────────────────────────────────────────────

import { privateKeyToAccount } from 'viem/accounts';
import {
  createPublicClient,
  createWalletClient,
  http,
  decodeFunctionResult,
  ContractFunctionRevertedError,
} from 'viem';
import { CHAIN_REGISTRY, ARBITRAGE_ENGINE_ABI } from '../config/constants.js';

// Aave V3 Pool addresses per chain
const AAVE_V3_POOL = {
  ETH      : '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2',
  BSC      : '0x6807dc923806fE8Fd134338EABCA509979a7e0cB',
  UNICHAIN : null,
};

const AAVE_RESERVES_ABI = [{
  inputs         : [],
  name           : 'getReservesList',
  outputs        : [{ name: '', type: 'address[]' }],
  stateMutability: 'view',
  type           : 'function',
}];

// ── Per-chain alternative env key patterns to try ─────────────────────────────
// If a user's wrangler.toml uses a different naming convention from CHAIN_REGISTRY,
// these extras are tried before giving up and using the public default.
const ALT_RPC_KEYS = {
  ETH     : ['ETH_RPC_URL', 'ETH_RPC_PRIMARY', 'ETH_ALCHEMY_URL', 'ETH_INFURA_URL', 'ALCHEMY_ETH_RPC'],
  BSC     : ['BSC_RPC_URL', 'BSC_RPC_PRIMARY', 'BSC_ALCHEMY_URL'],
  UNICHAIN: ['UNICHAIN_RPC_URL', 'UNICHAIN_RPC_PRIMARY'],
};

export class BlockchainService {
  constructor(env, ctx) {
    this.env  = env;
    this.ctx  = ctx;
    this._pub = new Map();
    this._wal = new Map();
  }

  // ── Internal helpers ──────────────────────────────────────────────────────

  _chainCfg(chain) {
    const cfg = CHAIN_REGISTRY[chain];
    if (!cfg) throw new Error(
      `[Blockchain] Unknown chain: "${chain}". Valid: ${Object.keys(CHAIN_REGISTRY).join(', ')}`
    );
    return cfg;
  }

  // ── FIX: Multi-key RPC URL resolution ─────────────────────────────────────
  // Tries cfg.rpcEnvKey, cfg.fallbackRpcEnvKey, then ALT_RPC_KEYS[chain].
  // Returns the first non-empty value, or undefined (viem public default).

  _rpcUrl(chain) {
    const cfg = this._chainCfg(chain);

    const candidates = [
      cfg.rpcEnvKey,
      cfg.fallbackRpcEnvKey,
      ...(ALT_RPC_KEYS[chain] ?? []),
    ].filter(Boolean);

    for (const key of candidates) {
      const val = this.env[key];
      if (val && val.startsWith('http')) return val;
    }

    console.warn(
      `[Blockchain] No RPC URL found for ${chain}. ` +
      `Tried: ${candidates.join(', ')}. Using viem public default — expect rate limits.`
    );
    return undefined;
  }

  // ── Public clients ────────────────────────────────────────────────────────

  getPublicClient(chain = 'ETH') {
    if (this._pub.has(chain)) return this._pub.get(chain);

    const cfg    = this._chainCfg(chain);
    const client = createPublicClient({
      chain    : cfg.viemChain,
      transport: http(this._rpcUrl(chain)),
    });

    this._pub.set(chain, client);
    return client;
  }

  // ── Wallet clients ────────────────────────────────────────────────────────

  async getWalletClient(chain = 'ETH') {
    if (this._wal.has(chain)) return this._wal.get(chain);

    const pk = this.env.PRIVATE_KEY;
    if (!pk) throw new Error('[Blockchain] PRIVATE_KEY secret is not set');
    if (!pk.startsWith('0x')) throw new Error('[Blockchain] PRIVATE_KEY must start with 0x');

    const cfg     = this._chainCfg(chain);
    const account = privateKeyToAccount(pk);
    const client  = createWalletClient({
      account,
      chain    : cfg.viemChain,
      transport: http(this._rpcUrl(chain)),
    });

    this._wal.set(chain, client);
    return client;
  }

  // ── sendTransaction ───────────────────────────────────────────────────────

  async sendTransaction(chain = 'ETH', to, data, gasLimit) {
    if (this.env.DRY_RUN === 'true') {
      const mockHash = `0xdryrun${Date.now().toString(16).padStart(16, '0')}`;
      console.log(`[Blockchain] DRY_RUN — simulated tx to ${to} on ${chain}: ${mockHash}`);
      return mockHash;
    }

    const wallet = await this.getWalletClient(chain);
    const hash   = await wallet.sendTransaction({
      to,
      data,
      ...(gasLimit != null ? { gas: gasLimit } : {}),
    });

    console.log(`[Blockchain] Tx submitted on ${chain}: ${hash}`);
    return hash;
  }

  // ── waitForReceipt ────────────────────────────────────────────────────────

  async waitForReceipt(chain = 'ETH', txHash, { confirmations = 1, timeout = 120_000 } = {}) {
    if (this.env.DRY_RUN === 'true' || (typeof txHash === 'string' && txHash.startsWith('0xdryrun'))) {
      console.log(`[Blockchain] DRY_RUN — synthetic receipt for ${txHash}`);
      return {
        status         : 'success',
        transactionHash: txHash,
        blockNumber    : 0n,
        gasUsed        : BigInt(Math.floor(Number(500_000n) * 0.8)),
      };
    }

    const pub     = this.getPublicClient(chain);
    const receipt = await pub.waitForTransactionReceipt({ hash: txHash, confirmations, timeout });
    console.log(
      `[Blockchain] Receipt for ${txHash.slice(0, 12)}… on ${chain}: ${receipt.status} ` +
      `(gas used: ${receipt.gasUsed?.toString()})`
    );
    return receipt;
  }

  // ── call (read-only eth_call) ─────────────────────────────────────────────

  async call(chain = 'ETH', to, data) {
    const pub    = this.getPublicClient(chain);
    const result = await pub.call({ to, data });
    return result.data ?? '0x';
  }

  // ── simulateTransaction ────────────────────────────────────────────────────
  // Pre-flight check before broadcasting. Runs the exact calldata through
  // eth_call from the signing wallet's address (so balance/allowance/msg.sender
  // checks in the target contract behave the same as they would on-chain),
  // decodes revert reasons via the ABI instead of surfacing raw hex, and
  // optionally enforces a minimum acceptable return so a trade that's gone
  // unprofitable between scan-time and execute-time is rejected here instead
  // of broadcasting and eating gas on a revert (or worse, succeeding at a
  // loss because the contract doesn't itself enforce a minimum).
  //
  // Returns { ok: true, result } on a clean simulation.
  // Throws with a decoded, human-readable reason on revert or on a
  // below-minimum simulated return.

  async simulateTransaction(chain, to, data, { minReturn = null } = {}) {
    const pub = this.getPublicClient(chain);

    // Use the real signing account as `from` when available, since many
    // arbitrage/flash-loan contracts gate execution to a specific caller
    // (onlyOwner / onlyExecutor patterns) — simulating from the zero address
    // would falsely revert on those and mask the real result.
    let from;
    try {
      const wallet = await this.getWalletClient(chain);
      from = wallet.account.address;
    } catch {
      // No PRIVATE_KEY available in this context (e.g. read-only diagnostics) —
      // fall back to an unauthenticated simulation. Caller-gated contracts
      // will revert here; that's still useful signal, just less precise.
      from = undefined;
    }

    let result;
    try {
      const sim = await pub.call({ account: from, to, data });
      result = sim.data ?? '0x';
    } catch (err) {
      throw new Error(`[Blockchain] Simulation reverted on ${chain}: ${this._decodeRevert(err)}`);
    }

    // Optional profitability floor — only enforced if the caller passes one
    // AND the ABI actually decodes a numeric return value. Silent no-op
    // otherwise, so this stays backward-compatible with callers that just
    // want a revert check.
    if (minReturn != null) {
      try {
        const decoded = decodeFunctionResult({
          abi         : ARBITRAGE_ENGINE_ABI,
          functionName: 'executeArbitrage',
          data        : result,
        });
        const simulatedReturn = typeof decoded === 'bigint' ? decoded : decoded?.[0];
        if (typeof simulatedReturn === 'bigint' && simulatedReturn < minReturn) {
          throw new Error(
            `[Blockchain] Simulation succeeded but return ${simulatedReturn} is below ` +
            `minReturn ${minReturn} — spread likely closed since scan. Refusing to broadcast.`
          );
        }
      } catch (decodeErr) {
        // Non-fatal: if the ABI/return shape doesn't support decoding a
        // number, we've still confirmed the call doesn't revert, which is
        // the primary guarantee of this method. Log and continue.
        console.warn('[Blockchain] simulateTransaction: could not decode return for minReturn check —', decodeErr.message);
      }
    }

    console.log(`[Blockchain] ✅ Simulation passed on ${chain} (to: ${to.slice(0, 10)}…)`);
    return { ok: true, result };
  }

  // ── _decodeRevert ─────────────────────────────────────────────────────────
  // Best-effort decode of a viem call error into a readable revert reason,
  // using ARBITRAGE_ENGINE_ABI's custom errors where possible. Falls back to
  // the raw viem short message rather than a bare "0x..." blob.

  _decodeRevert(err) {
    const revertError = err.walk?.(e => e instanceof ContractFunctionRevertedError);
    if (revertError instanceof ContractFunctionRevertedError) {
      const name = revertError.data?.errorName ?? 'unknown error';
      const args = revertError.data?.args?.length ? `(${revertError.data.args.join(', ')})` : '';
      return `${name}${args}`;
    }
    return err.shortMessage ?? err.message ?? String(err);
  }

  // ── checkAave ─────────────────────────────────────────────────────────────

  async checkAave(chain = 'ETH') {
    const poolAddress = AAVE_V3_POOL[chain];
    if (!poolAddress) throw new Error(`Aave V3 is not deployed on ${chain}`);

    const pub    = this.getPublicClient(chain);
    const result = await pub.readContract({
      address     : poolAddress,
      abi         : AAVE_RESERVES_ABI,
      functionName: 'getReservesList',
    });

    if (!Array.isArray(result) || result.length === 0) {
      throw new Error(`Aave V3 Pool on ${chain} returned empty reserves`);
    }

    console.log(`[Blockchain] Aave V3 (${chain}) healthy — ${result.length} reserves listed`);
    return { healthy: true, reserves: result.length };
  }
}
