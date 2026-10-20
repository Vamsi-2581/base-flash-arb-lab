"""No-wallet Base mainnet RPC smoke test. Results are snapshots, not PnL."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from bot.abi import encode, result_address
from bot.networks import AAVE_PROVIDER, BASE_CHAIN_ID, EXCHANGES, USDC, WETH
from bot.rpc import RPC, RpcError
from bot.searcher import discover, quoter_at, fetch_aave_pool_and_premium

ENDPOINTS = (
    ("base_official", "https://mainnet.base.org"),
    ("publicnode", "https://base-rpc.publicnode.com"),
    ("drpc", "https://base.drpc.org"),
)

def run_probe(rpc):
    began = time.monotonic()
    chain = rpc.chain_id()
    if chain != BASE_CHAIN_ID:
        raise RpcError("Not Base mainnet (8453)")
    block = rpc.block_number()
    tag = hex(block)
    pool, premium = fetch_aave_pool_and_premium(rpc, tag)
    gas_price = rpc.gas_price()
    if len(rpc.call("eth_getCode", [USDC, tag])) <= 2:
        raise RpcError("USDC contract missing")
    quotes, unavailable = [], []
    for dex in EXCHANGES:
        for fee in (500, 3000):
            try:
                address = result_address(rpc.eth_call(dex.factory,
                    encode("getPool(address,address,uint24)", USDC, WETH, fee), tag))
                if int(address, 16) == 0:
                    unavailable.append(f"{dex.label}/{fee}: no pool")
                    continue
                weth = quoter_at(rpc, dex.quoter, USDC, WETH, 1000*10**6, fee, tag)
                back = quoter_at(rpc, dex.quoter, WETH, USDC, weth, fee, tag)
                quotes.append({"dex": dex.label, "fee": fee, "pool": address,
                    "usdc_in": 1000*10**6, "weth_out": weth, "same_pool_usdc_roundtrip": back})
            except (RpcError, ValueError, TypeError) as exc:
                unavailable.append(f"{dex.label}/{fee}: {type(exc).__name__} at pinned block")
    if not quotes:
        raise RpcError("No complete same-block V3 USDC/WETH quotes")
    endblock = rpc.block_number()
    return {"status": "LIVE_READ_SUCCESS", "chain_id": chain, "block": block,
        "end_block": endblock, "age_blocks": endblock-block,
        "aave_pool": pool, "aave_flash_premium_bps": premium,
        "gas_price_wei": gas_price, "venue_quotes": quotes,
        "incomplete_venues": unavailable, "latency_seconds": round(time.monotonic()-began, 2)}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=Path("validation"))
    p.add_argument("--timeout", type=float, default=9)
    p.add_argument("--full-scan", action="store_true")
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    result = {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
              "chain": "Base 8453", "broadcast": False, "attempts": [],
              "scan_status": "NOT_REQUESTED"}
    selected = None
    for label, url in ENDPOINTS:
        try:
            rpc = RPC(url, timeout=args.timeout)
            probe = run_probe(rpc)
            selected = (label, url, rpc)
            result["probe"] = probe
            result["attempts"].append({"endpoint": label, "status": "OK"})
            break
        except (RpcError, ValueError, OSError) as exc:
            result["attempts"].append({"endpoint": label, "status": "UNAVAILABLE",
                "error": str(exc).replace(url, "[endpoint]")[:200]})
    if selected:
        label, url, rpc = selected
        result["selected_endpoint"] = label
        if args.full_scan:
            result["scan_attempts"] = []
            for scan_label, scan_url in [(label, url)] + [
                item for item in ENDPOINTS if item[0] != label
            ]:
                try:
                    scan_rpc = rpc if scan_label == label else RPC(scan_url, timeout=args.timeout)
                    t = time.monotonic()
                    scan = discover(scan_rpc, amounts_usdc=[1000*10**6, 5000*10**6],
                                    max_candidates=5)
                    result["scan_status"] = "STALE" if scan["stale_when_scan_finished"] else "COMPLETED"
                    result["scan_endpoint"] = scan_label
                    result["scan"] = {k:v for k,v in scan.items() if k != "candidates"}
                    result["scan"]["candidate_count"] = len(scan["candidates"])
                    result["scan"]["elapsed_seconds"] = round(time.monotonic()-t, 2)
                    result["scan_attempts"].append({"endpoint": scan_label,
                                                    "status": result["scan_status"]})
                    if result["scan_status"] == "COMPLETED" and scan["candidates"]:
                        with (args.out/"candidates.jsonl").open("w", encoding="utf8") as file:
                            for item in scan["candidates"]:
                                file.write(json.dumps(item)+"\n")
                    break
                except (RpcError, ValueError, OSError) as exc:
                    result["scan_status"] = "UNAVAILABLE"
                    result["scan_attempts"].append({
                        "endpoint": scan_label, "status": "UNAVAILABLE",
                        "error": str(exc).replace(scan_url, "[endpoint]")[:200]})
        if os.environ.get("GITHUB_ENV"):
            with open(os.environ["GITHUB_ENV"], "a") as f:
                f.write(f"BASE_RPC_URL={url}\n")
                f.write(f"PROBE_BLOCK={max(1, result['probe']['block']-128)}\n")
    (args.out/"report.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0 if selected else 2

if __name__ == "__main__":
    raise SystemExit(main())
