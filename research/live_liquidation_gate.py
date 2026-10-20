"""Base Aave live readiness: single pinned block, read-only, FAIL-CLOSED.

This module intentionally does not construct, sign, broadcast or recommend an
executable liquidation transaction. The account-level health factor alone
does NOT prove specific WETH collateral and USDC debt availability.

Operator-capable live trading remains prohibited until an independent
same-state simulation demonstrates liquidationCall, actual swap, flash
repayment and accurate Base L1+L2 gas accounting.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal, ROUND_CEILING

from bot.abi import encode, result_uint, result_address
from bot.networks import (
    BASE_CHAIN_ID, AAVE_PROVIDER, USDC, WETH,
    EXCHANGES,
)
from bot.rpc import RPC, RpcError
from bot.searcher import quoter_at

ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
WAD = 10**18
USDC_UNIT = 10**6

@dataclass(frozen=True)
class RiskPolicy:
    max_loan_usdc: int = 5_000 * USDC_UNIT
    min_net_usdc: int = 10 * USDC_UNIT
    max_single_revert_loss_usdc: int = 2 * USDC_UNIT
    max_daily_loss_usdc: int = 5 * USDC_UNIT
    max_total_loss_usdc: int = 20 * USDC_UNIT
    max_gas_price_gwei: int = 5
    max_head_age_s: int = 30
    max_quote_slippage_bps: int = 30
    # Must be true AFTER byte-accurate contract call on an installed executor:
    require_same_block_flash_liquidation_simulation: bool = True

def reserve_state(bits: int, role: str) -> dict:
    if bits < 0:
        raise ValueError("Invalid reserve config")
    return {
        "role": role,
        "active": bool((bits >> 56) & 1),
        "frozen": bool((bits >> 57) & 1),
        "borrowing_enabled": bool((bits >> 58) & 1),
        "paused": bool((bits >> 60) & 1),
        "flashloan_enabled": bool((bits >> 63) & 1),
        "liquidation_bonus_bps_raw": (bits >> 32) & 0xFFFF,
        "liquidation_protocol_fee_bps_raw": (bits >> 152) & 0xFFFF,
    }

def account_state(raw: str) -> dict:
    data = [result_uint(raw,i) for i in range(6)]
    collateral, debt, _available, threshold, ltv, health = data
    if threshold > 10000 or ltv > 10000:
        raise ValueError("Invalid Aave account data")
    return {
        "health_factor_wad": str(health),
        "health_factor": str(Decimal(health)/WAD) if debt>0 else None,
        "has_debt": debt > 0,
        "health_factor_below_one": debt > 0 and health < WAD,
        "has_collateral": collateral > 0,
        "total_debt_base_units": str(debt),
        "total_collateral_base_units": str(collateral),
    }

def evaluate_financial_gate(*, borrowed_units: int, expected_quote_units: int,
                            flash_premium_bps: int, gas_used_limit: int,
                            max_fee_per_gas_wei: int, eth_price_usdc_per_eth: Decimal,
                            expected_seized_weth_verified: bool,
                            exact_same_state_simulated: bool,
                            l1_fee_bound_usdc: int | None,
                            daily_remaining_loss_usdc: int,
                            total_remaining_loss_usdc: int,
                            policy: RiskPolicy = RiskPolicy()) -> dict:
    """All monetary integers are raw 6-decimal USDC; reference ETH USD = decimal.

    Expected quote is hypothetical output, not a guaranteed fill. Without a
    real successful same-state flash liquidation and verified seized WETH this
    cannot approve live execution regardless of positive estimated PnL.
    """
    values=[borrowed_units,expected_quote_units,flash_premium_bps,gas_used_limit,
            max_fee_per_gas_wei,daily_remaining_loss_usdc,total_remaining_loss_usdc]
    if (any(x<0 for x in values) or borrowed_units==0 or gas_used_limit==0 or
        max_fee_per_gas_wei==0 or eth_price_usdc_per_eth<=0 or
        flash_premium_bps>10000):
        raise ValueError("Invalid proposed trade or cost")
    premium=(borrowed_units*flash_premium_bps+9999)//10000
    stressed=expected_quote_units*(10000-policy.max_quote_slippage_bps)//10000
    # Use Decimal until final ceil. Reject optimistic floor truncation.
    max_l2_gas_usdc=int(
        (Decimal(gas_used_limit)*Decimal(max_fee_per_gas_wei)/
         Decimal(10**18)*eth_price_usdc_per_eth*USDC_UNIT
        ).to_integral_value(rounding=ROUND_CEILING))
    l1_known=l1_fee_bound_usdc is not None and l1_fee_bound_usdc>=0
    l1_fee=max(l1_fee_bound_usdc or 0,0)
    worst_fee_bound=max_l2_gas_usdc+l1_fee if l1_known else None
    modeled_net=stressed-borrowed_units-premium-(worst_fee_bound or 0)
    reasons=[]
    if borrowed_units>policy.max_loan_usdc: reasons.append("MAX_BORROW_EXCEEDED")
    if max_fee_per_gas_wei>policy.max_gas_price_gwei*10**9: reasons.append("GAS_PRICE_CAP_EXCEEDED")
    if not l1_known: reasons.append("BASE_L1_FEE_BOUND_UNAVAILABLE")
    if worst_fee_bound is not None and worst_fee_bound>policy.max_single_revert_loss_usdc:
        reasons.append("SINGLE_FAILURE_BUDGET_EXCEEDED")
    if worst_fee_bound is not None and worst_fee_bound>daily_remaining_loss_usdc:
        reasons.append("DAILY_FAILURE_BUDGET_EXCEEDED")
    if worst_fee_bound is not None and worst_fee_bound>total_remaining_loss_usdc:
        reasons.append("TOTAL_FAILURE_BUDGET_EXCEEDED")
    if modeled_net < policy.min_net_usdc: reasons.append("NET_AFTER_STRESS_BELOW_MINIMUM")
    if not expected_seized_weth_verified: reasons.append("COLLATERAL_SEIZURE_NOT_SIMULATED")
    if not exact_same_state_simulated: reasons.append("FLASH_LOAN_EXECUTION_NOT_SIMULATED")
    # Important: even a fully passing cost gate doesn't grant permission to
    # broadcast; this module is strictly read-only.
    return {
        "status":"SHADOW_MODEL_GATE_ONLY",
        "meets_modeled_cost_constraints":not reasons,
        "reasons":reasons,
        "flash_premium_usdc_units":premium,
        "stressed_dex_output_usdc_units":stressed,
        "max_l2_gas_usdc_units":max_l2_gas_usdc,
        "base_l1_fee_usdc_bound_units":l1_fee_bound_usdc,
        "max_total_gas_usdc_units":worst_fee_bound,
        "modeled_net_after_stress_usdc_units":modeled_net if l1_known else None,
        "broadcast_authorized":False,
        "live_profit_verified":False,
    }

def get_user_asset_balances(rpc: RPC, provider: str, account: str, block_tag: str) -> dict:
    """Read asset-specific Aave balances from the PoolDataProvider, not account HF.

    IPoolDataProvider.getUserReserveData returns currentATokenBalance,
    currentStableDebt, currentVariableDebt, principalStableDebt,
    scaledVariableDebt, stableBorrowRate, liquidityRate,
    stableRateLastUpdated and usageAsCollateralEnabled.
    """
    if not (ADDRESS.fullmatch(provider) and ADDRESS.fullmatch(account)):
        raise ValueError("Invalid data provider/account")
    raw_usdc=rpc.eth_call(provider,encode("getUserReserveData(address,address)",USDC,account),block_tag)
    raw_weth=rpc.eth_call(provider,encode("getUserReserveData(address,address)",WETH,account),block_tag)
    usdc_debt=result_uint(raw_usdc,1)+result_uint(raw_usdc,2)
    weth_atokens=result_uint(raw_weth,0)
    collateral=result_uint(raw_weth,8)
    if collateral not in (0,1):
        raise ValueError("Invalid Aave collateral flag")
    return {
        "verified_usdc_total_debt_units":str(usdc_debt),
        "verified_weth_atoken_balance_units":str(weth_atokens),
        "verified_weth_enabled_as_collateral":bool(collateral),
        "USDC_debt_token_balance_verified":True,
        "WETH_collateral_aToken_balance_verified":True,
        "USDC_WETH_pair_supported_for_liquidation":bool(usdc_debt>0 and weth_atokens>0 and collateral==1),
    }

def get_snapshot(rpc: RPC, candidate_addresses: list[str], *,
                 policy: RiskPolicy = RiskPolicy()) -> dict:
    if rpc.chain_id()!=BASE_CHAIN_ID: raise RpcError("Not Base mainnet")
    head=rpc.call("eth_getBlockByNumber",["latest",False])
    if not isinstance(head,dict) or not head.get("number") or not head.get("timestamp"):
        raise RpcError("Latest block incomplete")
    at=head["number"]
    block=int(at,16)
    timestamp=int(head["timestamp"],16)
    age_s=int(time.time())-timestamp
    if age_s < -8 or age_s>policy.max_head_age_s:
        raise RpcError(f"STALE_OR_INVALID_BLOCK_TIME:{age_s}")
    pool=result_address(rpc.eth_call(AAVE_PROVIDER,encode("getPool()"),at))
    if int(pool,16)==0: raise RpcError("No Aave pool")
    data_provider=None
    try:
        data_provider=result_address(rpc.eth_call(AAVE_PROVIDER,encode("getPoolDataProvider()"),at))
        if int(data_provider,16)==0:
            data_provider=None
    except (RpcError,ValueError,TypeError):
        data_provider=None
    premium=result_uint(rpc.eth_call(pool,encode("FLASHLOAN_PREMIUM_TOTAL()"),at))
    if not 0<=premium<=1000: raise RpcError("Invalid Aave flash premium")
    configs={}
    for label,token in (("USDC",USDC),("WETH",WETH)):
        raw=result_uint(rpc.eth_call(pool,encode("getConfiguration(address)",token),at))
        configs[label]=reserve_state(raw,label)
    reserve_risks=[]
    if not configs["USDC"]["active"] or configs["USDC"]["paused"] or not configs["USDC"]["flashloan_enabled"]:
        reserve_risks.append("USDC_FLASH_LOAN_DISABLED_OR_RESERVE_PAUSED")
    if not configs["WETH"]["active"] or configs["WETH"]["paused"]:
        reserve_risks.append("WETH_COLLATERAL_DISABLED_OR_PAUSED")
    addresses=[]
    for a in candidate_addresses:
        if ADDRESS.fullmatch(a) and a.lower() not in [x.lower() for x in addresses]:
            addresses.append(a)
    results=[]
    for address in addresses[:40]:
        try:
            state=account_state(rpc.eth_call(pool,encode("getUserAccountData(address)",address),at))
            row={"address":address,**state,
                "USDC_debt_token_balance_verified":False,
                "WETH_collateral_aToken_balance_verified":False,
                "single_transaction_simulated":False,
                "live_executable":False,
                "reason":"Exact liquidationCall, liquidation bonus, seize amount, router execution, loan repayment and Base L1+L2 Gas are not simulated."}
            if state["has_debt"] and int(state["health_factor_wad"]) < 105*WAD//100 and data_provider:
                try:
                    row.update(get_user_asset_balances(rpc,data_provider,address,at))
                except (RpcError,ValueError,TypeError):
                    row["asset_balances_status"]="DATA_PROVIDER_QUERY_UNAVAILABLE"
            elif not data_provider:
                row["asset_balances_status"]="POOL_DATA_PROVIDER_UNAVAILABLE"
            results.append(row)
        except (RpcError,ValueError,TypeError):
            results.append({"address":address,"status":"ACCOUNT_READ_FAILED",
                            "live_executable":False})
    return {
        "status":"MAINNET_SHADOW_READ_ONLY",
        "chain_id":BASE_CHAIN_ID,"block":block,"block_hash":head.get("hash"),
        "block_age_seconds":age_s,
        "aave_pool":pool,"aave_pool_data_provider":data_provider,"flash_premium_bps":premium,
        "reserves":configs,"reserve_risk_flags":reserve_risks,
        "addresses_considered":len(addresses[:40]),
        "account_reads_complete":sum(x.get("health_factor_wad") is not None for x in results),
        "account_level_underwater":sum(x.get("health_factor_below_one",False) for x in results),
        "asset_specific_pair_verified":sum(bool(x.get("USDC_WETH_pair_supported_for_liquidation")) for x in results),
        "underwater_and_USDC_WETH_pair_verified":sum(
            bool(x.get("health_factor_below_one") and x.get("USDC_WETH_pair_supported_for_liquidation"))
            for x in results),
        "accounts":results,
        "liquidation_candidates_verified":0,
        "onchain_same_state_successful_liquidations":0,
        "live_trading_enabled":False,
        "no_wallet_or_private_key_required":True,
        "limitation":"Asset-specific debt/collateral checked for near-liquidation accounts IF provider supports it; does not prove liquidatable collateral amount, Aave close-factor, executable DEX swap, exact flash-loan path, accurate Base L1 fee or transaction inclusion.",
    }

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",type=Path,default=Path("validation/p0/health/borrower-health-snapshot.json"))
    p.add_argument("--out",type=Path,default=Path("validation/shadow-live"))
    p.add_argument("--rpc",default="https://mainnet.base.org")
    args=p.parse_args(argv)
    args.out.mkdir(parents=True,exist_ok=True)
    addresses=[]
    if args.source.exists():
        src=json.loads(args.source.read_text())
        addresses=[x["borrower"] for x in src.get("sample",[]) if isinstance(x,dict) and x.get("borrower")]
    try:
        result=get_snapshot(RPC(args.rpc,timeout=8),addresses)
    except (RpcError,ValueError,OSError) as exc:
        result={"status":"MAINNET_SHADOW_UNAVAILABLE",
                "error_type":type(exc).__name__,"live_trading_enabled":False,
                "account_reads_complete":0}
    result["observed_at_utc"]=datetime.now(timezone.utc).isoformat()
    (args.out/"readiness.json").write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!="accounts"},indent=2))
    return 0 if result["status"]=="MAINNET_SHADOW_READ_ONLY" else 2

if __name__=="__main__":
    raise SystemExit(main())
