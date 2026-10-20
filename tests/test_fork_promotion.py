import unittest
from unittest.mock import patch

from bot.extended_research import promote_legacy_fork_candidate

def venue(name, pool, dex_id, kind="v3"):
    return {"name": name, "kind":kind, "pool":pool,
            "fee_or_stable":500, "quoter":"0x"+"e"*40, "dex_id":dex_id}

class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.row={"asset":"WETH","block":123,
            "amount_usdc_units":1000_000000,
            "intermediate_units":400_000000000000000,
            "received_usdc_units":1010_000000,
            "modeled_gas_usdc_units":250_000,
            "first":venue("Uni","0x"+"a"*40,0),
            "second":venue("Pancake","0x"+"b"*40,1)}

    def test_compatible_quote_not_marked_as_proven(self):
        with patch("bot.extended_research.quote_at",return_value=1008_000000):
            item=promote_legacy_fork_candidate(None,self.row,flash_bps=5,gas_price_wei=6_000000)
        self.assertIsNotNone(item)
        self.assertEqual(item["status"],"QUOTED_NOT_FORK_VERIFIED")
        self.assertEqual(item["first_dex"],0)
        self.assertEqual(item["second_dex"],1)
        self.assertLessEqual(item["min_usdc"],item["expected_usdc"])
        self.assertGreater(item["estimated_net_usdc"],0)

    def test_new_assets_and_aerodrome_never_promote(self):
        cb=dict(self.row,asset="cbBTC")
        self.assertIsNone(promote_legacy_fork_candidate(None,cb,flash_bps=5,gas_price_wei=6_000000))
        aero=dict(self.row,second=venue("Aero","0x"+"c"*40,2,kind="aerodrome"))
        self.assertIsNone(promote_legacy_fork_candidate(None,aero,flash_bps=5,gas_price_wei=6_000000))

    def test_missing_profit_and_slippage_reject(self):
        bad=dict(self.row,received_usdc_units=1000_000000)
        self.assertIsNone(promote_legacy_fork_candidate(None,bad,flash_bps=5,gas_price_wei=6_000000))
        with patch("bot.extended_research.quote_at",return_value=950_000000):
            self.assertIsNone(promote_legacy_fork_candidate(None,self.row,flash_bps=5,gas_price_wei=6_000000))

if __name__=="__main__":
    unittest.main()
