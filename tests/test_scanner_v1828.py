"""Unit tests for scanner.py v18.28 quote-verification changes.

Runs the real Scanner class from the Space's modules/scanner.py against a
stubbed price_client module and a fake Web3 pair contract, covering:
  1. _verify_pairs_onchain — upgrade / drop-stale / keep-unverifiable paths
  2. _fetch_dex_pairs — wrong-quote filtering + per-dex dedupe
  3. end-to-end: an upgraded DexScreener row yields quote_verified=True and
     a BUY when the exact two-leg math clears the floor
"""
import asyncio
import sys
import time
import types
import unittest
from dataclasses import dataclass

import os

# Repo mirrors the HF Space layout: modules/scanner.py lives one level up
# from this tests/ directory.
SPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- stub modules.price_client before importing scanner --------------------
pkg = types.ModuleType("modules")
pkg.__path__ = [SPACE + "/modules"]
sys.modules["modules"] = pkg

stub = types.ModuleType("modules.price_client")


@dataclass
class PriceEntry:
    asset: str = ""
    price: float = 0.0
    source: str = "test"
    is_live: bool = True
    age_secs: float = 0.0


class PriceClient:  # never used by these tests
    pass


stub.PriceEntry = PriceEntry
stub.PriceClient = PriceClient
sys.modules["modules.price_client"] = stub

import importlib.util

spec = importlib.util.spec_from_file_location(
    "modules.scanner", SPACE + "/modules/scanner.py"
)
scanner_mod = importlib.util.module_from_spec(spec)
sys.modules["modules.scanner"] = scanner_mod
spec.loader.exec_module(scanner_mod)

Scanner = scanner_mod.Scanner
DexPairInfo = scanner_mod.DexPairInfo
_MAX_RESERVE_AGE_SECS = scanner_mod._MAX_RESERVE_AGE_SECS


# ---- fake web3 --------------------------------------------------------------
class _Call:
    def __init__(self, value):
        self._v = value

    def call(self):
        if isinstance(self._v, Exception):
            raise self._v
        return self._v


class _Funcs:
    def __init__(self, reserves, token0):
        self._reserves, self._token0 = reserves, token0

    def getReserves(self):
        return _Call(self._reserves)

    def token0(self):
        return _Call(self._token0)


class FakeW3:
    """pair_addr(lower) -> (reserves_tuple_or_exc, token0)"""

    def __init__(self, pools):
        self._pools = pools
        self.eth = self

    def to_checksum_address(self, a):
        return a

    def contract(self, address=None, abi=None):
        c = types.SimpleNamespace()
        entry = self._pools[address.lower()]
        c.functions = _Funcs(*entry)
        return c


BASE = "0x" + "aa" * 20   # base token (e.g. XRP)
USDT = "0x55d398326f99059fF775485246999027B3197955".lower()

CFG = {
    "chain": "BSC", "base": "XRP", "stable": "USDT",
    "address": BASE, "dex_chain_id": "bsc",
    "base_decimals": 18, "stable_decimals": 18,
    "gas_asset": "BNB", "dex_a": "pancakeswap", "dex_b": "biswap",
}


def make_scanner(w3=None):
    return Scanner.__new__(Scanner)  # skip __init__; set attrs per test


def full_scanner(w3):
    s = Scanner(
        price_client=PriceClient(), http=None,
        min_profit=2.17, loan_amount=50_000.0, loan_fee_pct=0.09,
        w3_by_chain={"BSC": w3} if w3 else {},
    )
    return s


class VerifyPairsTests(unittest.TestCase):
    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_upgrade_fresh_dexscreener_row(self):
        now = int(time.time())
        pool = "0x" + "b1" * 20
        # 1000 base / 500_000 stable → price $500, fresh trade 60s ago
        w3 = FakeW3({pool: ((1000 * 10**18, 500_000 * 10**18, now - 60), BASE)})
        s = full_scanner(w3)
        rows = [DexPairInfo("biswap", 499.0, 900_000.0, 0.0, pair_address=pool)]
        out = self._run(s._verify_pairs_onchain(CFG, w3, rows))
        self.assertEqual(len(out), 1)
        r = out[0]
        self.assertGreater(r.base_reserve_raw, 0)
        self.assertGreater(r.stable_reserve_raw, 0)
        self.assertAlmostEqual(r.price_usd, 500.0, places=2)
        self.assertEqual(r.reserve_block_ts, now - 60)
        # original cached row must NOT have been mutated
        self.assertEqual(rows[0].base_reserve_raw, 0)

    def test_drop_stale_dexscreener_row(self):
        now = int(time.time())
        pool = "0x" + "b2" * 20
        stale_ts = now - int(_MAX_RESERVE_AGE_SECS) - 600  # 40+ min old
        w3 = FakeW3({pool: ((1000 * 10**18, 500_000 * 10**18, stale_ts), BASE)})
        s = full_scanner(w3)
        rows = [DexPairInfo("pancakeswap", 500.0, 900_000.0, 0.0, pair_address=pool)]
        out = self._run(s._verify_pairs_onchain(CFG, w3, rows))
        self.assertEqual(out, [])

    def test_keep_unverifiable_row(self):
        w3 = FakeW3({})
        s = full_scanner(w3)
        rows = [DexPairInfo("mdex", 500.0, 900_000.0, 0.0)]  # no pair_address
        out = self._run(s._verify_pairs_onchain(CFG, w3, rows))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].base_reserve_raw, 0)

    def test_rpc_failure_keeps_row_unverified(self):
        pool = "0x" + "b3" * 20
        w3 = FakeW3({pool: (RuntimeError("rpc down"), BASE)})
        s = full_scanner(w3)
        rows = [DexPairInfo("biswap", 500.0, 900_000.0, 0.0, pair_address=pool)]
        out = self._run(s._verify_pairs_onchain(CFG, w3, rows))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].base_reserve_raw, 0)

    def test_stale_gate_now_covers_raw_reserve_rows(self):
        now = int(time.time())
        s = full_scanner(FakeW3({}))
        fresh = DexPairInfo("pancakeswap", 500.0, 1e6, 0.0,
                            base_reserve_raw=10, stable_reserve_raw=10,
                            reserve_block_ts=now - 30)
        stale = DexPairInfo("mdex", 500.0, 1e6, 0.0,
                            base_reserve_raw=10, stable_reserve_raw=10,
                            reserve_block_ts=now - int(_MAX_RESERVE_AGE_SECS) - 60)
        out = self._run(s._verify_pairs_onchain(CFG, FakeW3({}), [fresh, stale]))
        self.assertEqual([p.dex_id for p in out], ["pancakeswap"])


class FetchDexPairsFilterTests(unittest.TestCase):
    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_wrong_quote_rows_dropped_and_deduped(self):
        s = full_scanner(None)
        wbnb = "0xbb4CdB9CBd36B01bD1cBaEF60aF814a3f6F0Ee75".lower()
        rows = [
            # biswap's deepest pool is WBNB-quoted (the shadow bug)
            DexPairInfo("biswap", 501.0, 5e6, 0.0, pair_address="0x1",
                        quote_token_addr=wbnb),
            DexPairInfo("biswap", 500.0, 1e6, 0.0, pair_address="0x2",
                        quote_token_addr=USDT),
            DexPairInfo("pancakeswap", 499.0, 2e6, 0.0, pair_address="0x3",
                        quote_token_addr=USDT),
        ]

        async def fake_http(token_address, chain_id):
            return rows

        s._fetch_dex_pairs_http = fake_http
        out = self._run(s._fetch_dex_pairs(BASE, "bsc", CFG))
        by_dex = {p.dex_id: p for p in out}
        self.assertEqual(set(by_dex), {"biswap", "pancakeswap"})
        # the USDT-quoted biswap row won, not the deeper WBNB one
        self.assertEqual(by_dex["biswap"].pair_address, "0x2")

    def test_no_stable_filter_for_non_bsc(self):
        s = full_scanner(None)
        rows = [
            DexPairInfo("uniswap", 501.0, 5e6, 0.0, quote_token_addr="0xdead"),
            DexPairInfo("uniswap", 500.0, 1e6, 0.0, quote_token_addr="0xbeef"),
        ]

        async def fake_http(token_address, chain_id):
            return rows

        s._fetch_dex_pairs_http = fake_http
        eth_cfg = {**CFG, "chain": "ETH"}
        out = self._run(s._fetch_dex_pairs(BASE, "ethereum", eth_cfg))
        # deduped to one row per dex, first (deepest) wins — original behavior
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].price_usd, 501.0)


class EndToEndProfitTests(unittest.TestCase):
    def test_compute_profit_verified_buy(self):
        s = full_scanner(None)
        result = scanner_mod.ScanResult(min_profit=2.17)
        # exact_gross_return present → quote_verified, net clears floor → BUY
        out = s._compute_profit(
            result, 0.005, "pancakeswap", "biswap",
            10_000.0, 0.0, 581.0, 1.0, exact_gross_return=20.0,
        )
        self.assertTrue(out.quote_verified)
        self.assertEqual(out.signal, "BUY")

    def test_compute_profit_unverified_still_held(self):
        s = full_scanner(None)
        result = scanner_mod.ScanResult(min_profit=2.17)
        out = s._compute_profit(
            result, 0.005, "pancakeswap", "biswap",
            10_000.0, 0.0, 581.0, 1.0, exact_gross_return=None,
        )
        self.assertFalse(out.quote_verified)
        self.assertEqual(out.signal, "HOLD")


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    unittest.main(verbosity=2)
