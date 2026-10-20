import unittest
from unittest.mock import patch
from bot.networks import USDC,WETH
from research.liquidation_preflight import score_one,WAD

class MockArchive:
    def __init__(self,hf):
        self.hf=hf
    def eth_call(self,to,data,at):
        if data.startswith("0x"):
            return "0x"+"".join(f"{x:064x}" for x in
               (100000000,90000000,0,8000,6000,self.hf))
        raise ValueError("bad call")
class LiquidationPreflightTests(unittest.TestCase):
    def seed(self):
        return {"block":52600000,"borrower":"0x"+"a"*40,
                "source_tx_hash":"0x"+"d"*64,
                "amount_usdc":1_000_000_000,"actual_historical_weth_liquidated_units":5*10**17}
    def test_underwater_gate_without_fabricated_execution(self):
        with patch("research.liquidation_preflight.fetch_aave_pool_and_premium",
                   return_value=("0x"+"b"*40,5)),\
             patch("research.liquidation_preflight.quoter_at",return_value=1_100_000_000):
            r=score_one(MockArchive(WAD-1),self.seed())
        self.assertEqual(r["status"],"B_MINUS_ONE_QUOTE_SCREEN_ONLY")
        self.assertEqual(r["premium_usdc_units"],500000)
        self.assertEqual(r["modeled_potential_surplus_before_gas"],99_500_000)
        self.assertFalse(r["full_fork_executed"])
        self.assertFalse(r["exact_pre_transaction_state"])
    def test_safe_health_factor_cannot_pass_liquidation_screen(self):
        with patch("research.liquidation_preflight.fetch_aave_pool_and_premium",
                   return_value=("0x"+"b"*40,5)),\
             patch("research.liquidation_preflight.quoter_at") as quote:
            r=score_one(MockArchive(WAD),self.seed())
            quote.assert_not_called()
        self.assertEqual(r["status"],"BORROWER_NOT_UNDERWATER_AT_B_MINUS_ONE")
    def test_invalid_borrow(self):
        with self.assertRaises(ValueError):
            score_one(MockArchive(WAD),{**self.seed(),"block":0})
if __name__=="__main__":
    unittest.main()
