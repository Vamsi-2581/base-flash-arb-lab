/**
 * Read-only UniswapX Base Dutch V3 SDK validation.
 * NEVER signs, broadcasts, persists a signature, or calls reactor.execute.
 * Successful EOA signature recovery does NOT establish on-chain fillability.
 */
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { CosignedV3DutchOrder, OrderType, REACTOR_ADDRESS_MAPPING } = require("@uniswap/uniswapx-sdk");
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

const CHAIN_ID=8453;
const OPEN_ENDPOINT="https://api.uniswap.org/v2/orders";
const HEX64=/^0x[0-9a-fA-F]{64}$/;
const ADDRESS=/^0x[0-9a-fA-F]{40}$/;
const expectedReactor=REACTOR_ADDRESS_MAPPING?.[CHAIN_ID]?.[OrderType.Dutch_V3];

export function evaluateEnvelope(record,{now=Math.floor(Date.now()/1000)}={}){
  if (!record || typeof record !== "object") return {status:"REJECTED",reason:"MALFORMED_RECORD",
    eoa_maker_signature_verified:false,eoa_cosigner_signature_verified:false,
    onchain_fillability_verified:false,quote_or_execution_profit_verified:false};
  const hash=typeof record.orderHash==="string"&&HEX64.test(record.orderHash) ? record.orderHash : null;
  const result={order_hash:hash,status:"REJECTED",eoa_maker_signature_verified:false,
    eoa_cosigner_signature_verified:false,onchain_fillability_verified:false,
    quote_or_execution_profit_verified:false};
  try {
    if (record.chainId!==CHAIN_ID || record.type!==OrderType.Dutch_V3)
      throw Error("WRONG_CHAIN_OR_TYPE");
    if (!["open","filled"].includes(record.orderStatus)) throw Error("UNSUPPORTED_ORDER_STATUS");
    if (!hash || typeof record.encodedOrder!=="string" || !record.encodedOrder.startsWith("0x") ||
        typeof record.signature!=="string" || !record.signature.startsWith("0x"))
      throw Error("MISSING_CANONICAL_SIGNED_FIELDS");
    if (!expectedReactor || !ADDRESS.test(expectedReactor)) throw Error("NO_KNOWN_BASE_REACTOR");
    const decoded=CosignedV3DutchOrder.parse(record.encodedOrder,CHAIN_ID);
    if (decoded.hash().toLowerCase()!==hash.toLowerCase()) throw Error("ORDER_HASH_MISMATCH");
    if (!decoded.info.reactor || decoded.info.reactor.toLowerCase()!==expectedReactor.toLowerCase())
      throw Error("UNRECOGNIZED_REACTOR");
    if (record.reactor && record.reactor.toLowerCase()!==decoded.info.reactor.toLowerCase())
      throw Error("REACTOR_API_MISMATCH");
    if (!ADDRESS.test(decoded.info.swapper)|| !decoded.info.cosigner)
      throw Error("MALFORMED_ACTORS");
    result.deadline=Number(decoded.info.deadline);
    if (!Number.isSafeInteger(result.deadline)) throw Error("DEADLINE_NOT_SAFE_INTEGER");
    result.expired_at_observation=result.deadline<now;
    result.eoa_signature_recovery_attempted=true;
    let recoveredSwapper;
    try { recoveredSwapper=decoded.getSigner(record.signature); }
    catch { throw Error("USER_EOA_SIGNATURE_INVALID_OR_ERC1271_REQUIRED"); }
    if (recoveredSwapper.toLowerCase()!==decoded.info.swapper.toLowerCase())
      throw Error("USER_EOA_SIGNATURE_MISMATCH_OR_ERC1271_REQUIRED");
    result.eoa_maker_signature_verified=true;
    let recoveredCosigner;
    try { recoveredCosigner=decoded.recoverCosigner(); }
    catch { throw Error("COSIGNATURE_INVALID"); }
    if (recoveredCosigner.toLowerCase()!==decoded.info.cosigner.toLowerCase())
      throw Error("COSIGNATURE_MISMATCH");
    result.eoa_cosigner_signature_verified=true;
    result.reactor_whitelist_verified=true;
    // Persist only public decoded economic terms; never the encoded signed order.
    // Filled orders are HISTORICAL examples, not currently fillable orders.
    const filledAt=record.orderStatus==="filled" ? Number(record.fillBlock) : NaN;
    result.fill_block_reported=Number.isSafeInteger(filledAt)&&filledAt>0;
    result.output_count=decoded.info.outputs?.length ?? null;
    if (result.fill_block_reported && result.output_count===1){
      try {
        const resolved=decoded.resolve({currentBlock:filledAt});
        const output=resolved.outputs?.[0];
        if (output?.token && resolved.input?.token &&
            resolved.input.amount?.gt?.(0) && output.amount?.gt?.(0)){
          result.historical_terms={
            fill_block:filledAt,
            input_token:resolved.input.token,
            input_amount_units:resolved.input.amount.toString(),
            output_token:output.token,
            required_output_units:output.amount.toString(),
            resolution_method:"SDK block-based decay, NOT an exact reactor state replay",
          };
        }
      }catch {result.historical_terms_status="UNRESOLVABLE_BLOCK_DECAY";}
    }
    if (record.orderStatus==="filled" && !result.historical_terms)
      result.historical_terms_status=result.historical_terms_status ||
        (!result.fill_block_reported ? "MISSING_FILL_BLOCK" :
         result.output_count!==1 ? "MULTI_OUTPUT_ORDER" : "RESOLUTION_NOT_POSITIVE");
    const filler=decoded.info.cosignerData?.exclusiveFiller;
    result.exclusivity_present=typeof filler==="string"&&
      filler.toLowerCase()!=="0x0000000000000000000000000000000000000000";
    // Dutch V3 decays by block, and actual eligibility also depends on
    // exclusivityOverrideBps, current on-chain state, reactor and Permit2.
    result.status=result.expired_at_observation?"OFFCHAIN_SIGNATURES_VALID_BUT_EXPIRED":
      (record.orderStatus==="filled"?"OFFCHAIN_SIGNATURES_VALID_ALREADY_FILLED":
        "OFFCHAIN_SIGNATURES_VALID_NOT_FILLABILITY_PROVEN");
    return result;
  } catch (error) {
    result.reason=String(error?.message||"UNKNOWN").slice(0,100);
    return result;
  }
}

export function summarizeRecords(records,status){
  if (!Array.isArray(records)) throw Error("EXPECTED_ARRAY");
  const checked=records.slice(0,50).map(x=>evaluateEnvelope(x));
  const counts={};
  for (const x of checked) counts[x.status]=(counts[x.status]||0)+1;
  const terms = checked.filter(o=>o.historical_terms);
  const classifications = {};
  for (const entry of checked) {
    if (entry.historical_terms_status)
      classifications[entry.historical_terms_status]=(classifications[entry.historical_terms_status]||0)+1;
  }
  const USDC="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913";
  const WETH="0x4200000000000000000000000000000000000006";
  const pairClass = (t)=>{
    const a=t.input_token?.toLowerCase(),b=t.output_token?.toLowerCase();
    if (a===USDC&&b===WETH)return "USDC_TO_WETH";
    if (a===WETH&&b===USDC)return "WETH_TO_USDC";
    return "OTHER_TOKEN_PAIR";
  };
  const knownAssets={
    [USDC]:"USDC",[WETH]:"WETH",
    "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf":"cbBTC",
    "0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22":"cbETH",
    "0x50c5725949a6f0c72e6c4a641f24049a917db0cb":"DAI",
    "0x60a3e35cc302bfa44cb288bc5a4f316fdb1adb42":"EURC",
    "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca":"USDbC"
  };
  const knownPairCounts={};
  for (const entry of terms){
    const t=entry.historical_terms;
    const lhs=knownAssets[t.input_token?.toLowerCase()]||"OTHER_ASSET";
    const rhs=knownAssets[t.output_token?.toLowerCase()]||"OTHER_ASSET";
    const key=lhs+"→"+rhs;
    knownPairCounts[key]=(knownPairCounts[key]||0)+1;
  }
  const pairHistogram={};
  for (const o of terms) {
    const label=pairClass(o.historical_terms);
    pairHistogram[label]=(pairHistogram[label]||0)+1;
  }
  return {
    historical_terms_decoded:terms.length,
    historical_terms_unavailable_reasons:classifications,
    historical_terms_pair_classes:pairHistogram,
    known_asset_pair_histogram:knownPairCounts,
    status:"ORDER_SDK_OFFCHAIN_SCREEN_ONLY",
    requested_order_status:status,
    fetched:records.length,
    sdk_validated_eoa_maker:checked.filter(x=>x.eoa_maker_signature_verified).length,
    sdk_validated_eoa_cosigner:checked.filter(x=>x.eoa_cosigner_signature_verified).length,
    statuses:counts,orders:checked,
    cryptographic_scope:"EOA-style Permit2 witness signature plus V3 cosignature recovery. ERC1271 not supported; no on-chain nonce/permit/fillability or exact prices checked.",
    safe_to_trade:false, no_private_keys_no_transactions:true,
  };
}

export async function queryAndScreen(status,{limit=25,fetchImpl=fetch}={}){
  if (!["open","filled"].includes(status)||!Number.isInteger(limit)||limit<1||limit>50)
    throw Error("BAD_QUERY");
  const url=new URL(OPEN_ENDPOINT);
  url.search=new URLSearchParams({chainId:String(CHAIN_ID),orderType:OrderType.Dutch_V3,
    orderStatus:status,limit:String(limit)}).toString();
  try {
    const response=await fetchImpl(url.toString(),{
      method:"GET",headers:{"accept":"application/json"},
      signal:AbortSignal.timeout(12000)
    });
    if (response.status===429) return {status:"RATE_LIMITED",requested_order_status:status,orders:null};
    if (!response.ok) return {status:"HTTP_UNAVAILABLE",requested_order_status:status,http_status:response.status,orders:null};
    const data=await response.json();
    if (!Array.isArray(data?.orders)) return {status:"INVALID_API_SCHEMA",requested_order_status:status,orders:null};
    return summarizeRecords(data.orders,status);
  } catch (e) {
    return {status:"API_UNAVAILABLE",requested_order_status:status,
      error_category:e?.name||"NETWORK",orders:null};
  }
}

async function main(){
  const out=process.env.OUT_DIR||"validation/p0/uniswapx-sdk";
  const reports=[];
  reports.push(await queryAndScreen("open"));
  // Two requests, separated to stay well below documented 4 RPS ceiling.
  await new Promise(resolve=>setTimeout(resolve,1100));
  reports.push(await queryAndScreen("filled"));
  const summary={
    timestamp_utc:new Date().toISOString(), chain_id:CHAIN_ID, order_type:OrderType.Dutch_V3,
    upstream_sdk_version:"@uniswap/uniswapx-sdk@3.1.1",
    expected_reactor:expectedReactor,
    reports,profit_verified_count:0,live_broadcast_enabled:false
  };
  await mkdir(out,{recursive:true});
  await writeFile(join(out,"sdk-validation.json"),JSON.stringify(summary,null,2));
  console.log(JSON.stringify({...summary,reports:reports.map(r=>({...r,orders:undefined}))},null,2));
  // Missing orders is a valid observation, but absence of a healthy API is not.
  if (reports.every(r=>r.status!=="ORDER_SDK_OFFCHAIN_SCREEN_ONLY"))process.exitCode=2;
}
if (process.argv[1]?.endsWith("validate.mjs"))await main();
