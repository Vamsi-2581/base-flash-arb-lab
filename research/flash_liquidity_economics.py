"""Read-only Flash Loan + same-transaction liquidity economics, standard library only.

This is an upper-bound stylized model, not a claim that any AMM market maker
can use a flash loan across multiple independent transactions. A third-party
order must be validly executed INSIDE THE SAME TRANSACTION as the flash loan.

It ignores nonuniform liquidity across ticks, inventory convexity, slippage
beyond a specified hedge charge, order exclusivity, revert probability,
protocol LP-fee allocation and builder competition other than explicit costs.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
from decimal import Decimal
import json

D = Decimal
BPS = D("10000")

@dataclass(frozen=True)
class Scenario:
    loan_usd: Decimal
    third_party_swap_volume_usd: Decimal
    pool_fee_bps: Decimal
    share_of_effective_swap_fees: Decimal
    flashloan_premium_bps: Decimal
    hedge_notional_usd: Decimal
    hedge_cost_bps: Decimal
    gas_usd: Decimal
    ordering_usd: Decimal
    inventory_mark_to_market_usd: Decimal = D("0")
    same_transaction_authorized_order: bool = False

    def validate(self) -> None:
        nonnegative = [
            self.third_party_swap_volume_usd, self.pool_fee_bps,
            self.flashloan_premium_bps, self.hedge_notional_usd,
            self.hedge_cost_bps, self.gas_usd, self.ordering_usd,
        ]
        if self.loan_usd <= 0 or any(x < 0 for x in nonnegative):
            raise ValueError("Invalid loan or negative cost/volume/fee")
        if not D("0") <= self.share_of_effective_swap_fees <= D("1"):
            raise ValueError("Effective LP fee share outside [0, 1]")
        if self.pool_fee_bps > BPS or self.flashloan_premium_bps > BPS:
            raise ValueError("Fee bps cannot exceed 10000")


def evaluate(s: Scenario) -> dict:
    s.validate()
    gross_fee = s.third_party_swap_volume_usd * s.pool_fee_bps / BPS
    lp_income = gross_fee * s.share_of_effective_swap_fees
    loan_fee = s.loan_usd * s.flashloan_premium_bps / BPS
    hedge_cost = s.hedge_notional_usd * s.hedge_cost_bps / BPS
    costs = loan_fee + hedge_cost + s.gas_usd + s.ordering_usd
    net = lp_income + s.inventory_mark_to_market_usd - costs
    marginal = s.pool_fee_bps * s.share_of_effective_swap_fees / BPS
    threshold = max(D("0"), costs - s.inventory_mark_to_market_usd) / marginal if marginal > 0 else None
    if not s.same_transaction_authorized_order:
        status = "NOT_FLASHLOAN_COMPATIBLE_MULTITX_JIT"
        realized_like_net = None
    else:
        status = "THEORETICAL_POSITIVE" if net > 0 else "THEORETICAL_NONPOSITIVE"
        realized_like_net = net
    return {
        "status": status,
        "flashloan_eligible": s.same_transaction_authorized_order,
        "third_party_swap_required": True,
        "pool_total_fee_usd": str(gross_fee),
        "theoretical_lp_fee_income_usd": str(lp_income),
        "flashloan_fee_usd": str(loan_fee),
        "hedging_cost_usd": str(hedge_cost),
        "gas_usd": str(s.gas_usd),
        "ordering_usd": str(s.ordering_usd),
        "inventory_mark_to_market_usd": str(s.inventory_mark_to_market_usd),
        "theoretical_net_usd": str(net),
        "flashloan_eligible_net_usd": str(realized_like_net) if realized_like_net is not None else None,
        "break_even_third_party_swap_volume_usd": str(threshold) if threshold is not None else None,
        "warning": "Only linear upper-bound planning; not real swap receipts or JIT execution. An ordinary 3-transaction JIT bundle cannot carry an Aave flash loan between transactions.",
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--loan", default="1000000")
    p.add_argument("--volume", default="2000000")
    p.add_argument("--lp-fee-bps", default="5")
    p.add_argument("--share", default="0.7")
    p.add_argument("--flash-bps", default="5")
    p.add_argument("--hedge", default="600000")
    p.add_argument("--hedge-bps", default="2")
    p.add_argument("--gas", default="8")
    p.add_argument("--ordering", default="80")
    p.add_argument("--inventory-pnl", default="0")
    p.add_argument("--same-tx-order", action="store_true",
                   help="ONLY if a third-party order can legally execute within the borrowing transaction")
    a = p.parse_args()
    scenario = Scenario(D(a.loan), D(a.volume), D(a.lp_fee_bps),
                        D(a.share), D(a.flash_bps), D(a.hedge), D(a.hedge_bps),
                        D(a.gas), D(a.ordering), D(a.inventory_pnl), a.same_tx_order)
    result = {"input": {k: str(v) for k,v in asdict(scenario).items()},
              "result": evaluate(scenario)}
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
