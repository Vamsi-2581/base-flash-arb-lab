import test from "node:test";
import assert from "node:assert/strict";
import {
  evaluateEnvelope, summarizeRecords, queryAndScreen
} from "../validate.mjs";
import { createRequire } from "node:module";
const require=createRequire(import.meta.url);
const {OrderType,REACTOR_ADDRESS_MAPPING}=require("@uniswap/uniswapx-sdk");

const maker="0x"+"1".repeat(40);
function fixture(overrides={}){
  return {
    chainId:8453,type:"Dutch_V3",orderStatus:"open",
    orderHash:"0x"+"a".repeat(64),swapper:maker,
    encodedOrder:"0x1234",signature:"0x123456",
    ...overrides
  };
}
test("SDK exports the Base Dutch V3 reactor address",()=>{
  const reactor=REACTOR_ADDRESS_MAPPING[8453][OrderType.Dutch_V3];
  assert.match(reactor,/^0x[0-9a-fA-F]{40}$/);
  assert.equal(reactor.toLowerCase(),"0x000000008a8330b5d1f43a62bf4c673a49f27ba0");
});
test("Reject wrong chain, type, status and missing fields without signing",()=>{
  for(const record of [fixture({chainId:1}),fixture({type:"Dutch_V2"}),
    fixture({orderStatus:"expired"}),fixture({orderHash:undefined}),
    fixture({signature:undefined}),null]){
    const result=evaluateEnvelope(record);
    assert.equal(result.status,"REJECTED");
    assert.equal(result.quote_or_execution_profit_verified,false);
  }
});
test("Malformed serialized order is never counted as signed or fillable",()=>{
  const result=evaluateEnvelope(fixture());
  assert.equal(result.status,"REJECTED");
  assert.equal(result.eoa_maker_signature_verified,false);
  assert.equal(result.onchain_fillability_verified,false);
});
test("Reports retain coverage and mark all orders as unproven",()=>{
  const result=summarizeRecords([fixture({type:"Dutch_V2"}),fixture()],"filled");
  assert.equal(result.fetched,2);
  assert.equal(result.statuses.REJECTED,2);
  assert.equal(result.safe_to_trade,false);
});
test("429 is unavailable, not a zero-order observation",async()=>{
  let calls=0;
  const result=await queryAndScreen("open",{fetchImpl:async()=>{calls++;return{status:429,ok:false};}});
  assert.equal(result.status,"RATE_LIMITED");
  assert.equal(result.orders,null);
  assert.equal(calls,1);
});
test("Mocked healthy empty open orders retain genuine zero",async()=>{
  const result=await queryAndScreen("open",{fetchImpl:async()=>({status:200,ok:true,json:async()=>({orders:[]})})});
  assert.equal(result.fetched,0);
  assert.equal(result.status,"ORDER_SDK_OFFCHAIN_SCREEN_ONLY");
});
