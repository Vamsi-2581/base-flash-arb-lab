import unittest
from bot.abi import encode
from research.aave_borrower_health import TOPIC,extract_borrower,decode_account_data,WAD

POOL="0x"+"a"*40
DEBTOR="0x"+"b"*40

def topic(a):return "0x"+f"{int(a,16):064x}"

class AaveHealthTests(unittest.TestCase):
    def test_borrow_event_detects_on_behalf_of_not_tx_caller(self):
        log={"address":POOL,"topics":[TOPIC,topic("0x"+"c"*40),topic(DEBTOR),topic("0x"+"0"*40)]}
        self.assertEqual(extract_borrower(log,POOL).lower(),DEBTOR)
        with self.assertRaises(ValueError):extract_borrower({**log,"address":"0x"+"d"*40},POOL)

    def test_health_factor_threshold_and_debt(self):
        fields=[2_00000000,1_00000000,100000,8000,7000,int(.97*WAD)]
        raw="0x"+"".join(f"{x:064x}" for x in fields)
        o=decode_account_data(raw)
        self.assertTrue(o["currently_underwater"])
        self.assertTrue(o["near_liquidation"])
        self.assertTrue(o["has_debt"])
        fields[-1]=int(1.08*WAD)
        p=decode_account_data("0x"+"".join(f"{x:064x}" for x in fields))
        self.assertFalse(p["near_liquidation"])
        fields[1]=0
        q=decode_account_data("0x"+"".join(f"{x:064x}" for x in fields))
        self.assertIsNone(q["health_factor"])
        self.assertFalse(q["currently_underwater"])

    def test_noncanonical_health_struct(self):
        with self.assertRaises(ValueError):decode_account_data("0x1234")
        fields=[1,2,3,10001,2,WAD]
        with self.assertRaises(ValueError):
            decode_account_data("0x"+"".join(f"{x:064x}" for x in fields))
if __name__=="__main__":
    unittest.main()
