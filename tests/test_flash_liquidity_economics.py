import unittest
from decimal import Decimal as D
from research.flash_liquidity_economics import Scenario, evaluate

class EconomicsTests(unittest.TestCase):
    def scenario(self, atomic=True, volume="2000000"):
        return Scenario(
            D("1000000"), D(volume), D("5"), D("0.7"), D("5"),
            D("600000"), D("2"), D("8"), D("80"), D("0"), atomic
        )

    def test_break_even(self):
        result = evaluate(self.scenario())
        self.assertEqual(D(result["theoretical_lp_fee_income_usd"]), D("700"))
        self.assertEqual(D(result["flashloan_fee_usd"]), D("500"))
        self.assertEqual(D(result["theoretical_net_usd"]), D("-8"))
        self.assertGreater(D(result["break_even_third_party_swap_volume_usd"]), D("2000000"))

    def test_trade_flow_gate(self):
        result = evaluate(self.scenario(atomic=False))
        self.assertFalse(result["flashloan_eligible"])
        self.assertIsNone(result["flashloan_eligible_net_usd"])

    def test_increased_external_flow(self):
        result = evaluate(self.scenario(volume="4000000"))
        self.assertEqual(D(result["theoretical_net_usd"]), D("692"))

    def test_input_validation(self):
        with self.assertRaises(ValueError):
            evaluate(Scenario(D("0"), D("20"), D("5"), D("0.7"), D("5"), D("0"), D("0"), D("0"), D("0")))

if __name__ == "__main__":
    unittest.main()
