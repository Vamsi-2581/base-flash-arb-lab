import unittest
from bot.networks import USDC,WETH
from research.uniswapx_filled_quote_screen import eligible, grade, route_proxy, NATIVE_ZERO, ERC20_ETH_BRIDGE

class FilledOrderQuoteTests(unittest.TestCase):
    def test_strict_filled_signed_usdc_weth_filter(self):
        candidate={"status":"OFFCHAIN_SIGNATURES_VALID_ALREADY_FILLED",
            "eoa_maker_signature_verified":True,
            "eoa_cosigner_signature_verified":True,
            "historical_terms":{"input_token":USDC,"output_token":WETH,
                "input_amount_units":"1000000000","required_output_units":"400000000000000000",
                "fill_block":52600000}}
        self.assertTrue(eligible(candidate))
        self.assertFalse(eligible({**candidate,"eoa_cosigner_signature_verified":False}))
        self.assertFalse(eligible({**candidate,"status":"REJECTED"}))
        self.assertFalse(eligible({**candidate,"historical_terms":None}))
        self.assertFalse(eligible({**candidate,"historical_terms":
            {**candidate["historical_terms"],"fill_block":0}}))

    def test_native_eth_output_is_weth_proxy_only(self):
        inp,out,native=route_proxy(USDC,NATIVE_ZERO)
        self.assertEqual(inp,USDC)
        self.assertEqual(out,WETH)
        self.assertTrue(native)
        inp,out,native=route_proxy(USDC,WETH)
        self.assertFalse(native)
        for inp in (NATIVE_ZERO,ERC20_ETH_BRIDGE):
            with self.assertRaises(ValueError):
                route_proxy(inp,USDC)

    def test_no_gas_or_execution_profit_claim(self):
        result=grade(1_000_000_000,400_000_000_000_000_000,402_000_000_000_000_000)
        self.assertGreater(result["hypothetical_gross_surplus_output_token_units"],0)
        self.assertFalse(result["execution_and_gas_profit_verified"])
        self.assertTrue(result["stressed_gross_positive_before_gas"])
        almost=grade(1_000_000_000,173991850016495531,174163872890967575)
        self.assertTrue(almost["gross_surplus_positive"])
        self.assertFalse(almost["stressed_gross_positive_before_gas"])
        self.assertLess(almost["stressed_quote_surplus_units"],0)
        self.assertIn("NOT_EXECUTABLE",result["status"])
        with self.assertRaises(ValueError):
            grade(0,1,2)
if __name__=="__main__":unittest.main()
