"""
modules/llm_advisor.py — Garden Angel LLM Advisor v1.0
Wraps Qwen Cloud (Alibaba DashScope, OpenAI-compatible endpoint) to give a
fast, ADVISORY second opinion on scan results.

IMPORTANT — this module never fires a trade and never overrides the
deterministic profit-gate math in scanner.py / local_executor.py. It only
produces a short natural-language read you can log or send to Telegram.
The real net-profit numbers already come from a live on-chain quote
(slippage-haircut fix already in your scanner). An LLM call adds real
network latency (typically 0.5-3s) per invocation and can hallucinate —
neither property is acceptable for something that gates a real flash-loan
transaction. Use this to help interpret *why* a pair keeps failing, or to
summarize scans on demand — not to greenlight trades.

Setup:
  1. httpx is already a dependency of this project — no new install needed.
  2. Set env var DASHSCOPE_API_KEY to your Qwen Cloud API key (starts sk-).
  3. Construct LLMAdvisor(http=self._http) once, reusing the bot's shared
     IPv4-pinned AsyncClient (same pattern as PriceClient/TelegramClient).

Endpoint confirmed against Qwen Cloud's own docs (2026-07-10):
  base_url = https://dashscope-intl.aliyuncs.com/compatible-mode/v1
  model    = qwen3.7-plus  (OpenAI-compatible chat.completions schema)
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("modules.llm_advisor")

_DASHSCOPE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
_MODEL         = os.environ.get("QWEN_MODEL", "qwen3.7-plus")
_TIMEOUT       = httpx.Timeout(connect=5.0, read=12.0, write=5.0, pool=5.0)

_SYSTEM_PROMPT = (
    "You are a terse risk analyst for a BSC flash-loan arbitrage bot. "
    "You are given one scan result already computed with real on-chain "
    "slippage, not just a top-of-book spread. Reply with STRICT JSON only, "
    "no prose outside the JSON, matching this schema: "
    '{"verdict": "HOLD"|"WATCH"|"DROP_PAIR", "one_liner": "<=15 words"}. '
    "You are advisory only. You cannot and must not claim a trade should "
    "execute. HOLD = nothing actionable. WATCH = promising, keep polling. "
    "DROP_PAIR = structurally too thin right now, deprioritize this pair."
)


@dataclass
class AdvisorVerdict:
    verdict: str
    one_liner: str
    raw: dict[str, Any] | None = None


class LLMAdvisor:
    """Advisory-only Qwen wrapper. Every call is best-effort: on any
    failure (missing key, timeout, bad JSON, rate limit) this returns None
    instead of raising, so a broken advisor call can never interrupt the
    scan loop or crash the bot."""

    def __init__(self, http: httpx.AsyncClient | None = None, api_key: str | None = None) -> None:
        self._http     = http
        self._own_http: httpx.AsyncClient | None = None
        self._api_key  = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        if not self._api_key:
            logger.warning(
                "[LLMAdvisor] DASHSCOPE_API_KEY not set — advisor calls "
                "will be skipped (returns None) until it's configured."
            )

    async def _client(self) -> httpx.AsyncClient:
        if self._http is not None:
            return self._http
        if self._own_http is None:
            self._own_http = httpx.AsyncClient(timeout=_TIMEOUT)
        return self._own_http

    async def analyze(self, scan_diag: dict[str, Any]) -> AdvisorVerdict | None:
        """
        scan_diag: the same compact dict you already build for scan logging,
        e.g.:
          {"pair": "BNB/BTCB", "route": "pancakeswap->mdex",
           "loan_usd": 1706.40, "spread_pct": 0.032, "net_usd": -171.34,
           "gas_usd": 0.0259, "min_profit_usd": 0.01,
           "buy_liq_usd": 34127.93, "sell_liq_usd": 647840.03}

        Returns None on any failure — see class docstring.
        """
        if not self._api_key:
            return None

        try:
            client = await self._client()
            resp = await client.post(
                _DASHSCOPE_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model": _MODEL,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": json.dumps(scan_diag, default=str)},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.0,
                    "max_tokens": 120,
                },
            )
            resp.raise_for_status()
            data    = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed  = json.loads(content)
            return AdvisorVerdict(
                verdict   = str(parsed.get("verdict", "HOLD"))[:20],
                one_liner = str(parsed.get("one_liner", ""))[:200],
                raw       = parsed,
            )
        except Exception as exc:
            logger.warning("[LLMAdvisor] call failed (non-critical, skipping): %s", exc)
            return None

    async def aclose(self) -> None:
        if self._own_http is not None:
            await self._own_http.aclose()
