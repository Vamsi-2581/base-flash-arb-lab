import unittest
from unittest.mock import patch

from bot.extended_research import (
    ASSETS, Venue, parse_assets, refine_amounts, discover_venues,
    quote_at, research_snapshot, balanced_routes, USDC_UNIT)
from bot.networks import USDC, WETH
from bot.rpc import RpcError
from scripts.extended_event_backtest import event_blocks

def ret(n):
    return "0x"+f"{n:064x}"

class FakePoolRpc:
    def chain_id(self): return 8453
    def gas_price(self): return 6_000_000
    def eth_call(self, to, data, tag):
        from bot.abi import selector
        if data[:10]==("0x"+selector("getPool(address,address,bool)")):
            return ret(0xAAA)
        if data[:10]==("0x"+selector("getAmountOut(uint256,address)")):
            return ret(321)
        return ret(0)
class ExtendedTests(unittest.TestCase):
    def test_asset_registry_and_rejections(self):
        self.assertEqual(parse_assets("WETH,cbBTC,EURC")[1].decimals,8)
        with self.assertRaises(ValueError): parse_assets("AERO")
        with self.assertRaises(ValueError): parse_assets("WETH,WETH")

    def test_local_log_midpoint_is_bounded(self):
        amounts=[100,1000,10000]
        self.assertEqual(refine_amounts(amounts,1),[316,3162])
        self.assertEqual(refine_amounts(amounts,0),[316])
        self.assertEqual(refine_amounts(amounts,2),[3162])
        with self.assertRaises(ValueError): refine_amounts(amounts,3)

    def test_route_cap_covers_distinct_exchange_directions(self):
        venues=[
            Venue("AeroV","aerodrome","0x"+"a"*40,0,"",2),
            Venue("AeroS","aerodrome","0x"+"b"*40,1,"",2),
            Venue("Uni","v3","0x"+"c"*40,500,"",0),
            Venue("Pancake","v3","0x"+"d"*40,500,"",1),
        ]
        route_list=balanced_routes(venues,8)
        pairs={(a.dex_id,b.dex_id) for a,b in route_list}
        self.assertIn((0,1),pairs)
        self.assertIn((1,0),pairs)
        self.assertIn((2,0),pairs)
        self.assertIn((0,2),pairs)
        self.assertEqual(len(route_list),8)

    def test_aerodrome_quote_real_pool_abi(self):
        rpc=FakePoolRpc()
        v=Venue("Aero","aerodrome","0x"+"a"*40,0,"",2)
        self.assertEqual(quote_at(rpc,v,USDC,WETH,1000,"0x20"),321)
        found,_=discover_venues(rpc,ASSETS["WETH"],"0x20")
        self.assertEqual(len([v for v in found if v.kind=="aerodrome"]),2)

    def test_adaptive_optimization_is_research_only(self):
        rpc=FakePoolRpc()
        first=Venue("UniswapV3","v3","0x"+"a"*40,500,"0x"+"1"*40,0)
        second=Venue("AerodromeVolatile","aerodrome","0x"+"b"*40,0,"",2)
        def fake_quote(_rpc,venue,tin,tout,amount,tag):
            if tin.lower()==USDC.lower():
                return amount * 100
            # first route wins: >2% spread minus diminishing depth
            if venue.name=="AerodromeVolatile":
                input_usdc=amount//100
                return input_usdc + (input_usdc*2)//100 - input_usdc**2//(100000*USDC_UNIT)
            return amount//100
        with patch("bot.extended_research.discover_venues",return_value=([first,second],[])), \
             patch("bot.extended_research.quote_at",side_effect=fake_quote), \
             patch("bot.extended_research.gas_usdc_estimate",return_value=110_000), \
             patch("bot.extended_research.fetch_aave_pool_and_premium",return_value=("0x"+"f"*40,5)):
            report=research_snapshot(rpc,123,[ASSETS["WETH"]],[100*USDC_UNIT,1000*USDC_UNIT,5000*USDC_UNIT],refine_top=1)
        self.assertGreater(report["total_full_two_leg_quotes"],4)
        self.assertGreater(report["asset_summaries"][0]["adaptive_extra_amount_evaluations"],0)
        self.assertTrue(report["positive_quote_rows"])
        self.assertTrue(all(r["execution_status"]=="RESEARCH_QUOTE_NOT_FORK_EXECUTED"
            for r in report["positive_quote_rows"]))
        self.assertTrue(all(not r["can_use_existing_executor"] for r in report["positive_quote_rows"]
            if r["second"]["kind"]=="aerodrome"))

class EventTests(unittest.TestCase):
    def test_exact_before_after_blocks_and_tx_provenance(self):
        events={"selected":[{"block":999,"transaction_hash":"0xabcd"}]}
        rows=event_blocks(events,head=1010)
        self.assertEqual([r["block"] for r in rows],[998,999])
        self.assertEqual(rows[0]["trigger_transaction"],"0xabcd")
        self.assertEqual(rows[1]["side"],"POST_EVENT_BLOCK")

    def test_no_event_falls_back_to_baseline(self):
        x=event_blocks({},head=1010)
        self.assertEqual(x[0]["side"],"BASELINE_NO_EVENT")
        self.assertEqual(x[0]["block"],978)

if __name__=="__main__": unittest.main()
