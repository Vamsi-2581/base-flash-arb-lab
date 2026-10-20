import unittest
from bot.networks import USDC,WETH
from research.aave_receipt_quote_screen import eligible_for_usdc_weth_screen, grade_receipt

class ReceiptScreenTests(unittest.TestCase):
    def test_excludes_atokens_and_unsupported_debt_collateral(self):
        sample={"debt_asset":USDC,"collateral_asset":WETH,
                "received_a_tokens":False,"debt_repaid_units":1_000_000_000,
                "collateral_received_units":500_000_000_000_000_000}
        self.assertTrue(eligible_for_usdc_weth_screen(sample))
        self.assertFalse(eligible_for_usdc_weth_screen({**sample,"received_a_tokens":True}))
        self.assertFalse(eligible_for_usdc_weth_screen({**sample,"debt_asset":WETH}))

    def test_full_costs_and_slippage_stress(self):
        v=grade_receipt(1_000_000_000,1_020_000_000,5,1_000_000,15)
        self.assertEqual(v["flash_premium_usdc_units"],500_000)
        self.assertEqual(v["stressed_output_usdc_units"],1_018_470_000)
        self.assertEqual(v["modeled_net_usdc_units"],16_970_000)
        self.assertTrue(v["hypothetically_positive"])
        self.assertNotIn("FORK_EXECUTION_SUCCESS",v["status"])

    def test_not_profitable_when_collateral_underwater(self):
        v=grade_receipt(1_000_000_000,980_000_000,5,1_000_000,15)
        self.assertLess(v["modeled_net_usdc_units"],0)

    def test_bad_inputs(self):
        for params in ((0,1000,5,100,5),(1000,0,5,100,5),
                       (1000,1000,-1,100,5),(1000,1000,5,100,10000)):
            with self.assertRaises(ValueError):
                grade_receipt(*params)

if __name__=="__main__":
    unittest.main()
