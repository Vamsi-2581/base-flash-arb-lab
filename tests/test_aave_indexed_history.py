import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from research.aave_indexed_history import classify, survey
from research.aave_liquidation_survey import TOPIC

POOL="0x"+"a"*40

class IndexedHistoryTests(unittest.TestCase):
    def test_no_records_not_equal_to_paid_api_failure(self):
        ok=classify({"status":"0","message":"No records found","result":[]},POOL)
        blocked=classify({"status":"0","message":"NOTOK","result":"API key required"},POOL)
        self.assertEqual(ok["status"],"INDEXER_NO_EVENTS_IN_RESPONDED_QUERY")
        self.assertEqual(blocked["status"],"INDEXER_UNAVAILABLE_OR_ERROR")

    def test_nonmatching_event_is_not_liquidation(self):
        ev={"address":POOL,"topics":["0x"+"1"*64],"data":"0x"}
        result=classify({"status":"1","result":[ev]},POOL)
        self.assertEqual(result["events"],[])
        self.assertEqual(result["invalid_logs"],1)

    def test_http_403_is_unavailable_not_absence_of_opportunity(self):
        with patch("research.aave_indexed_history.urllib.request.urlopen",
                   side_effect=HTTPError("https://base.blockscout.com",403,"denied",{},None)) as mock:
            result=survey(pool=POOL,limit=10)
        self.assertEqual(result["status"],"INDEXER_HTTP_UNAVAILABLE")
        self.assertEqual(result["http_status"],403)
        self.assertFalse(result["full_history_coverage_proven"])
        self.assertEqual(mock.call_count,1)

if __name__=="__main__":
    unittest.main()
