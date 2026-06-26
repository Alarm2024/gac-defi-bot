"""
🪬🧿✝️  GARDEN ANGEL — Strategy Module (v2)
         Deferred Gas Execution Gate
         Math: Decimal-safe, EVM-grade precision
         No gas in decision – pure spread vs loan fee
"""

import os
import logging
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from dataclasses import dataclass, field
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s][%(levelname)s][Strategy] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("strategy")

PRECISION       = Decimal("0.000001")
MIN_NET_PROFIT  = Decimal("5.00")
ZERO            = Decimal("0")

@dataclass
class StrategyConfig:
    rpc_url         : str   = field(default_factory=lambda: os.getenv("RPC_URL", "http://localhost:8545"))
    private_key     : str   = field(default_factory=lambda: os.getenv("PRIVATE_KEY", ""))
    gas_limit       : int   = field(default_factory=lambda: int(os.getenv("GAS_LIMIT", "500000")))
    min_net_profit  : Decimal = field(default_factory=lambda: Decimal(os.getenv("MIN_NET_PROFIT_USD", str(MIN_NET_PROFIT))))
    loan_fee_pct    : Decimal = field(default_factory=lambda: Decimal(os.getenv("LOAN_FEE_PCT", "0.09")))
    slippage_pct    : Decimal = field(default_factory=lambda: Decimal(os.getenv("SLIPPAGE_PCT", "0.5")))

    def __post_init__(self):
        if not self.rpc_url.startswith(("http", "ws")):
            raise ValueError(f"RPC_URL malformed: {self.rpc_url!r}")
        log.info("Config loaded | min_profit=$%s | loan_fee=%s%%",
                 self.min_net_profit, self.loan_fee_pct)

@dataclass
class OpportunityResult:
    gross_return   : Decimal
    loan_fee       : Decimal
    net_after_fee  : Decimal
    should_exec    : bool
    skip_reason    : Optional[str] = None

    def __str__(self) -> str:
        tag = "✅ EXECUTE" if self.should_exec else f"⛔ SKIP ({self.skip_reason})"
        return f"{tag} | gross=${self.gross_return} fee=${self.loan_fee} → net_after_fee=${self.net_after_fee}"

class TradingStrategy:
    def __init__(self):
        self.config = StrategyConfig()

    @staticmethod
    def _to_decimal(value, name: str) -> Decimal:
        try:
            d = Decimal(str(value))
        except InvalidOperation:
            raise ValueError(f"{name} invalid: {value!r}")
        if d < ZERO:
            raise ValueError(f"{name} must be ≥ 0, got {d}")
        return d

    def calculate_net_after_fee(self, gross_return: float | Decimal, loan_fee: float | Decimal) -> Decimal:
        g = self._to_decimal(gross_return, "gross_return")
        lf = self._to_decimal(loan_fee, "loan_fee")
        net = (g - lf).quantize(PRECISION, rounding=ROUND_DOWN)
        log.debug("Net after fee: gross=%s - fee=%s = %s", g, lf, net)
        return net

    def should_execute(self, net_after_fee: float | Decimal) -> bool:
        np = self._to_decimal(net_after_fee, "net_after_fee")
        exec_ = np > self.config.min_net_profit
        log.info("Signal: %s | net_after_fee=$%s vs threshold=$%s",
                 "EXECUTE" if exec_ else "HOLD", np, self.config.min_net_profit)
        return exec_

    def analyse_opportunity(self, gross_return: float | Decimal, loan_fee: float | Decimal) -> OpportunityResult:
        net = self.calculate_net_after_fee(gross_return, loan_fee)
        g = self._to_decimal(gross_return, "gross_return")
        lf = self._to_decimal(loan_fee, "loan_fee")
        do_it = self.should_execute(net)
        skip_reason = None
        if not do_it:
            skip_reason = f"below_threshold_${self.config.min_net_profit}" if net > ZERO else "net_negative"
        result = OpportunityResult(
            gross_return=g,
            loan_fee=lf,
            net_after_fee=net,
            should_exec=do_it,
            skip_reason=skip_reason,
        )
        log.info(str(result))
        return result
