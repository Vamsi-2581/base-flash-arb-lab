"""Historical full Aave V3 + two-DEX fork replay (no on-chain broadcasts).

Only a real fork-run emitted JSON proof with positive post-repayment USDC
is accepted as successful execution. Net-after-gas is conservatively MODELED,
not a historical receipt or a claim of mainnet inclusion.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

PROOF = Path("validation/fork-proof.json")
USDC_UNIT = 1_000_000


def modeled_gas_cost(candidate: dict, measured_operation_gas: int) -> int:
    """Never charge less than scanner's conservative all-in Gas USDC assumption.

    Operation gas excludes intrinsic and transaction data/L1 fees; scan model
    includes a gas multiplier and explicit L1 cushion. This is an estimate.
    """
    assumed_cost = int(candidate["estimated_gas_usdc"])
    assumed_units = int(candidate["model_gas_units"])
    if assumed_units <= 0 or assumed_cost < 0:
        raise ValueError("Invalid scan gas model")
    return max(assumed_cost,
               (assumed_cost * max(1, measured_operation_gas) + assumed_units - 1)//assumed_units)


def verify_fork_proof(candidate: dict, raw: dict) -> dict:
    """Validate one strict proof; stale or mismatched proof cannot imply profit."""
    if (raw.get("status") != "FORK_EXECUTION_SUCCESS" or
        int(raw["block"]) != int(candidate["block"]) or
        int(raw["loan_usdc_units"]) != int(candidate["amount_usdc"])):
        raise ValueError("Fork proof does not match pinned candidate")
    profit = int(raw["profit_usdc_units"])
    operation_gas = int(raw["operation_gas_used"])
    if profit <= 0 or operation_gas <= 0:
        raise ValueError("Fork proof has nonpositive profit or invalid gas")
    expected_floor = int(candidate["min_profit_usdc"])
    if profit < expected_floor:
        raise ValueError("Fork proof violated profit floor")
    gas_estimate = modeled_gas_cost(candidate, operation_gas)
    net_estimate = profit - gas_estimate
    return {
        "status": "FORK_EXECUTION_SUCCESS",
        "loan_usdc_units": int(raw["loan_usdc_units"]),
        "realized_token_profit_usdc_units": profit,
        "measured_operation_gas": operation_gas,
        "conservative_modeled_gas_usdc_units": gas_estimate,
        "modeled_net_after_gas_usdc_units": net_estimate,
        "net_after_gas_status": "MODELED_POSITIVE" if net_estimate > 0 else "MODELED_NONPOSITIVE",
        "proof_type": "Actual Aave loan and swaps executed on a fork at the specified Base block",
        "not_proven": "Historical on-chain inclusion, exact L1 DA fee, MEV competition, gas receipt, real profit",
    }


def summarize_results(rows: list[dict]) -> dict:
    clean = [x for x in rows if x.get("status") in ("FORK_EXECUTION_SUCCESS", "FORK_REVERT")]
    winners = sum(x["status"] == "FORK_EXECUTION_SUCCESS" for x in clean)
    positive_net = sum(x.get("net_after_gas_status") == "MODELED_POSITIVE" for x in clean)
    return {"fork_attempts_excluding_infrastructure_errors": len(clean),
            "fork_successes": winners,
            "fork_success_rate": winners / len(clean) if clean else None,
            "fork_modeled_positive_net": positive_net,
            "note": "Local EVM fork execution and modeled after-gas, not an actual chain inclusion or realized PnL"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, default=Path("candidates.jsonl"))
    p.add_argument("--output", type=Path, default=Path("fork_results.jsonl"))
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--timeout", type=int, default=90)
    p.add_argument("--rpc", default=os.environ.get("BASE_RPC_URL", ""))
    args = p.parse_args(argv)
    if not args.rpc.startswith("https://"):
        p.error("An HTTPS archive-capable Base RPC is required")
    if not shutil.which("forge"):
        p.error("Foundry forge not found")
    if not (1 <= args.limit <= 100 and 10 <= args.timeout <= 600):
        p.error("Invalid --limit or --timeout")
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if r.get("status") == "QUOTED_NOT_FORK_VERIFIED"]
    rows.sort(key=lambda x: int(x.get("estimated_net_usdc", 0)), reverse=True)
    rows = rows[:args.limit]
    outcomes = []
    PROOF.parent.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for candidate in rows:
        PROOF.unlink(missing_ok=True)
        env = os.environ.copy()
        env["BASE_RPC_URL"] = args.rpc
        # Existing ForkProof reads an input file through Foundry cheatcodes.
        source = Path("validation/fork-candidate.json")
        source.write_text(json.dumps(candidate), encoding="utf8")
        env["CANDIDATE_PATH"] = str(source)
        proof = {}
        try:
            run = subprocess.run(
                ["forge", "script", "script/ForkProof.s.sol:ForkProof", "-vvv"],
                env=env, capture_output=True, text=True, timeout=args.timeout, check=False)
            evidence = (run.stdout + "\n" + run.stderr)[-3500:]
            if run.returncode == 0 and PROOF.is_file():
                proof = verify_fork_proof(candidate, json.loads(PROOF.read_text()))
                status = proof["status"]
            elif run.returncode == 0:
                status = "INFRASTRUCTURE_OR_PROOF_MISSING"
                evidence += "\nForge reported exit 0 but produced no structured execution proof."
            elif any(x in evidence.lower() for x in ("revert", "insufficientprofit", "execution reverted")):
                status = "FORK_REVERT"
            else:
                status = "ENVIRONMENT_UNAVAILABLE"
        except (subprocess.TimeoutExpired, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            status = "ENVIRONMENT_UNAVAILABLE"
            evidence = f"{type(exc).__name__}: {str(exc)[:300]}"
        finally:
            PROOF.unlink(missing_ok=True)
        item = {"block": int(candidate["block"]), "first_pool": candidate["first_pool"],
                "second_pool": candidate["second_pool"], "amount_usdc": int(candidate["amount_usdc"]),
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "status": status, "diagnostic_tail": evidence[-1500:], **proof}
        outcomes.append(item)
        with args.output.open("a", encoding="utf8") as file:
            file.write(json.dumps(item)+"\n")
        print(f"{candidate['block']} {status} "
              f"modeled_net={proof.get('modeled_net_after_gas_usdc_units', 'unknown')} units")
    print(json.dumps(summarize_results(outcomes), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
