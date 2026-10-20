import unittest
from decimal import Decimal
from unittest.mock import patch
from bot.networks import USDC,WETH
from research.live_liquidation_gate import (
    RiskPolicy, reserve_state, account_state, evaluate_financial_gate,
    get_snapshot, get_user_asset_balances, WAD,
)
from bot.rpc import RpcError

def abi(*words):
    return "0x"+"".join(f"{w:064x}" for w in words)

class FakeRPC:
    def chain_id(self):return 8453
    def call(self,method,args):
        if method=="eth_getBlockByNumber":
            import time
            return {"number":"0x1234","timestamp":hex(int(time.time())),"hash":"0x"+"a"*64}
        raise AssertionError(method)
    def eth_call(self,to,data,block):
        if block!="0x1234":raise AssertionError("Unpinned call")
        # Instead of parsing keccak selectors, define directly from ABI signatures.
        from bot.abi import encode
        if data==encode("getPool()"):return abi(int("0x"+"a"*40,16))
        if data==encode("getPoolDataProvider()"):return abi(int("0x"+"c"*40,16))
        if data==encode("FLASHLOAN_PREMIUM_TOTAL()"):return abi(5)
        if data==encode("getConfiguration(address)",USDC):
            return abi((1<<56)|(1<<58)|(1<<63)|(10500<<32))
        if data==encode("getConfiguration(address)",WETH):
            return abi((1<<56)|(10500<<32))
        if data==encode("getUserAccountData(address)","0x"+"b"*40):
            return abi(200_000_000,110_000_000,0,8000,7000,int(Decimal("0.97")*WAD))
        if data==encode("getUserReserveData(address,address)",USDC,"0x"+"b"*40):
            return abi(0,0,150_000_000,0,0,0,0,0,0)
        if data==encode("getUserReserveData(address,address)",WETH,"0x"+"b"*40):
            return abi(7*10**17,0,0,0,0,0,0,0,1)
        raise AssertionError("Unexpected RPC request")

class ShadowGateTests(unittest.TestCase):
    def test_reserve_flags(self):
        r=reserve_state((1<<56)|(1<<63)|(11000<<32),"USDC")
        self.assertTrue(r["active"])
        self.assertTrue(r["flashloan_enabled"])
        self.assertFalse(r["paused"])
        self.assertEqual(r["liquidation_bonus_bps_raw"],11000)

    def test_account_hf_is_account_wide_only(self):
        x=account_state(abi(200,100,0,8000,7500,int(Decimal("0.99")*WAD)))
        self.assertTrue(x["health_factor_below_one"])
        self.assertFalse(x.get("USDC_debt_token_balance_verified",False))
        self.assertFalse(account_state(abi(200,0,0,8000,7500,0))["has_debt"])

    def test_full_pipeline_read_only_at_single_block(self):
        result=get_snapshot(FakeRPC(),["0x"+"b"*40])
        self.assertEqual(result["account_level_underwater"],1)
        self.assertEqual(result["account_reads_complete"],1)
        self.assertEqual(result["reserve_risk_flags"],[])
        self.assertEqual(result["liquidation_candidates_verified"],0)
        self.assertFalse(result["live_trading_enabled"])
        self.assertFalse(result["accounts"][0]["live_executable"])
        self.assertTrue(result["accounts"][0]["USDC_debt_token_balance_verified"])
        self.assertTrue(result["accounts"][0]["WETH_collateral_aToken_balance_verified"])
        self.assertTrue(result["accounts"][0]["USDC_WETH_pair_supported_for_liquidation"])
        self.assertEqual(result["underwater_and_USDC_WETH_pair_verified"],1)

    def test_dataprovider_separates_debt_from_account_wide_hf(self):
        r=get_user_asset_balances(FakeRPC(),"0x"+"c"*40,"0x"+"b"*40,"0x1234")
        self.assertEqual(r["verified_usdc_total_debt_units"],"150000000")
        self.assertEqual(r["verified_weth_atoken_balance_units"],str(7*10**17))
        self.assertTrue(r["verified_weth_enabled_as_collateral"])
        with self.assertRaises(ValueError):
            get_user_asset_balances(FakeRPC(),"bad","0x"+"b"*40,"0x1234")

    def test_positive_spread_without_simulation_always_blocked(self):
        value=evaluate_financial_gate(
            borrowed_units=1_000_000_000, expected_quote_units=1_100_000_000,
            flash_premium_bps=5,gas_used_limit=500000,max_fee_per_gas_wei=100000000,
            eth_price_usdc_per_eth=Decimal("2000"),l1_fee_bound_usdc=100000,
            expected_seized_weth_verified=False,exact_same_state_simulated=False,
            daily_remaining_loss_usdc=5_000_000,total_remaining_loss_usdc=20_000_000)
        self.assertGreater(value["modeled_net_after_stress_usdc_units"],0)
        self.assertFalse(value["broadcast_authorized"])
        self.assertIn("FLASH_LOAN_EXECUTION_NOT_SIMULATED",value["reasons"])
        self.assertIn("COLLATERAL_SEIZURE_NOT_SIMULATED",value["reasons"])

    def test_unpriced_l1_and_large_gas_loss_fail_closed(self):
        c=dict(borrowed_units=1_000_000_000, expected_quote_units=1_200_000_000,
            flash_premium_bps=5,gas_used_limit=2_000_000,max_fee_per_gas_wei=8_000_000_000,
            eth_price_usdc_per_eth=Decimal("2500"),l1_fee_bound_usdc=None,
            expected_seized_weth_verified=True,exact_same_state_simulated=True,
            daily_remaining_loss_usdc=5_000_000,total_remaining_loss_usdc=20_000_000)
        result=evaluate_financial_gate(**c)
        self.assertIn("BASE_L1_FEE_BOUND_UNAVAILABLE",result["reasons"])
        self.assertIn("GAS_PRICE_CAP_EXCEEDED",result["reasons"])
        self.assertIsNone(result["modeled_net_after_stress_usdc_units"])
        c["l1_fee_bound_usdc"]=2_000_000
        result=evaluate_financial_gate(**c)
        self.assertIn("SINGLE_FAILURE_BUDGET_EXCEEDED",result["reasons"])
        self.assertIn("DAILY_FAILURE_BUDGET_EXCEEDED",result["reasons"])

    def test_even_all_model_checks_cannot_enable_broadcast(self):
        value=evaluate_financial_gate(
            borrowed_units=1_000_000_000,expected_quote_units=1_100_000_000,
            flash_premium_bps=5,gas_used_limit=300000,max_fee_per_gas_wei=100000000,
            eth_price_usdc_per_eth=Decimal("2000"),l1_fee_bound_usdc=100000,
            expected_seized_weth_verified=True,exact_same_state_simulated=True,
            daily_remaining_loss_usdc=5_000_000,total_remaining_loss_usdc=20_000_000)
        self.assertEqual(value["reasons"],[])
        self.assertTrue(value["meets_modeled_cost_constraints"])
        self.assertFalse(value["broadcast_authorized"])

    def test_wrong_chain_stale_block_reject(self):
        class Wrong(FakeRPC):
            def chain_id(self):return 1
        with self.assertRaises(RpcError):get_snapshot(Wrong(),[])
        class Stale(FakeRPC):
            def call(self,method,args):
                return {"number":"0x1234","timestamp":"0x1"}
        with self.assertRaises(RpcError):get_snapshot(Stale(),[])

    def test_invalid_trade_parameters(self):
        with self.assertRaises(ValueError):
            evaluate_financial_gate(
                borrowed_units=0,expected_quote_units=1,flash_premium_bps=5,
                gas_used_limit=10,max_fee_per_gas_wei=1,eth_price_usdc_per_eth=Decimal("1"),
                l1_fee_bound_usdc=0,daily_remaining_loss_usdc=1,total_remaining_loss_usdc=1,
                expected_seized_weth_verified=True,exact_same_state_simulated=True)

if __name__=="__main__":
    unittest.main()
