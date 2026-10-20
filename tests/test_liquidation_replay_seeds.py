import unittest
from bot.networks import USDC,WETH
from research.aave_liquidation_survey import TOPIC
from scripts.liquidation_replay_seeds import receipt_to_seeds,seeds_from_survey,AAVE_POOL

def topic(a):return "0x"+f"{int(a,16):064x}"

class SeedTests(unittest.TestCase):
    def sample(self,status="0x1",atoken=0):
        return {"status":status,"transactionHash":"0x"+"d"*64,
            "blockNumber":"0x20","transactionIndex":"0x02",
            "logs":[{"address":AAVE_POOL,
              "transactionHash":"0x"+"d"*64,
              "blockNumber":"0x20","transactionIndex":"0x02","logIndex":"0x1",
              "topics":[TOPIC,topic(WETH),topic(USDC),topic("0x"+"c"*40)],
              "data":"0x"+"".join(f"{x:064x}" for x in (42_061_674_725,19*10**18,int("0x"+"b"*40,16),atoken))}]}
    def test_successful_real_log_is_seed_not_claimed_profit(self):
        x=receipt_to_seeds(self.sample())
        self.assertEqual(len(x),1)
        self.assertEqual(x[0]["block"],32)
        self.assertEqual(x[0]["fork_pre_transaction_approx_block"],31)
        self.assertEqual(x[0]["amount_usdc"],42_061_674_725)
        self.assertFalse(x[0]["result_is_new_profit"])
    def test_atoken_and_wrong_debt_not_supported(self):
        self.assertEqual(receipt_to_seeds(self.sample(atoken=1)),[])
    def test_recent_aave_survey_seeds_only_canonical_repayable_collateral(self):
        event={"debt_asset":USDC,"collateral_asset":WETH,"received_a_tokens":False,
               "debt_repaid_units":42_061_674_725,
               "collateral_received_units":19_000_000_000_000_000_000,
               "transaction_hash":"0x"+"f"*64,"borrower":"0x"+"d"*40,
               "block":45903542,"transaction_index":3}
        candidates=seeds_from_survey({"pool":AAVE_POOL,"events":[event]})
        self.assertEqual(len(candidates),1)
        self.assertEqual(candidates[0]["fork_pre_transaction_approx_block"],45903541)
        self.assertFalse(candidates[0]["result_is_new_profit"])
        self.assertEqual(seeds_from_survey({"pool":AAVE_POOL,
            "events":[{**event,"received_a_tokens":True}]}),[])
        with self.assertRaises(ValueError):
            seeds_from_survey({"pool":"0x"+"b"*40,"events":[event]})

    def test_failing_receipts_not_validated(self):
        with self.assertRaises(ValueError):receipt_to_seeds(self.sample(status="0x0"))
        with self.assertRaises(ValueError):receipt_to_seeds({})
if __name__=="__main__":unittest.main()
