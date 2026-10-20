import unittest

from bot.historical import HistoricalRPC
from bot.replay import verify_fork_proof, modeled_gas_cost
from bot.rpc import RpcError

class HistoricStub:
    def __init__(self):
        self.calls=[]
    def chain_id(self):
        return 8453
    def call(self, method, params):
        self.calls.append((method,params))
        if method == "eth_getBlockByNumber":
            return {"baseFeePerGas":"0x5b8d80"}  # 6m wei
        raise AssertionError(method)
    def eth_call(self, address, payload, block, sender=None):
        self.calls.append(("eth_call",block))
        return "0x"+"0"*64
    def batch_eth_call(self, calls, chunk_size=50):
        self.calls.append(("batch_eth_call", [x[2] for x in calls]))
        return ["0x"+"0"*64 for _ in calls]

class HistoricalSnapshotTests(unittest.TestCase):
    def test_historical_basefee_and_pinned_eth_call(self):
        rpc=HistoricStub()
        h=HistoricalRPC(rpc, 52648105)
        self.assertEqual(h.chain_id(),8453)
        self.assertEqual(h.block_number(),52648105)
        self.assertEqual(h.gas_price(),6_000_000)
        h.eth_call("0x1","0x",hex(52648105))
        # mismatch must not leak into latest-state query
        with self.assertRaises(RpcError):
            h.batch_eth_call([("0x1","0x","latest")])
        self.assertEqual(len([x for x in rpc.calls if x[0]=="eth_getBlockByNumber"]),1)

    def test_reject_bad_block(self):
        with self.assertRaises(ValueError):
            HistoricalRPC(HistoricStub(),0)

class ProofTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {"block":300,"amount_usdc":1_000_000_000,
            "min_profit_usdc":1_000_000,"estimated_gas_usdc":200_000,
            "model_gas_units":800_000}
        self.proof = {"status":"FORK_EXECUTION_SUCCESS","block":300,
            "loan_usdc_units":1_000_000_000,
            "profit_usdc_units":2_000_000,"operation_gas_used":600_000}

    def test_profit_after_full_fork_proof(self):
        item = verify_fork_proof(self.candidate,self.proof)
        self.assertEqual(item["status"],"FORK_EXECUTION_SUCCESS")
        self.assertEqual(item["net_after_gas_status"],"MODELED_POSITIVE")
        self.assertEqual(item["modeled_net_after_gas_usdc_units"],1_800_000)

    def test_stale_or_mismatched_proof_rejected(self):
        other=dict(self.proof,block=301)
        with self.assertRaises(ValueError):
            verify_fork_proof(self.candidate,other)
        other=dict(self.proof,loan_usdc_units=1)
        with self.assertRaises(ValueError):
            verify_fork_proof(self.candidate,other)

    def test_negative_modeled_pnl_does_not_count(self):
        x=dict(self.candidate,estimated_gas_usdc=3_000_000)
        p=verify_fork_proof(x,self.proof)
        self.assertEqual(p["net_after_gas_status"],"MODELED_NONPOSITIVE")

    def test_invalid_gas_model_rejected(self):
        with self.assertRaises(ValueError):
            modeled_gas_cost(dict(self.candidate,model_gas_units=0),500_000)

if __name__=="__main__":
    unittest.main()
