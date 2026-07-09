// 🪬🧿✝️  GARDEN ANGEL — SHARED ASSET REGISTRY (JS)
// ─────────────────────────────────────────────────────────────────────────────
// PURPOSE
//   Single source of truth for "address → decimals/symbol/chain" on the JS
//   side. This file exists because of a real production bug:
//
//     executor.js's local ASSET_DECIMALS map only had 5 entries (WETH, WBTC,
//     USDC, USDT, DAI — all ETH-mainnet addresses) and was missing BTCB
//     (0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c), the BSC-side asset
//     scanner.py had already been correctly emitting with 18 decimals since
//     the pair definition at scanner_4.py's ARBITRAGE_PAIRS list. Because
//     resolveDecimals() only runs when decision.amountIn is undefined, and
//     because BTCB was simply absent from the map rather than present with
//     a wrong value, this specific gap didn't always throw the fail-closed
//     error you'd expect — depending on caller, amountIn could already be
//     set upstream, letting a BSC BTCB trade reach startArbitrage's
//     simulation with no clean decimal error, only a generic on-chain
//     revert with no reason string. That's the class of bug this file is
//     for: registries duplicated by hand in two languages WILL drift.
//
// SYNC CONTRACT
//   This file must stay in lockstep with garden_angel/shared/assets.py.
//   Both are hand-authored (no shared codegen exists yet — that's a real
//   follow-up, not a claim this fully solves the drift problem). Until then:
//     1. Any new asset added to scanner.py's ARBITRAGE_PAIRS MUST be added
//        here AND in assets.py in the same change.
//     2. Run `node shared/check_asset_sync.js` (or the CI equivalent) before
//        deploying — it fails loudly on any address present in one file
//        but not the other, or present in both with different decimals.
//     3. Decimals here are the ON-CHAIN token contract's actual decimals()
//        value — always verify against a block explorer, never assume by
//        symbol. BTCB (BSC) is 18 decimals; WBTC (ETH) is 8. Same rough
//        "meaning" (BTC-pegged), different contracts, different decimals.
// ─────────────────────────────────────────────────────────────────────────────

// key = lowercase checksum-independent address (we lowercase on lookup)
export const ASSET_REGISTRY = {
  // ── Ethereum mainnet ────────────────────────────────────────────────────
  '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2': { symbol: 'WETH', decimals: 18, chain: 'ETH' },
  '0x2260fac5e5542a773aa44fbcfedf7c193bc2c599': { symbol: 'WBTC', decimals: 8,  chain: 'ETH' },
  '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48': { symbol: 'USDC', decimals: 6,  chain: 'ETH' },
  '0xdac17f958d2ee523a2206206994597c13d831ec7': { symbol: 'USDT', decimals: 6,  chain: 'ETH' },
  '0x6b175474e89094c44da98b954eedeac495271d0f': { symbol: 'DAI',  decimals: 18, chain: 'ETH' },

  // ── BNB Smart Chain ──────────────────────────────────────────────────────
  '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c': { symbol: 'WBNB', decimals: 18, chain: 'BSC' },
  '0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c': { symbol: 'BTCB', decimals: 18, chain: 'BSC' },
  // NOTE: BSC USDC/USDT/DAI are DIFFERENT CONTRACTS from ETH mainnet.
  // Not added yet because scanner.py has no BSC stable pair using them —
  // add here (with BSC addresses, NOT the ETH ones above) the same day
  // any such pair is added to ARBITRAGE_PAIRS. Do not reuse the ETH
  // addresses for BSC stables; that would silently repeat this exact bug.
};

/**
 * Resolve decimals for an asset address. FAILS CLOSED — no default.
 * A wrong guess here (e.g. defaulting to 18) is a silent, expensive error;
 * an unknown asset must be added to the registry before it's tradeable.
 */
export function resolveDecimals(assetAddress) {
  const entry = ASSET_REGISTRY[String(assetAddress).toLowerCase()];
  if (!entry) {
    throw new Error(
      `No known decimals for asset ${assetAddress}. Add it to ` +
      `shared/assets.js (ASSET_REGISTRY) AND shared/assets.py before ` +
      `trading it — defaulting to 18 risks a silent scaling error.`
    );
  }
  return entry.decimals;
}

/** Full lookup (symbol/decimals/chain), same fail-closed behavior. */
export function resolveAsset(assetAddress) {
  const entry = ASSET_REGISTRY[String(assetAddress).toLowerCase()];
  if (!entry) {
    throw new Error(`No known asset entry for ${assetAddress}.`);
  }
  return entry;
}