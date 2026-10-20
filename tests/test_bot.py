import json
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bot.abi import (keccak256, selector, encode, result_uint, result_address,
                     START_SIGNATURE, QUOTER_SIGNATURE)
from bot.policy import reserve_budget, LimitExceeded
from bot.preflight import load_candidate, preflight
from bot.searcher import (ceil_div, discover, quoter_at, Candidate, USDC_UNIT)
from bot.networks import AAVE_PROVIDER, EXCHANGES, USDC, WETH, BASE_CHAIN_ID
from bot.rpc import RpcError


def ret(n):
    return "0x" + f"{n:064x}"


class FakeRPC:
    """Same-block V3 quoting mock. Router fee-tier values are deterministic only."""
    def __init__(self, *, spread=True, chain=BASE_CHAIN_ID):
        self.spread = spread
        self.chain = chain
        self.block = 100
        self.calls = []

    def chain_id(self): return self.chain
    def block_number(self): return self.block
    def gas_price(self): return 1_000_000
    def estimate_gas(self, tx, block="latest"):
        return int(self.call("eth_estimateGas", [tx, block]), 16)

    def eth_call(self, to, data, block, sender=None):
        self.calls.append((to.lower(), data[:10], block))
        sig = data[2:10]
        if sig == selector("getPool()"):
            return ret(0xA238Dd80C259a72e81d7e4664a9801593F98d1c5)
        if sig == selector("FLASHLOAN_PREMIUM_TOTAL()"):
            return ret(5)
        if sig == selector("getPool(address,address,uint24)"):
            fee = int(data[-64:], 16)
            if fee not in (500, 3000):
                return ret(0)
            pool = (0x1111 if to.lower() == EXCHANGES[0].factory.lower() else 0x2222)
            return ret(pool + fee)
        if sig == selector(QUOTER_SIGNATURE):
            inp = result_address("0x" + data[10:74])
            amount = int(data[10+128:10+192], 16)
            if inp.lower() == USDC.lower():
                # 1 USDC -> 0.0005 WETH
                return ret(amount * 500_000_000) + f"{0:064x}" * 3
            if inp.lower() == WETH.lower():
                price = 2100 if self.spread and to.lower() == EXCHANGES[1].quoter.lower() else 2000
                return ret(amount * price * USDC_UNIT // 10**18) + f"{0:064x}" * 3
            raise AssertionError(inp)
        raise AssertionError(sig)


class AbiTests(unittest.TestCase):
    def test_reference_keccak(self):
        self.assertEqual(keccak256(b"").hex(),
                         "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")
        self.assertEqual(selector("transfer(address,uint256)"), "a9059cbb")
        self.assertEqual(selector(QUOTER_SIGNATURE), "c6a5026a")
        self.assertEqual(selector("getPool(address,address,uint24)"), "1698ee82")

    def test_encode(self):
        result = encode(START_SIGNATURE, 100, 0, 500, 1, 500, 99, 110, 3, 900)
        self.assertEqual(len(result), 2 + 8 + 9*64)
        self.assertEqual(result_uint("0x" + result[10:], 0), 100)
        self.assertRaises(ValueError, encode, "foo()", -1)
        self.assertEqual(result_address(ret(0xABC)), "0x" + "0"*37 + "abc")


class SearchTests(unittest.TestCase):
    def test_bps_roundup(self):
        self.assertEqual(ceil_div(1, 10000), 1)
        self.assertEqual(ceil_div(10000, 10000), 1)

    def test_chain_guard(self):
        with self.assertRaises(RpcError):
            discover(FakeRPC(chain=1), amounts_usdc=[1000*USDC_UNIT])

    def test_same_block_quotes(self):
        rpc = FakeRPC(spread=True)
        data = discover(rpc, amounts_usdc=[1000*USDC_UNIT], min_profit_usdc=2*USDC_UNIT)
        self.assertTrue(data["candidates"])
        self.assertTrue(all(x[2] == "0x64" for x in rpc.calls))
        self.assertEqual(data["premium_bps"], 5)
        for candidate in data["candidates"]:
            self.assertNotEqual(candidate["first_pool"], candidate["second_pool"])
            self.assertGreater(candidate["estimated_net_usdc"], candidate["min_profit_usdc"])
            self.assertEqual(candidate["status"], "QUOTED_NOT_FORK_VERIFIED")

    def test_no_arbitrage_when_flat(self):
        data = discover(FakeRPC(spread=False), amounts_usdc=[1000*USDC_UNIT])
        self.assertFalse(data["candidates"])

    def test_price_failure_fails_closed(self):
        class NoQuote(FakeRPC):
            def eth_call(self, to, data, block, sender=None):
                if data[2:10] == selector(QUOTER_SIGNATURE):
                    raise RpcError("no quote")
                return super().eth_call(to, data, block, sender)
        with self.assertRaises(RpcError):
            discover(NoQuote(), amounts_usdc=[1000*USDC_UNIT])


class RiskTests(unittest.TestCase):
    def test_atomic_budget_limits(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "budget_state.json"
            reserve_budget(path, 300_000, daily_usdc=500_000, lifetime_usdc=700_000)
            with self.assertRaises(LimitExceeded):
                reserve_budget(path, 300_000, daily_usdc=500_000, lifetime_usdc=700_000)
            row = json.loads(path.read_text())
            self.assertEqual(row["lifetime_reserved_usdc"], 300_000)

    def test_load_rejects_untrusted_candidate(self):
        with TemporaryDirectory() as tmp:
            file = Path(tmp) / "candidate.json"
            file.write_text(json.dumps({"status":"LIVE_READY","estimated_net_usdc":123}))
            with self.assertRaises(ValueError):
                load_candidate(file)

    def test_preflight_rejects_stale(self):
        with self.assertRaises(RpcError):
            preflight(FakeRPC(), {"block":90}, "0x" + "1"*40, "0x" + "2"*40)

    def test_preflight_rejects_high_gas(self):
        class HighGas(FakeRPC):
            def gas_price(self): return 10**10
        with self.assertRaises(RpcError):
            preflight(HighGas(), {"block":100,"amount_usdc":10*USDC_UNIT},
                      "0x" + "1"*40, "0x" + "2"*40)


if __name__ == "__main__": unittest.main()

class AnalyticsTests(unittest.TestCase):
    def test_persistence_only_sampled_blocks(self):
        from bot.analytics import analyze
        rows = [{"status": "QUOTED_NOT_FORK_VERIFIED", "block": i,
                 "first_pool": "0x1", "second_pool": "0x2", "estimated_net_usdc": 100000}
                for i in (10, 11, 11, 14)]
        r = analyze(rows)
        self.assertEqual(r["unique_observed_blocks"], 3)
        self.assertEqual(r["routes"][0]["longest_consecutive_sampled_block_streak"], 2)
        self.assertEqual(r["routes"][0]["positive_sampled_blocks"], 3)


class PreflightTests(unittest.TestCase):
    def test_preflight_succeeds_only_after_chain_simulation(self):
        class ExecutionRPC(FakeRPC):
            def call(self, method, params):
                if method == "eth_call": return "0x"
                if method == "eth_estimateGas": return hex(300000)
                if method == "eth_getBalance": return hex(10**17)
                raise AssertionError(method)
        cand = {"block": 100, "amount_usdc": 1000*USDC_UNIT,
                "first_dex": 0, "first_fee": 500, "second_dex": 1, "second_fee": 500,
                "min_weth": 1000, "min_usdc": 1001000000, "min_profit_usdc": 2*USDC_UNIT,
                "estimated_gas_usdc": 100_000, "model_gas_units": 850000,
                "gas_price_wei": 1000000}
        result = preflight(ExecutionRPC(), cand, "0x" + "1"*40, "0x" + "2"*40)
        self.assertTrue(result["ready_for_manual_decision"])
        self.assertEqual(result["gas_limit"], 390000)

    def test_rpc_revert_blocks_execution(self):
        class RevertingRPC(FakeRPC):
            def call(self, method, params):
                if method == "eth_call": raise RpcError("revert")
                raise AssertionError(method)
        cand = {"block": 100, "amount_usdc": 1000*USDC_UNIT,
                "first_dex": 0, "first_fee": 500, "second_dex": 1, "second_fee": 500,
                "min_weth": 1000, "min_usdc": 1001000000, "min_profit_usdc": 2*USDC_UNIT,
                "estimated_gas_usdc": 100_000, "model_gas_units": 850000,
                "gas_price_wei": 1000000}
        with self.assertRaises(RpcError):
            preflight(RevertingRPC(), cand, "0x" + "1"*40, "0x" + "2"*40)


class RpcBatchTests(unittest.TestCase):
    def test_batch_response_order_and_call_revert(self):
        from unittest.mock import patch
        from bot.rpc import RPC
        class Response:
            def __init__(self, contents): self.contents = contents
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return self.contents
        def fake_urlopen(req, timeout):
            sent = json.loads(req.data)
            messages = []
            for item in reversed(sent):
                if item["params"][0]["to"] == "0xbad":
                    messages.append({"jsonrpc":"2.0", "id":item["id"],
                                     "error":{"code":-32000, "message":"reverted"}})
                else:
                    messages.append({"jsonrpc":"2.0", "id":item["id"],"result":"0x1234"})
            return Response(json.dumps(messages).encode())
        with patch("bot.rpc.urllib.request.urlopen", side_effect=fake_urlopen):
            rpc = RPC("https://rpc.example")
            out = rpc.batch_eth_call([("0x1", "0xab", "0x64"),
                                       ("0xbad", "0xab", "0x64"),
                                       ("0x2", "0xab", "0x64")], chunk_size=2)
        self.assertEqual(out, ["0x1234", None, "0x1234"])


class ForkReplayTests(unittest.TestCase):
    def test_fork_outcome_metrics(self):
        from bot.replay import summarize_results
        r = summarize_results([{"status":"FORK_EXECUTION_SUCCESS"},
                               {"status":"FORK_REVERT"},
                               {"status":"ENVIRONMENT_UNAVAILABLE"}])
        self.assertEqual(r["fork_attempts_excluding_infrastructure_errors"], 2)
        self.assertEqual(r["fork_success_rate"], 0.5)
