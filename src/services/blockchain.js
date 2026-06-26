// 🪬🧿✝️  BlockchainService – v16.2
// ─────────────────────────────────────────────────────────────────────────────
// Provides viem public + wallet clients for every chain in CHAIN_REGISTRY.
// Handles DRY_RUN mode transparently — callers never need to branch on it.
//
// Public API:
//   getPublicClient(chain)                          → viem PublicClient
//   getWalletClient(chain)                          → viem WalletClient (async)
//   sendTransaction(chain, to, data, gasLimit)      → txHash string
//   waitForReceipt(chain, txHash, opts?)            → viem TransactionReceipt
//   checkAave(chain)                                → { healthy, reserves }
//   call(chain, to, data)                           → return data (hex)
// ─────────────────────────────────────────────────────────────────────────────

import {
  createPublicClient,
  createWalletClient,
  http,
} from 'viem';
import { privateKeyToAccount } from 'viem/accounts';
import { CHAIN_REGISTRY } from '../config/constants.js';

// Aave V3 Pool addresses per chain
const AAVE_V3_POOL = {
  ETH      : '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2',
  BSC      : '0x6807dc923806fE8Fd134338EABCA509979a7e0cB',
  UNICHAIN : null,   // Aave not yet deployed on Unichain
};

// Minimal ABI for Aave health-check
const AAVE_RESERVES_ABI = [{
  inputs         : [],
  name           : 'getReservesList',
  outputs        : [{ name: '', type: 'address[]' }],
  stateMutability: 'view',
  type           : 'function',
}];

export class BlockchainService {
  constructor(env, ctx) {
    this.env  = env;
    this.ctx  = ctx;
    this._pub = new Map();   // chain → PublicClient  (sync cache)
    this._wal = new Map();   // chain → WalletClient  (sync cache after first build)
  }

  // ── Internal helpers ──────────────────────────────────────────────────────

  _chainCfg(chain) {
    const cfg = CHAIN_REGISTRY[chain];
    if (!cfg) throw new Error(`[Blockchain] Unknown chain: "${chain}". Valid: ${Object.keys(CHAIN_REGISTRY).join(', ')}`);
    return cfg;
  }

  _rpcUrl(chain) {
    const cfg      = this._chainCfg(chain);
    const primary  = this.env[cfg.rpcEnvKey];
    const fallback = cfg.fallbackRpcEnvKey ? this.env[cfg.fallbackRpcEnvKey] : null;
    if (!primary && !fallback) {
      console.warn(`[Blockchain] No RPC URL set for ${chain} (env key: ${cfg.rpcEnvKey}). Using public default.`);
    }
    return primary || fallback || undefined;   // undefined → viem uses its built-in default transport
  }

  // ── Public clients (read-only, no private key) ────────────────────────────

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

  // ── Wallet clients (requires PRIVATE_KEY) ────────────────────────────────
  // Async because privateKeyToAccount is synchronous but we want the error
  // to surface as a rejected promise, consistent with caller await patterns.

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

  // ── sendTransaction ────────────────────────────────────────────────────────
  // In DRY_RUN mode: returns a mock hash, nothing is broadcast.
  // In live mode   : signs and broadcasts via walletClient.

  async sendTransaction(chain = 'ETH', to, data, gasLimit) {
    if (this.env.DRY_RUN === 'true') {
      const mockHash = `0xdryrun${Date.now().toString(16).padStart(16, '0')}`;
      console.log(`[Blockchain] DRY_RUN — simulated tx to ${to} on ${chain}: ${mockHash}`);
      return mockHash;
    }

    const wallet = await this.getWalletClient(chain);

    const hash = await wallet.sendTransaction({
      to,
      data,
      ...(gasLimit != null ? { gas: gasLimit } : {}),
    });

    console.log(`[Blockchain] Tx submitted on ${chain}: ${hash}`);
    return hash;
  }

  // ── waitForReceipt ────────────────────────────────────────────────────────

  async waitForReceipt(chain = 'ETH', txHash, { confirmations = 1, timeout = 120_000 } = {}) {
    // Dry-run hashes are synthetic — return a synthetic receipt immediately
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
    const receipt = await pub.waitForTransactionReceipt({
      hash: txHash,
      confirmations,
      timeout,
    });

    console.log(
      `[Blockchain] Receipt for ${txHash.slice(0, 12)}… on ${chain}: ${receipt.status} ` +
      `(gas used: ${receipt.gasUsed?.toString()})`
    );
    return receipt;
  }

  // ── call (read-only eth_call, no gas) ────────────────────────────────────
  // Useful for on-chain simulations (quoting DEX prices, etc.).

  async call(chain = 'ETH', to, data) {
    const pub    = this.getPublicClient(chain);
    const result = await pub.call({ to, data });
    return result.data ?? '0x';
  }

  // ── checkAave — health-check for /flashloan command ───────────────────────
  // Calls getReservesList() on the Aave V3 Pool. If it returns an array
  // the pool is live and properly deployed on this chain.

  async checkAave(chain = 'ETH') {
    const poolAddress = AAVE_V3_POOL[chain];
    if (!poolAddress) {
      throw new Error(`Aave V3 is not deployed on ${chain}`);
    }

    const pub    = this.getPublicClient(chain);
    const result = await pub.readContract({
      address     : poolAddress,
      abi         : AAVE_RESERVES_ABI,
      functionName: 'getReservesList',
    });

    if (!Array.isArray(result) || result.length === 0) {
      throw new Error(`Aave V3 Pool on ${chain} returned empty reserves — may be wrong address or wrong network`);
    }

    console.log(`[Blockchain] Aave V3 (${chain}) healthy — ${result.length} reserves listed`);
    return { healthy: true, reserves: result.length };
  }
}
