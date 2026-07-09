#!/usr/bin/env node
// 🪬🧿✝️  GARDEN ANGEL — ASSET REGISTRY SYNC CHECK
// ─────────────────────────────────────────────────────────────────────────────
// Run this before every deploy: `node shared/check_asset_sync.js`
//
// Parses ASSET_REGISTRY out of both assets.js (this process, native import)
// and assets.py (via a tiny regex — no Python runtime dependency required
// in the Worker/CI environment). Fails (non-zero exit) if:
//   - an address exists in one file but not the other
//   - an address exists in both but with different decimals or chain
//
// This exists because the BTCB gap that caused the "Execution reverted for
// an unknown reason" incident was exactly this: two hand-maintained maps,
// one updated, one not, with no automated check catching the mismatch
// before it reached production.
// ─────────────────────────────────────────────────────────────────────────────

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { ASSET_REGISTRY as JS_REGISTRY } from './assets.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

function parsePythonRegistry(pyPath) {
  const src = readFileSync(pyPath, 'utf8');
  const registry = {};
  // Matches lines like:
  //   "0xabc...": AssetInfo("BTCB", 18, "BSC"),
  const lineRe = /"(0x[0-9a-fA-F]{40})":\s*AssetInfo\("([A-Za-z0-9]+)",\s*(\d+),\s*"([A-Z]+)"\)/g;
  let m;
  while ((m = lineRe.exec(src)) !== null) {
    const [, addr, symbol, decimals, chain] = m;
    registry[addr.toLowerCase()] = { symbol, decimals: parseInt(decimals, 10), chain };
  }
  return registry;
}

function main() {
  const pyPath = join(__dirname, 'assets.py');
  const pyRegistry = parsePythonRegistry(pyPath);

  const jsAddrs = new Set(Object.keys(JS_REGISTRY));
  const pyAddrs = new Set(Object.keys(pyRegistry));

  const errors = [];

  for (const addr of jsAddrs) {
    if (!pyAddrs.has(addr)) {
      errors.push(`❌ ${addr} (${JS_REGISTRY[addr].symbol}) exists in assets.js but NOT assets.py`);
    }
  }
  for (const addr of pyAddrs) {
    if (!jsAddrs.has(addr)) {
      errors.push(`❌ ${addr} (${pyRegistry[addr].symbol}) exists in assets.py but NOT assets.js`);
    }
  }
  for (const addr of jsAddrs) {
    if (pyAddrs.has(addr)) {
      const a = JS_REGISTRY[addr];
      const b = pyRegistry[addr];
      if (a.decimals !== b.decimals || a.chain !== b.chain) {
        errors.push(
          `❌ ${addr} MISMATCH — js: {symbol:${a.symbol}, decimals:${a.decimals}, chain:${a.chain}} ` +
          `vs py: {symbol:${b.symbol}, decimals:${b.decimals}, chain:${b.chain}}`
        );
      }
    }
  }

  if (errors.length) {
    console.error(`\n🚨 ASSET REGISTRY DRIFT DETECTED (${errors.length} issue(s)):\n`);
    errors.forEach(e => console.error('  ' + e));
    console.error('\nFix assets.js and/or assets.py so both files agree, then re-run.\n');
    process.exit(1);
  }

  console.log(`✅ Asset registries in sync — ${jsAddrs.size} assets, no drift.`);
  process.exit(0);
}

main();