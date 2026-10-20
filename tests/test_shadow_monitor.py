import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
from scripts.run_shadow_monitor import cycle,run

class ShadowMonitorTests(unittest.TestCase):
    def test_single_cycle_writes_only_readonly_evidence(self):
        with TemporaryDirectory() as tmp, \
             patch("scripts.run_shadow_monitor.inspect",return_value={
                 "borrow_log_blocks_covered":256,
                 "sample":[{"borrower":"0x"+"a"*40}]
             }),\
             patch("scripts.run_shadow_monitor.get_snapshot",return_value={
                 "status":"MAINNET_SHADOW_READ_ONLY","account_reads_complete":1,
                 "account_level_underwater":0,"asset_specific_pair_verified":0,
                 "underwater_and_USDC_WETH_pair_verified":0,"block":1234
             }):
            result=cycle(object(),Path(tmp))
            self.assertEqual(result["status"],"SHADOW_READ_ONLY")
            self.assertFalse(result["live_ready"])
            self.assertFalse(result["broadcast"])
            self.assertTrue((Path(tmp)/"samples.jsonl").is_file())
            self.assertTrue((Path(tmp)/"latest_snapshot.json").is_file())
    def test_read_failure_is_logged_not_proposed_for_trading(self):
        with TemporaryDirectory() as tmp,patch("scripts.run_shadow_monitor.inspect",side_effect=ValueError("bad")):
            r=cycle(object(),Path(tmp))
            self.assertEqual(r["status"],"RPC_OR_CHAIN_DATA_UNAVAILABLE")
            self.assertFalse(r["broadcast"])
    def test_higher_than_one_minute_poll_budget_required(self):
        with self.assertRaises(SystemExit):
            run(["--interval","2","--iterations","1"])
        with self.assertRaises(SystemExit):
            run(["--interval","60","--iterations","-1"])
if __name__=="__main__":
    unittest.main()
