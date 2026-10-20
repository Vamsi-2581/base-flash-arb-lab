import unittest
from decimal import Decimal, getcontext

from scripts.event_microstructure import (
    SWAP_TOPIC, Q192, analyze_swaps, price_usdc_per_weth,
    decode_swap_price, implied_roundtrip_spread)
from bot.networks import USDC, WETH
getcontext().prec=74
D=Decimal

def sqrtp_for_human_price(price: str, token0: str) -> int:
    p=D(price)
    raw=(p / D(10**12) if token0.lower()==WETH.lower()
         else D(10**12) / p)
    return int(raw.sqrt()*D(2**96))

def make_pool(addr,price,token0=WETH):
    return {"pool":addr.lower(),"token0":token0,"fee_ppm":500,
            "before_price_usdc_per_weth":price,
            "before_sqrt_price_x96":sqrtp_for_human_price(price,token0)}
def make_log(pool,txhash,tx_index,log_index,price,token0=WETH):
    sqrt=sqrtp_for_human_price(price,token0)
    words=[12,34,sqrt,100000000000,0]
    return {"address":pool,"topics":[SWAP_TOPIC],
            "transactionHash":txhash,"transactionIndex":hex(tx_index),
            "logIndex":hex(log_index),
            "data":"0x"+"".join(f"{n:064x}" for n in words),
            "removed":False}

class PriceMathTests(unittest.TestCase):
    def test_both_token_orders_are_identical_for_2400_usdc_per_weth(self):
        for order in (USDC,WETH):
            sqrt=sqrtp_for_human_price("2400",order)
            actual=price_usdc_per_weth(sqrt,order)
            self.assertLess(abs(actual-D("2400")) / D("2400"), D("1e-15"))

    def test_bad_input_and_truncated_event_rejected(self):
        with self.assertRaises(ValueError): price_usdc_per_weth(0,WETH)
        with self.assertRaises(ValueError): decode_swap_price("0x1")
        with self.assertRaises(ValueError): decode_swap_price("0x"+"f"*320)

    def test_roundtrip_cost_includes_both_dex_fees_and_loan_and_gas(self):
        p={"p1":D("2400"),"p2":D("2400")}
        cost=implied_roundtrip_spread(p,{"p1":500,"p2":500})
        self.assertLess(D(cost["spot_model_usdc"]),0)
        p["p2"]=D("2410")
        profitable=implied_roundtrip_spread(p,{"p1":500,"p2":500})
        self.assertGreater(D(profitable["spot_model_usdc"]),0)
        self.assertEqual(profitable["buy_pool"],"p1")
        self.assertEqual(profitable["sell_pool"],"p2")
        self.assertGreater(D(profitable["pre_fee_spread_bps"]), D("40"))
        self.assertGreater(D(profitable["break_even_required_spread_bps"]), D("16"))
        self.assertGreater(D(profitable["excess_spread_bps"]), D("20"))

class SwapEventsTests(unittest.TestCase):
    def test_evaluate_only_after_whole_transaction(self):
        a,b="0x"+"a"*40,"0x"+"b"*40
        pools=[make_pool(a,"2400"),make_pool(b,"2400")]
        # Same tx changes both pools to 2450, no opportunity to insert after first log.
        logs=[
            make_log(a,"0x"+"1"*64,0,1,"2450"),
            make_log(b,"0x"+"1"*64,0,2,"2450"),
            make_log(a,"0x"+"2"*64,1,3,"2470")
        ]
        result=analyze_swaps(pools,logs)
        self.assertEqual(result["distinct_transactions_with_tracked_swaps"],2)
        self.assertFalse(result["transaction_boundary_snapshots"][0]["preliminary_positive"])
        self.assertTrue(result["transaction_boundary_snapshots"][1]["preliminary_positive"])
        self.assertEqual(result["positive_spot_mispricing_transaction_boundaries"],1)
        self.assertEqual(result["signals"][0]["transaction_index"],1)
        self.assertTrue(result["signals"][0]["NOT_EXECUTABLE"])

    def test_log_ordering_and_ignored_events(self):
        a,b="0x"+"a"*40,"0x"+"b"*40
        pools=[make_pool(a,"2400"),make_pool(b,"2400")]
        logs=[make_log(a,"0x"+"1"*64,0,7,"2401"),
              make_log(a,"0x"+"1"*64,0,5,"2450"),
              {"address":"0x"+"c"*40,"topics":[SWAP_TOPIC],"data":"0x"}]
        x=analyze_swaps(pools,logs)
        self.assertEqual(x["ignored_logs"],1)
        self.assertEqual(x["distinct_transactions_with_tracked_swaps"],1)
        self.assertEqual(x["positive_spot_mispricing_transaction_boundaries"],0)

if __name__=="__main__":
    unittest.main()
