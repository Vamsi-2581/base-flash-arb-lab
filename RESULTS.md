# Results

Every measurement this repository produced, in one place. Each section states what was
measured, on which blocks, and what it does **not** prove. None of these are realised
trades; the vocabulary is deliberate — `QUOTED_NOT_FORK_VERIFIED`,
`FORK_ESTIMATED_AFTER_GAS_POSITIVE`, `POST_FILL_BLOCK_QUOTE_ONLY`.

| Section | Question it answers |
|---|---|
| Historic V3 backtest | Do cross-DEX flash cycles pay on Base? |
| Extended research runs | Do wider routes, fee tiers or loan sizes change the answer? |
| Live read-only validation | Does the pipeline see real RPC state and connectivity? |
| Historical fork replay | Can the contract execute the full path against real protocol state? |

## Base USDC/WETH V3 historic backtest: verified evidence

Run completed October 15, 2026 UTC. These are pinned historical quotes, not executed trades.
Source: https://github.com/starky2000/base-flash-arb-lab/actions/runs/38779696410

### Summary
- Chain ID: 8453; head at selection: 52,648,442.
- Sampled historical blocks: 7, all queried successfully.
- Valid route/loan-size quotations: 992; incomplete quotations: 576.
- USDC loan-size grid: 200, 1,000, 5,000, 20,000.
- Uniswap V3 and PancakeSwap V3, USDC to WETH to USDC.
- Positive post-fee quote candidates: 0.
- Full Aave plus two DEX profitable fork proofs: 0 (not attempted, since no profitable quote).
- Best quote before gas but after DEX and Aave fees: -0.134730 USDC.
- Best quote after conservative modeled gas: -0.250308 USDC.
- Mean of the best net quote on each sampled block: -0.262403 USDC.
- Wall time: 112.53 seconds.

| Historical block | Quoted routes | Missing paths | Best after Aave fee (USDC) | Best after modeled gas (USDC) |
| --- | ---: | ---: | ---: | ---: |
| 52,648,426 | 168 | 56 | -0.151935 | -0.267538 |
| 52,648,394 | 125 | 99 | -0.150765 | -0.266407 |
| 52,648,314 | 162 | 62 | -0.150845 | -0.266421 |
| 52,647,930 | 125 | 99 | -0.134730 | -0.250308 |
| 52,646,394 | 138 | 86 | -0.144693 | -0.260153 |
| 52,640,250 | 156 | 68 | -0.154844 | -0.269801 |
| 52,615,674 | 118 | 106 | -0.140804 | -0.256193 |
| **Total / best** | **992** | **576** | **-0.134730** | **-0.250308** |

### What this means
This is only a sparse block-snapshot sample, NOT realized PnL, a statistically representative period, an independent-trial success rate, or a claim there is no Base arbitrage. The V3 QuoterV2 incorporates DEX fees, and loan premium and modeled gas are then subtracted. Historical block states ignore competing MEV searchers and within-block ordering.

### Strict positive fork candidate criteria
1. A pinned-block snapshot shows modeled net positive after lender fee and conservative gas/slippage.
2. A real forked Aave V3 flash loan borrows USDC, executes both real DEX swaps, repays fully, and asserts positive token profit.
3. Structured proof JSON contains the exact source block, loan amount, USDC owner balance delta, and measured operation gas.
4. Post-gas conservative modeled net profit is strictly positive.
5. Classification remains hypothetical fork-executable, not a real chain receipt: inclusion, MEV and exact L1 data costs are not proven.

Scan runs are started manually through GitHub Actions and use no wallet keys. Actions minutes count toward the repository owner's plan. See the latest workflow artifacts for subsequent snapshots.
### Additional Base backtest runs (October 15, 2026 UTC)

| Run | Sampled blocks | Successful quote paths | Best modeled net, USDC | Positive candidates | Full execution proofs |
| --- | ---: | ---: | ---: | ---: | ---: |
| [B](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38780494566) | 7 | 956 | -0.254409 | 0 | 0 |
| [C — initial event selector, threshold too high](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38780532958) | 7 | 945 | -0.254909 | 0 | 0 |
| [D — event-selected large swaps](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38781241956) | 11 | 1,563 | -0.228278 | 0 | 0 |

Run D selected four actual historical high-volume Swap blocks using public RPC eth_getLogs,
with the largest sampled Swap about 12,360 USDC (the four selected volumes
were approximately 12,360, 11,809, 7,153, and 3,775 USDC). Event selection encountered
403 errors on some public log providers; it used partial successfully retrieved
log windows rather than claiming complete order flow. The 11 snapshot quotes completed.
Best Run D gross after DEX and flash-loan fees but before Gas was -0.112722 USDC.

Across runs A-D there were **32 sampled block observations and 4,456 completed
route/amount quotations**, with **zero modeled profitable candidates** and thus
**zero complete positive-profit ForkProof executions**. Samples and routes
are not independent; some time windows overlap and unsuccessful RPC quotes
are missing data, not proof of losses. These figures do NOT demonstrate that
Base has no arbitrage opportunities.

The workflow can be re-run on demand to collect more evidence; it never broadcasts transactions.
An opportunity is only promoted after a successful full Aave/DEX fork execution
and strictly positive conservative modeled net-after-Gas profit.

## Extended Base arbitrage research — verified next-round evidence

**October 16, 2026 (Asia/Shanghai).** Authenticated GitHub Actions runner read real Base mainnet snapshots. No wallet, contracts deployed, broadcasts, or blockchain Gas fees.

### New research scope

- **Trading pairs:** USDC against WETH, cbBTC, DAI, and EURC in the default CI run. Registry additionally supports cbETH and USDbC, not used by this default run.
- **DEX adapters:** Uniswap V3, PancakeSwap V3, and Aerodrome **classic stable/volatile pools**. Aerodrome pricing calls its pool's getAmountOut(), which includes the pool's configured fee. Aerodrome Slipstream CL is not integrated.
- **Borrow optimization:** evaluate 200, 1000, 5000, and 20000 USDC coarse loan sizes, then probe geometric means adjacent to promising routes. This is local bounded refinement, not a global optimum.
- **Event-conditioned pricing:** identify significant historical Swap logs and retain the originating transaction hash and log index. Compare end-of-block state B-1 versus B. This is **not intrablock transaction-level EVM replay**.
- **Sampling fairness:** round-robin directed exchange pair selection replaces a biased first-N list.
- **Strict execution gate:** only legacy USDC/WETH Uniswap/Pancake V3 paths may become fork candidates. A quote alone cannot be labeled execution-verified or profitable.

### Verified GitHub Actions evidence (October 15, 2026 UTC)

| Run | Event-conditioned snapshots quoted | Full two-leg read-only quotes | Modeled positive quotes | Positive full Aave/DEX fork proofs |
| --- | ---: | ---: | ---: | ---: |
| [Initial expanded pipeline](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38783242811) | 4 | 554 | 0 | 0 |
| [Strict candidate conversion and fork path](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38783495090) | 4 | 911 | 0 | 0 |
| [Balanced route sampling](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38783669774) | 3 | 372 | 0 | 0 |
| [Coarse-vs-adaptive borrowing metrics](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38783908436) | 4 | 910 | 0 | 0 |
| **Total observations, not independent trades** | **15** | **2,747** | **0** | **0** |

These workflow runs share nearby historical time windows and do not represent statistically independent trials. Route coverage and RPC capacity differ. A lower count on the third run is **not** evidence of poorer search quality. Public RPC throttling, logs coverage and time budgets can leave missing routes.

These runs found no positively modeled opportunities within their sampled paths, so no positive Aave+DEX real-liquidity ForkProof was performed. No actual PnL or win-rate claim is supported.

Each workflow run uploads an **extended-base-research-evidence** artifact with full JSON (30-day retention), including exact pool discovery, per-asset metrics, event hashes, before/after quote comparisons and the strict verification summary.

### Run again, still no Gas cost

Use GitHub Actions → **Extended Base multi-asset event research (no wallets)** → **Run workflow**. For local testing, run the offline Python tests followed by the event selector and extended event backtest scripts. No private keys are used.

### Remaining production gates

1. Aerodrome and additional ERC20 pairs have NO corresponding supported on-chain executor or full flash-loan fork proof; outputs are research signals only.
2. End-of-block B-1/B prices cannot isolate the selected event from other transactions within block B. An exact intra-block replay needs predecessor-transaction state reconstruction and reliable archive/tracing infrastructure.
3. Adaptive size probing can miss non-unimodal price impact, tick ranges, narrow profit peaks and competitor interactions.
4. USDC gas conversion and L1 data fee estimates are only models, not actual on-chain transaction receipts.
5. Do not fund a live wallet, deploy or send real arbitrage transactions until candidate-level evidence and independent code review.

Official source references:
- https://github.com/Uniswap/default-token-list/blob/main/src/tokens/base.json
- https://github.com/aerodrome-finance/contracts/blob/main/contracts/Pool.sol
- https://github.com/aerodrome-finance/contracts/blob/main/contracts/factories/PoolFactory.sol

## Base mainnet live read-only validation

**Scope:** Real Base JSON-RPC reads, pinned-block Uniswap/PancakeSwap V3 quotes,
historical local-fork connectivity, and bounded candidate scanning.
**No contract deployment, private keys, on-chain broadcasts or Gas expenditure.**

### Evidence — October 15, 2026 (UTC)

- [Run 1 — RPC probe and fork](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38777885931) — succeeded. The broader quote search encountered HTTP 429 rate limiting on the public Base RPC.
- [Run 2 — complete bounded search and fork](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38778180236) — GitHub Actions succeeded. The real sampled search completed but **was stale by completion**, so it exported no actionable candidates.
- Each run publishes artifact **base-read-only-evidence**, with JSON results and fork logs (retained for 14 days).

#### Second run facts, from actual Base state

| Measure | Observed |
| --- | --- |
| Base chain ID | 8453 |
| Probed block | 52,648,105 |
| Aave V3 Pool | 0xa238dd80c259a72e81d7e4664a9801593f98d1c5 |
| Aave total flash-loan premium | 5 bps (0.05%) at that block |
| USDC/WETH live V3 quote venues in small probe | 3 (Uniswap fee 500 and 3000; Pancake fee 500) |
| Full candidate scanner pools discovered | 8 |
| Modeled two-leg route/size combinations tested | 112 |
| Failed individual quote paths | 9 |
| Scan duration | 8.97 s |
| Age of pinned-block data by scan completion | 5 blocks |
| Modeled positive candidate count | 0 in sampled configuration |
| Historical local EVM fork smoke | PASS (Forge script without --broadcast) |
| Full opportunity-level flash-loan execution replay | NOT EXECUTED — no eligible positive candidate |

**Interpretation:** The live integration works, but these observations **do not**
establish an absence of arbitrage on Base. Sampling only two nominal USDC
flash-loan sizes and a small set of fee tiers can miss opportunities. The
public RPC cannot support low-latency, broad MEV searching reliably. A
historical-state connectivity test is not a full profitable flash-loan replay.

### How to repeat for $0 in on-chain fees

1. GitHub repository → **Actions** → **Base mainnet read-only RPC and fork validation** → **Run workflow** (choose main).
2. Open **Summary** for the new run; download **base-read-only-evidence** to inspect report.json, fork-smoke.log and replay.log.
3. Never interpret a green Actions job as proof of profitable execution: the JSON explicitly marks stale, unavailable, skipped, and unverified states.

#### Remaining milestones before any live wallet activity

- Reduce latency and RPC request count; add production-grade provider or colocated node only if justified.
- Improve price search beyond a two-size grid; cover additional venues and multi-hop routes.
- Record many repeated blocks, keep quote timestamps and robust opportunity persistence metrics.
- Obtain genuinely positive candidates, then run exact-block flash-loan execution replay with a compatible archive RPC.
- Compare real simulated outputs, Aave repayment, slippage and Base L1 data fees; audit the execution contract.
- **Do not deploy, fund an operator, or enable broadcasts** merely because this smoke test passed.

This document records the observed data from GitHub Actions, not predictions,
and does not contain any signing credentials or private RPC tokens.

## Historical fork replay of an already-mined Aave liquidation

> **What this is not:** the profit below was captured on chain by the sender of the
> source transaction in the same block. This replay demonstrates that the contract
> can execute the full borrow → liquidate → swap → repay path against real protocol
> state; it is not an available opportunity and cannot be repeated. The source block
> (15,066,218) is from 2024-05-28 and is no longer served by public RPCs, so the
> replay can only be reproduced against an archive node.

**Observed in GitHub Actions:** 2026-10-16 UTC  
**Primary audited run:** [38875703678](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38875703678)  
**Evidence artifact:** `aave-full-liquidation-fork-attempt-evidence`, attached to that run. CI artifacts expire after 30 days, so treat the run log as the durable record; the artifact held the receipt-derived candidates, per-endpoint health/quote preflight, the Forge attempts, `fork-profit-proof.json` and `classification.json`.

### Result and meaning

**ONE historical Base state has passed a genuine contract-level full flash-loan execution and modeled after-Gas profitability gate. This is not live executed P&L.** It does not imply the same liquidated position can be liquidated again.

| Field | Reproduced fork observation |
| --- | --- |
| Chain | Base mainnet, 8453 |
| Source already-mined liquidation transaction | [0x26eaac4bbffbfd0f3e20717289f2f288ce5cb4d1636444238bcb56c89f4148d4](https://basescan.org/tx/0x26eaac4bbffbfd0f3e20717289f2f288ce5cb4d1636444238bcb56c89f4148d4) |
| Source transaction index | 2 |
| Historical liquidation block | 15,066,218 |
| Actual replay fork state | **end of block 15,066,217** (not precise pre-tx state inside B) |
| USDC flash loan | 1,468.185640 |
| Aave borrower | `0x147733ca6eadcd14021101ca4be1467a7e0feecc` |
| Real Aave collateral liquidation | WETH underlying token, receiveAToken=false |
| WETH → USDC exit | Uniswap V3 router, **100 ppm / 0.01% fee tier** selected by historical real QuoterV2 at B-1 |
| Gross token profit **after full Aave flash principal + premium repayment** | **65.133400 USDC** |
| Measured Forge call-path gas | **776,443 units** |
| Conservative modeled Gas/inclusion/L1 reserve | **19.704783 USDC** |
| **Positive after-modeled-Gas fork P&L** | **45.428617 USDC** |
| Onchain transaction sent? | **No.** Fork-only local EVM execution. |
| Previous 2026 order/lending bot live P&L | **$0 observed; not involved** |

#### What the test actually executed

The fork instantiated the new pause-default `src/FlashLiquidator.sol`, activated it in the local EVM, and then called:

1. Real Base Aave Pool `flashLoanSimple(USDC, 1468185640,...)`.
2. Authenticated Aave callback verified initiator+Pool address.
3. Real Aave Pool `liquidationCall(WETH,USDC,borrower,borrowed,false)` against the historical borrower and reserve storage.
4. Real deployed Uniswap V3 Router **100 ppm** WETH→USDC swap; proceeds subject to EVM pool reserve state and swap math.
5. Aave Pool automatically pulled borrowed USDC **plus actual premium** as required before control returned.
6. Only the post-repayment USDC balance increase was treated as token profit.
7. The proof then converted measured operation gas with a real forked WETH/USDC Quoter reference, used `3*block.basefee + 2 gwei` as hypothetical priority-inclusive gas price, +35% gas headroom and **10 USDC additional buffer**. It wrote a success proof **only after** asserting the modeled conservative positive USDC net.

The output status `FORK_ESTIMATED_AFTER_GAS_POSITIVE` is narrowly defined. It does **not** mean a live realized trade, a known real historical liquidator profit, a precise historical transaction-index EVM state replay, an audited deployment, or expected future profitability.

#### Primary evidence JSON (unmodified)

```json
{"status":"FORK_ESTIMATED_AFTER_GAS_POSITIVE","real_aave_loan_liquidation_swap_and_repayment":true,"state_exactness":"B_MINUS_1_NOT_TRANSACTION_INDEX","block":15066218,"borrowed_usdc_units":1468185640,"usdc_profit_units":65133400,"gas_used_estimate":776443,"modeled_gas_usdc_units":19704783,"net_after_gas_usdc_units":45428617,"broadcast":false}
```

#### Independent repeat and RPC coverage

A second dedicated GitHub Actions run, [38876246651](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38876246651), replayed the **exact frozen fixture** on a separate CI job at the same B-1 historical Base block using the Base official RPC. The loan+liquidation+Uniswap route+repayment executed successfully again:

| Quantity | Original run 38875703678 | Frozen-fixture repeat 38876246651 |
| --- | ---: | ---: |
| Flash loan (USDC) | 1,468.185640 | 1,468.185640 |
| **USDC token profit after flash repayment** | **65.133400** | **65.133400** |
| Conservative modeled Gas USDC | 19.704783 | 19.709006 |
| **Modeled after-Gas net USDC** | **45.428617** | **45.424394** |
| Full loan/callback/liquidation/swap/repayment | PASS | PASS |
| Live onchain broadcast | NONE | NONE |

Both runs use the same historical source state and Base official RPC (distinct CI jobs, **not independent trading samples**). The regression also attempted dRPC; it **failed due HTTP 429 historical storage/account rate limits**, which is an infrastructure failure, not a proven economic loss. Therefore the evidence is reproducibility via the Base official endpoint, **not validation across two independent providers**.

#### Replay with the frozen fixture

Data fixture: `fixtures/liquidation-15066218.json` — derived from canonical `LiquidationCall` event, real receipt transaction hash and preflight-selected Uniswap V3 fee tier.

```bash
# WARNING: local fork simulation ONLY — never supply --broadcast.
# Foundry installed, archive-capable Base public RPC accessible.
mkdir -p validation/liquidation-fork
RUN_HISTORICAL_FORK=true \
LQ_CANDIDATE_PATH=fixtures/liquidation-15066218.json \
BASE_RPC_URL=https://mainnet.base.org \
forge test --match-contract LiquidationForkProofTest \
  --match-test testHistoricalRealProtocolLiquidation -vvv
cat validation/liquidation-fork/fork-profit-proof.json
```

Dedicated independent-replay workflow: [verified-liquidation-regression.yml](https://github.com/starky2000/base-flash-arb-lab/actions/workflows/verified-liquidation-regression.yml).

### Essential limitations / roadmap before risking the $100 budget

1. **State granularity:** source event was tx index 2; replay uses B−1, not the exact immediate preceding intra-block state. Full transaction-order-accurate replay requires archive/tracing and reconstructed preceding transactions. Historical success doesn't grant current eligibility.
2. **Competition:** previous winner got transaction ordering. A new liquidator must compete on timing, builder bids, gas, revert risk and block inclusion, none of which can be measured by this Fork alone.
3. **Economics:** above gas is an intentionally conservative modeling assumption, *not* observed live receipt including L1 data fee. One-time contract deployment, wallet onboarding, failed execution, professional audit and infra costs not included in the single-trade net estimate.
4. **Capital:** the $100 user budget is unspent on blockchain Gas. The proof borrowed USDC inside a sandbox of *real historical state*. It is not a withdrawable or recoverable $45 gain.
5. **Safety:** contract is **NOT audited**. Restrict authorized owner, pause by default, debt and collateral token whitelist, known Aave Pool, two known V3 routers; additional formal review and testnet deployment needed before any live use.
6. **External validity:** Aave and DEX fees/state evolve; profitable historical liquidation events are low-frequency and competitive. One positive result cannot establish an expected return or durable strategy.

**Decision:** Full flash-loan liquidation *technical feasibility and one historical positive fork* are now evidenced. Next milestone is a wider, statistically sound sample, accurate pre-transaction state, zero-spend monitoring of current eligible loans, and independently repeated Fork proofs. **Do not enable live transactions yet.**
