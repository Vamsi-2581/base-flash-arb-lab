"""Fail-closed on-chain dry-run for a previously discovered candidate.

Requires a deployed paused/unpaused FlashArb contract and configured operator.
No transaction is sent. Cast/Foundry handles signing in separate live mode.
"""
from __future__ import annotations
import argparse
from decimal import Decimal
import json
import os
import sys
import time
from pathlib import Path

from .abi import START_SIGNATURE, encode, result_address
from .networks import BASE_CHAIN_ID, AAVE_PROVIDER
from .rpc import RPC, RpcError

USDC_UNIT = 10**6


def load_candidate(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        if len(raw) != 1:
            raise ValueError("Candidate file must contain one candidate object")
        raw = raw[0]
    if raw.get("status") != "QUOTED_NOT_FORK_VERIFIED":
        raise ValueError("Unexpected candidate status")
    if int(raw.get("estimated_net_usdc", 0)) <= 0:
        raise ValueError("Candidate has no modeled profit")
    if raw.get("first_pool", "").lower() == raw.get("second_pool", "").lower():
        raise ValueError("Same pool cannot be both legs")
    return raw


def preflight(rpc: RPC, candidate: dict, executor: str, sender: str,
              *, max_age: int = 2, max_gas_usdc: int = 500_000,
              max_gas_gwei: Decimal = Decimal("0.5"), max_loan_usdc: int = 20_000_000_000,
              gas_padding_bps: int = 13000, extra_l1_fee_usdc: int = 100_000) -> dict:
    if rpc.chain_id() != BASE_CHAIN_ID:
        raise RpcError("RPC chain ID mismatch; expected Base Mainnet 8453")
    current_block = rpc.block_number()
    age = current_block - int(candidate["block"])
    if not 0 <= age <= max_age:
        raise RpcError(f"Candidate stale ({age} blocks); refuse simulation or send")
    if not (0 < int(candidate["amount_usdc"]) <= max_loan_usdc):
        raise ValueError("Loan size exceeds limit")
    gas_price = rpc.gas_price()
    if Decimal(gas_price) > max_gas_gwei * Decimal(10**9):
        raise RpcError("Gas price exceeds configured cap")
    # Ownership/operator, paused and dependencies are checked by eth_call.
    deadline = int(time.time()) + 55
    args = [int(candidate[k]) for k in ("amount_usdc", "first_dex", "first_fee",
            "second_dex", "second_fee", "min_weth", "min_usdc", "min_profit_usdc")]
    calldata = encode(START_SIGNATURE, *args, deadline)
    tx = {"from": sender, "to": executor, "data": calldata, "value": "0x0"}
    # If pool cannot fully execute now (or executor paused), eth_call reverts.
    rpc.call("eth_call", [tx, "latest"])
    gas = rpc.estimate_gas(tx, "latest")
    gas_limit = (gas * gas_padding_bps + 9999) // 10000
    # Current candidate gas model gives implied USDC/wei conversion.
    assumed_gas = int(candidate["estimated_gas_usdc"])
    model_units = int(candidate["model_gas_units"])
    model_price = int(candidate["gas_price_wei"])
    if model_units <= 0 or model_price <= 0:
        raise ValueError("Missing gas conversion model")
    # Conservative calculation: whole model amount (which already includes L1 buffer),
    # plus a separate L1 buffer because the actual calldata may be larger.
    estimate_usdc = (assumed_gas * gas_limit * gas_price + (model_units * model_price - 1)) // (model_units * model_price)
    estimate_usdc += extra_l1_fee_usdc
    if estimate_usdc > max_gas_usdc:
        raise RpcError(f"Estimated upper bound ${estimate_usdc / USDC_UNIT:.3f} > cap")
    balance = int(rpc.call("eth_getBalance", [sender, "latest"]), 16)
    if balance < gas_limit * gas_price:
        raise RpcError("Insufficient ETH for worst-case configured gas limit")
    return {"ready_for_manual_decision": True, "chain_id": BASE_CHAIN_ID,
            "candidate_age_blocks": age, "executor": executor, "sender": sender,
            "calldata": calldata, "gas_estimate": gas, "gas_limit": gas_limit,
            "gas_price_wei": gas_price, "estimated_max_fee_usdc": estimate_usdc,
            "deadline": deadline, "args": args + [deadline],
            "warning": "Simulation is point-in-time; a later block may differ. Never a profit guarantee."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc", default=os.environ.get("BASE_RPC_URL", "https://mainnet.base.org"))
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--executor", default=os.environ.get("EXECUTOR_ADDRESS", ""), required=False)
    parser.add_argument("--sender", default=os.environ.get("OPERATOR_ADDRESS", ""), required=False)
    parser.add_argument("--max-age", type=int, default=2)
    parser.add_argument("--max-gas-usdc", type=Decimal, default=Decimal("0.5"))
    parser.add_argument("--max-gas-gwei", type=Decimal, default=Decimal("0.5"))
    parser.add_argument("--output", type=Path, default=Path("preflight.json"))
    args = parser.parse_args(argv)
    if len(args.executor) != 42 or len(args.sender) != 42:
        parser.error("Must supply --executor and --sender as 0x addresses")
    try:
        raw = preflight(RPC(args.rpc), load_candidate(args.candidate), args.executor, args.sender,
            max_age=args.max_age, max_gas_usdc=int(args.max_gas_usdc * USDC_UNIT),
            max_gas_gwei=args.max_gas_gwei)
        args.output.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        print(json.dumps(raw, indent=2))
        print("No transaction sent. Verify current state again before real sending.")
        return 0
    except (RpcError, ValueError, KeyError, OSError) as e:
        print(f"BLOCKED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
