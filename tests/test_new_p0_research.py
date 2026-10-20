import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from io import BytesIO

from bot.rpc import RpcError
from research.aave_liquidation_survey import (
    TOPIC, decode_liquidation, topic_address, survey, ASSET_DECIMALS)
from research.uniswapx_orders_readonly import (
    safe_order_identity, summarize_payload, request_orders)
from bot.networks import USDC,WETH

POOL="0x"+"a"*40
BORROWER="0x"+"b"*40
LIQUIDATOR="0x"+"c"*40

def topic(addr):
    return "0x"+f"{int(addr,16):064x}"

def liquidation_event(block=1000):
    return {
        "address":POOL,
        "topics":[TOPIC, topic(WETH), topic(USDC), topic(BORROWER)],
        "data":"0x"+"".join(f"{x:064x}" for x in (1_000_000, 250_000_000_000_000_000, int(LIQUIDATOR,16), 0)),
        "blockNumber":hex(block),
        "transactionHash":"0x"+"d"*64,
        "transactionIndex":"0x1",
        "logIndex":"0x0",
        "removed":False,
    }

class TestAaveLiquidationABI(unittest.TestCase):
    def test_decode_seven_parameter_aave_event(self):
        result=decode_liquidation(liquidation_event(),POOL)
        self.assertEqual(result["debt_repaid_units"],1_000_000)
        self.assertEqual(result["collateral_received_units"],250_000_000_000_000_000)
        self.assertEqual(result["borrower"].lower(),BORROWER)
        self.assertEqual(result["debt_asset"].lower(),USDC.lower())
        self.assertFalse(result["received_a_tokens"])
        self.assertFalse(result["new_strategy_profit_verified"])

    def test_reject_spoofed_pool_wrong_topic_and_noncanonical_bool(self):
        event=liquidation_event()
        with self.assertRaises(ValueError):
            decode_liquidation(event,"0x"+"f"*40)
        with self.assertRaises(ValueError):
            decode_liquidation({**event,"topics":[TOPIC]},POOL)
        altered={**event,"data":event["data"][:-64]+f"{2:064x}"}
        with self.assertRaises(ValueError):
            decode_liquidation(altered,POOL)
        with self.assertRaises(ValueError):
            topic_address("0x"+"f"*64)

    def test_partial_ranges_not_claimed_full(self):
        class Stub:
            def chain_id(self):return 8453
            def eth_call(self,to,data,block):return "0x"+f"{int(POOL,16):064x}"
            def call(self,method,params):
                if method=="eth_getCode":return "0x1234"
                if method=="eth_getLogs":
                    lo=int(params[0]["fromBlock"],16)
                    if lo<995:raise RpcError("unavailable")
                    return [liquidation_event()] if lo<=1000<=int(params[0]["toBlock"],16) else []
                raise AssertionError(method)
        # One near-head window contains a valid log, older one error should
        # remain counted as failed coverage rather than "no liquidations".
        result=survey(Stub(),head=1000,span=10,chunk=5)
        self.assertEqual(result["events_found"],1)
        self.assertEqual(result["range_windows_successful"],1)
        self.assertEqual(result["range_windows_failed"],1)

class TestUniswapXEnvelope(unittest.TestCase):
    def test_open_base_dutch_v3_records_only_status_not_validity(self):
        p={"orders":[{"chainId":8453,"orderStatus":"open","orderType":"Dutch_V3",
                     "orderHash":"0x"+"1"*64,"encodedOrder":"0x1234",
                     "signature":"0x5678"}]}
        result=summarize_payload(p)
        self.assertEqual(result["envelopes_consistent"],1)
        self.assertFalse(result["signature_verified"])
        self.assertFalse(result["fillability_verified"])
        self.assertEqual(result["entries"][0]["hash"],"0x"+"1"*64)
        self.assertNotIn("signature",result["entries"][0])

    def test_wrong_chain_or_status_rejected(self):
        for raw in ({"chainId":1},{"orderStatus":"filled"},{"orderType":"Dutch_V2"}):
            with self.assertRaises(ValueError):
                safe_order_identity(raw)

    def test_no_throttled_retry(self):
        with patch("research.uniswapx_orders_readonly.urllib.request.urlopen",
                   side_effect=HTTPError("u",429,"throttled",{},None)) as mock:
            result=request_orders(limit=5)
        self.assertEqual(result["status"],"RATE_LIMITED_429_NO_RETRY")
        self.assertEqual(mock.call_count,1)

    def test_invalid_response_rejected(self):
        with self.assertRaises(ValueError):
            summarize_payload({"orders":"not-a-list"})

if __name__=="__main__":
    unittest.main()
