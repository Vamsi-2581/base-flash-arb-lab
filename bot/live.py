"""Opt-in one-candidate broadcaster. OFF by default, never runs unattended.

Requires Foundry `cast`, an existing keystore account, and a recent preflight.
Never accepts a raw private key on the command line.
"""
from __future__ import annotations
import argparse
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from .policy import LimitExceeded, reserve_budget


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--preflight", type=Path, required=True)
    p.add_argument("--rpc", default=os.environ.get("BASE_RPC_URL", ""))
    p.add_argument("--account", default=os.environ.get("FOUNDRY_KEYSTORE_ACCOUNT", ""))
    p.add_argument("--password-file", type=Path, help="Optional local-only keystore password file")
    p.add_argument("--budget-file", type=Path, default=Path("budget_state.json"))
    p.add_argument("--daily-loss-usdc", type=Decimal, default=Decimal("2"))
    p.add_argument("--total-loss-usdc", type=Decimal, default=Decimal("20"))
    p.add_argument("--confirm-send", choices=["I_ACCEPT_RISK"], required=True)
    args = p.parse_args(argv)
    if os.environ.get("LIVE_TRADING") != "I_ACCEPT_FINANCIAL_RISK":
        p.error("Live trading disabled. Explicitly set LIVE_TRADING=I_ACCEPT_FINANCIAL_RISK")
    if not shutil.which("cast"):
        p.error("Foundry cast not installed")
    if not args.rpc or not args.account:
        p.error("RPC and --account are required")
    try:
        x = json.loads(args.preflight.read_text(encoding="utf-8"))
        if (not x.get("ready_for_manual_decision") or x["chain_id"] != 8453
            or time.time() > x["deadline"] - 15):
            raise ValueError("Preflight invalid, wrong chain or too close to expiry")
        # Do NOT trust old simulation: force user to generate a fresh preflight
        # and do not send after deadline. Live use remains experimental.
        reserved = int(x["estimated_max_fee_usdc"])
        state = reserve_budget(args.budget_file, reserved,
            daily_usdc=int(args.daily_loss_usdc * 10**6),
            lifetime_usdc=int(args.total_loss_usdc * 10**6))
        command = ["cast", "send", "--rpc-url", args.rpc, "--account", args.account,
                   "--gas-limit", str(x["gas_limit"]),
                   "--gas-price", str(x["gas_price_wei"]),
                   "--json", x["executor"],
                   "startArbitrage(uint256,uint8,uint24,uint8,uint24,uint256,uint256,uint256,uint256)",
                   *[str(a) for a in x["args"]]]
        if args.password_file:
            command[command.index("--json"):command.index("--json")] = ["--password-file", str(args.password_file)]
        print(f"Reserved Gas risk ${reserved/1e6:.3f}, cumulative ${state['lifetime_reserved_usdc']/1e6:.3f}.", flush=True)
        print("Submitting explicitly enabled transaction; no profit is guaranteed.", flush=True)
        result = subprocess.run(command, timeout=100, check=False)
        return result.returncode
    except (ValueError, KeyError, OSError, LimitExceeded, subprocess.TimeoutExpired) as e:
        print(f"BLOCKED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
