"""
modules/scanner.py — Garden Angel On-Demand Market Scanner v18.28
──────────────────────────────────────────────────────────────────────────────
v18.28 — vs v18.27:

  FIX (root cause — the standing "UNVERIFIED — clears the floor on the
    estimate, but no live two-leg quote confirmed it — Watching." loop,
    2026-07-11 13:42-13:51 production log) — DexScreener-sourced venue
    rows carry no raw reserves, so any candidate whose buy or sell leg
    was a DexScreener fill was unverifiable BY CONSTRUCTION: v18.21's
    guard (correctly) held it every cycle, the near-miss notifier
    (correctly) reported it every hour, and nothing ever attempted to
    actually verify it. The bot could see the opportunity but had
    permanently blinded itself to confirming it. Three changes, all
    additive:

    1. NEW _verify_pairs_onchain() — runs once per BSC scan on the final
       candidate set, right before the spread math. Any row without raw
       reserves but WITH a known pool address (DexScreener reports
       pairAddress; the configured-pair path knows cfg's pair_a/pair_b)
       gets that EXACT pool read live via getReserves():
         • fresh → row upgraded with live price/liquidity/raw reserves,
           so the exact two-leg round trip (and therefore a real BUY)
           becomes reachable through it;
         • last trade older than _MAX_RESERVE_AGE_SECS → row DROPPED —
           the frozen-ratio phantom spread dies at the source instead
           of re-alerting every cycle (the XRP pancakeswap→biswap
           0.909% that sat in the log for 10 straight minutes was this:
           pancakeswap's pool 47-57 min since last trade, correctly
           dropped from the LIVE set, then readmitted unverifiable via
           DexScreener because its last trade still fell inside the h1
           txn window);
         • read failed / no address → row kept as-is, still unverified,
           v18.21's guard unchanged. Execution safety is untouched — a
           BUY still requires the exact two-leg quote; there are just
           far fewer candidates left with an excuse not to have one.

    2. FIX (phantom spreads from wrong-quote pools) — the DexScreener
       path kept only the single highest-liquidity pool per dexId
       regardless of what it was quoted in, so e.g. a deep XRP/WBNB
       pool could permanently shadow the same DEX's XRP/USDT pool while
       the spread math priced it as if it were USDT-quoted. Rows are now
       deduped per (dexId, quoteToken) upstream, and _fetch_dex_pairs
       drops rows whose quote token isn't the configured stable when the
       stable's address is known (BSC) — the stable→base→stable loop
       cannot route through them, so no spread against them is real.

    3. _fetch_reserves_onchain() now populates the raw reserves +
       last-trade timestamp it always read and then threw away, so
       configured-pair rows are exact-math-capable too (and get the
       same staleness gate via _verify_pairs_onchain, which they never
       had before).

    Net effect on behavior: a genuinely profitable, floor-clearing
    spread through an executable venue now verifies and fires BUY the
    same cycle instead of "held correctly. Watching." forever; a
    stale-pool phantom disappears from the candidate set (and from
    Telegram) instead of masquerading as a near-miss. Cost: at most a
    couple of extra getReserves() RPC reads per scan, only for rows
    DexScreener filled.

v18.27 — vs v18.26:

  FIX (efficiency/robustness — duplicate concurrent DexScreener calls
    within one cycle) — scan() runs every configured pair concurrently
    via asyncio.gather, and several pairs deliberately share a
    token_address for the DexScreener fallback (BTCB backs both
    WBTC/USDC and WBTC/USDT; Binance-Peg ETH backs both ETH/USDT and
    ETH/USDC — see _SCAN_PAIRS). _fetch_dex_pairs_http()'s cache was a
    plain check-then-act dict read with no lock and no in-flight
    tracking, so whenever the cache was cold, every coroutine for those
    pairs hit the "not cached" check in the same tick and each fired
    its own independent HTTP request for the identical resource before
    any of them had written back. Confirmed in production
    (2026-07-11 13:29:51): two back-to-back GET + "DROPPED stale" log
    lines for the same BTCB token address inside a single scan cycle.
    Doesn't corrupt data (both calls return the same thing), but it's a
    wasted round-trip every time it happens and, often enough, risks
    tripping DexScreener's own rate limiting — which would degrade the
    fallback for every pair that cycle, not just the ones racing.

    Fix: added self._dex_inflight, a token_address -> Future map. The
    first coroutine to reach a cold cache for a given token_address
    registers a Future there and does the real fetch; any other
    coroutine racing in for the same token_address before that Future
    resolves just awaits it and reuses the one result — one HTTP call,
    N callers. No lock needed: asyncio is single-threaded and there's
    no `await` between the inflight-dict check and the inflight-dict
    write, so that check-and-register step itself can't race. The
    Future is always resolved (success or the existing graceful
    fallback-to-cache-or-empty-list on error) and always released in a
    `finally`, so a failure can't leave other pairs awaiting a Future
    that never fires, and the next cold cycle starts clean.

    Not addressed here (separate, unconfirmed observation): the same
    production log's two consecutive cycles both reported an identical
    best spread — 0.233% / net $2.07 — 48s apart, despite every pair's
    live reserves visibly moving between them. That's consistent with
    either a genuinely quiet market on whichever pair won "best" both
    times, or a second, not-yet-isolated bug in the highest-net
    fallback pick. Watching the next several cycles' logs before
    chasing this further — one repeat isn't enough to localize it, and
    this file's caches all expire well within the 45s hunt interval so
    it isn't an obvious carryover from the caches touched above.

v18.26 — vs v18.25:

  FIX (root cause — why the bot never actually reaches BUY) — the
    cross-pair "best of cycle" pick was:
        best = max(clean_results, key=lambda r: r.net_after_fee)
    — pure highest-net, with NO regard for quote_verified. Every guard
    later in scan() (v18.21's BUY→HOLD downgrade, _reason(),
    summary_line()) only ever looks at whichever single result THIS
    line already chose. Since the linear/DexScreener-liquidity-sized
    estimate is systematically inflated (the exact reason v18.21 exists
    — real incident: scanner said net +$2.93, live quotes said
    -$654.22), an unverified candidate will almost always out-rank a
    smaller, genuinely verified, floor-clearing one. Once picked as
    "best," that smaller verified candidate is simply discarded — never
    reported, never reaches the BUY guards, never executes — even on a
    cycle where it was real and tradeable. This matches every
    production log reviewed to date: the same unverified pair (highest
    naive net, never confirmed) wins every single cycle, so the bot can
    look like it's finding opportunities constantly while never once
    reaching execute_trade().

    Fix: if ANY result this cycle is both quote_verified AND already
    clears its own min_profit (i.e., actually executable), pick the
    best *among those* instead — a real, smaller, confirmed opportunity
    now wins over a bigger, unconfirmed one. If nothing verified clears
    the floor this cycle, falls back to the exact same highest-net-
    regardless-of-verification pick as before, so near-miss
    reporting/visibility on a quiet cycle is unchanged. Also added an
    INFO-level per-cycle line showing verified vs unverified counts, so
    future logs can confirm directly whether an executable candidate
    existed and was chosen, instead of only inferring it from code.

v18.25 — vs v18.24:

  FIX (correctness) — companion to qwen_client.py v1.4. v18.24 fixed the
    deterministic Telegram text so an unverified-but-cleared HOLD no
    longer shows a fabricated negative "short of floor" number, but the
    🤖 Qwen line appended right after it was still built from
    near_miss_note()/market_pulse() prompts that unconditionally told
    the model the trade was "below the floor" — so the AI commentary
    kept contradicting the now-correct text above it. qwen_client.py
    v1.4 added a `quote_verified` param to both methods to fix the
    prompt itself; this version just passes it through using the exact
    same `not result.quote_verified and result.net_after_fee >=
    result.min_profit` condition v18.24 already computes for the
    Telegram card, so both pieces of every alert tell the same story.

    Version coupling: requires qwen_client.py v1.4+ deployed alongside
    it. Against the older v1.3 (no `quote_verified` param), the added
    kwarg raises TypeError on every near_miss_note()/market_pulse()
    call — already caught by this file's own try/except around each
    call (logs a warning, sends the deterministic text with no 🤖 line),
    so it fails safe, not silent-wrong — just loses AI commentary until
    both files ship together.

v18.24 — vs v18.23:

  FIX (correctness) — near-miss/pulse alerts always framed a HOLD as
    "short of the floor," even when it wasn't. Production alert
    (2026-07-11 12:14): "Spread 0.313% | net $5.48 ... $-3.30 short of
    the $2.17 auto-trade floor — held correctly." Net $5.48 is actually
    $3.31 ABOVE that floor, not short of it — the scan log line for the
    same cycle says the real reason: "no live two-leg quote backs it —
    not sent to execution." This is the v18.21 quote_verified=False
    HOLD path (unverified linear/DexScreener estimate, signal forced to
    HOLD regardless of net) — `_reason()` already handles this case
    correctly (line ~1253: "HOLD — unverified spread only... but no
    live two-leg quote confirmed it"), but `_notify_near_miss()` and
    `_notify_pulse()` never checked `quote_verified` at all. Both just
    computed `shortfall = min_profit - net_after_fee` and always
    printed it as "short of floor," which goes negative — and gets
    mislabeled as a shortfall — any time net_after_fee exceeds
    min_profit while quote_verified is False.

    Fix: both notifiers now branch on the same `not quote_verified and
    net_after_fee >= min_profit` condition `_reason()` already uses.
    When true, the alert reports the estimate clearing the floor but
    being held for lack of a confirmed live quote (no shortfall figure,
    since there isn't one) instead of a fabricated negative "short of
    floor" number. Genuine under-floor HOLDs (quote_verified or not)
    keep the original shortfall-vs-floor wording unchanged.
    `_pulse_best` now also carries `quote_verified` so the hourly
    digest can make the same distinction, not just the immediate
    near-miss alert.

    NOT fixed here (separate, pre-existing issue — see v18.22/v18.23
    notes below, still blocked on bot.py/config.py): the floor value
    itself showing $2.17 instead of the app's configured $4.35. That's
    unrelated to this bug — it affects what min_profit IS; this fix
    only affects how a HOLD against whatever min_profit turns out to
    be gets described. Confirmed independent by re-deriving this
    cycle's own numbers: net $5.48 clears BOTH $2.17 and $4.35, so
    the mislabeled-shortfall bug reproduces regardless of which floor
    value is the "true" one.

v18.23 — vs v18.22:

  DIAGNOSTIC ONLY, narrowed further — still can't fix without bot.py, but
    now know more precisely where to look. The v18.22 warning (fires only
    when scan(min_profit=...) is called with an override that DIFFERS
    from self.min_profit) never appeared in the 2026-07-11 12:08 log —
    yet the near-miss card that cycle still showed the same $2.17 floor
    as before. That rules out a per-call override as the cause: either no
    override was passed at all, or it was passed equal to self.min_profit
    — either way, self.min_profit was ALREADY ~$2.17 the moment this
    Scanner was constructed, not $4.35 as the app's own "Config loaded:
    ... min_profit=4.35 ..." startup banner claims.

    This file can't see how that banner computes its number or how the
    Scanner(...) call site computes its min_profit= argument — both live
    in bot.py/config.py, not here. What it CAN do: log exactly what value
    Scanner itself received at construction, immediately after __init__
    assigns it, so the next startup's console has this line and the
    app's banner line back to back. If they disagree, that's on-the-record
    proof the bug is in whatever builds the Scanner(...) call (reading a
    different env var, a stale default, a unit/currency conversion,
    etc.) — and bot.py is the file needed to go further.

v18.22 — vs v18.21:

  DIAGNOSTIC ONLY — could not fix the root cause from this file; see note.
    Production near-miss alert (2026-07-11 11:59:47) reported a $2.17
    floor while the app's startup config banner says min_profit=4.35 —
    suspiciously close to exactly half. Traced every place this file
    touches min_profit: `scan()`'s `effective_min_profit` is either the
    caller's override verbatim or `self.min_profit` verbatim, nothing in
    between divides or rescales it, and `ScanResult.min_profit` is set
    from that same value once at construction and never reassigned. That
    rules out this file as the source — if the floor really is halved,
    the value arriving at `scan(min_profit=...)` was ALREADY halved by
    whatever called it (bot.py's auto-hunt loop is the likely candidate,
    per the v18.14 note that it passes `self.effective_min_profit` — but
    bot.py isn't available in this session, so the actual line can't be
    identified or fixed yet).

    What this version DOES do: `scan()` now logs a loud warning any time
    a caller-supplied override differs from this Scanner's own
    constructor-configured baseline, showing both values and the
    override as a percent of baseline. Next time this happens, the log
    line points straight at it instead of only surfacing indirectly in a
    Telegram near-miss card after the fact. If the override turns out to
    be intentional (e.g. mint mode deliberately hunting with a lower
    floor), this same log line confirms that too — either way it's no
    longer silent.

v18.21 — vs v18.20:

  FIX (correctness/risk) — never signal a real BUY off the DexScreener
    linear-model fallback; downgrade it to HOLD instead. Production
    evidence (2026-07-11 11:39:24): BNB/USDC on biswap→pancakeswap
    signaled "🚀 BUY ... spread 0.166% net +$2.93". LocalExecutor's
    pre-flight live quote for the SAME trade came back $654.22 WORSE
    (expected $3,271.26 back on $3,925.48 in) and correctly refused to
    send it — but that's a near-miss, not a clean result: the scanner's
    own estimate was off by two orders of magnitude from what execution
    actually saw, not "spread too thin."

    Root cause, traced end to end: BNB/USDC's biswap and mdex pools were
    both correctly dropped as stale that cycle (37m/171m since last
    trade — v18.19's filter working as intended), leaving only
    pancakeswap live. `len(live_pairs) < 2` routed the whole calc into
    the v18.11 DexScreener-only fallback branch — the ORIGINAL widest-
    gross + linear-model path (`loan_amount * spread_pct`, haircut sized
    off DexScreener's own `liquidity_usd`), not the exact two-leg
    constant-product math LocalExecutor actually runs at execution time.
    v18.20's recent-txns gate only checks whether DexScreener's PRICE is
    stale — it says nothing about whether DexScreener's LIQUIDITY figure
    is accurate, and this file's own v18.8 changelog already documented
    that figure as unreliable "ranging from completely dead pool to ~13%
    overstated." A thin/dead biswap pool behind a plausible-looking
    DexScreener price is exactly the gap between the two numbers above.

    Fix: `_compute_profit()` now only allows `signal = "BUY"` when
    `exact_gross_return is not None` — i.e. only when BOTH legs carried
    live on-chain raw reserves and the exact two-leg round trip actually
    ran (the same computation LocalExecutor mirrors). Whenever the
    linear/DexScreener-liquidity-sized fallback was used instead, the
    would-be signal is downgraded to HOLD regardless of how the naive
    math nets out, with a warning logging what was suppressed and why —
    so the number is still visible for tuning/near-miss visibility, it
    just can never reach execute_trade(). New `ScanResult.quote_verified`
    field surfaces this distinction to every caller/consumer (to_dict(),
    summary_line(), Telegram cards) instead of leaving it implicit.

    This does not change anything about the `len(live_pairs) >= 2`
    branch (already exact, already trustworthy — unaffected) or the
    stale-pool filters themselves (v18.19/v18.20, still correct and
    unchanged). It only closes the gap where a data-quality fallback
    meant for informational/near-miss purposes could still reach a real,
    fund-moving BUY signal.

v18.20 — vs v18.19:

  FIX (data quality) — the DexScreener fallback was never actually
    running in the situation it exists for. Production logs since
    v18.19 shipped showed near-permanent HOLD: cycle after cycle,
    "Only 1 usable DEX pair(s) from live on-chain reads this cycle".
    Root cause was a one-line guard bug, not the stale-pool filter
    itself doing anything wrong:

      if not dex_pairs:
          dex_pairs = await self._fetch_dex_pairs(...)   # DexScreener

    `not dex_pairs` is only True when live gave back ZERO pools. But
    v18.19's freshness filter routinely leaves EXACTLY ONE fresh pool
    (PancakeSwap trades constantly; Biswap/MDEX often go hours between
    trades on these pairs and get correctly dropped) — and a
    single-item list is non-empty, so the guard skipped the fallback
    entirely. The very next check (`len(dex_pairs) < 2`) then HELD
    immediately, every cycle, without ever trying to find a second
    venue. The fallback only worked at all when live returned nothing
    whatsoever, which was the rare case, not the common one.

    Fix: re-arm the fallback whenever live gave back FEWER THAN 2
    usable pools (not only zero), and MERGE the result in rather than
    replacing — the live pool(s) we do have are the highest-fidelity
    data available (v18.8's whole rationale) and are never discarded
    just to make room for a DexScreener-derived number.

    Safety note — this required a companion fix, not just a wider
    trigger. DexScreener-sourced DexPairInfo rows carry
    reserve_block_ts=0 (no on-chain last-trade timestamp), so they are
    invisible to the v18.19 staleness filter above. Naively merging
    them in to fill the gap would have quietly reopened the exact
    "frozen reserve ratio → phantom spread" bug v18.19 closed — just
    laundered through DexScreener's numbers instead of raw RPC ones.
    _fetch_dex_pairs_http() now applies its own recency gate for this
    reason: a DexScreener listing with zero buys/sells in its m5 AND
    h1 windows is dropped before it ever reaches the comparison logic
    — the DexScreener-side equivalent of _MAX_RESERVE_AGE_SECS, using
    the closest signal DexScreener actually exposes (it has no
    timestamp field, only rolling txn/volume windows).

    Net effect: pairs where a second venue is genuinely dead (as most
    of the "Only 1 usable" cycles in the logs actually were — DexScreener
    agrees there's been no trade in over an hour) still correctly HOLD.
    Pairs where the on-chain read missed a venue that DexScreener can
    see traded recently (a transient RPC hiccup on one router, or a
    pool discovery edge case) now get a real chance at a signal instead
    of being given up on by a guard-clause bug. `max_loan_pct` and the
    "source" label are now derived from how many pools actually carry
    raw on-chain reserves post-merge (`len(live_pairs) >= 2`), not the
    pre-merge live/DexScreener boolean, so a DexScreener-filled gap
    never silently inherits the more permissive live-liquidity loan cap
    meant only for ground-truth on-chain reserve data.

v18.19 — vs v18.18:

  FIX (data quality) — exclude stale/dead pools from the candidate set.
    The v18.18 freshness probe immediately paid off: production logs
    showed several pools — the v18.17 USDC-quoted pairs on biswap/mdex in
    particular — going 2 to 13 HOURS between trades. A pool untraded that
    long has a frozen reserve ratio, so it reports a FIXED phantom spread
    against a live pool on every scan. That is the origin of the recurring
    "best spread 0.628% net $-0.03" that looked like a persistent near-miss
    but never was: the exact constant-product math always (correctly)
    netted it negative, so no funds were ever at risk, but it polluted the
    reported best-spread number and made the market look closer to a trade
    than it actually was. _fetch_live_bsc_reserves() now drops any pool
    whose last on-chain trade is older than _MAX_RESERVE_AGE_SECS (30 min)
    and logs which it dropped, so every spread the scanner reports is
    against a genuinely live, executable pool. Does not change execution
    safety (that was already correct) — it makes the SIGNAL honest.

v18.18 — vs v18.17:

  DIAGNOSTIC — surface each live BSC pool's last-trade block timestamp.
    Prompted by a sharp production observation: three scans logged the
    IDENTICAL "best spread 0.628% net $-0.03" up to ~8 minutes apart,
    which is suspicious for live pool data. Investigation confirmed the
    live path (_read_live_reserves) reads getReserves() FRESH every scan
    with no stale cache — only the pair *contract object* and pair
    *address* are cached (both immutable, correct). But getReserves()
    returns a third value, _blockTimestampLast (the timestamp of the last
    trade that moved the pool), which was being discarded. It's now kept
    (DexPairInfo.reserve_block_ts) and logged once per pair per scan. This
    turns the suspicion into a definitive on-chain answer:
      • identical block_ts across scans → the pool had no reserve-changing
        trade in between, so an unchanged spread is REAL (low-volume pair
        or slightly-stale RPC state), not a caching bug in our code;
      • advancing block_ts with a byte-identical computed spread → a real
        bug, now visible instead of hidden.
    Read-only, additive: DexPairInfo gains one int field (default 0);
    _read_live_reserves returns a 5-tuple (was 4); its single caller
    (_fetch_live_bsc_reserves) updated to match. No execution-path change.

v18.17 — vs v18.16:

  NEW — three USDC-quoted BSC pairs (BNB/USDC, ETH/USDC, BTCB/USDC),
    taking BSC from 5 to 8 scanned pairs — with ZERO new address trust:
    the Binance-Peg USDC address (0x8AC76a51..., 18 decimals) has been
    sitting verified in constants.py's BSC_TOKENS since before this
    file's changelog began, all three base tokens are already in use by
    existing pairs, and the 3 routers are the same verified set. USDC
    itself prices via price_client's stablecoin peg tier ($1.00, no
    network call), same as USDT. _BSC_STABLE_ADDRESSES gains the USDC
    entry so the live-reserve path can resolve pools for it. Pools that
    don't exist or are near-empty on a given router are skipped/capped
    by the existing discovery + liquidity machinery — a thin USDC pool
    contributes nothing rather than a bad signal. Rationale: the same
    token often trades at slightly different prices against USDT vs
    USDC pools; quoting both stables nearly doubles the venue
    combinations the fee-aware picker (v18.12) can search each cycle.

v18.16 — vs v18.15:

  NEW — AI Market Pulse + near-miss alerts (optional `telegram=` /
    `qwen=` constructor params, wired from bot.py v17.24). Gives the
    operator continuous visibility into the hunt WITHOUT changing a
    single decision: the trade trigger remains 100% the exact
    constant-product math, exactly as before.
      • Hourly pulse — a Telegram digest of the last hour (scans run,
        best spread/net seen, which pair/venues came closest, the
        current floor), with a short Qwen-written market read appended
        when QWEN_API_KEY is configured. Deterministic stats text always
        sends even if Qwen fails — same best-effort contract as every
        other notification in this deployment.
      • Near-miss alert — fired immediately (1h cooldown) when a cycle's
        best result lands at/near break-even (net > -$0.10) on real,
        live-priced data: the moments genuinely worth watching. Reports
        both distance-to-break-even and the honest shortfall against the
        auto-trade floor (min_profit − net), since "3 cents below zero"
        and "how far below the floor" are different numbers and only
        showing the first would overstate how close a trade was.
    Both fire as tracked background tasks at scan()'s single return
    point — the one choke point every scan passes through regardless of
    caller (auto-loop, /hunt, dashboard) — so no caller can bypass or
    forget them, same structural lesson as payout/report wiring (v18.1/
    v18.2). Omitting telegram= keeps Scanner's behavior byte-identical
    to v18.15.

v18.15 — vs v18.14:

  NEW — Binance-Peg XRP/USDT added to _SCAN_PAIRS (BSC), the 6th pair
    overall / 5th on BSC. Address independently cross-checked against
    CoinGecko, OKX, Uniswap's token explorer, and OKLink (not just
    BscScan alone) specifically because BscScan itself flags a "displayed
    name does not match contract's Name function" warning on this token —
    confirmed as a known cosmetic quirk of BSC's early-2020 Binance-Peg
    contract template, not a red flag once cross-verified. Also required
    price_client.py (v2.16) AND, this time, the Cloudflare Worker's
    src/services/price.js in the same change — CAKE's v18.13 rollout
    shipped scanner.py + price_client.py alone and the oracle mirror
    silently dropped CAKE from every response until the Worker side was
    separately patched afterward (v17.6 there); doing all three together
    this time avoids repeating that gap.

v18.14 — vs v18.13:

  NEW — bridged ETH/USDT added to _SCAN_PAIRS (BSC), the 4th BSC pair
    alongside BNB/USDT, BTCB/USDT, CAKE/USDT. Deliberately NOT BUSD: a
    stablecoin-vs-stablecoin (BUSD/USDT) pool has structurally near-zero
    spread by design, and BUSD's supply has been winding down since
    Binance stopped minting it — thinning liquidity for no real upside.
    Bridged ETH (Binance-Peg Ethereum Token,
    0x2170Ed0880ac9A755fd29B2688956BD959F933F8) is a real blue-chip with
    actual volatility. No new trust introduced: this address has been
    sitting verified and unchanged in constants.py's BSC_TOKENS all
    along, and price_client.py already resolves "ETH" live (same rows
    the ETH-chain WETH pair already uses) — this pair required editing
    scanner.py only, nothing else. Same free ride as CAKE got in v18.13:
    _fetch_live_bsc_reserves() checks every BSC pair against all 3
    already-verified routers (pancakeswap/biswap/mdex) regardless of
    pair config, so this new pair gets full live-reserve + fee-aware
    venue selection immediately.

v18.13 — vs v18.12:

  NEW — CAKE/USDT added to _SCAN_PAIRS (BSC). Widens the scan universe
    beyond BNB/USDT and BTCB/USDT — CAKE is PancakeSwap's own token,
    verified via BscScan "Source Code Verified — Exact Match" plus the
    operator's own pasted full source (contract CakeToken, constructor
    BEP20('PancakeSwap Token', 'Cake')), address
    0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82 — matching constants.py's
    new BSC_TOKENS entry (see that file's own changelog; local_executor.py's
    _resolve_token() reads from there, so this had to land in both places
    for a real CAKE BUY signal to actually be executable, not just found).
    No new DEX trust required: _fetch_live_bsc_reserves() already loops
    every entry in _BSC_ROUTER_ADDRESSES (pancakeswap/biswap/mdex) for
    EVERY BSC pair regardless of a pair's own dex_a/dex_b, so this new
    pair gets the full live-reserve + fee-aware venue selection (v18.12)
    for free, reusing the same 3 already-verified routers and the same
    already-verified USDT stable address. dex_a/dex_b below are only used
    as DexScreener-fallback labels if live RPC reads ever fail for this
    pair — not a claim that only those two DEXs get checked.

    Also required modules/price_client.py to actually resolve a live CAKE
    price (CoinGecko id "pancakeswap-token", OKX/Binance symbol CAKE —
    see that file's own changelog) — without it, CAKE's price would
    silently fall through to the static-fallback tier (price=0, since no
    static entry existed either) and this pair would just HOLD forever
    with "No price for CAKE", found but never actually scanned.

v18.11 — vs v18.10:

  FIX — the real cause of "Live quotes no longer support a profitable
    round trip" rejections at execution time. Confirmed in production:
    two consecutive real BSC signals (net +$5.19, then net +$5.31, ~6
    minutes apart, one from a 13.2s scan and one from a 4.8s scan) were
    BOTH rejected by LocalExecutor at execution with min_final_out ~10.5%
    BELOW amount_in — an order of magnitude larger than either a genuine
    0.4% spread closing, or normal block-to-block price drift on BSC
    (~3s blocks). Root cause wasn't timing — it was a methodology gap:

    _compute_profit()'s gross_return_raw = loan_amount * spread_pct is a
    LINEAR model (spot-price spread applied flatly across the whole
    loan), softened by one crude slippage_haircut_pct term
    (_SLIPPAGE_IMPACT_COEFF * loan/liquidity, explicitly documented above
    as "no AMM curve math"). LocalExecutor's live getAmountsOut() calls,
    by contrast, apply the REAL constant-product curve on BOTH legs
    sequentially (buy leg's output becomes sell leg's input) — a
    fundamentally convex function that a single linear haircut term
    cannot approximate at the loan sizes these thin BSC pools are
    already being capped to (_MAX_LOAN_LIQUIDITY_PCT_LIVE = 5%). Scanner
    was scoring signals as profitable under its own rough model that
    were never going to clear the exact math LocalExecutor (correctly)
    checks before sending anything on-chain — no funds were ever at
    risk, but real opportunities were being computed, "found", and then
    reliably thrown away.

    _read_live_reserves() already reads raw getReserves() integers every
    cycle (base_reserve, stable_reserve) and immediately discarded them
    after reducing them to a single implied_price_usd float —
    everything needed for the exact math was already being fetched and
    thrown away. Fix: DexPairInfo now carries the raw reserve integers
    through (base_reserve_raw/stable_reserve_raw, default 0 — DexScreener-
    sourced entries that never went through the live on-chain path
    simply leave these at 0 and fall back to the old linear model
    unchanged). New _v2_amount_out() is the exact same constant-product
    formula every Uniswap-V2-fork router's getAmountsOut() runs on-chain
    (see local_executor.py's own buy_quote/sell_quote calls — same
    formula, same shape, now computed a second time here at signal time
    instead of only at execution time). Whenever both buy_pair and
    sell_pair carry live raw reserves, _scan_pair() now runs the exact
    two-leg round trip at the (already liquidity-capped) effective_loan
    size and uses THAT as gross_return — not the linear estimate. This
    doesn't just fix a display number: it means a signal that would have
    failed LocalExecutor's check now correctly resolves to HOLD before
    ever reaching _notify_payout, instead of round-tripping through a
    doomed execute_trade() call. The crude linear+haircut model is kept
    verbatim as the fallback for the DexScreener-only path (raw reserves
    genuinely unavailable there), and slippage_haircut_pct is still
    computed and reported for the exact path too — now retroactively, as
    (1 - exact_gross_return / naive_gross_return), so existing warnings/
    Telegram formatting that reads that field keep working unchanged.

    Per-DEX fee_bps (_DEX_FEE_BPS: pancakeswap 25, mdex 30, biswap 10)
    were sourced from each protocol's own public docs at the time this
    was written, not verified against each pool's actual on-chain fee
    (Biswap in particular can run per-pair dynamic fees) — treat these
    as a reasonable default, not a guarantee, the same caveat already
    given for the router addresses above.

    This does NOT address a second, smaller latency contributor also
    visible in this session's logs: scan() awaits self._price.get_prices()
    (5-13s under oracle primary-timeout+mirror-failover, per the last two
    sessions' logs) BEFORE _scan_pair()'s live reserve read even starts,
    serially in front of every pair. That's in price_client.py, which
    hasn't been shared in this session — worth a follow-up once that
    file's available, since even the exact math above still reads
    reserves however many seconds late this delay creates.

v18.10 — vs v18.9:

  NEW — "mdex" added to _BSC_EXECUTABLE_DEXES / _BSC_ROUTER_ADDRESSES.
    Independently confirmed via a user-supplied BscScan screenshot:
    0x7dae51bd3e3376b8c7c4900e9107f12be3af1ba8 is labeled by BscScan
    itself as "Mdex: Router", matching MDEX's official URL
    (bsc.mdex.com) — a real verified match, not a memory guess.
    BabySwap/Nomiswap were NOT added — the same screenshot batch only
    showed factory/LP/pair contracts for those two, not their router.
    Widens the live on-chain reserve comparison (v18.8) to 3 DEXs,
    increasing the chance of finding a genuine, executable price gap.
    contract_manager.py's ROUTER_MAP updated to match — see that file's
    own changelog.

v18.9 — vs v18.8:

  TUNING — _DEFAULT_LOAN_USD 10_000 → 50_000, now that v18.8's live
    on-chain reserve reads have replaced DexScreener's unreliable
    liquidity figure for BSC. The liquidity-proportional cap
    (_MAX_LOAN_LIQUIDITY_PCT_LIVE, 5%) still governs the actual loan
    size used against real reserves — this just removes the artificially
    low starting point that was capping trades below what a genuinely
    deep pool could otherwise support. See the constant's own comment
    for the full reasoning.

v18.8 — vs v18.7:

  NEW — v18.5 through v18.7 spent three rounds tuning
    _MAX_LOAN_LIQUIDITY_PCT against DexScreener's advertised liquidity
    figure, and it never converged: 15% let a totally dead pool through,
    3% still lost ~13% on a real-but-thin pool, and 1% (last resort)
    shrank loan sizes so far that spreads stopped clearing min_profit at
    all ("best spread 0.885% net $8.89, need $15.00" — real edge, too
    small in dollar terms to matter). The actual problem was never the
    percentage — it was trusting DexScreener's liquidity number at any
    percentage.

    This replaces that entirely for BSC: _fetch_live_bsc_reserves() reads
    REAL getReserves() from each executable router's own pool, discovered
    via router.factory().getPair() (standard, universal UniswapV2-fork
    interface — no new addresses trusted, only read calls against the
    router addresses already proven correct across every trade so far).
    Price is now computed from each pool's own reserve ratio (assuming
    USDT ≈ $1), not a single oracle price shared across every DEX — this
    also fixes a latent bug in the pre-existing (never-activated, since
    w3_by_chain was never wired in) _fetch_reserves_onchain path, which
    would have reported 0% spread between any two on-chain-sourced pairs.

    DexScreener remains the fallback whenever live RPC reads fail for any
    reason (bot.py's w3_by_chain not wired, RPC hiccup, pool doesn't
    exist) — same v18.4-v18.7 filtering/tuning applies unchanged in that
    case. When live reserves ARE used, the loan-liquidity cap uses the
    more generous _MAX_LOAN_LIQUIDITY_PCT_LIVE (5%) instead of 1%, since
    ground truth deserves more trust than DexScreener's numbers. Requires
    bot.py to pass w3_by_chain={"BSC": ...} — see bot.py's own changelog.

v18.7 — vs v18.6:

  TUNING — _MAX_LOAN_LIQUIDITY_PCT 0.03 → 0.01. Even at 3%, a real
    (non-dead) Biswap BTCB/USDT pool still returned a live quote ~13.3%
    underwater on a $4,341.56 loan. Tightened further; see the
    constant's own comment for the full data.

v18.6 — vs v18.5:

  FIX — removed "apeswap" from _BSC_EXECUTABLE_DEXES. Confirmed across
    two consecutive production scans at very different loan sizes
    ($26,397.86, then $5,290.98 after v18.5's tighter cap) that ApeSwap's
    live BTCB/USDT quote returns the same ~$0.00096 output regardless of
    input size — a near-dust/dead pool (or possibly a stale router
    address; unverified, no BSC RPC access from this environment).
    v18.5's loan-size reduction couldn't fix this because the ceiling
    isn't proportional to trade size. Only pancakeswap/biswap remain
    until ApeSwap's router address and pair liquidity are independently
    verified on BscScan.

v18.5 — vs v18.4:

  TUNING — confirmed in production: a $26,397.86 BSC loan (already
    capped to 15% of DexScreener's advertised pool liquidity) hit
    ApeSwap's real BTCB/USDT reserves and produced a live round-trip
    quote of ~$0.001 — LocalExecutor correctly refused to send it (no
    funds lost), but it means DexScreener's liquidity figures aren't
    trustworthy at anywhere near a 15% loan fraction for BSC's thinner
    pairs. _DEFAULT_LOAN_USD 50_000 → 10_000, _MAX_LOAN_LIQUIDITY_PCT
    0.15 → 0.03. See the constants' own comments below for the full
    reasoning.

v18.4 — vs v18.3:

  FIX — BSC BUY signals were computed across every DEX DexScreener
    indexes for a pair, including plenty local_executor.py has no router
    address for (ROUTER_MAP only knows pancakeswap/biswap/apeswap).
    Confirmed in production: a real $493.53 WBTC BUY signal was found and
    then failed to execute because the winning sell-side quote came from
    a DEX outside that whitelist (DexScreener reported it by raw contract
    address, not even a name). _scan_pair() now restricts BSC candidate
    DEX pairs to _BSC_EXECUTABLE_DEXES before computing the buy/sell
    spread — every BSC BUY signal from here on is guaranteed executable.
    New unresolvable-DEX opportunities are treated as insufficient data
    (HOLD with a warning) rather than a signal that will just fail again.

v18.3 — vs v18.2:

  FIX — _notify_payout()'s oracle.execute_trade() call was missing
    sell_dex/chain/min_profit_usd, even though ScanResult already carries
    all three (sell_on, chain, min_profit). This was harmless for the old
    Worker-based OracleClient (which computed its own exit route
    server-side) but is a real gap for the new local_executor.py path
    (modules/local_executor.py, wired in from bot.py when
    LOCAL_EXECUTION_ENABLED=true) — LocalExecutor calls startArbitrage()
    directly and needs the real exit-leg DEX to build the second swap.
    See the call site below for the exact fields added.

v18.2 — vs v18.1:

  NEW — optional `report_channel` constructor param, same structural
    pattern as payout_manager below: when wired in, scan() calls
    ReportChannel.report_scan() at its own single return point, and
    ReportChannel.report_error() from its own except-block, so every
    caller reports itself with no new convention to remember. This is
    modules/report_channel.py's first actual wiring — it existed as a
    complete, tested module but nothing constructed or passed it before
    this version (see bot.py v17.17).

  FIX — ScanResult.to_dict() was missing the "spreadPct" and "warnings"
    keys that ReportChannel._format_scan() reads. Without them every
    scan report would have silently rendered "spread 0.000%" with no
    warnings shown, since ReportChannel's own dict.get() fallbacks have
    no way to know those keys are simply absent vs. genuinely zero.
    Added both as additive keys — existing to_dict() consumers
    (TelegramClient's card, command_handlers.py) are unaffected.

v18.1 (Incident Report §2.B follow-up) — vs v18.0:

  NEW — optional `payout_manager` constructor param. When wired in,
    scan() now calls PayoutManager.record_scan() and
    check_and_auto_sweep() itself, at its own single return point,
    instead of relying on every caller (command_handlers.py's /hunt
    handler, and whatever calls scan() next) to remember to do it by
    hand. That's exactly the class of bug the original incident report
    was about — the wiring existed, calling it was optional-by-convention,
    and one call site forgot. Making it structural here means it can't
    be forgotten again by a future call site. command_handlers.py's own
    explicit record_scan()/check_and_auto_sweep() calls have been
    removed accordingly — Scanner does it now, so calling both would
    double-count every scan. A caller can still read the outcome via the
    new ScanResult.sweep_result / .payout_notified fields (or their
    to_dict() keys sweepResult / payoutNotified) without re-triggering
    anything. Passing no payout_manager keeps Scanner exactly as before
    — a read-only pricing engine with zero PayoutManager side effects.

v18.0 (Scanner upgrade pass) — vs v17.12:

  FIX (correctness/risk) — removed the fabricated-spread fallback. When
    DexScreener returned <2 usable pairs, or the observed spread failed the
    sanity check (> _MAX_SANE_SPREAD_PCT), the old code SIMULATED a fake
    0.15% spread and ran it straight through _compute_profit(). On a
    $50k loan that fabricated number alone (~$75 gross before fees) could
    clear the $30 min-profit gate and signal BUY — i.e. the bot could
    recommend/execute a trade based on a number that was never observed
    anywhere, not even a stale one. Both cases now return an explicit HOLD
    with data_quality="insufficient" and no profit computation at all. This
    is the same category of protection v17.12's price_is_live guard already
    gave you for stale prices; it just hadn't been applied to DEX-pair data.

  FIX (correctness) — gas cost was computed as
    `_DEFAULT_GAS_UNITS * 30e-9 * <base asset's own price>`, which happened
    to be correct only because both original pairs traded the chain's own
    gas asset (WETH on ETH, BNB on BSC). Adding a WBTC pair would have
    silently priced ETH gas using BTC's price (~65000 vs ~1600 — a ~40x
    error). Gas cost is now computed from each pair's `gas_asset` (the
    chain's native gas token) independently of which asset is being
    arbitraged.

  NEW — Real per-chain gas pricing. Previously a single hardcoded 30 gwei
    constant was applied to every chain, including BSC (where 30 gwei is
    roughly 5-10x actual typical gas price, understating BSC opportunities'
    true cost by under-charging... actually overcharging BSC and making
    real opportunities look worse than they are). Static per-chain
    defaults (_STATIC_GAS_GWEI) replace the single constant, and an
    optional `w3_by_chain` constructor param lets a caller wire in live
    `eth_gasPrice` lookups (via asyncio.to_thread, matching the blocking-
    call pattern already established in contract_manager.py /
    command_handlers.py) with the static table as a fallback if that RPC
    call fails or isn't wired up. MAX_GAS_GWEI (already documented in
    config.py) is honored here too — a live gas spike is capped for the
    cost model rather than producing a wildly-wrong profit estimate.

  NEW — Liquidity-aware sizing. A spread quoted against a pool with only
    slightly more liquidity than the loan amount is not a spread you can
    actually capture at that size — the trade itself moves the price. If
    the loan is more than _MAX_LOAN_LIQUIDITY_PCT of the shallower pool's
    liquidity, the loan amount used for THIS quote's profit calculation is
    capped down to that fraction (never silently assumes full size is
    fillable), and a proportional slippage haircut is applied to the gross
    return. Both are rough models — documented as such — not a substitute
    for the real slippage-buffered quote ContractManager now gets before
    /execute (see contract_manager.py v2.0 changelog).

  NEW — Concurrent pair scanning. Pairs were scanned sequentially in a
    for-loop, each one a full DexScreener round-trip. With 4 pairs now
    (was 2) that's a meaningful chunk of scan_timeout paid serially for no
    reason — nothing about pair N+1 depends on pair N's result. Pairs now
    scan via asyncio.gather, same pattern price_client.py already uses for
    per-instrument OKX/Binance fetches.

  FIX — Scanner.scan() now actually accepts a `min_profit` override.
    command_handlers.py's _run_hunt() has been calling
    `self._scanner.scan(min_profit=self.effective_min_profit)` since
    v17.14 and silently falling back to a no-arg call on TypeError — this
    build never had that kwarg, so Mint Mode's "halve the profit gate" has
    been a no-op for every scan since it shipped. Fixed by accepting the
    override directly; no command_handlers.py change needed for this fix
    to take effect (the existing try/except just stops hitting the
    except branch).

  NEW — More pairs. Added WBTC/USDC (ETH: uniswap↔sushiswap) and
    WBTC/USDT (BSC: pancakeswap↔biswap). Both use assets price_client.py
    already resolves live (WBTC is already in _CG_IDS / _OKX_BASE /
    _BINANCE_SYMBOL / _STATIC_PRICES — no price_client changes needed).
    On BSC this token actually trades as BTCB, not WBTC — see the
    _SCAN_PAIRS comment below. `pairs` is now also a constructor
    parameter, so more can be added without editing this file.

  NEW — Short-TTL cache + simple failure backoff per DexScreener token
    address, so a manual /hunt landing right after the scheduled auto-scan
    (or scan_lock contention edge cases) doesn't necessarily double up on
    external requests, and a token that just failed isn't retried on every
    single cycle. Simpler than price_client.py's exponential backoff
    (fixed cooldown, no jitter) since this is a supplementary data source,
    not a critical price feed.

  Every new address below (routers, WBTC, BTCB) was cross-checked against
  Etherscan/BscScan at the time this was written. Addresses controlling
  real funds should still be independently re-verified by you before
  relying on them — a typo in a hardcoded address is a silent way to lose
  money, not a loud one.

v17.12 FIX (kept):
  _DEFAULT_MIN_PROFIT: 100.0 → 30.0 — real DEX spreads on a $50k flash
  loan often net $65-85 after fees, well under the old $100 gate.
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

import httpx

from .price_client import PriceClient, PriceEntry

if TYPE_CHECKING:
    from .payout_manager import PayoutManager
    from .report_channel import ReportChannel
    from .oracle import OracleClient

logger = logging.getLogger(__name__)

_DEFAULT_MIN_PROFIT    = 30.0
# v18.5 FIX (2026-07-10) — was 50_000.0. Confirmed in production: a
# liquidity-capped loan of $26,397.86 (15% of DexScreener's advertised
# pool liquidity, per _MAX_LOAN_LIQUIDITY_PCT below) against ApeSwap's
# real BTCB/USDT reserves returned a live getAmountsOut() quote of
# ~$0.001 on the round trip — DexScreener's advertised liquidity for
# that pool was nowhere close to what the pool could actually absorb.
# LocalExecutor's live-quote check correctly refused to send that
# transaction (see local_executor.py — no funds were lost), but it means
# $50k as a *starting point* before any capping is far too large for
# these thinner BSC pairs. Lowered to $10k, matching the MIN_LOAN_USD
# floor already established on the JS/Worker side (src/config/
# constants.js CFG.MIN_LOAN_USD).
#
# v18.9 FIX — back to 50_000.0. The $10k drop above was a workaround for
# DexScreener's unreliable liquidity figure, which v18.8 has since
# replaced with real getReserves() reads for BSC pairs whenever
# w3_by_chain is wired in (confirmed live: production logs show BSC
# scans no longer even calling DexScreener). With ground-truth liquidity,
# the loan-liquidity cap (_MAX_LOAN_LIQUIDITY_PCT_LIVE, 5%) already
# clamps effective_loan down to what the pool can actually support
# regardless of how high this default is — raising the default back up
# just lets a genuinely deep pool actually use its full 5% instead of
# being artificially capped by a starting point that was too low to
# reach it. The DexScreener-fallback path (when live reserves aren't
# available that cycle) still uses the conservative
# _MAX_LOAN_LIQUIDITY_PCT (1%), so a fallback cycle stays just as safe
# as before this change.
_DEFAULT_LOAN_USD      = 50_000.0
_DEFAULT_LOAN_FEE_PCT  = 0.09
_DEFAULT_GAS_UNITS     = 900_000
_DEXSCREENER_BASE      = "https://api.dexscreener.com/latest/dex"
_MIN_LIQUIDITY_USD     = 50_000
_MAX_SANE_SPREAD_PCT   = 0.02

# ── Risk model knobs (v18.0) ─────────────────────────────────────────────────
# Don't trust a quote's profit at full loan size if the loan is more than
# this fraction of the shallower side's liquidity — the trade itself would
# move the price by more than this model can responsibly ignore.
#
# v18.5 FIX — was 0.15 (15%). This is exactly the ratio that let the
# $26,397.86 trade above through — 15% of DexScreener's *advertised*
# liquidity was still catastrophically too large for the pool's *real*
# depth. DexScreener's liquidity figure is not reliable enough to trust
# at anywhere near this fraction for BSC's thinner altcoin pairs.
# Tightened to 3% — still liquidity-proportional (deeper pools still
# support proportionally larger loans), but with far more margin against
# a stale/overstated liquidity figure. LocalExecutor's live on-chain
# getAmountsOut() check remains the authoritative, final safety gate
# regardless of this value — this only reduces how often a doomed trade
# gets that far in the first place.
#
# v18.6 FIX — was 0.03 (3%). Even at 3%, a $4,341.56 loan against
# Biswap's real BTCB/USDT reserves (a genuine, non-dead pool — unlike
# ApeSwap's above) still came back ~13.3% underwater on the live quote
# (min_final_out=$3,763.39 < amount_in=$4,341.56). Tightened further to
# 1%. This is calibrated off limited production data (two rejected
# trades) against a DexScreener liquidity figure that's proven
# unreliable more than once now — may need further tuning. Either way,
# LocalExecutor's live getAmountsOut() check keeps rejecting anything
# unprofitable regardless of this value, so no funds are at risk while
# this gets dialed in.
_MAX_LOAN_LIQUIDITY_PCT = 0.01
# v18.8 — used instead of _MAX_LOAN_LIQUIDITY_PCT whenever liquidity came
# from a LIVE on-chain getReserves() read (_fetch_live_bsc_reserves)
# rather than DexScreener's advertised figure. Ground-truth reserves
# deserve more trust than a third party's number that's proven unreliable
# (see the changelog above) — but this still isn't 100%, since even real
# reserves mean a real constant-product price-impact curve as trade size
# grows, and LocalExecutor's live getAmountsOut() check is still the
# actual final word regardless of this value.
_MAX_LOAN_LIQUIDITY_PCT_LIVE = 0.05
# Rough price-impact model: haircut_pct ≈ coeff * (effective_loan / liquidity),
# capped at 50%. This is intentionally crude (no AMM curve math) — it exists
# to stop a thin pool from looking as profitable as a deep one, not to be a
# precise slippage quote. The real slippage floor that actually protects an
# on-chain trade lives in contract_manager.py's auto-quoted minIntermediate/
# minFinalOut, not here.
_SLIPPAGE_IMPACT_COEFF  = 0.5

# DexScreener response cache / failure backoff (per token address). Short —
# this exists to dedupe near-simultaneous scans, not to serve stale data as
# current across a real 2.5s+ polling interval.
_DEX_CACHE_TTL_SECS     = 3.0
_DEX_FAIL_BACKOFF_SECS  = 15.0

# Static per-chain gas price fallback (gwei) — used when no live w3_by_chain
# lookup is wired in, or the live lookup fails. Approximate; override via
# the `w3_by_chain` constructor param for a real eth_gasPrice read.
_STATIC_GAS_GWEI: dict[str, float] = {
    "ETH":      25.0,
    "ETHEREUM": 25.0,
    "BSC":      5.0,
    "BNB":      5.0,
}

# ── Scan pairs ────────────────────────────────────────────────────────────────
# `gas_asset` is the chain's native gas-paying token — used ONLY for the gas
# cost calculation, independent of which asset is being arbitraged (`base`).
# Router/token addresses were cross-checked against Etherscan/BscScan;
# re-verify independently before trusting real funds to them.
#
# pair_a / pair_b (NEW — direct-RPC mode): the actual Uniswap-V2-style
# LP/pool contract address for this token on dex_a / dex_b respectively
# (NOT the token address — a pair address, e.g. from
# https://etherscan.io/address/<factory>#readContract getPair(tokenA,
# tokenB), or the "Pair" link on the DexScreener page for that pool).
# When both are set AND a Web3 instance for `chain` exists in
# w3_by_chain, Scanner reads getReserves() directly on-chain instead of
# calling DexScreener — no HTTP round-trip, typically sub-100ms vs
# 200-400ms+ per DexScreener call, and removes a third-party API as a
# point of failure entirely for that pair.
#
# Leave pair_a/pair_b empty (or omit them) to keep using DexScreener for
# that pair unchanged — this is fully backward compatible per-pair, not
# an all-or-nothing switch. YOU must fill in real pair addresses below;
# placeholders are left blank deliberately rather than guessed, since a
# wrong pair address would silently read reserves from the wrong pool.
_SCAN_PAIRS: list[dict] = [
    {
        "base"        : "WETH",
        "stable"      : "USDC",
        "chain"       : "ETH",
        "gas_asset"   : "WETH",
        "dex_chain_id": "ethereum",
        "dex_a"       : "uniswap",
        "dex_b"       : "sushiswap",
        "address"     : "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
        "pair_a"      : "",  # TODO: Uniswap V2 WETH/USDC pair address
        "pair_b"      : "",  # TODO: SushiSwap WETH/USDC pair address
        "base_decimals"  : 18,
        "stable_decimals": 6,
    },
    {
        "base"        : "BNB",
        "stable"      : "USDT",
        "chain"       : "BSC",
        "gas_asset"   : "BNB",
        "dex_chain_id": "bsc",
        "dex_a"       : "pancakeswap",
        "dex_b"       : "biswap",
        "address"     : "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB
        "pair_a"      : "",  # TODO: PancakeSwap WBNB/USDT pair address
        "pair_b"      : "",  # TODO: Biswap WBNB/USDT pair address
        "base_decimals"  : 18,
        "stable_decimals": 18,
    },
    # NEW v18.0 — both use assets price_client.py already resolves live.
    {
        "base"        : "WBTC",
        "stable"      : "USDC",
        "chain"       : "ETH",
        "gas_asset"   : "WETH",
        "dex_chain_id": "ethereum",
        "dex_a"       : "uniswap",
        "dex_b"       : "sushiswap",
        "address"     : "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",  # WBTC (8 decimals!)
        "pair_a"      : "",  # TODO: Uniswap V2 WBTC/USDC pair address
        "pair_b"      : "",  # TODO: SushiSwap WBTC/USDC pair address
        "base_decimals"  : 8,
        "stable_decimals": 6,
    },
    {
        # This token is BTCB on BSC, not WBTC — BTCB is a separate BEP-20
        # contract, 1:1 BTC-pegged. "base": "WBTC" here is only the
        # price_client lookup key (BTC's price feed); it's a reasonable
        # proxy since BTCB is directly pegged, not a claim these are the
        # same contract. Note BTCB uses 18 decimals vs WBTC's 8 on ETH —
        # get this wrong in an /execute amount and you're off by 10^10.
        "base"        : "WBTC",
        "stable"      : "USDT",
        "chain"       : "BSC",
        "gas_asset"   : "BNB",
        "dex_chain_id": "bsc",
        "dex_a"       : "pancakeswap",
        "dex_b"       : "biswap",
        "address"     : "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c",  # BTCB
        "pair_a"      : "",  # TODO: PancakeSwap BTCB/USDT pair address
        "pair_b"      : "",  # TODO: Biswap BTCB/USDT pair address
        "base_decimals"  : 18,
        "stable_decimals": 18,
    },
    # NEW v18.13 — CAKE (PancakeSwap Token). Verified via BscScan "Source
    # Code Verified — Exact Match" + operator-pasted full source (contract
    # CakeToken). dex_a/dex_b below are only the DexScreener-fallback
    # labels — the live on-chain path (_fetch_live_bsc_reserves) already
    # checks this pair against all of _BSC_ROUTER_ADDRESSES regardless.
    {
        "base"        : "CAKE",
        "stable"      : "USDT",
        "chain"       : "BSC",
        "gas_asset"   : "BNB",
        "dex_chain_id": "bsc",
        "dex_a"       : "pancakeswap",
        "dex_b"       : "biswap",
        "address"     : "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82",  # CAKE
        "pair_a"      : "",  # TODO: PancakeSwap CAKE/USDT pair address
        "pair_b"      : "",  # TODO: Biswap CAKE/USDT pair address
        "base_decimals"  : 18,
        "stable_decimals": 18,
    },
    # NEW v18.14 — bridged ETH (Binance-Peg Ethereum Token) on BSC. No new
    # trust introduced: this exact address has been sitting verified and
    # unchanged in constants.py's BSC_TOKENS since before this file's own
    # changelog started tracking address additions, and price_client.py
    # already resolves "ETH" live (same rows as the ETH-chain WETH pair
    # above). Deep, real liquidity — chosen over BUSD, whose stablecoin-
    # vs-stablecoin spread against USDT is structurally near-zero and
    # whose supply has been winding down since Binance stopped minting it.
    {
        "base"        : "ETH",
        "stable"      : "USDT",
        "chain"       : "BSC",
        "gas_asset"   : "BNB",
        "dex_chain_id": "bsc",
        "dex_a"       : "pancakeswap",
        "dex_b"       : "biswap",
        "address"     : "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",  # Binance-Peg ETH
        "pair_a"      : "",  # TODO: PancakeSwap ETH/USDT pair address
        "pair_b"      : "",  # TODO: Biswap ETH/USDT pair address
        "base_decimals"  : 18,
        "stable_decimals": 18,
    },
    # NEW v18.15 — Binance-Peg XRP. BscScan flags "displayed name does not
    # match contract's Name function" on this address — a known cosmetic
    # quirk of BSC's early-2020 Binance-Peg contract template (internal
    # name() = "XRP Token", not the later official label), not unique to
    # XRP. Independently cross-checked against CoinGecko, OKX, Uniswap's
    # token explorer, and OKLink before trusting it — all five agree on
    # this exact address (~530K+ holders, price consistent with real
    # XRP). See constants.py's own changelog for the same verification
    # note against BSC_TOKENS.
    {
        "base"        : "XRP",
        "stable"      : "USDT",
        "chain"       : "BSC",
        "gas_asset"   : "BNB",
        "dex_chain_id": "bsc",
        "dex_a"       : "pancakeswap",
        "dex_b"       : "biswap",
        "address"     : "0x1D2F0da169ceB9fC7B3144628dB156f3F6c60dBE",  # Binance-Peg XRP
        "pair_a"      : "",  # TODO: PancakeSwap XRP/USDT pair address
        "pair_b"      : "",  # TODO: Biswap XRP/USDT pair address
        "base_decimals"  : 18,
        "stable_decimals": 18,
    },
    # NEW v18.17 — USDC-quoted twins of existing pairs. Zero new address
    # trust (see the v18.17 changelog): same base tokens, same routers,
    # USDC already canonical in constants.py. Nearly doubles the venue
    # combinations the fee-aware picker can search each cycle.
    {
        "base"        : "BNB",
        "stable"      : "USDC",
        "chain"       : "BSC",
        "gas_asset"   : "BNB",
        "dex_chain_id": "bsc",
        "dex_a"       : "pancakeswap",
        "dex_b"       : "biswap",
        "address"     : "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB
        "pair_a"      : "",
        "pair_b"      : "",
        "base_decimals"  : 18,
        "stable_decimals": 18,
    },
    {
        "base"        : "ETH",
        "stable"      : "USDC",
        "chain"       : "BSC",
        "gas_asset"   : "BNB",
        "dex_chain_id": "bsc",
        "dex_a"       : "pancakeswap",
        "dex_b"       : "biswap",
        "address"     : "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",  # Binance-Peg ETH
        "pair_a"      : "",
        "pair_b"      : "",
        "base_decimals"  : 18,
        "stable_decimals": 18,
    },
    {
        # BTCB against USDC — "base": "WBTC" is the price_client lookup
        # key only, same convention as the BTCB/USDT pair above.
        "base"        : "WBTC",
        "stable"      : "USDC",
        "chain"       : "BSC",
        "gas_asset"   : "BNB",
        "dex_chain_id": "bsc",
        "dex_a"       : "pancakeswap",
        "dex_b"       : "biswap",
        "address"     : "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c",  # BTCB
        "pair_a"      : "",
        "pair_b"      : "",
        "base_decimals"  : 18,
        "stable_decimals": 18,
    },
]

# DexScreener dexId values this deployment can actually execute a swap
# through — must match modules/contract_manager.py's ROUTER_MAP keys
# exactly. Deliberately NOT auto-expanded from DexScreener's listings:
# every entry here is a router address LocalExecutor will call
# swapExactTokensForTokens() on with real funds, so each one has to be
# independently verified (BscScan) before being added — a wrong address
# here is unrecoverable fund loss, not a bad trade. See scanner.py v18.4
# changelog / _scan_pair()'s BSC filter for why this exists.
#
# v18.6 FIX — "apeswap" removed. Confirmed in production across two
# consecutive scans at very different loan sizes ($26,397.86 then
# $5,290.98 after v18.5's tighter cap): the live getAmountsOut() quote
# through ApeSwap's router for BTCB/USDT returned the SAME ~$0.00096
# output both times — the classic signature of a pool whose real
# reserves are near-dust, where output asymptotes to a fixed ceiling
# regardless of input size. Lowering loan size further cannot fix this;
# the route itself needs to stop being proposed. This could also mean
# the ApeSwap router address in contract_manager.py's ROUTER_MAP
# (0xcF0feBd3f17CEf5b47b0cD257aCf6025c5BFf3b7) is stale/deprecated —
# unconfirmed, since this environment has no BSC RPC/BscScan access to
# verify independently. Re-add "apeswap" only after confirming on
# BscScan that address is still ApeSwap's live, current V2 router AND
# that it has real BTCB/USDT (or whichever pair) liquidity.
#
# v18.10 FIX — "mdex" added. User-supplied BscScan screenshot confirmed
# 0x7dae51bd3e3376b8c7c4900e9107f12be3af1ba8 is independently labeled by
# BscScan itself as "Mdex: Router", matching MDEX's official URL
# (bsc.mdex.com) — a real, verified match, not a memory guess. MDEX is a
# standard UniswapV2 fork (same interface as PancakeSwap/Biswap).
# BabySwap and Nomiswap were NOT added from the same screenshot batch —
# only factory/LP/pair contracts were visible for those two, not their
# actual router.
_BSC_EXECUTABLE_DEXES: set[str] = {"pancakeswap", "biswap", "mdex"}

# Standard Uniswap-V2-compatible pair ABI — only the methods we need.
_PAIR_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"name": "_reserve0", "type": "uint112"},
            {"name": "_reserve1", "type": "uint112"},
            {"name": "_blockTimestampLast", "type": "uint32"},
        ],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token0",
        "outputs": [{"name": "", "type": "address"}],
        "type": "function",
    },
]

# v18.8 — standard UniswapV2-fork router/factory interface, universal
# across PancakeSwap V2 and Biswap (both are direct V2 forks). Used to
# AUTO-DISCOVER each router's real pair contract for a token pair — no
# hardcoded pool address needed, and therefore no new address to
# independently verify. The router addresses these are called against
# are the same ones already proven correct across every trade so far
# (contract_manager.py's ROUTER_MAP / scanner.py's _BSC_ROUTER_ADDRESSES
# below) — factory()/getPair() are just read-only interface calls
# against an already-trusted contract, not new trust of their own.
_ROUTER_FACTORY_ABI = [
    {
        "constant": True, "inputs": [], "name": "factory",
        "outputs": [{"name": "", "type": "address"}], "type": "function",
    },
]
_FACTORY_PAIR_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "tokenA", "type": "address"}, {"name": "tokenB", "type": "address"}],
        "name": "getPair",
        "outputs": [{"name": "pair", "type": "address"}],
        "type": "function",
    },
]
_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Same router addresses as contract_manager.py's ROUTER_MAP, filtered to
# _BSC_EXECUTABLE_DEXES — kept as a local copy rather than importing
# contract_manager.py directly, since that module requires
# FLASH_ARBITRAGE_CONTRACT_ADDRESS to be set at import/construction time
# and Scanner must be able to run (in DexScreener-only mode) without it.
# Keep this in sync with contract_manager.py's ROUTER_MAP by hand.
_BSC_ROUTER_ADDRESSES: dict[str, str] = {
    "pancakeswap": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
    "biswap":      "0x3a6d8cA21D1CF76F653A67577FA0D27453350dD8",
    # v18.10 — confirmed via BscScan's own "Mdex: Router" label (user
    # screenshot), matching bsc.mdex.com's official router.
    "mdex":        "0x7DAe51BD3E3376B8c7c4900E9107f12Be3AF1bA8",
}
# BSC-USDT (18 decimals) — same address already in use throughout this
# deployment (constants.py BSC_TOKENS, contract_manager.py AssetRegistry).
_BSC_STABLE_ADDRESSES: dict[str, str] = {
    "USDT": "0x55d398326f99059fF775485246999027B3197955",
    # v18.17 — Binance-Peg USDC, 18 decimals. Same address already
    # canonical in constants.py's BSC_TOKENS (predates this changelog) —
    # no new trust. Prices via price_client's stablecoin peg tier.
    "USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
}

# v18.11 — per-DEX swap fee, basis points, for the exact constant-product
# round-trip simulation below. Sourced from each protocol's own public docs
# at the time this was written — NOT independently confirmed on-chain per
# pool the way the router addresses above were. Biswap in particular is
# capable of per-pair dynamic fees; treat this as a reasonable default, not
# a guarantee. An unlisted dex_id falls back to 25 (PancakeSwap-style) via
# .get() below rather than raising — better a slightly-off exact estimate
# than none at all.
_DEX_FEE_BPS: dict[str, int] = {
    "pancakeswap": 25,  # 0.25%
    "mdex":        30,  # 0.30%
    "biswap":      10,  # 0.10% — Biswap's marketed low-fee model
}


def _v2_amount_out(amount_in: int, reserve_in: int, reserve_out: int, fee_bps: int) -> int:
    """Exact Uniswap-V2-fork constant-product output for one swap leg —
    the identical formula every _BSC_ROUTER_ADDRESSES router runs on-chain
    inside getAmountsOut() (see local_executor.py's ROUTER_QUOTE_ABI calls).
    All amounts are raw integer token units — decimals cancel out of the
    ratio, so no decimal conversion is needed here, only when translating
    the final result back to a human/USD figure at the call site.
    Returns 0 (never raises) on any non-positive input, matching the rest
    of this deployment's convention of a safe zero rather than a signal
    computed on garbage."""
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return 0
    fee_mult = 10_000 - fee_bps
    amount_in_with_fee = amount_in * fee_mult
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in * 10_000 + amount_in_with_fee
    return numerator // denominator

# Direct-RPC reserve reads get their own short TTL + failure backoff,
# mirroring the DexScreener cache below — same reasoning (dedupe
# near-simultaneous scans, don't hammer a stuck RPC every cycle).
_RPC_CACHE_TTL_SECS    = 3.0
_RPC_FAIL_BACKOFF_SECS = 15.0

# v18.19 — a live pool whose last on-chain trade is older than this is
# treated as stale/dead and excluded from the candidate set: its frozen
# reserve ratio produces a phantom spread that can't actually be executed.
# 30 min is generous enough to keep genuinely-active-but-quiet pools while
# dropping the multi-hour-dead ones the v18.18 probe exposed.
_MAX_RESERVE_AGE_SECS  = 1800.0

# v18.20 — DexScreener-side counterpart to _MAX_RESERVE_AGE_SECS.
# DexScreener rows have no on-chain block timestamp (reserve_block_ts is
# always 0 for them), so "traded at least once" is checked via its
# rolling txn-count windows instead: at least this many combined
# buys+sells across the m5 AND h1 windows together. 1 is deliberately
# minimal — this is a dead/alive gate, not a liquidity or volume filter
# (that's _MIN_LIQUIDITY_USD's job); a pool with a single recent trade
# is exactly the "genuinely live, executable pool" v18.19's docstring
# describes, same bar as a fresh block_ts on the RPC side.
_MIN_DEXSCREENER_RECENT_TXNS = 1

# ── AI Market Pulse / near-miss knobs (v18.16) ──────────────────────────────
# Purely notification cadence — nothing here influences any trade decision.
_PULSE_INTERVAL_SECS      = 3600.0  # hourly digest to Telegram
# A cycle whose best net lands above this (i.e. at/near break-even) fires
# an immediate near-miss alert. -0.10 = within 10 cents of break-even.
_NEAR_MISS_NET_USD        = -0.10
_NEAR_MISS_COOLDOWN_SECS  = 3600.0  # at most one near-miss alert per hour


@dataclass
class DexPairInfo:
    dex_id    : str
    price_usd : float
    liquidity : float
    volume24h : float
    # v18.11 — raw getReserves() integers (base/stable, native decimals),
    # additive with a 0 default so existing positional DexScreener-path
    # call sites (DexPairInfo(dex_name, price_usd, liquidity_usd, 0.0))
    # are unaffected and simply carry no raw reserves. Only
    # _fetch_live_bsc_reserves() populates these — they're what let
    # _scan_pair() run the exact constant-product round trip instead of
    # the linear spread_pct*loan_amount approximation. 0 means "unknown/
    # unavailable", never a real reserve value (guarded by <= 0 checks
    # in _v2_amount_out()).
    base_reserve_raw   : int = 0
    stable_reserve_raw : int = 0
    # v18.18 — UniswapV2 getReserves()'s 3rd return value: the block
    # timestamp of the LAST trade that moved this pool. Read fresh every
    # scan and previously discarded. Surfaced now purely as a freshness
    # probe: if this value is identical across scans minutes apart, the
    # pool had no reserve-changing trade in between (so an unchanged spread
    # is real, not a stale cache); if it advances while the computed spread
    # stays byte-identical, that's the signature of a genuine bug worth
    # chasing. 0 = DexScreener-sourced (no on-chain timestamp).
    reserve_block_ts   : int = 0
    # v18.28 — the pool contract address this row describes, when known.
    # DexScreener rows carry their `pairAddress`; the configured-pair
    # on-chain path carries cfg's pair_a/pair_b. Empty = unknown. This is
    # what lets _verify_pairs_onchain() upgrade a DexScreener-sourced row
    # to live raw reserves (making it quote-verifiable) without a factory
    # lookup.
    pair_address       : str = ""
    # v18.28 — the quote-token address DexScreener reports for this row.
    # Empty for on-chain-sourced rows (their quote token is the cfg
    # stable by construction). Used to reject pools quoted in something
    # other than the configured stable (e.g. an XRP/WBNB pool posing as
    # a "biswap XRP venue") — those can't run the stable→base→stable
    # loop this bot executes, so any spread against them is phantom.
    quote_token_addr   : str = ""


@dataclass
class ScanResult:
    ts                   : float         = field(default_factory=time.time)
    duration_ms          : float         = 0.0
    signal               : str           = "HOLD"
    has_opportunity      : bool          = False
    chain                : str           = ""
    base_asset           : str           = ""
    stable_asset         : str           = ""
    buy_on               : str           = ""
    sell_on              : str           = ""
    loan_amount          : float         = _DEFAULT_LOAN_USD
    gross_return         : float         = 0.0
    loan_fee             : float         = 0.0
    gas_cost_usd         : float         = 0.0
    net_after_fee        : float         = 0.0
    min_profit           : float         = _DEFAULT_MIN_PROFIT
    spread_pct           : float         = 0.0
    price_entries        : dict[str, PriceEntry] = field(default_factory=dict)
    price_source         : str           = "unknown"
    price_is_live        : bool          = False
    pairs_checked        : int           = 0
    dex_pairs            : list[DexPairInfo] = field(default_factory=list)
    error                : str | None    = None
    warnings              : list[str]    = field(default_factory=list)
    # NEW v18.0 — additive fields only; existing dict consumers use .get()
    # with fallbacks throughout this codebase so nothing breaks.
    data_quality          : str          = "unknown"   # "real" | "insufficient"
    liquidity_usd          : float       = 0.0
    slippage_haircut_pct   : float       = 0.0
    gas_price_gwei         : float       = 0.0
    # NEW — the chain's actual native gas-cost asset for this pair (e.g.
    # "BNB" on BSC), as distinct from base_asset (the token being
    # arbitraged). Previously to_dict()'s priceQuality.gasAsset was
    # mistakenly set to base_asset — this field is the real value that
    # fixes that mislabel. Populated in _scan_pair() alongside
    # gas_price_gwei.
    gas_asset               : str        = ""
    # NEW — set by Scanner.scan() itself when a PayoutManager is wired in
    # (see Scanner.__init__'s `payout_manager` param) and this scan just
    # triggered an auto-sweep. None means "no PayoutManager wired" OR
    # "wired, but no sweep fired this cycle" — callers that want to tell
    # the two apart should check payout_notified below instead.
    sweep_result            : dict[str, Any] | None = None
    # True whenever a PayoutManager was wired in and record_scan()/
    # check_and_auto_sweep() were actually called for this scan — lets a
    # caller confirm the ledger was updated without having to call it
    # again itself.
    payout_notified         : bool       = False
    # NEW v18.21 — True only when spread_pct/net_after_fee came from the
    # exact two-leg constant-product round trip against LIVE raw reserves
    # on BOTH legs (the same math LocalExecutor mirrors at execution
    # time). False means the DexScreener-liquidity-sized linear-model
    # fallback was used instead — real numbers worth showing for
    # near-miss/tuning visibility, but never trustworthy enough to size
    # an actual on-chain trade against (see v18.21 changelog). signal is
    # forced to "HOLD" whenever this is False, regardless of net.
    quote_verified           : bool       = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal"      : self.signal,
            "chain"       : self.chain,
            # FIX (Incident Report follow-up): TelegramClient.send_opportunity()
            # reads flat "base" / "priceSource" / "priceLive" keys — they were
            # missing here (only the nested "priceQuality" block existed), so
            # every scan card silently rendered "Base: ?" and "Price source: ?".
            "base"        : self.base_asset,
            "buyOn"       : self.buy_on,
            "sellOn"      : self.sell_on,
            "spread"      : self.spread_pct / 100.0,
            "grossReturn" : self.gross_return,
            "loanFee"     : self.loan_fee,
            "gasCostUSD"  : self.gas_cost_usd,
            "netAfterFee" : self.net_after_fee,
            "minProfit"   : self.min_profit,
            "loanAmount"  : self.loan_amount,
            "priceSource" : self.price_source,
            "priceLive"   : self.price_is_live,
            "priceQuality": {
                "source"   : self.price_source,
                "isLive"   : self.price_is_live,
                # FIX — this was self.base_asset (the token being
                # arbitraged, e.g. a memecoin), not the chain's actual
                # gas-cost asset (e.g. BNB). Any dashboard/card reading
                # this key was displaying the wrong asset. gas_asset is
                # now a real field on ScanResult, populated in
                # _scan_pair() from cfg["gas_asset"].
                "gasAsset" : self.gas_asset,
            },
            # NEW v18.0 — additive
            "dataQuality"        : self.data_quality,
            "liquidityUSD"       : self.liquidity_usd,
            "slippageHaircutPct" : self.slippage_haircut_pct,
            "gasPriceGwei"       : self.gas_price_gwei,
            "gasAsset"           : self.gas_asset,
            "sweepResult"        : self.sweep_result,
            "payoutNotified"     : self.payout_notified,
            # NEW v18.21 — see ScanResult.quote_verified docstring.
            "quoteVerified"      : self.quote_verified,
            "reason": self._reason(),
            "ts"    : self.ts,
            # NEW — added for ReportChannel (modules/report_channel.py).
            # Its formatter reads "spreadPct" (percent units, matching
            # self.spread_pct directly — NOT the "spread" key above, which
            # is a 0-1 fraction for TelegramClient's card) and "warnings".
            # Neither existed on this dict before, so every scan report
            # was silently rendering "spread 0.000%" with no warnings —
            # additive fix, existing consumers unaffected.
            "spreadPct" : self.spread_pct,
            "warnings"  : list(self.warnings),
        }

    def _reason(self) -> str:
        if self.error:
            return f"Error: {self.error}"
        if not self.price_is_live:
            return (
                f"HOLD — price source is stale (static fallback). "
                f"Net ${self.net_after_fee:.2f} not trusted."
            )
        if self.data_quality == "insufficient":
            return "HOLD — insufficient real DEX-pair data this cycle (no signal computed)."
        if not self.quote_verified and self.net_after_fee >= self.min_profit:
            # v18.21 — would have cleared min_profit on the linear/
            # DexScreener-liquidity-sized estimate, but no live two-leg
            # quote confirmed it — downgraded, not a real miss.
            return (
                f"HOLD — unverified spread only (net ${self.net_after_fee:.2f} would "
                f"clear ${self.min_profit:.2f}, but no live two-leg quote confirmed "
                "it — see v18.21 changelog)."
            )
        if self.signal == "BUY":
            return f"Net ${self.net_after_fee:.2f} ≥ min ${self.min_profit:.2f}"
        return f"Net ${self.net_after_fee:.2f} < min ${self.min_profit:.2f}"

    def summary_line(self) -> str:
        if self.error:
            return f"❌ Scan error: {self.error}"
        if self.signal == "BUY":
            return (
                f"🚀 BUY on {self.chain}: {self.base_asset} "
                f"({self.buy_on}→{self.sell_on}) "
                f"spread {self.spread_pct:.3f}% net +${self.net_after_fee:.2f}"
            )
        if not self.quote_verified and self.net_after_fee >= self.min_profit:
            return (
                f"🔒 HOLD (unverified) — {self.base_asset} "
                f"({self.buy_on}→{self.sell_on}) spread {self.spread_pct:.3f}% "
                f"would net +${self.net_after_fee:.2f}, but no live two-leg quote "
                "backs it — not sent to execution"
            )
        return (
            f"😴 HOLD — best spread {self.spread_pct:.3f}% "
            f"net ${self.net_after_fee:.2f} (need ${self.min_profit:.2f})"
        )


class Scanner:

    def __init__(
        self,
        price_client : PriceClient,
        http         : httpx.AsyncClient,
        min_profit   : float = _DEFAULT_MIN_PROFIT,
        loan_amount  : float = _DEFAULT_LOAN_USD,
        loan_fee_pct : float = _DEFAULT_LOAN_FEE_PCT,
        pairs        : list[dict] | None = None,
        w3_by_chain  : dict[str, Any] | None = None,
        max_gas_gwei : float = 0.0,
        payout_manager: "PayoutManager | None" = None,
        payout_manager_by_chain: "dict[str, PayoutManager] | None" = None,
        report_channel: "ReportChannel | None" = None,
        oracle: "OracleClient | None" = None,
        oracle_enabled: bool = True,
        # v18.16 — AI Market Pulse / near-miss alerts. Duck-typed (Any)
        # to avoid new imports: telegram needs only .safe_send(text),
        # qwen only .enabled/.market_pulse()/.near_miss_note(). Both
        # optional; omitting telegram keeps behavior identical to
        # v18.15. Notification-only — no decision path reads these.
        telegram: Any | None = None,
        qwen: Any | None = None,
    ) -> None:
        """
        New in v18.0 (all optional, backward compatible with existing
        `Scanner(price_client, http, min_profit=..., loan_amount=...,
        loan_fee_pct=...)` call sites):

          pairs        — override the default _SCAN_PAIRS list.
          w3_by_chain  — {"ETH": Web3(...), "BSC": Web3(...)} for live
                         eth_gasPrice lookups. Omit for static per-chain
                         gas defaults (_STATIC_GAS_GWEI).
          max_gas_gwei — same MAX_GAS_GWEI env var config.py already
                         documents; 0 disables the cap (per-chain static
                         default still applies as the pre-cap baseline).

        New in v18.1 (Incident Report §2.B follow-up — see scan()):

          payout_manager — optional PayoutManager instance. When wired in,
                         every scan() call feeds PayoutManager.record_scan()
                         and check_and_auto_sweep() itself, at scan()'s one
                         return point, regardless of which caller triggered
                         the scan. This exists specifically so the "wiring
                         gap" that previously required command_handlers.py's
                         /hunt handler to remember this call by hand can't
                         recur the next time a new call site (an automatic
                         scan loop, a different command, a test harness)
                         calls scan() without knowing about that convention.
                         Omit to keep Scanner purely a read-only pricing
                         engine with no PayoutManager side effects, same as
                         before this param existed.

          payout_manager_by_chain — NEW (v18.4, multi-chain incident fix).
                         Optional {"BSC": PayoutManager, "ETH": PayoutManager}
                         mapping. When set, _notify_payout picks the manager
                         matching THIS scan's own result.chain instead of
                         always using the single `payout_manager` above —
                         this is the actual fix for the bug where an ETH
                         signal's profit was being recorded into a BSC-only
                         PayoutManager's ledger, inflating BSC's sweep-
                         eligible net_profit with money that was never in
                         the BSC hot wallet. If result.chain has no entry in
                         this dict, falls back to `payout_manager` (if any)
                         and logs that the signal's chain has no dedicated
                         manager — so it's still visible/accounted for
                         somewhere rather than silently dropped, but won't
                         incorrectly trigger another chain's sweep. Omit
                         entirely to keep the old single-manager behavior.

          report_channel — optional ReportChannel instance (see
                         modules/report_channel.py). Same "wire it once at
                         the constructor, fire it once at the single return
                         point" lesson as payout_manager above — every
                         scan() call reports itself to the Telegram reports
                         channel regardless of which caller triggered it,
                         so no future call site can forget to report a scan
                         the way command_handlers.py once forgot to record
                         one. Best-effort/fire-and-forget: a reporting
                         failure is logged and swallowed, never raised, so
                         a Telegram/proxy outage can't take down a scan.
                         Omit to keep Scanner silent, same as before this
                         param existed.

          oracle — NEW. Optional OracleClient instance (see
                         modules/oracle.py). This is the fix for the
                         phantom-ledger bug: previously _notify_payout()
                         called payout_manager.record_scan(is_buy=True, ...)
                         and check_and_auto_sweep() the instant a BUY
                         signal was computed, with no dependency on any
                         on-chain execution — the ledger credited (and
                         could sweep) spread arithmetic that was never
                         actually traded. When oracle is wired in, a BUY
                         signal now calls oracle.execute_trade() FIRST;
                         record_scan(is_buy=...) only passes is_buy=True if
                         that call returns confirmed=True with a real
                         tx_hash, and gross_return is replaced with the
                         Oracle's own independently-computed
                         realized_profit_usd rather than re-trusting
                         Scanner's pre-trade spread estimate. An
                         unconfirmed/failed/timed-out execution is recorded
                         as is_buy=False (same as an ordinary HOLD) so
                         scan-count/audit visibility is unaffected but
                         nothing gets credited or swept. Omitting oracle
                         entirely preserves the OLD behavior (credit on
                         signal alone) — this is opt-in specifically so a
                         partially-deployed rollout doesn't silently change
                         behavior; deploy oracle.py and pass it in here to
                         actually close the gap.

          oracle_enabled — NEW (2026-07-07). Explicit kill-switch,
                         defaults True. When False, _notify_payout() never
                         calls oracle.execute_trade() at all — no attempt
                         to reach the Worker or mirror, so no ~7-14.5s
                         hang per BUY signal, and obviously no wallet
                         access. Unlike passing oracle=None, this does NOT
                         fall back to the pre-fix "credit ledger on spread
                         arithmetic alone" behavior — a BUY signal is
                         still logged/reported/scan-counted for
                         visibility, but is ALWAYS recorded as
                         is_buy=False (same treatment as a HOLD): no
                         ledger credit, no auto-sweep, no execution
                         attempt. This is the correct switch for "keep
                         scanning and logging opportunities, but never
                         call Oracle or attempt execution" — flip it via a
                         config/env-driven flag in bot.py to pause
                         execution attempts entirely without touching the
                         confirmation-gating logic itself.
        """
        self._oracle_enabled = oracle_enabled
        self._price       = price_client
        self._http        = http
        self.min_profit   = min_profit
        self.loan_amount  = loan_amount
        self.loan_fee_pct = loan_fee_pct
        # v18.23 — DIAGNOSTIC. The v18.22 override-mismatch warning never
        # fired (confirmed: 2026-07-11 12:08 log has no
        # "min_profit override" line), yet the near-miss card still shows
        # a $2.17 floor. That rules OUT a per-call scan(min_profit=...)
        # override as the cause — it means self.min_profit was ALREADY
        # ~$2.17 the moment this constructor ran, not $4.35 as the app's
        # own "Config loaded: ... min_profit=4.35 ..." startup banner
        # claims. This file has no way to know what that banner computed
        # (it lives in config.py/app.py, not here) — so log exactly what
        # Scanner itself actually received, at construction time, right
        # next to where that banner already prints. The next startup's
        # console log will have both lines back to back and the mismatch
        # (if any) will be visible by eye without needing bot.py at all.
        logger.info(
            "[Scanner] Constructed with min_profit=$%.2f loan_amount=$%.2f "
            "loan_fee_pct=%.3f%% — compare min_profit against this app's "
            "own \"Config loaded: ... min_profit=...\" banner line above; "
            "any mismatch here (not in scan()'s override, per v18.22) means "
            "whatever builds the Scanner(...) call passed a different "
            "value than the banner reports.",
            self.min_profit, self.loan_amount, self.loan_fee_pct,
        )
        self._pairs        = pairs if pairs is not None else _SCAN_PAIRS
        self._w3_by_chain  = w3_by_chain or {}
        self._max_gas_gwei = max_gas_gwei

        # Direct-RPC reserve-read cache/backoff (keyed by pair contract
        # address, separate from the DexScreener cache below since these
        # are two independent data sources that can each fail/succeed on
        # their own schedule).
        self._rpc_cache: dict[str, tuple[float, DexPairInfo]] = {}
        self._rpc_backoff_until: dict[str, float] = {}
        # pair_address -> w3.eth.contract instance, built lazily on first
        # use per pair so we don't construct contract objects for pairs
        # that never get scanned or never had an address filled in.
        self._pair_contracts: dict[str, Any] = {}
        # v18.8 — router_addr -> factory address, and
        # "factory:tokenA:tokenB" -> resolved pair address. Both are
        # immutable on-chain facts once read (a router's factory never
        # changes; a given token pair's pool address is deterministic),
        # so these cache forever for the life of this Scanner instance —
        # no TTL needed, unlike the reserve reads themselves.
        self._factory_cache: dict[str, str] = {}
        self._pair_addr_cache: dict[str, str] = {}
        self._payout       = payout_manager
        self._payout_by_chain = payout_manager_by_chain or {}
        self._report       = report_channel
        self._oracle       = oracle

        # v18.16 — AI Market Pulse / near-miss state. Notification only.
        self._telegram = telegram
        self._qwen     = qwen
        self._pulse_window_start: float = time.monotonic()
        self._pulse_scans: int = 0
        # Best (highest-net) real, live-priced result seen this window —
        # kept as a plain snapshot dict so the ScanResult itself isn't
        # retained past its cycle.
        self._pulse_best: dict[str, Any] | None = None
        self._near_miss_last_ts: float = 0.0

        # DexScreener short-TTL cache / failure backoff (v18.0)
        self._dex_cache: dict[str, tuple[float, list[DexPairInfo]]] = {}
        self._dex_backoff_until: dict[str, float] = {}
        # v18.27 — in-flight request coalescing, keyed by token_address.
        # See the long comment in _fetch_dex_pairs_http() for why this
        # exists: pairs are scanned concurrently (asyncio.gather in
        # scan()) and more than one configured pair can share the same
        # underlying token_address for the DexScreener fallback.
        self._dex_inflight: dict[str, asyncio.Future] = {}

        # v18.3 — strong refs for fire-and-forget payout/report tasks.
        # asyncio only holds a weak reference to a bare create_task()
        # result; without keeping this set, a task can be garbage
        # collected mid-flight (a real, documented asyncio footgun), which
        # would silently drop sweeps/reports rather than just delaying
        # scan() as intended. Cleared automatically via the done-callback
        # added at each call site.
        self._background_tasks: set[asyncio.Task] = set()

    async def scan(self, min_profit: float | None = None) -> ScanResult:
        t0 = time.monotonic()
        effective_min_profit = min_profit if min_profit is not None else self.min_profit
        # v18.22 — DIAGNOSTIC (can't fully fix from this file — see note).
        # Production near-miss alert (2026-07-11 11:59:47) showed a $2.17
        # floor where the app's own startup banner says min_profit=4.35 —
        # suspiciously close to exactly half. Traced as far as this file
        # goes: effective_min_profit here is either the caller's override
        # verbatim or self.min_profit verbatim — nothing in Scanner touches
        # or divides it in between, and result.min_profit is set from this
        # same value at construction and never reassigned. So if it really
        # is halved, the override is coming in already halved from whatever
        # calls scan(min_profit=...) — e.g. bot.py's auto-hunt loop, per
        # the v18.14 changelog note that it passes
        # self.effective_min_profit. That file isn't available here, so
        # this can't be fixed at the source yet — but every scan now logs
        # loudly whenever the caller's override differs from this
        # Scanner's own configured baseline, so the next occurrence is
        # self-diagnosing instead of only visible after the fact in a
        # Telegram near-miss card.
        if min_profit is not None and self.min_profit and abs(min_profit - self.min_profit) > 0.01:
            logger.warning(
                "[Scanner] scan() called with min_profit override=$%.2f — "
                "differs from this Scanner's own configured baseline=$%.2f "
                "(%.0f%% of baseline). Every field on this cycle's "
                "ScanResult, including the BUY floor and near-miss "
                "shortfall, uses the OVERRIDE value, not the baseline. If "
                "this wasn't an intentional caller-side adjustment (e.g. "
                "mint mode), the bug is upstream of Scanner, in whatever "
                "computed this override before calling scan().",
                min_profit, self.min_profit,
                (min_profit / self.min_profit * 100),
            )
        result = ScanResult(min_profit=effective_min_profit, loan_amount=self.loan_amount)

        try:
            assets = list(
                {p["base"] for p in self._pairs} |
                {p["stable"] for p in self._pairs} |
                {p["gas_asset"] for p in self._pairs}
            )
            prices = await self._price.get_prices(assets)
            result.price_entries = prices

            live_count = sum(1 for e in prices.values() if e.is_live)
            if live_count > 0:
                sources              = [e.source for e in prices.values() if e.is_live]
                result.price_source  = sources[0] if sources else "unknown"
                result.price_is_live = True
            else:
                result.price_source  = "static"
                result.price_is_live = False
                result.warnings.append(
                    "⚠️ All prices from static fallback — BUY signal blocked"
                )

            # v18.0 — concurrent pair scanning (was a sequential for-loop)
            pair_results = await asyncio.gather(
                *(self._scan_pair(pair_cfg, prices, effective_min_profit)
                  for pair_cfg in self._pairs),
                return_exceptions=True,
            )
            clean_results: list[ScanResult] = []
            for pair_cfg, pr in zip(self._pairs, pair_results):
                if isinstance(pr, Exception):
                    logger.warning(
                        "[Scanner] pair %s/%s on %s raised %s — skipping this cycle",
                        pair_cfg["base"], pair_cfg["stable"], pair_cfg["chain"], pr,
                    )
                    continue
                clean_results.append(pr)
                result.pairs_checked += pr.pairs_checked

            if clean_results:
                # v18.26 — prefer a real, executable candidate (verified AND
                # already clearing ITS OWN min_profit) over a bigger but
                # unverified estimate. Only when nothing this cycle actually
                # qualifies do we fall back to the old highest-net pick,
                # which keeps near-miss reporting on a quiet cycle unchanged.
                executable = [
                    r for r in clean_results
                    if r.quote_verified and r.net_after_fee >= r.min_profit
                ]
                verified_count = sum(1 for r in clean_results if r.quote_verified)
                logger.info(
                    "[Scanner] Cycle candidates: %d total, %d quote_verified, "
                    "%d executable (verified AND clears its own floor)%s",
                    len(clean_results), verified_count, len(executable),
                    " — picking from executable pool" if executable else
                    " — none executable, falling back to highest-net (may be unverified)",
                )
                best = max(executable or clean_results, key=lambda r: r.net_after_fee)
                result.signal              = best.signal
                result.has_opportunity     = best.has_opportunity
                result.chain               = best.chain
                result.base_asset          = best.base_asset
                result.stable_asset        = best.stable_asset
                result.buy_on              = best.buy_on
                result.sell_on             = best.sell_on
                result.gross_return        = best.gross_return
                result.loan_fee            = best.loan_fee
                result.gas_cost_usd        = best.gas_cost_usd
                result.net_after_fee       = best.net_after_fee
                result.spread_pct          = best.spread_pct
                result.dex_pairs           = best.dex_pairs
                result.data_quality        = best.data_quality
                result.liquidity_usd       = best.liquidity_usd
                result.slippage_haircut_pct= best.slippage_haircut_pct
                result.gas_price_gwei      = best.gas_price_gwei
                result.gas_asset           = best.gas_asset
                result.warnings.extend(best.warnings)
                # v18.21 — MUST be copied alongside signal/has_opportunity
                # above, or the aggregated-result quote_verified guard
                # below would wrongly downgrade every valid BUY (result
                # defaults to quote_verified=False until this runs).
                result.quote_verified       = best.quote_verified

            # Safety guard — never signal BUY on stale price data.
            # v18.5: applied to EVERY clean per-pair result now, not just
            # the aggregated `result` — see the record_scan loop below,
            # which fires once per pair. Before that change this guard
            # only needed to protect the single aggregated result; now
            # each pair's own result can independently reach _notify_payout
            # and must carry the same protection individually.
            if not result.price_is_live:
                for pr in clean_results:
                    if pr.signal == "BUY":
                        pr.signal          = "HOLD"
                        pr.has_opportunity = False
                        pr.warnings.append(
                            f"BUY signal downgraded to HOLD: price source is "
                            f"'{result.price_source}' (not live). "
                            f"Profitable ${pr.net_after_fee:.2f} net NOT acted on."
                        )

            # v18.0 — never signal BUY on fabricated/insufficient DEX data.
            # v18.5: same per-pair application as above.
            for pr in clean_results:
                if pr.signal == "BUY" and pr.data_quality != "real":
                    pr.signal          = "HOLD"
                    pr.has_opportunity = False
                    pr.warnings.append(
                        "BUY signal downgraded to HOLD: DEX-pair data quality "
                        f"was '{pr.data_quality}', not 'real'."
                    )

            # v18.21 — never signal BUY off the linear/DexScreener-
            # liquidity-sized fallback (see this version's changelog for
            # the production incident that prompted it). _compute_profit()
            # already enforces this at the source, but every other
            # BUY-downgrade condition in this codebase is double-guarded
            # here too (belt-and-suspenders on anything that can move
            # funds) — same per-pair + aggregated pattern as the two
            # guards immediately above.
            for pr in clean_results:
                if pr.signal == "BUY" and not pr.quote_verified:
                    pr.signal          = "HOLD"
                    pr.has_opportunity = False
                    pr.warnings.append(
                        "BUY signal downgraded to HOLD: no live two-leg quote "
                        f"backed it (linear/DexScreener-liquidity-sized estimate "
                        f"only). Unverified ${pr.net_after_fee:.2f} net NOT acted on."
                    )

            # Aggregated `result`'s own signal already went through the
            # equivalent guards below (unchanged) — kept as-is so the
            # Telegram card / dashboard "best of cycle" view behaves
            # exactly as before.
            if result.signal == "BUY" and not result.price_is_live:
                result.signal          = "HOLD"
                result.has_opportunity = False
                result.warnings.append(
                    f"BUY signal downgraded to HOLD: price source is "
                    f"'{result.price_source}' (not live). "
                    f"Profitable ${result.net_after_fee:.2f} net NOT acted on."
                )
                logger.warning(
                    "[Scanner] BUY → HOLD: profitable but price is stale (%s)",
                    result.price_source,
                )

            # v18.0 — never signal BUY on fabricated/insufficient DEX data
            if result.signal == "BUY" and result.data_quality != "real":
                result.signal          = "HOLD"
                result.has_opportunity = False
                result.warnings.append(
                    "BUY signal downgraded to HOLD: DEX-pair data quality "
                    f"was '{result.data_quality}', not 'real'."
                )

            # v18.21 — aggregated-result counterpart to the per-pair guard
            # above. See this version's changelog.
            if result.signal == "BUY" and not result.quote_verified:
                result.signal          = "HOLD"
                result.has_opportunity = False
                result.warnings.append(
                    "BUY signal downgraded to HOLD: no live two-leg quote "
                    f"backed it (linear/DexScreener-liquidity-sized estimate "
                    f"only). Unverified ${result.net_after_fee:.2f} net NOT acted on."
                )
                logger.warning(
                    "[Scanner] BUY → HOLD: %s/%s (%s→%s) net $%.2f unverified — "
                    "no live two-leg quote (see v18.21 changelog)",
                    result.base_asset, result.stable_asset,
                    result.buy_on, result.sell_on, result.net_after_fee,
                )

        except Exception as exc:
            logger.exception("Scanner error: %s", exc)
            result.error  = str(exc)
            result.signal = "ERROR"
            if self._report is not None:
                try:
                    await self._report.report_error("Scanner.scan", exc)
                except Exception as report_exc:
                    logger.error(
                        "[Scanner] ReportChannel.report_error failed: %s",
                        report_exc, exc_info=True,
                    )

        result.duration_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "[Scanner] %s in %.0fms — %s",
            "Scan complete", result.duration_ms, result.summary_line(),
        )

        # v18.3 FIX — payout/report notification no longer blocks scan()'s
        # return. Production logs showed scan() taking 12,878ms on a BUY
        # cycle vs. ~350ms on ordinary cycles — the pricing/DEX-fetch work
        # (asyncio.gather over pairs) was never the slow part. The slow
        # part was these two calls being *awaited in-line* right here:
        # check_and_auto_sweep() can walk into a full on-chain sweep
        # pipeline (balance check, gas fetch, simulate, broadcast,
        # wait_receipt polling up to 120s), and report_scan() posts through
        # a Telegram proxy chain that logs show routinely retrying 2
        # mirrors x 2 attempts x several seconds each on failure. Both were
        # already written defensively (try/except, never raise) — they
        # were safe to await, just not free. Awaiting them here meant the
        # NEXT scan cycle couldn't start until a slow/failing sweep or
        # report finished, which is exactly the "missing profitable
        # spreads due to delay" symptom reported. Firing them as
        # background tasks decouples scan cadence from sweep/report
        # latency entirely.
        #
        # Trade-off, stated plainly: result.sweep_result / .payout_notified
        # will NOT be populated on the ScanResult a caller gets back from
        # THIS scan() call anymore, since the notify coroutine hasn't run
        # yet at return time. Callers that need the outcome (e.g. /hunt
        # wanting to report "swept $X") should inspect PayoutManager's own
        # ledger/history afterward rather than result.sweep_result.
        if self._payout is not None or self._payout_by_chain:
            # v18.5 — one record_scan() per pair result, not one for only
            # the cycle's aggregated "best" result. Previously _notify_payout
            # was fired once for `result` (whichever single pair had the
            # highest net_after_fee that cycle), so every other pair's
            # chain never got its scan_count incremented even though
            # _scan_pair() actually ran for it — a chain could be checked
            # every cycle and still show scan_count=0 in its ledger purely
            # because it never happened to be the winner. Firing once per
            # clean_results entry means each chain's ledger reflects how
            # often it was actually scanned; is_buy is still only True on
            # whichever pair(s) individually cleared min_profit as BUY —
            # note more than one pair could independently signal BUY in
            # the same cycle, and each is recorded/swept independently via
            # its own chain's PayoutManager (see _resolve_payout_manager).
            for pair_result in clean_results:
                task = asyncio.create_task(
                    self._notify_payout(pair_result),
                    name=f"payout_notify_{id(pair_result)}",
                )
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

        if self._report is not None:
            task = asyncio.create_task(
                self._notify_report(result),
                name=f"report_notify_{id(result)}",
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

        # ── v18.16 — AI Market Pulse / near-miss (notification only) ─────
        # Wrapped whole: a stats/alert bug must never break a scan.
        if self._telegram is not None:
            try:
                self._pulse_scans += 1
                is_real = (
                    result.error is None
                    and result.data_quality == "real"
                    and result.price_is_live
                )
                if is_real and (
                    self._pulse_best is None
                    or result.net_after_fee > self._pulse_best["net"]
                ):
                    self._pulse_best = {
                        "net"        : result.net_after_fee,
                        "spread_pct" : result.spread_pct,
                        "pair"       : f"{result.base_asset}/{result.stable_asset}",
                        "buy_on"     : result.buy_on,
                        "sell_on"    : result.sell_on,
                        "floor"      : result.min_profit,
                        "signal"     : result.signal,
                        # v18.24 — needed so _notify_pulse can tell a
                        # genuine under-floor HOLD apart from an
                        # unverified-quote HOLD that already cleared the
                        # floor on the estimate (see v18.24 changelog).
                        "quote_verified": result.quote_verified,
                    }

                now = time.monotonic()
                if (
                    is_real
                    and result.signal == "HOLD"
                    and result.net_after_fee > _NEAR_MISS_NET_USD
                    and (now - self._near_miss_last_ts) >= _NEAR_MISS_COOLDOWN_SECS
                ):
                    self._near_miss_last_ts = now
                    task = asyncio.create_task(
                        self._notify_near_miss(result),
                        name=f"near_miss_{id(result)}",
                    )
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)

                if (
                    (now - self._pulse_window_start) >= _PULSE_INTERVAL_SECS
                    and self._pulse_scans > 0
                ):
                    snapshot = (self._pulse_scans, self._pulse_best,
                                now - self._pulse_window_start)
                    self._pulse_window_start = now
                    self._pulse_scans = 0
                    self._pulse_best = None
                    task = asyncio.create_task(
                        self._notify_pulse(*snapshot),
                        name="market_pulse",
                    )
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
            except Exception as exc:
                logger.warning("[Scanner] pulse bookkeeping failed: %s", exc)

        return result

    async def aclose(self, timeout: float = 15.0) -> None:
        """
        v18.3 — wait briefly for any in-flight fire-and-forget payout/report
        tasks (from the last scan cycle before shutdown) to finish, rather
        than yanking them mid-sweep when the process exits. Best-effort:
        logs and moves on if they don't finish in time, since bot.py's own
        shutdown sequence already has an overall deadline to respect.
        """
        pending = list(self._background_tasks)
        if not pending:
            return
        logger.info(
            "[Scanner] Waiting on %d in-flight background task(s) before shutdown…",
            len(pending),
        )
        done, still_pending = await asyncio.wait(pending, timeout=timeout)
        if still_pending:
            logger.warning(
                "[Scanner] %d background task(s) still running after %.0fs — "
                "shutting down anyway (they may be mid-sweep).",
                len(still_pending), timeout,
            )

    async def _notify_report(self, result: "ScanResult") -> None:
        """Best-effort — a ReportChannel failure must never surface as a
        scan failure to the caller, same defensive posture as
        _notify_payout below."""
        try:
            await self._report.report_scan(result)
        except Exception as exc:
            logger.error(
                "[Scanner] ReportChannel.report_scan failed: %s", exc, exc_info=True,
            )

    # ── v18.16 — AI Market Pulse / near-miss (notification only) ─────────────

    async def _notify_near_miss(self, result: "ScanResult") -> None:
        """Immediate Telegram alert when a cycle's best lands at/near
        break-even — the honest 'watch this' moment. Best-effort, never
        raises. Reports BOTH distance-to-break-even and the shortfall
        against the auto-trade floor, since only quoting the first would
        overstate how close a trade actually was."""
        try:
            # v18.24 — an unverified-quote HOLD (v18.21's quote_verified
            # gate) that already clears min_profit on the estimate is NOT
            # a "short of floor" case; shortfall = min_profit - net would
            # be negative here and read as nonsense ("$-3.30 short of the
            # $2.17 floor" on a trade that was actually $3.31 clear of
            # it). Same condition `_reason()` already uses.
            if not result.quote_verified and result.net_after_fee >= result.min_profit:
                text = (
                    f"👀 UNVERIFIED — {result.base_asset}/{result.stable_asset} "
                    f"({result.buy_on}→{result.sell_on})\n"
                    f"Spread {result.spread_pct:.3f}% | net "
                    f"${result.net_after_fee:,.2f} after all fees/gas — "
                    f"clears the ${result.min_profit:,.2f} floor on the estimate, "
                    f"but no live two-leg quote confirmed it — held correctly. Watching."
                )
            else:
                shortfall = result.min_profit - result.net_after_fee
                text = (
                    f"👀 NEAR MISS — {result.base_asset}/{result.stable_asset} "
                    f"({result.buy_on}→{result.sell_on})\n"
                    f"Spread {result.spread_pct:.3f}% | net "
                    f"${result.net_after_fee:,.2f} after all fees/gas\n"
                    f"${shortfall:,.2f} short of the ${result.min_profit:,.2f} "
                    f"auto-trade floor — held correctly. Watching."
                )
            if self._qwen is not None and getattr(self._qwen, "enabled", False):
                try:
                    note = await self._qwen.near_miss_note(
                        pair=f"{result.base_asset}/{result.stable_asset}",
                        buy_dex=result.buy_on, sell_dex=result.sell_on,
                        spread_pct=result.spread_pct,
                        net_usd=result.net_after_fee,
                        floor_usd=result.min_profit,
                        # v18.25 — same condition used above to pick the
                        # Telegram card's own wording; see qwen_client.py
                        # v1.4 changelog for why this needs to be passed.
                        quote_verified=result.quote_verified,
                    )
                    if note:
                        text += f"\n\n🤖 {note}"
                except Exception as exc:
                    logger.warning("[Scanner] Qwen near-miss note failed: %s", exc)
            await self._telegram.safe_send(text)
        except Exception as exc:
            logger.warning("[Scanner] near-miss alert failed: %s", exc)

    async def _notify_pulse(
        self, scans: int, best: dict[str, Any] | None, window_secs: float,
    ) -> None:
        """Hourly Telegram digest of the hunt. The stats text is
        deterministic and always sends; Qwen's market read is appended
        best-effort when configured. Never raises."""
        try:
            hours = max(window_secs / 3600.0, 0.01)
            if best is None:
                text = (
                    f"📡 MARKET PULSE — last {hours:.1f}h\n"
                    f"{scans} scan(s) completed, but no cycle produced a "
                    f"real, live-priced result to rank (price sources "
                    f"degraded or insufficient DEX data). Hunt continues."
                )
            else:
                # v18.24 — same fix as _notify_near_miss: an unverified
                # quote that already cleared the floor on the estimate
                # isn't "short of floor" (that math goes negative and
                # reads as nonsense). Fall back to .get() with a default
                # of True so older _pulse_best snapshots without the new
                # key (unlikely to persist across a restart, but cheap
                # to guard) don't misfire this branch.
                unverified_clear = (
                    not best.get("quote_verified", True)
                    and best["net"] >= best["floor"]
                )
                if best["signal"] == "BUY":
                    tail = "Cleared the floor and traded this window. 🚀"
                elif unverified_clear:
                    tail = (
                        f"Cleared the ${best['floor']:,.2f} floor on the "
                        f"estimate, but no live two-leg quote confirmed it "
                        f"— no trade this window."
                    )
                else:
                    shortfall = best["floor"] - best["net"]
                    tail = (
                        f"${shortfall:,.2f} short of the "
                        f"${best['floor']:,.2f} floor — no trade, correctly."
                    )
                text = (
                    f"📡 MARKET PULSE — last {hours:.1f}h\n"
                    f"Scans: {scans}\n"
                    f"Best seen: {best['pair']} "
                    f"({best['buy_on']}→{best['sell_on']}) — spread "
                    f"{best['spread_pct']:.3f}%, net ${best['net']:,.2f}\n"
                    + tail
                )
                if self._qwen is not None and getattr(self._qwen, "enabled", False):
                    try:
                        read = await self._qwen.market_pulse(
                            hours=hours, scans=scans,
                            best_spread_pct=best["spread_pct"],
                            best_pair=best["pair"],
                            best_buy_dex=best["buy_on"],
                            best_sell_dex=best["sell_on"],
                            best_net_usd=best["net"],
                            floor_usd=best["floor"],
                            # v18.25 — see near_miss_note() call above /
                            # qwen_client.py v1.4 changelog.
                            quote_verified=best.get("quote_verified", True),
                        )
                        if read:
                            text += f"\n\n🤖 {read}"
                    except Exception as exc:
                        logger.warning("[Scanner] Qwen pulse read failed: %s", exc)
            await self._telegram.safe_send(text)
        except Exception as exc:
            logger.warning("[Scanner] market pulse failed: %s", exc)

    def _resolve_payout_manager(self, result: "ScanResult") -> "PayoutManager | None":
        """
        Pick the PayoutManager that actually owns result.chain.

        This is the fix for the incident where an ETH-chain buy signal was
        recorded into a BSC-only PayoutManager's ledger: net_profit crossed
        the sweep threshold using money that was never in the BSC hot
        wallet, because nothing here checked which chain the signal
        actually happened on before calling record_scan().

        Resolution order:
          1. payout_manager_by_chain[result.chain] if that mapping exists
             and has an entry for this chain — the correct, chain-scoped
             manager.
          2. The single `payout_manager` fallback, ONLY if no by-chain
             mapping was provided at all (pre-v18.4 single-manager mode).
          3. None, if a by-chain mapping exists but doesn't cover this
             chain — logged clearly rather than silently mis-routed into
             the wrong manager.
        """
        chain = (result.chain or "").upper()

        if self._payout_by_chain:
            manager = self._payout_by_chain.get(chain)
            if manager is not None:
                return manager
            logger.warning(
                "[Scanner] Signal on chain=%s has no matching PayoutManager "
                "in payout_manager_by_chain (%s configured) — skipping "
                "record_scan/auto-sweep for this signal rather than "
                "crediting it to a different chain's ledger.",
                chain or "unknown", list(self._payout_by_chain.keys()),
            )
            return None

        # No by-chain mapping at all — old single-manager behavior.
        return self._payout

    async def _notify_payout(self, result: "ScanResult") -> None:
        """Best-effort — a PayoutManager failure must never surface as a
        scan failure to the caller; same defensive posture already used
        everywhere else this ledger is touched.

        NEW (phantom-ledger fix) — a BUY signal is no longer credited to
        the ledger on spread arithmetic alone. If self._oracle is wired
        in, a BUY signal must first clear oracle.execute_trade() with
        confirmed=True and a real tx_hash; only then is record_scan()
        called with is_buy=True, and using the Oracle's own independently
        -computed realized_profit_usd in place of this scan's pre-trade
        gross_return estimate. An unconfirmed/failed/timed-out execution
        (or any non-BUY signal) is recorded as is_buy=False — same ledger
        visibility as an ordinary HOLD, no credit, no sweep trigger.

        Omitting oracle entirely (self._oracle is None) preserves the
        OLD behavior unchanged — credit fires on signal alone. This is
        intentional: a partially-deployed rollout (oracle.py written but
        not yet passed into Scanner's constructor) must not silently
        change what gets credited.
        """
        manager = self._resolve_payout_manager(result)
        if manager is None:
            return

        is_buy         = result.signal == "BUY"
        credited_gross = result.gross_return
        tx_hash        = None

        if is_buy and self._oracle is not None and self._oracle_enabled:
            execution = await self._oracle.execute_trade(
                base_asset     = result.base_asset,
                stable_asset   = result.stable_asset,
                target_dex     = result.buy_on,
                amount         = result.loan_amount,
                net_profit_usd = result.gross_return - result.loan_fee - result.gas_cost_usd,
                # NEW (local_executor.py wiring) — the exit-leg DEX, chain,
                # and the actual configured profit floor were never passed
                # before, even though ScanResult already carries them
                # (sell_on/chain/min_profit). OracleClient's Worker-based
                # path never needed them (the Worker computed its own
                # route), but LocalExecutor calls startArbitrage() directly
                # and needs the real exit router to build the second swap
                # leg. Additive kwargs — OracleClient.execute_trade()
                # accepts and ignores them, so this is safe regardless of
                # which executor is wired into self._oracle.
                sell_dex        = result.sell_on,
                chain           = result.chain,
                min_profit_usd  = result.min_profit,
            )
            if execution.confirmed:
                tx_hash = execution.tx_hash
                # Use the Oracle's own independently-computed realized
                # profit for the ledger credit, not Scanner's pre-trade
                # spread estimate — re-crediting the same number Scanner
                # already computed once would just move the
                # rubber-stamping problem one hop over rather than fix it.
                if execution.realized_profit_usd is not None:
                    credited_gross = (
                        execution.realized_profit_usd
                        + result.loan_fee
                        + result.gas_cost_usd
                    )
                logger.info(
                    "[Scanner] BUY confirmed on-chain — tx=%s realized=$%s "
                    "(chain=%s %s/%s)",
                    tx_hash,
                    f"{execution.realized_profit_usd:.2f}"
                    if execution.realized_profit_usd is not None else "unknown",
                    result.chain, result.base_asset, result.stable_asset,
                )
            else:
                # Execution failed/timed out/unconfirmed — do NOT credit.
                # Recorded as is_buy=False below, same as a HOLD: the scan
                # still counts for audit/scan_count visibility, but no
                # profit is credited and no sweep can trigger off it.
                is_buy = False
                result.warnings.append(
                    f"BUY signal computed but execution unconfirmed "
                    f"({execution.error}) — not credited to ledger."
                )
        elif is_buy and self._oracle is not None and not self._oracle_enabled:
            # NEW (2026-07-07) — oracle IS wired in, but explicitly paused
            # via oracle_enabled=False. Never calls execute_trade() at
            # all: no attempt to reach the Worker/mirror, no ~7-14.5s hang
            # per signal, no wallet access. Deliberately does NOT fall
            # into the "no oracle wired in" branch below — that branch
            # preserves the OLD pre-fix behavior (credit on signal alone),
            # which is exactly what we must NOT do here. Recorded as
            # is_buy=False, same as an unconfirmed execution above: scan
            # visibility is preserved, nothing is credited, nothing can
            # trigger a sweep.
            is_buy = False
            result.warnings.append(
                "BUY signal computed but Oracle execution is currently "
                "paused (oracle_enabled=False) — not attempted, not "
                "credited to ledger."
            )
            logger.info(
                "[Scanner] BUY signal on chain=%s %s/%s logged only — "
                "oracle_enabled=False, execution not attempted.",
                result.chain, result.base_asset, result.stable_asset,
            )
        elif is_buy and self._oracle is None:
            # No oracle wired in at all — old behavior, unchanged.
            # Logged clearly so it's visible in production logs that
            # this scan's credit was NOT execution-gated.
            logger.warning(
                "[Scanner] BUY signal credited WITHOUT execution "
                "confirmation — no OracleClient wired in (chain=%s %s/%s). "
                "Pass oracle= to Scanner's constructor to gate this on a "
                "real confirmed tx_hash.",
                result.chain, result.base_asset, result.stable_asset,
            )

        try:
            await manager.record_scan(
                gross_return = credited_gross,
                loan_fee     = result.loan_fee,
                gas_cost     = result.gas_cost_usd,
                is_buy       = is_buy,
                chain        = result.chain,
            )
        except Exception as exc:
            logger.error(
                "[Scanner] PayoutManager.record_scan failed: %s", exc, exc_info=True,
            )
            return

        result.payout_notified = True
        if tx_hash:
            result.warnings.append(f"Executed — tx_hash={tx_hash}")

        if not is_buy:
            # Nothing was credited this cycle — never worth calling
            # check_and_auto_sweep() off a HOLD or an unconfirmed BUY.
            # (record_scan() above still updates scan_count/history either
            # way, so audit visibility is unaffected.)
            return

        try:
            sweep_result = await manager.check_and_auto_sweep(
                note=f"Auto-sweep after scan ({result.chain or 'multi-pair'})",
            )
        except Exception as exc:
            logger.error(
                "[Scanner] PayoutManager.check_and_auto_sweep failed: %s",
                exc, exc_info=True,
            )
            return

        if sweep_result is not None:
            result.sweep_result = sweep_result

    async def _scan_pair(
        self, cfg: dict, prices: dict[str, PriceEntry], min_profit: float,
    ) -> ScanResult:
        result = ScanResult(
            chain        = cfg["chain"],
            base_asset   = cfg["base"],
            stable_asset = cfg["stable"],
            loan_amount  = self.loan_amount,
            min_profit   = min_profit,
            gas_asset    = cfg["gas_asset"],
        )

        base_entry = prices.get(cfg["base"])
        if not base_entry or not base_entry.price:
            result.error = f"No price for {cfg['base']}"
            return result

        gas_price_gwei = await self._get_gas_price_gwei(cfg["chain"])
        result.gas_price_gwei = gas_price_gwei
        gas_asset_entry = prices.get(cfg["gas_asset"])

        if gas_asset_entry and gas_asset_entry.price:
            gas_native_price = gas_asset_entry.price
        elif cfg["gas_asset"] == cfg["base"]:
            # Only case where substituting base_entry.price is actually
            # correct: the pair's gas asset IS the base asset (e.g. a
            # BNB/stable pair on BSC), so this isn't a substitution at
            # all — it's the same price either way.
            gas_native_price = base_entry.price
        else:
            # FIX — previously fell back to base_entry.price even when
            # gas_asset and base_asset are different tokens (e.g. pricing
            # BNB gas using a memecoin's price). That's the same class of
            # bug the v18.0 changelog already fixed once for the general
            # case (WBTC pair pricing ETH gas at BTC's price) — it had
            # just re-appeared here as a "last resort" fallback. No live
            # gas-asset price means the gas-cost estimate can't be
            # trusted, so this is now treated the same as insufficient
            # DEX-pair data: explicit HOLD, no signal computed on a
            # number that isn't real.
            result.data_quality = "insufficient"
            result.warnings.append(
                f"No live price for gas asset {cfg['gas_asset']} and it "
                f"differs from base asset {cfg['base']} — HOLD (gas-cost "
                "estimate would be priced against the wrong token)."
            )
            return result

        # NEW — attach the live base-asset USD price (already resolved
        # above from price_client) so _fetch_dex_pairs' on-chain path can
        # convert getReserves()'s raw reserve ratio into a USD price
        # without re-fetching or duplicating price_client's own logic.
        # Shallow-copied so we never mutate the shared _SCAN_PAIRS entry
        # across concurrent asyncio.gather'd scans of other pairs.
        cfg_with_price = {**cfg, "_live_base_price_hint": base_entry.price}

        # v18.8 — for BSC, try LIVE on-chain reserves first (see
        # _fetch_live_bsc_reserves above). This entirely replaces
        # DexScreener's advertised price/liquidity for the executable
        # DEXs when it succeeds — v18.5 through v18.7 found DexScreener's
        # liquidity figure unreliable at ANY flat percentage for these
        # pairs (ranging from "completely dead pool" to "~13% overstated"
        # across different DEXs), so ground-truth reserves are strictly
        # better whenever available. used_live_reserves is threaded
        # through to the liquidity-cap sizing below, which trusts a live
        # reading much more than a DexScreener-derived one.
        used_live_reserves = False
        dex_pairs: list[DexPairInfo] = []
        if cfg["chain"].upper() == "BSC":
            w3 = self._w3_by_chain.get("BSC")
            if w3 is not None:
                dex_pairs = await self._fetch_live_bsc_reserves(cfg, w3)
                used_live_reserves = bool(dex_pairs)

        # v18.20 FIX — was `if not dex_pairs:`, which only re-armed this
        # fallback when live returned literally ZERO pools. v18.19's
        # freshness filter routinely leaves exactly ONE fresh live pool
        # (PancakeSwap almost always fresh; Biswap/MDEX often correctly
        # dropped as stale on these pairs) — a non-empty list that still
        # skipped the fallback outright and fell straight into the
        # `len(dex_pairs) < 2` HOLD-out below every cycle. Now re-arms
        # whenever live gave back fewer than 2 usable pools, and MERGES
        # by dex_id (never discarding a live pool to make room for a
        # DexScreener one — live is strictly higher-fidelity per v18.8).
        # _fetch_dex_pairs_http()'s own recency gate (v18.20) keeps this
        # from reopening the v18.19 phantom-spread bug via a stale
        # DexScreener listing.
        dexscreener_filled_gap = False
        if len(dex_pairs) < 2:
            fallback_pairs = await self._fetch_dex_pairs(cfg["address"], cfg["dex_chain_id"], cfg_with_price)
            already_have = {p.dex_id for p in dex_pairs}
            added = [p for p in fallback_pairs if p.dex_id not in already_have]
            dex_pairs = dex_pairs + added
            dexscreener_filled_gap = bool(added)

            # v18.4 FIX — BSC BUY signals were computing real spreads across
            # every DEX DexScreener indexes (pancakeswap, biswap, apeswap, and
            # plenty of smaller/unlabeled ones DexScreener sometimes reports
            # by raw contract address instead of a name), but local_executor.py
            # can only actually execute through the router addresses in its
            # ROUTER_MAP whitelist — 3 verified BSC routers, deliberately never
            # guessed or auto-expanded (a wrong router address in a live,
            # fund-moving contract call is unrecoverable). Confirmed in
            # production: a real $493.53 WBTC BUY signal was found and then
            # failed with "Unknown DEX '0x571521f8...' — not in ROUTER_MAP",
            # because the winning sell-side quote came from a DEX outside that
            # whitelist. Rather than keep finding opportunities it can't take,
            # BSC pairs now only consider DexScreener listings from DEXs this
            # deployment can actually execute through — every BUY signal from
            # here on is guaranteed executable. This does not change ETH pairs
            # (LocalExecutor is BSC-only regardless) or the DEX names
            # themselves — only which candidate listings feed the buy/sell
            # spread comparison below. Keep in sync with
            # modules/contract_manager.py's ROUTER_MAP.
            if cfg["chain"].upper() == "BSC":
                executable_pairs = [p for p in dex_pairs if p.dex_id in _BSC_EXECUTABLE_DEXES]
                if executable_pairs:
                    dex_pairs = executable_pairs
                elif dex_pairs:
                    result.warnings.append(
                        f"{len(dex_pairs)} DEX pair(s) found but none are on a "
                        f"router this deployment can execute through "
                        f"({', '.join(sorted(_BSC_EXECUTABLE_DEXES))}) — treating "
                        "as insufficient data rather than signaling on an "
                        "unexecutable spread."
                    )
                    dex_pairs = []

        # v18.28 — on-demand quote verification (see _verify_pairs_onchain).
        # Every DexScreener-filled BSC row either gets upgraded to live raw
        # reserves (making the exact two-leg quote — and therefore a real
        # BUY — reachable through it), dropped as a stale-pool phantom, or
        # left unverified for v18.21's guard to hold back, same as before.
        # This is the fix for the standing "UNVERIFIED — clears the floor
        # on the estimate, but no live two-leg quote confirmed it —
        # Watching." loop: the scanner now goes and GETS the live quote
        # for the exact pool DexScreener reported instead of shrugging.
        if dex_pairs and cfg["chain"].upper() == "BSC":
            w3_verify = self._w3_by_chain.get("BSC")
            if w3_verify is not None:
                dex_pairs = await self._verify_pairs_onchain(cfg, w3_verify, dex_pairs)

        result.dex_pairs      = dex_pairs
        result.pairs_checked  = len(dex_pairs)

        # v18.0 — FIX: no more fabricated spread. Insufficient real DEX-pair
        # data means HOLD, full stop — never feed a made-up number into the
        # profit calc where it could clear min_profit and signal BUY.
        if len(dex_pairs) < 2:
            result.data_quality = "insufficient"
            if used_live_reserves and dexscreener_filled_gap:
                source = "live on-chain + DexScreener"
            elif used_live_reserves:
                source = "live on-chain reads"
            else:
                source = "DexScreener"
            result.warnings.append(
                f"Only {len(dex_pairs)} usable DEX pair(s) from {source} "
                "this cycle — HOLD (no signal computed on fabricated data)."
            )
            return result

        # v18.12 — fee-aware venue selection. Instead of assuming the widest
        # GROSS price spread is the best trade, evaluate every ordered
        # (buy_venue, sell_venue) combination across the live pools and pick
        # the one whose EXACT two-leg constant-product round trip — each leg
        # charged that venue's OWN fee (Biswap 10bps is 3x cheaper than
        # MDEX 30bps) — nets the most after the per-combo liquidity cap.
        # A narrower spread routed through two cheap-fee venues can net more
        # than a wider one through an expensive leg; the old widest-gross
        # picker silently discarded those. Only runs when >=2 pools carry
        # live raw reserves; the DexScreener-only fallback keeps the original
        # widest-gross + linear-model behavior unchanged.
        live_pairs = [
            p for p in dex_pairs
            if p.base_reserve_raw and p.stable_reserve_raw
        ]
        # v18.20 — keyed off actual post-merge raw-reserve coverage
        # (len(live_pairs) >= 2, the same condition the exact-math branch
        # below checks), not the pre-merge used_live_reserves flag. Before
        # this fix that flag could be True (live returned 1 pool) while
        # the trade actually executes against a DexScreener-derived
        # liquidity figure for the other leg — v18.5-v18.7 found that
        # figure unreliable (dead pool to ~13% overstated), so it must
        # never silently get the more permissive live-reserve loan cap.
        max_loan_pct = _MAX_LOAN_LIQUIDITY_PCT_LIVE if len(live_pairs) >= 2 else _MAX_LOAN_LIQUIDITY_PCT

        buy_pair = None
        sell_pair = None
        spread_pct = 0.0
        effective_loan = self.loan_amount
        exact_gross_return = None

        if len(live_pairs) >= 2:
            base_decimals = cfg.get("base_decimals", 18)
            stable_decimals = cfg.get("stable_decimals", 18)
            best_gross = None  # best exact round-trip gross return found so far

            for cand_buy in live_pairs:
                for cand_sell in live_pairs:
                    if cand_buy is cand_sell:
                        continue
                    combo_liq = min(cand_buy.liquidity, cand_sell.liquidity)
                    combo_loan = self.loan_amount
                    if combo_liq > 0 and (combo_loan / combo_liq) > max_loan_pct:
                        combo_loan = combo_liq * max_loan_pct
                    amount_in_raw = int(combo_loan * (10 ** stable_decimals))
                    buy_fee_bps = _DEX_FEE_BPS.get(cand_buy.dex_id, 25)
                    sell_fee_bps = _DEX_FEE_BPS.get(cand_sell.dex_id, 25)
                    base_out_raw = _v2_amount_out(
                        amount_in_raw,
                        cand_buy.stable_reserve_raw, cand_buy.base_reserve_raw,
                        buy_fee_bps,
                    )
                    stable_out_raw = _v2_amount_out(
                        base_out_raw,
                        cand_sell.base_reserve_raw, cand_sell.stable_reserve_raw,
                        sell_fee_bps,
                    )
                    if base_out_raw <= 0 or stable_out_raw <= 0:
                        continue
                    cand_gross = stable_out_raw / (10 ** stable_decimals) - combo_loan
                    if best_gross is None or cand_gross > best_gross:
                        best_gross = cand_gross
                        buy_pair = cand_buy
                        sell_pair = cand_sell
                        effective_loan = combo_loan
                        exact_gross_return = cand_gross

            if buy_pair is None:
                result.data_quality = "insufficient"
                result.warnings.append(
                    "No venue combination produced a usable two-leg quote "
                    "this cycle — HOLD."
                )
                return result

            spread_pct = (
                (sell_pair.price_usd - buy_pair.price_usd) / buy_pair.price_usd
                if buy_pair.price_usd else 0.0
            )
            if abs(spread_pct) > _MAX_SANE_SPREAD_PCT:
                result.data_quality = "insufficient"
                result.warnings.append(
                    f"Discarded implausible {spread_pct * 100:.1f}% spread as "
                    "bad data — HOLD (no signal computed on fabricated data)."
                )
                return result

            result.data_quality = "real"
            result.liquidity_usd = min(buy_pair.liquidity, sell_pair.liquidity)
            naive_gross = effective_loan * spread_pct
            result.slippage_haircut_pct = (
                min(max(0.0, 1 - (exact_gross_return / naive_gross)), 1.0)
                if naive_gross > 0 else 0.0
            )
        else:
            # DexScreener-only fallback — original widest-gross picker +
            # linear model, unchanged from v18.11.
            sorted_pairs = sorted(dex_pairs, key=lambda p: p.price_usd)
            buy_pair, sell_pair = sorted_pairs[0], sorted_pairs[-1]
            spread_pct = (sell_pair.price_usd - buy_pair.price_usd) / buy_pair.price_usd

            if abs(spread_pct) > _MAX_SANE_SPREAD_PCT:
                result.data_quality = "insufficient"
                result.warnings.append(
                    f"Discarded implausible {spread_pct * 100:.1f}% spread as "
                    "bad data — HOLD (no signal computed on fabricated data)."
                )
                return result

            result.data_quality = "real"
            min_liquidity = min(buy_pair.liquidity, sell_pair.liquidity)
            result.liquidity_usd = min_liquidity
            if min_liquidity > 0:
                loan_fraction = effective_loan / min_liquidity
                if loan_fraction > max_loan_pct:
                    capped = min_liquidity * max_loan_pct
                    result.warnings.append(
                        f"Loan ${effective_loan:,.0f} is {loan_fraction * 100:.0f}% "
                        f"of shallow-side liquidity ${min_liquidity:,.0f} (DexScreener) "
                        f"— capped to ${capped:,.0f} for this quote's profit math."
                    )
                    effective_loan = capped
                result.slippage_haircut_pct = min(
                    _SLIPPAGE_IMPACT_COEFF * (effective_loan / min_liquidity), 0.5
                )
            else:
                result.slippage_haircut_pct = 0.0

        # v18.11 — exact constant-product round trip whenever BOTH legs
        # carry live raw reserves (only the on-chain path populates these;
        # DexScreener-sourced pairs leave them at 0 and fall through to
        # the linear model in _compute_profit unchanged, same as before
        # this version — this is additive, not a replacement for that
        # fallback). Runs the identical two-leg getAmountsOut() math
        # LocalExecutor will run at execution time, against the SAME
        # reserves already fetched above for the price/liquidity numbers
        # — no new RPC calls.
        exact_gross_return = None
        if (buy_pair.base_reserve_raw and buy_pair.stable_reserve_raw and
                sell_pair.base_reserve_raw and sell_pair.stable_reserve_raw):
            base_decimals   = cfg.get("base_decimals", 18)
            stable_decimals = cfg.get("stable_decimals", 18)
            amount_in_raw   = int(effective_loan * (10 ** stable_decimals))

            buy_fee_bps  = _DEX_FEE_BPS.get(buy_pair.dex_id, 25)
            sell_fee_bps = _DEX_FEE_BPS.get(sell_pair.dex_id, 25)

            # Leg 1 (buy): stable → base, against buy_pair's own reserves.
            base_out_raw = _v2_amount_out(
                amount_in_raw,
                buy_pair.stable_reserve_raw, buy_pair.base_reserve_raw,
                buy_fee_bps,
            )
            # Leg 2 (sell): base → stable, against sell_pair's own
            # reserves — leg 1's output feeds leg 2's input, exactly like
            # LocalExecutor's sequential getAmountsOut() calls.
            stable_out_raw = _v2_amount_out(
                base_out_raw,
                sell_pair.base_reserve_raw, sell_pair.stable_reserve_raw,
                sell_fee_bps,
            )
            if base_out_raw > 0 and stable_out_raw > 0:
                final_stable_usd   = stable_out_raw / (10 ** stable_decimals)
                exact_gross_return = final_stable_usd - effective_loan
                # Retroactive haircut purely so existing warnings/
                # Telegram-card fields that already read
                # slippage_haircut_pct stay populated — the exact path
                # no longer uses this value to derive gross_return.
                naive_gross = effective_loan * spread_pct
                if naive_gross > 0:
                    result.slippage_haircut_pct = min(max(
                        0.0, 1 - (exact_gross_return / naive_gross)
                    ), 1.0)

        return self._compute_profit(
            result, spread_pct,
            buy_pair.dex_id, sell_pair.dex_id,
            effective_loan, result.slippage_haircut_pct,
            gas_native_price, gas_price_gwei,
            exact_gross_return=exact_gross_return,
        )

    def _compute_profit(
        self,
        result               : ScanResult,
        spread_pct           : float,
        buy_on                : str,
        sell_on                : str,
        loan_amount            : float,
        slippage_haircut_pct   : float,
        gas_native_price        : float,
        gas_price_gwei          : float,
        exact_gross_return      : float | None = None,
    ) -> ScanResult:
        if exact_gross_return is not None:
            # v18.11 — caller already ran the exact two-leg
            # constant-product round trip (see _scan_pair) — use it
            # directly instead of the linear spread_pct*loan_amount
            # approximation below. slippage_haircut_pct at this point is
            # the retroactive figure the caller derived FROM this same
            # exact_gross_return, kept only so existing warning/Telegram
            # formatting that reads that field keeps working unchanged.
            gross_return = exact_gross_return
        else:
            gross_return_raw = loan_amount * spread_pct
            gross_return     = gross_return_raw * (1 - slippage_haircut_pct)
        loan_fee         = loan_amount * (self.loan_fee_pct / 100)
        gas_native_units = _DEFAULT_GAS_UNITS * gas_price_gwei * 1e-9
        gas_cost_usd     = gas_native_price * gas_native_units
        net              = gross_return - loan_fee - gas_cost_usd

        # v18.21 — only the exact two-leg round trip against LIVE raw
        # reserves on both legs (exact_gross_return is not None) is
        # trustworthy enough to size a real trade against — it's the
        # SAME math LocalExecutor's getAmountsOut() pre-flight check
        # mirrors. The linear/DexScreener-liquidity-sized fallback below
        # produced a real incident (2026-07-11, BNB/USDC biswap→
        # pancakeswap: scanner said net +$2.93, live quotes said -$654.22
        # — see changelog) precisely because DexScreener's liquidity
        # figure is known-unreliable for these pools. A BUY signal is
        # never emitted off that path now; the naive net is still
        # computed and logged (near-miss/tuning visibility) but
        # execute_trade() can never be reached from it.
        quote_verified = exact_gross_return is not None
        signal = "BUY" if (quote_verified and net >= result.min_profit) else "HOLD"

        result.buy_on          = buy_on
        result.sell_on         = sell_on
        result.spread_pct      = spread_pct * 100
        result.loan_amount     = loan_amount
        result.gross_return    = gross_return
        result.loan_fee        = loan_fee
        result.gas_cost_usd    = gas_cost_usd
        result.net_after_fee   = net
        result.signal          = signal
        result.has_opportunity = signal == "BUY"
        result.quote_verified  = quote_verified
        if not quote_verified and net >= result.min_profit:
            result.warnings.append(
                f"Would-be BUY suppressed — {buy_on}→{sell_on} net ${net:.2f} "
                f"clears the ${result.min_profit:.2f} floor on the linear/"
                "DexScreener-liquidity-sized estimate only; no live two-leg "
                "quote confirmed it, so this was downgraded to HOLD rather "
                "than risk sizing a trade off an unreliable liquidity figure "
                "(see v18.21 changelog)."
            )
        return result

    # ── Gas pricing (v18.0) ──────────────────────────────────────────────────

    async def _get_gas_price_gwei(self, chain: str) -> float:
        chain_key = chain.upper()
        w3 = self._w3_by_chain.get(chain_key)
        if w3 is not None:
            try:
                # web3's HTTPProvider does blocking I/O — never await it
                # directly from this coroutine (same lesson as
                # contract_manager.py's asyncio.to_thread usage).
                wei = await asyncio.to_thread(lambda: w3.eth.gas_price)
                gwei = wei / 1e9
                if self._max_gas_gwei and gwei > self._max_gas_gwei:
                    logger.warning(
                        "[Scanner] live gas price %.1f gwei on %s exceeds "
                        "MAX_GAS_GWEI cap %.1f — using cap for cost model",
                        gwei, chain, self._max_gas_gwei,
                    )
                    return self._max_gas_gwei
                return gwei
            except Exception as exc:
                logger.warning(
                    "[Scanner] live gas price fetch failed for %s: %s — "
                    "falling back to static default", chain, exc,
                )
        return _STATIC_GAS_GWEI.get(chain_key, 25.0)

    # ── Live on-chain reserves via router factory (v18.8) ─────────────────────
    # Replaces DexScreener's advertised liquidity/price for
    # _BSC_EXECUTABLE_DEXES entirely when a BSC Web3 instance is wired in
    # (bot.py's w3_by_chain={"BSC": ...}) — see v18.5 through v18.7's
    # changelogs for the production evidence that DexScreener's liquidity
    # figure is not trustworthy enough for these thinner BSC pairs at any
    # flat percentage. No new addresses are trusted here: router
    # addresses are the same ones already proven correct in
    # _BSC_ROUTER_ADDRESSES, and factory()/getPair() are standard,
    # universal UniswapV2-fork read calls, not pool-specific guesses.

    async def _resolve_pair_address(
        self, w3: Any, router_addr: str, token_a: str, token_b: str,
    ) -> str | None:
        """router -> factory() -> getPair(tokenA, tokenB). Cached forever
        (both facts are immutable on-chain). Never raises — any failure
        just means this router can't be used for live sizing this cycle,
        falling back to DexScreener for that side."""
        router_addr = w3.to_checksum_address(router_addr)
        factory_addr = self._factory_cache.get(router_addr)
        if factory_addr is None:
            try:
                router = w3.eth.contract(address=router_addr, abi=_ROUTER_FACTORY_ABI)
                factory_addr = await asyncio.to_thread(router.functions.factory().call)
                self._factory_cache[router_addr] = factory_addr
            except Exception as exc:
                logger.warning(
                    "[Scanner] Could not read factory() for router %s: %s",
                    router_addr, exc,
                )
                return None

        token_a = w3.to_checksum_address(token_a)
        token_b = w3.to_checksum_address(token_b)
        cache_key = f"{factory_addr}:{token_a}:{token_b}"
        pair_addr = self._pair_addr_cache.get(cache_key)
        if pair_addr is None:
            try:
                factory = w3.eth.contract(
                    address=w3.to_checksum_address(factory_addr), abi=_FACTORY_PAIR_ABI,
                )
                pair_addr = await asyncio.to_thread(
                    factory.functions.getPair(token_a, token_b).call
                )
                self._pair_addr_cache[cache_key] = pair_addr
            except Exception as exc:
                logger.warning(
                    "[Scanner] Could not resolve pair for %s/%s on factory %s: %s",
                    token_a, token_b, factory_addr, exc,
                )
                return None

        if pair_addr.lower() == _ZERO_ADDRESS:
            return None
        return pair_addr

    async def _read_live_reserves(
        self, w3: Any, pair_addr: str, base_addr: str,
        base_decimals: int, stable_decimals: int,
    ) -> tuple[float, float, int, int, int] | None:
        """Reads getReserves() for an already-resolved pair contract and
        returns (implied_price_usd, liquidity_usd, base_reserve_raw,
        stable_reserve_raw, reserve_block_ts) computed directly from THIS
        pool's own reserve ratio — NOT from an external oracle price
        shared across every DEX,
        which would make every on-chain-sourced pair report an identical
        price and always show 0% spread. Assumes the stable asset ≈ $1
        (USDT peg) — same assumption the rest of this deployment already
        makes throughout. Returns None (never raises) on any failure,
        including zero/negative reserves.

        v18.11 — now also returns the raw reserve integers (previously
        computed here, used once for the price/liquidity ratio, then
        discarded) so _scan_pair() can run the exact constant-product
        round trip instead of a linear approximation. No new RPC calls —
        same getReserves() read as before."""
        try:
            cache_key = pair_addr.lower()
            contract = self._pair_contracts.get(cache_key)
            if contract is None:
                contract = w3.eth.contract(
                    address=w3.to_checksum_address(pair_addr), abi=_PAIR_ABI,
                )
                self._pair_contracts[cache_key] = contract

            reserves = await asyncio.to_thread(contract.functions.getReserves().call)
            token0    = await asyncio.to_thread(contract.functions.token0().call)

            reserve0, reserve1, block_ts = reserves  # v18.18 — keep the 3rd value
            base_is_token0 = token0.lower() == base_addr.lower()
            base_reserve, stable_reserve = (
                (reserve0, reserve1) if base_is_token0 else (reserve1, reserve0)
            )
            if base_reserve <= 0 or stable_reserve <= 0:
                return None

            base_units   = base_reserve / (10 ** base_decimals)
            stable_units = stable_reserve / (10 ** stable_decimals)
            implied_price_usd = stable_units / base_units
            liquidity_usd      = stable_units * 2  # both sides of the pool, in USD
            return implied_price_usd, liquidity_usd, base_reserve, stable_reserve, int(block_ts)
        except Exception as exc:
            logger.warning(
                "[Scanner] getReserves failed for pair %s: %s", pair_addr, exc,
            )
            return None

    async def _fetch_live_bsc_reserves(
        self, cfg: dict, w3: Any,
    ) -> list[DexPairInfo]:
        """One DexPairInfo per _BSC_ROUTER_ADDRESSES entry whose pool
        resolves and has usable reserves. Empty list (never raises) if
        nothing resolves — caller falls back to the DexScreener path."""
        stable_addr = _BSC_STABLE_ADDRESSES.get(cfg["stable"])
        if not stable_addr:
            return []

        base_addr = cfg["address"]
        base_decimals   = cfg.get("base_decimals", 18)
        stable_decimals = cfg.get("stable_decimals", 18)

        results: list[DexPairInfo] = []
        for dex_name, router_addr in _BSC_ROUTER_ADDRESSES.items():
            pair_addr = await self._resolve_pair_address(w3, router_addr, base_addr, stable_addr)
            if not pair_addr:
                continue
            reserve_result = await self._read_live_reserves(
                w3, pair_addr, base_addr, base_decimals, stable_decimals,
            )
            if reserve_result is None:
                continue
            (price_usd, liquidity_usd, base_reserve_raw,
             stable_reserve_raw, reserve_block_ts) = reserve_result
            results.append(DexPairInfo(
                dex_name, price_usd, liquidity_usd, 0.0,
                base_reserve_raw=base_reserve_raw,
                stable_reserve_raw=stable_reserve_raw,
                reserve_block_ts=reserve_block_ts,
            ))

        # v18.19 — stale-pool filter. The v18.18 freshness probe proved
        # (in production) that some pools — notably the USDC-quoted pairs
        # on biswap/mdex — go 2-13 HOURS between trades. A pool that hasn't
        # traded that long has a frozen reserve ratio, so it shows a fixed,
        # phantom "spread" against a live pool every scan (the recurring
        # "best spread 0.628%" that looked like a near-miss but never was —
        # the exact math always correctly netted it negative, but it
        # polluted the readout). Exclude any pool whose last trade is older
        # than _MAX_RESERVE_AGE_SECS so every spread the scanner reports is
        # against a genuinely live, executable pool. Uses wall-clock now vs
        # the pool's on-chain block timestamp (both ~unix-time on BSC).
        now = time.time()
        fresh: list[DexPairInfo] = []
        dropped: list[str] = []
        for p in results:
            age = now - p.reserve_block_ts if p.reserve_block_ts else 0.0
            if p.reserve_block_ts and age > _MAX_RESERVE_AGE_SECS:
                dropped.append(f"{p.dex_id}({age/60:.0f}m)")
            else:
                fresh.append(p)

        if results:
            logger.info(
                "[Scanner] %s/%s live reserves — %s%s",
                cfg["base"], cfg["stable"],
                ", ".join(
                    f"{p.dex_id}@{(now - p.reserve_block_ts)/60:.1f}m"
                    for p in results if p.reserve_block_ts
                ) or "no on-chain ts",
                f" | DROPPED stale: {', '.join(dropped)}" if dropped else "",
            )
        return fresh

    async def _verify_pairs_onchain(
        self, cfg: dict, w3: Any, dex_pairs: list[DexPairInfo],
    ) -> list[DexPairInfo]:
        """v18.28 — upgrade-or-drop quote verification for the final BSC
        candidate set, run once per scan right before the spread math.

        Rows that already carry live raw reserves pass straight through
        (re-checked against _MAX_RESERVE_AGE_SECS, which also now covers
        the configured-pair on-chain path that never had a stale gate).
        Rows WITHOUT raw reserves — i.e. DexScreener-sourced fills — get
        their exact pool (the pairAddress DexScreener reported, no
        factory lookup) read live via getReserves():

          • fresh read  → the row is upgraded in place of itself (a new
            DexPairInfo, never mutating the shared DexScreener cache
            entry) with live price/liquidity/raw reserves — it can now
            back an exact two-leg quote, so a genuinely profitable
            spread through it becomes an executable BUY instead of the
            permanent "UNVERIFIED — Watching" near-miss loop.
          • stale read  → the row is DROPPED, killing the frozen-ratio
            phantom spread at the source instead of re-alerting on it
            every cycle.
          • failed read / no pairAddress → the row is kept as-is; it
            simply stays unverified and v18.21's guard keeps it away
            from execution, exactly as before this version.
        """
        base_decimals   = cfg.get("base_decimals", 18)
        stable_decimals = cfg.get("stable_decimals", 18)
        now = time.time()

        kept: list[DexPairInfo] = []
        upgraded: list[str] = []
        dropped: list[str] = []
        unverifiable: list[str] = []

        for p in dex_pairs:
            if p.base_reserve_raw and p.stable_reserve_raw:
                age = (now - p.reserve_block_ts) if p.reserve_block_ts else 0.0
                if p.reserve_block_ts and age > _MAX_RESERVE_AGE_SECS:
                    dropped.append(f"{p.dex_id}({age/60:.0f}m)")
                    continue
                kept.append(p)
                continue

            if not p.pair_address:
                unverifiable.append(p.dex_id)
                kept.append(p)
                continue

            reserve_result = await self._read_live_reserves(
                w3, p.pair_address, cfg["address"], base_decimals, stable_decimals,
            )
            if reserve_result is None:
                unverifiable.append(p.dex_id)
                kept.append(p)
                continue

            (price_usd, liquidity_usd, base_reserve_raw,
             stable_reserve_raw, reserve_block_ts) = reserve_result
            age = (now - reserve_block_ts) if reserve_block_ts else 0.0
            if reserve_block_ts and age > _MAX_RESERVE_AGE_SECS:
                dropped.append(f"{p.dex_id}({age/60:.0f}m)")
                continue

            kept.append(DexPairInfo(
                p.dex_id, price_usd, liquidity_usd, p.volume24h,
                base_reserve_raw=base_reserve_raw,
                stable_reserve_raw=stable_reserve_raw,
                reserve_block_ts=reserve_block_ts,
                pair_address=p.pair_address,
                quote_token_addr=p.quote_token_addr,
            ))
            upgraded.append(f"{p.dex_id}@{age/60:.1f}m")

        if upgraded or dropped or unverifiable:
            logger.info(
                "[Scanner] %s/%s quote-verify — %s%s%s",
                cfg["base"], cfg["stable"],
                (f"upgraded to live reserves: {', '.join(upgraded)}"
                 if upgraded else "no upgrades needed"),
                f" | DROPPED stale: {', '.join(dropped)}" if dropped else "",
                (f" | still unverified (no addr/RPC failed): {', '.join(unverifiable)}"
                 if unverifiable else ""),
            )
        return kept

    # ── DexScreener (with v18.0 short-TTL cache + simple failure backoff) ────

    async def _fetch_dex_pairs(
        self, token_address: str, chain_id: str, cfg: dict | None = None,
    ) -> list[DexPairInfo]:
        """
        NEW (direct-RPC mode): for each of dex_a/dex_b in cfg, if a
        pair_a/pair_b contract address is configured AND a Web3 instance
        for cfg["chain"] exists in self._w3_by_chain, read getReserves()
        directly on-chain instead of calling DexScreener for that side.
        Falls back to DexScreener (the original behavior, unchanged)
        for any side that has no pair address configured, or whose RPC
        read fails.

        This means a fully-configured cfg (both pair_a and pair_b set,
        both RPC reads succeeding) never touches DexScreener or the
        network at all for that scan — no third-party API, no HTTP
        round-trip, no dependency on the Cloudflare/PythonAnywhere relay
        chain for PRICE data. (Execution confirmation via OracleClient
        is a separate, still-unresolved concern — see oracle.py.)
        """
        onchain_results: list[DexPairInfo] = []
        dexscreener_needed = True

        if cfg is not None:
            w3 = self._w3_by_chain.get(cfg["chain"].upper())
            if w3 is not None:
                onchain_results = await self._fetch_reserves_onchain(cfg, w3)
                # Only skip DexScreener entirely if BOTH configured sides
                # (dex_a and dex_b) actually produced a result on-chain —
                # a partial on-chain result still needs DexScreener to
                # fill the gap for the missing side, same as before.
                configured_sides = sum(
                    1 for k in ("pair_a", "pair_b") if cfg.get(k)
                )
                dexscreener_needed = len(onchain_results) < max(configured_sides, 2)

        if not dexscreener_needed:
            return onchain_results

        dex_results = await self._fetch_dex_pairs_http(token_address, chain_id)

        # v18.28 — the HTTP path now returns up to one row per
        # (dex_id, quote_token) rather than one per dex_id (see its
        # changelog note). Collapse that back to one row per dex_id
        # here, where cfg tells us WHICH quote token is the right one:
        # when the configured stable's address is known (BSC), a row
        # quoted in anything else is rejected outright — the
        # stable→base→stable loop can't route through it, so any spread
        # against it is phantom by construction. Rows with no reported
        # quote token (rare) are kept and left to the on-chain
        # verification gate in _scan_pair. Non-BSC cfgs (no stable
        # address map) keep the original highest-liquidity-per-dex pick.
        stable_addr = ""
        if cfg is not None and cfg.get("chain", "").upper() == "BSC":
            stable_addr = (_BSC_STABLE_ADDRESSES.get(cfg["stable"]) or "").lower()
        if stable_addr:
            picked: dict[str, DexPairInfo] = {}
            dropped_wrong_quote: list[str] = []
            for row in dex_results:  # already sorted best-liquidity-first
                if row.quote_token_addr and row.quote_token_addr != stable_addr:
                    dropped_wrong_quote.append(row.dex_id)
                    continue
                picked.setdefault(row.dex_id, row)
            if dropped_wrong_quote:
                logger.info(
                    "[Scanner] DexScreener %s — DROPPED wrong-quote pool(s) "
                    "(not quoted in %s): %s",
                    token_address, cfg["stable"],
                    ", ".join(sorted(set(dropped_wrong_quote))),
                )
            dex_results = list(picked.values())
        else:
            picked = {}
            for row in dex_results:
                picked.setdefault(row.dex_id, row)
            dex_results = list(picked.values())

        if not onchain_results:
            return dex_results

        # Merge: prefer on-chain results (fresher, no third-party lag),
        # fill in any dex_id not already covered on-chain from DexScreener.
        seen = {p.dex_id for p in onchain_results}
        merged = list(onchain_results) + [p for p in dex_results if p.dex_id not in seen]
        return merged

    async def _fetch_reserves_onchain(
        self, cfg: dict, w3: Any,
    ) -> list[DexPairInfo]:
        """
        Direct getReserves() read for pair_a and/or pair_b, whichever are
        configured. Returns a DexPairInfo per successful read; price is
        derived from the reserve ratio × the base asset's live USD price
        (already fetched by the caller from price_client), NOT from any
        DexScreener-reported priceUsd — this is actually more current
        than DexScreener's field, which lags the chain by however long
        DexScreener's own indexer takes to catch up.

        Never raises. A failed/misconfigured pair is simply omitted from
        the result list so the DexScreener fallback can cover it.
        """
        base_price = None
        base_entry = None
        # cfg doesn't carry live prices itself — caller (_scan_pair) has
        # already resolved prices before calling _fetch_dex_pairs, but to
        # keep this method self-contained and not require threading the
        # whole `prices` dict through, we accept a pre-attached hint if
        # present, else skip on-chain pricing for this call (falls back
        # to DexScreener instead of guessing).
        base_price = cfg.get("_live_base_price_hint")
        if not base_price:
            return []

        results: list[DexPairInfo] = []
        now = time.monotonic()
        base_decimals   = cfg.get("base_decimals", 18)
        stable_decimals = cfg.get("stable_decimals", 18)
        base_addr       = cfg["address"].lower()

        for side_key, dex_id in (("pair_a", cfg["dex_a"]), ("pair_b", cfg["dex_b"])):
            pair_addr = cfg.get(side_key)
            if not pair_addr:
                continue

            cache_key = pair_addr.lower()
            cached = self._rpc_cache.get(cache_key)
            if cached is not None and (now - cached[0]) < _RPC_CACHE_TTL_SECS:
                results.append(cached[1])
                continue
            if now < self._rpc_backoff_until.get(cache_key, 0.0):
                if cached is not None:
                    results.append(cached[1])
                continue

            try:
                contract = self._pair_contracts.get(cache_key)
                if contract is None:
                    contract = w3.eth.contract(
                        address=w3.to_checksum_address(pair_addr),
                        abi=_PAIR_ABI,
                    )
                    self._pair_contracts[cache_key] = contract

                # web3.py's HTTPProvider is blocking I/O — never await it
                # directly (same lesson _get_gas_price_gwei already
                # follows for eth_gasPrice).
                reserves = await asyncio.to_thread(contract.functions.getReserves().call)
                token0   = await asyncio.to_thread(contract.functions.token0().call)

                reserve0, reserve1, block_ts = reserves
                base_is_token0 = token0.lower() == base_addr

                if base_is_token0:
                    base_reserve, stable_reserve = reserve0, reserve1
                else:
                    base_reserve, stable_reserve = reserve1, reserve0

                if base_reserve <= 0 or stable_reserve <= 0:
                    raise ValueError(f"zero reserve (base={base_reserve} stable={stable_reserve})")

                base_units   = base_reserve / (10 ** base_decimals)
                stable_units = stable_reserve / (10 ** stable_decimals)
                # Implied pool price of base in stable-asset terms, then
                # converted to USD via the live base_price the caller
                # supplied — same end unit (USD) DexScreener's priceUsd
                # would have given us, just sourced on-chain.
                pool_price_usd = base_price
                liquidity_usd  = base_units * base_price * 2  # both sides of the pool, rough USD total

                # v18.28 — carry the raw reserves + last-trade timestamp
                # this read already produced (previously discarded), so
                # the exact two-leg round trip in _scan_pair can run on
                # configured-pair rows too instead of the linear model —
                # same data, one less reason for quote_verified=False.
                info = DexPairInfo(
                    dex_id, pool_price_usd, liquidity_usd, 0.0,
                    base_reserve_raw=base_reserve,
                    stable_reserve_raw=stable_reserve,
                    reserve_block_ts=int(block_ts),
                    pair_address=pair_addr,
                )
                self._rpc_cache[cache_key] = (now, info)
                self._rpc_backoff_until.pop(cache_key, None)
                results.append(info)

            except Exception as exc:
                logger.warning(
                    "[Scanner] on-chain reserve read failed for %s (%s): %s "
                    "— falling back to DexScreener for this side",
                    dex_id, pair_addr, exc,
                )
                self._rpc_backoff_until[cache_key] = now + _RPC_FAIL_BACKOFF_SECS
                if cached is not None:
                    results.append(cached[1])

        return results

    async def _fetch_dex_pairs_http(
        self, token_address: str, chain_id: str
    ) -> list[DexPairInfo]:
        """Original DexScreener HTTP path — used as fallback."""
        now = time.monotonic()

        cached = self._dex_cache.get(token_address)
        if cached is not None and (now - cached[0]) < _DEX_CACHE_TTL_SECS:
            return cached[1]

        if now < self._dex_backoff_until.get(token_address, 0.0):
            return cached[1] if cached is not None else []

        # v18.27 FIX (root cause — duplicate concurrent DexScreener
        # calls within a single cycle) — scan() runs every configured
        # pair concurrently via asyncio.gather, and several pairs share
        # a token_address here on purpose (BTCB backs both WBTC/USDC
        # and WBTC/USDT; Binance-Peg ETH backs both ETH/USDT and
        # ETH/USDC — see _SCAN_PAIRS). Whenever the cache is cold, every
        # coroutine for those pairs reaches the two checks above in the
        # same tick, all see "not cached," and — with nothing async
        # between that check and the HTTP call below — all of them fire
        # an independent request for the identical resource before any
        # of them has had a chance to write back to self._dex_cache.
        # Confirmed in production: 2026-07-11 13:29:51 shows two
        # back-to-back GET + "DROPPED stale" log lines for the same
        # BTCB token address inside one scan cycle. Harmless to
        # correctness (both calls return the same data), but it wastes
        # a round-trip every time it happens and, done often enough,
        # risks tripping DexScreener's own rate limiting — which would
        # degrade the fallback for every pair, not just the racing
        # ones.
        #
        # Fix: the first coroutine to reach a cold cache for a given
        # token_address registers a Future in self._dex_inflight and
        # does the real fetch; any other coroutine racing in for the
        # same token_address before that Future resolves just awaits
        # it and reuses the one result. No lock needed — asyncio is
        # single-threaded and there's no `await` between the dict
        # lookup and the dict write, so this check-and-register step
        # itself can't race.
        inflight = self._dex_inflight.get(token_address)
        if inflight is not None:
            return await inflight

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._dex_inflight[token_address] = fut

        try:
            resp = await self._http.get(
                f"{_DEXSCREENER_BASE}/tokens/{token_address}", timeout=8.0
            )
            resp.raise_for_status()
            data = resp.json()

            result: list[DexPairInfo] = []
            # v18.28 — dedupe by (dex_id, quote_token) instead of dex_id
            # alone. Before this, the single highest-liquidity pool per
            # DEX won the row no matter what it was quoted in — so a
            # deep XRP/WBNB pool could permanently shadow the XRP/USDT
            # pool on the same DEX, and the caller (which assumes every
            # row is quoted in the configured stable) would compute a
            # phantom spread against a pool the stable→base→stable loop
            # can't even route through. Keeping one row per quote token
            # lets _fetch_dex_pairs pick the right-quote row per DEX;
            # the raw (unfiltered) list is what gets cached so pairs
            # sharing a token_address but differing in stable (WBTC/USDC
            # vs WBTC/USDT) still share one HTTP call.
            seen: set[tuple[str, str]] = set()
            dropped_stale: list[str] = []

            for p in sorted(
                data.get("pairs", []) or [],
                key=lambda x: x.get("liquidity", {}).get("usd", 0),
                reverse=True,
            ):
                if p.get("chainId") != chain_id:
                    continue
                dex_id = p.get("dexId", "unknown")
                quote_addr = ((p.get("quoteToken") or {}).get("address") or "").lower()
                if (dex_id, quote_addr) in seen:
                    continue
                try:
                    price = float(p.get("priceUsd") or 0)
                    liq   = float(p.get("liquidity", {}).get("usd", 0))
                    if price <= 0 or liq <= _MIN_LIQUIDITY_USD:
                        continue

                    # v18.20 — DexScreener-side counterpart to the
                    # v18.19 on-chain staleness filter. These rows have
                    # no block timestamp (reserve_block_ts stays 0 for
                    # them all the way through _scan_pair), so without
                    # this check a pool that hasn't traded in hours
                    # would sail through as a comparison venue whenever
                    # this fallback is used to fill a gap — reopening
                    # the exact "frozen reserve ratio → phantom spread"
                    # bug v18.19 fixed, just via DexScreener's numbers
                    # instead of raw RPC ones. DexScreener exposes
                    # rolling buy/sell counts rather than a timestamp,
                    # so "at least one trade across the m5+h1 windows"
                    # is the closest equivalent to a fresh block_ts.
                    txns = p.get("txns", {}) or {}
                    m5 = txns.get("m5", {}) or {}
                    h1 = txns.get("h1", {}) or {}
                    recent_txns = (
                        int(m5.get("buys", 0) or 0) + int(m5.get("sells", 0) or 0)
                        + int(h1.get("buys", 0) or 0) + int(h1.get("sells", 0) or 0)
                    )
                    if recent_txns < _MIN_DEXSCREENER_RECENT_TXNS:
                        dropped_stale.append(dex_id)
                        continue

                    result.append(DexPairInfo(
                        dex_id,
                        price,
                        liq,
                        float(p.get("volume", {}).get("h24", 0)),
                        # v18.28 — carried so _verify_pairs_onchain() can
                        # read this exact pool's reserves live, and so
                        # _fetch_dex_pairs can reject wrong-quote pools.
                        pair_address=(p.get("pairAddress") or ""),
                        quote_token_addr=quote_addr,
                    ))
                    seen.add((dex_id, quote_addr))
                except (TypeError, ValueError):
                    continue
                if len(result) >= 10:
                    break

            if dropped_stale:
                logger.info(
                    "[Scanner] DexScreener %s — DROPPED stale (no m5/h1 txns): %s",
                    token_address, ", ".join(dropped_stale),
                )

            self._dex_cache[token_address] = (now, result)
            self._dex_backoff_until.pop(token_address, None)
            # v18.27 — resolve the shared Future so anyone who raced in
            # on this token_address and is awaiting it gets this same
            # result instead of firing their own request.
            fut.set_result(result)
            return result

        except Exception as exc:
            logger.warning("[Scanner] DexScreener error for %s: %s", token_address, exc)
            self._dex_backoff_until[token_address] = now + _DEX_FAIL_BACKOFF_SECS
            # Serve stale cache on failure rather than an empty list, if we
            # have anything at all — better than treating a transient
            # DexScreener hiccup as "zero liquidity everywhere."
            fallback = cached[1] if cached is not None else []
            # v18.27 — same reasoning as the success path: give every
            # waiter the same graceful fallback rather than an
            # exception, so one DexScreener hiccup doesn't also blow up
            # every other pair that happened to be racing on this
            # token_address this cycle.
            fut.set_result(fallback)
            return fallback

        finally:
            # v18.27 — always release the in-flight slot, success or
            # failure, so the NEXT cold-cache cycle for this
            # token_address starts clean instead of awaiting a Future
            # that already fired.
            self._dex_inflight.pop(token_address, None)