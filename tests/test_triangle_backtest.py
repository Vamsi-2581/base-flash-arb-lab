import unittest
from unittest.mock import patch

from scripts.triangle_backtest import pairs, QuoteCache, scan_one
from bot.abi import result_address
from bot.networks import USDC
from bot.extended_research import ASSETS

def ret(n):return "0x"+f"{n:064x}"

class FakeTriangleRpc:
    def gas_price(self):return 6_000_000
    def batch_eth_call(self,jobs,chunk_size=40):
        results=[]
        for to,data,_block in jobs:
            token_in=result_address("0x"+data[10:74]).lower()
            amount=int(data[10+128:10+192],16)
            if token_in==USDC.lower(): out=amount*2
            elif token_in==ASSETS["WETH"].address.lower():out=amount*2
            else:out=amount//3
            results.append(ret(out))
        return results

class TriangleTests(unittest.TestCase):
    def test_asset_pairs_validate(self):
        self.assertEqual(pairs("WETH,cbETH;DAI,USDbC"),[
            ("WETH","cbETH"),("DAI","USDbC")])
        for invalid in ("WETH,WETH","WETH,UNKNOWN","WETH"):
            with self.assertRaises(ValueError):pairs(invalid)

    def test_batch_quote_dedup_and_stage_order(self):
        rpc=FakeTriangleRpc()
        pool={"pool":"0x"+"a"*40,"dex":"Uni","dex_id":0,
              "quoter":"0x"+"b"*40,"fee":500}
        cache=QuoteCache(rpc,"0x123")
        value=cache.get_many([(pool,USDC,ASSETS["WETH"].address,200),
                              (pool,USDC,ASSETS["WETH"].address,200)])
        self.assertEqual(value,[400,400])
        self.assertEqual(cache.attempts,1)

    def test_no_complete_route_must_not_count_as_backtested(self):
        class RevertingQuotes(FakeTriangleRpc):
            def batch_eth_call(self,jobs,chunk_size=40):
                return [None for _ in jobs]
        rpc=RevertingQuotes()
        venue={"pool":"0x"+"a"*40,"dex":"Uni","dex_id":0,
               "quoter":"0x"+"1"*40,"fee":500}
        with patch("scripts.triangle_backtest.pools_for_link",return_value=[venue]),\
             patch("scripts.triangle_backtest.fetch_aave_pool_and_premium",
                   return_value=("0x"+"9"*40,5)),\
             patch("scripts.triangle_backtest.gas_usdc_estimate",return_value=100_000):
            r=scan_one(rpc,100,"WETH","cbETH",[1_000_000_000])
        self.assertEqual(r["status"],"NO_COMPLETE_THREE_LEG_QUOTES")
        self.assertEqual(r["quoted_paths"],0)

    def test_three_hop_quoted_positive_remains_unverified(self):
        rpc=FakeTriangleRpc()
        vals=[
          [{"pool":"0x"+"a"*40,"dex":"Uni","dex_id":0,
            "quoter":"0x"+"1"*40,"fee":500}],
          [{"pool":"0x"+"b"*40,"dex":"Pancake","dex_id":1,
            "quoter":"0x"+"2"*40,"fee":500}],
          [{"pool":"0x"+"c"*40,"dex":"Uni","dex_id":0,
            "quoter":"0x"+"1"*40,"fee":500}],
        ]
        with patch("scripts.triangle_backtest.pools_for_link",side_effect=vals),\
             patch("scripts.triangle_backtest.fetch_aave_pool_and_premium",
                   return_value=("0x"+"9"*40,5)),\
             patch("scripts.triangle_backtest.gas_usdc_estimate",return_value=100_000):
            report=scan_one(rpc,100,"WETH","cbETH",[1_000_000_000])
        self.assertEqual(report["quoted_paths"],1)
        self.assertGreater(report["positive_after_modeled_gas"],0)
        self.assertEqual(report["best"]["status"],"UNEXECUTED_THREE_HOP_RESEARCH_ONLY")
        self.assertGreater(report["best"]["modeled_net_usdc_units"],0)

if __name__=="__main__":
    unittest.main()
