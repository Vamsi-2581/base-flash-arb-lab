import unittest
from research.live_shadow_candidates import choose
from bot.networks import USDC, WETH

class CandidateTests(unittest.TestCase):
    def fixture(self):
        return {
            "status":"MAINNET_SHADOW_READ_ONLY","chain_id":8453,"block":56666666,
            "block_age_seconds":3,"block_hash":"0x"+"f"*64,
            "reserve_risk_flags":[],"aave_pool_data_provider":"0x"+"c"*40,
            "accounts":[{"address":"0x"+"b"*40,
              "health_factor_below_one":True,
              "health_factor_wad":str(99*10**16),
              "USDC_debt_token_balance_verified":True,
              "WETH_collateral_aToken_balance_verified":True,
              "USDC_WETH_pair_supported_for_liquidation":True,
              "verified_usdc_total_debt_units":str(20_000_000_000),
              "verified_weth_atoken_balance_units":str(10**18)}]
        }
    def test_positive_input_only_requires_all_current_gates(self):
        candidates=choose(self.fixture())
        self.assertEqual(len(candidates),1)
        self.assertEqual(candidates[0]["borrowed_usdc_units"],5_000_000_000)
        self.assertEqual(candidates[0]["snapshot_block"],56666666)
        self.assertFalse(candidates[0]["live_approved"])
        self.assertFalse(candidates[0]["fork_simulated"])
        self.assertFalse(candidates[0]["transaction_broadcast"])
    def test_stale_report_or_non_base_reject(self):
        for attrs in ({"block_age_seconds":31},{"chain_id":1},
                      {"reserve_risk_flags":["WETH_PAUSED"]},
                      {"aave_pool_data_provider":None}):
            self.assertEqual(choose({**self.fixture(),**attrs}),[])
    def test_health_or_asset_data_missing_reject(self):
        for attrs in ({"health_factor_below_one":False},
                      {"USDC_WETH_pair_supported_for_liquidation":False},
                      {"USDC_debt_token_balance_verified":False},
                      {"WETH_collateral_aToken_balance_verified":False},
                      {"health_factor_wad":str(10**18)},
                      {"verified_usdc_total_debt_units":"0"}):
            sample=self.fixture()
            sample["accounts"]=[{**sample["accounts"][0],**attrs}]
            self.assertEqual(choose(sample),[])
    def test_under_100_usdc_sample_rejected(self):
        sample=self.fixture()
        sample["accounts"][0]["verified_usdc_total_debt_units"]=str(200_000_000)
        self.assertEqual(choose(sample),[])
if __name__=="__main__":
    unittest.main()
