# Research log

The reasoning trail, kept in the order it happened. Each phase lists what it tried, what
it ruled out, and what remained unproven.

| Section | Question it answers |
|---|---|
| P0 research | Which opportunity classes were considered, and which were rejected up front? |
| P1 research | Do Aave liquidations or UniswapX fills actually pay? |
| Flash loans and market making | Can a flash loan fund liquidity provision? |
| Strategy iteration | How did the strategy change as the evidence came in? |

## P0 development: Aave liquidations and UniswapX Dutch V3 orders

**Research scope: Base mainnet (chain 8453), read-only. Never sign or broadcast.** This phase is an observation and data-quality gate, not completed liquidation or order-filling execution.

### Implemented

#### Aave V3 liquidation event survey

`research/aave_liquidation_survey.py` connects to Base JSON-RPC, resolves the live Pool via `PoolAddressesProvider.getPool()`, verifies it has contract code and filters canonical `LiquidationCall(address,address,address,uint256,uint256,address,bool)` logs. Decode validates pool/topic, three indexed token/user addresses and the four non-indexed ABI fields. Stores debt repaid, collateral seized, borrower, liquidator, receiveAToken, block and transaction hash.

Query ranges are individually audited. A 4,096-block continuous sample is **not** a long-horizon liquidation frequency estimate. Historical wide-window stratified sampling stores each queried range and reports the precise fraction successfully covered, rather than mistaking unsampled history for zero events. Historical liquidations are **already won by other liquidators**; their receipts are not new opportunities.

The next step after observing actual eligible events is a fork of state before execution of the liquidation transaction. End-of-block `eth_call` at B-1 cannot reconstruct other transactions preceding the liquidation within block B. A successful real Aave flash-borrow → liquidationCall → collateral swap → repayment on an EVM fork, plus measured/estimated L1 Gas, is required for even a simulated positive-profit classification.

#### UniswapX order intake

`research/uniswapx_orders_readonly.py` sends a single GET to the official `/v2/orders` API with `chainId=8453`, `orderType=Dutch_V3`, and `orderStatus=open`. API key not required. A 429 is a **rate limit**, never a zero-order sample. The module intentionally does not persist encoded order bodies or signatures and **does not perform signature verification, Dutch decay/exclusivity checks or routing quotes**.

Follow-on iteration must use UniswapX SDK to decode the signed Dutch V3 order, verify Permit2 order authorization, cosignature, reactor, expiry and exclusivity, then obtain exact same-block DEX quotes and compare gas/cost. Some callback orders can be filled without a flash loan because the user's input asset arrives first. There is **no current filler execution contract**.

### Validated first-run observations

[First P0 CI run](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38868858176), 2026-10-16 UTC:
- Aave Pool `0xa238dd80c259a72e81d7e4664a9801593f98d1c5` observed via official provider. 16/16 contiguous windows succeeded, 4,096 Base blocks, zero LiquidationCall events **within that measured range only**.
- UniswapX API returned success with **zero currently open Base Dutch V3 orders at query time**.
- Actual profit execution proofs: **zero**. Wallet/Gas spend: **zero**.

[Recent-window P0 CI run](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38869641741), 2026-10-16 UTC: all 24/24 RPC windows succeeded and covered exactly 6,000 contiguous recent Base blocks. Zero LiquidationCall events in these 6,000 blocks; UniswapX returned zero open Base Dutch V3 orders at the snapshot time. No observed event qualified for the new post-block USDC/WETH valuation step, so none was priced. These results are a measured sample, **not proof no liquidation or fill opportunities ever occur**.

The experimental workflows sometimes encounter public-provider failures. A green Actions job only means the pipeline completed; inspect per-provider coverage and API status before treating an observation as evidence.

[Wide historical trial](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38869413399): a request spanning 1,200,000 blocks was **not** actually covered. Only 768 blocks were successfully read; 3 subsequent windows failed with HTTP 403 and the scanner stopped instead of issuing all 70 scheduled queries. There were no events in the **768 successfully covered blocks**, not in the entire requested period. Therefore the default workflow uses recent contiguous history rather than inaccessible archive windows. A dedicated archive/indexer provider remains necessary for deep historical research.

### Run and inspect

GitHub Actions ran this as a scheduled read-only job (retired), or locally:

```bash
python -m unittest discover -s tests -v
python -m research.aave_liquidation_survey --out validation/p0/liquidations --span 6000 --chunk 256
# Older history is OPTIONAL, and should be attempted only with supporting RPCs:
# python -m research.aave_liquidation_survey --span 1200000 --windows 70 --chunk 768
python -m research.uniswapx_orders_readonly --out validation/p0/uniswapx
```

The GitHub Actions artifact `p0-aave-uniswapx-readonly-evidence` contains JSON coverage, results and stdout, retained 30 days. The job is started manually and scans a 6,000-block recent-history liquidation window. It may use GitHub Actions minutes under your repository plan, and an occasional delayed run or provider failure can still leave coverage gaps. UniswapX is sampled once per job; such polling **cannot discover all fleeting orders** or compete with real-time solvers.

The quote-only second-stage module `research/aave_receipt_quote_screen.py` is also integrated. It evaluates only USDC-debt/WETH-collateral historical receipts, prices their seized WETH through same-block V3 QuoterV2, and deducts flash premium, estimated Gas and stress. Results are **post-block counterfactuals**, not actual profit or fork-verified opportunities. No eligible receipts appeared in initial sampled runs.

### Protocol docs and next engineering gates

- Aave V3 Pool / liquidationCall / LiquidationCall: https://aave.com/docs/aave-v3/smart-contracts/pool and https://github.com/aave-dao/aave-v3-origin/blob/main/src/contracts/interfaces/IPool.sol
- UniswapX Base Filler: https://developers.uniswap.org/docs/liquidity/uniswapx/filling/overview
- Open-order endpoint, 4 RPS limit and Dutch_V3: https://developers.uniswap.org/docs/liquidity/uniswapx/filling/dutch-v3-chains/filling-on-dutch-v3-chains

Future milestones:
1. Gather documented liquidation event sample; classify collateral/debt pools and historical block coverage.
2. Replay eligible liquidation at pre-execution fork; use actual Aave bonus/fees and USDC gas accounting; test non-positive/revert cases.
3. Integrate UniswapX DutchV3 SDK and order state/expiry validation before any pricing. Compare signed order exact outputs to multi-DEX execution routes, not a fixed raw spot price.
4. Build protocol-specific permissioned, pause-by-default Solidity adapters only after reproduced profitable forks and independent security review.

## Phase 2: Aave borrower health and UniswapX intent economics

**Date:** 2026-10-16 UTC. **Scope:** read-only Base chain 8453. No wallet, deployment, contract calls with state effects, or real trade. The experimental $100 on-chain budget remains unused.

### Evidence and milestones

| Track | Action and run | Result | What is NOT proven |
| --- | --- | --- | --- |
| Aave risk scan | [Borrower health, initial](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38871764861) | 2,048 Borrow-log blocks; 32 sampled accounts, one below HF 1.05 and none below 1 | Not all Aave borrowers; no repayable liquidation or flash-loan PnL |
| Aave risk scan expanded | [40 accounts](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38871933883) | 3,072/4,096 target blocks (early stop at 40 accounts), 40 accounts validated, one below HF 1.05, none below 1 | No viable liquidation |
| Aave historical log indexer | [Base Blockscout trial](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38871151224) | Legacy indexer HTTP 403 | No full-range event coverage, not "zero events" |
| UniswapX SDK signed orders | [Dutch V3 real signed page](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38871193795) | 25 **already-filled** orders, 25/25 EOA maker and cosigner recoveries passed; open snapshot zero | No current fill or economically profitable trade |
| UniswapX asset universe | [Redacted sample diagnostics](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38871874736) | All 25 SDK order fill block/output terms resolved; 19 USDC→OTHER_ASSET, 4 OTHER→OTHER, 1 OTHER→USDC, 1 cbBTC→USDC | Only one sampled page; does not represent all order flow |
| First V3 filled-order quote check | [Live historical quote CI](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38872047397) | 5 signed historical orders with one of the supported base assets; **one** post-block V3 quote completed; **zero** quote-only positive gross edges | No exact transaction-state or net-after-Gas replay |

These runs are **overlapping repeated experiments**, NOT independent strategy trades or a statistically reliable estimate of win rate. Counts refer to observed samples, not lifetime trading opportunities.

### Further evidence — historical native-ETH output quote and fragility

A later [Base real-history UniswapX quote run](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38872446120) found 25 signed/filled Dutch V3 orders; 5 of the first page were selected for post-block quotes (USDC/WETH-side filter); 4 completed and 1 had a positive **gross** quote. Three selected orders required native ETH output and were modeled only with a **WETH-price proxy**; this is neither protocol-native ETH settlement nor an executable profit.

The positive quote on fill block `52674386` (already-filled order hash `0x3c6cd65b96c4dfa8c5559ec34806587f7fe7e17cf0feb3365835fd6afdc3d197`) required 0.173991850016495531 ETH; best sampled Pancake V3 100 ppm WETH quote yielded 0.174163872890967575 WETH at the same block end. The difference was **+0.000172022874472044 ETH/WETH equivalent**, before unwrap, Gas, competition and block-ordering effects. A **hypothetical 15 bps adverse haircut on the whole output** lowers the estimate to roughly **−0.000089223 ETH**, before Gas. **15 bps is an assumed stress scenario, not observed slippage.** The correct status is `POST_FILL_BLOCK_QUOTE_ONLY_NOT_EXECUTABLE`, not profitable fork execution.

This suggests studying fee tiers, native ETH settlement, exact fill ordering and price-risk persistence before any filler contract. It does NOT demonstrate a positive expected-value strategy.

#### Actual order asset mix

[Sample histogram CI](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38871874736), 25 previously filled signed orders: 19 USDC→other, 4 other→other, 1 other→USDC and 1 cbBTC→USDC. This sample is biased to API recency, not market share. The old USDC↔WETH-only two-swap executor does not cover most of these routes.

#### Broader Aave borrower risk snapshot

[Expanded Borrow log and health-factor scan](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38871933883) covered 3,072 actual blocks before the cap of 40 recent borrower addresses was reached; all 40 accounts returned valid latest `getUserAccountData`, one with HF below 1.05, zero with HF below 1. The scan **did not cover all 4,096 requested blocks nor all Aave accounts**. It found no confirmed profitable liquidation. Every two hours a bounded sampling CI is configured; it may incur GitHub Actions minutes but has zero blockchain Gas cost.

### Implemented code (test-validated where CI indicates)

- `research/aave_borrower_health.py`: sample recently emitted `Borrow` logs, deduplicate borrowers, resolve live `getUserAccountData` in bulk. Health factors are snapshots, not a full debt/collateral asset enumeration. Strict upper bound of 40 borrowers/run. Workflows run every 2h; usage may consume GitHub Actions CI minutes.
- `research/uniswapx_validator/validate.mjs`: pinned official `@uniswap/uniswapx-sdk@3.1.1` with CommonJS loading; validate Base 8453 Dutch V3 reactor and order hash; recover EOA user/cosigner signatures, decode fill block and order output as SDK resolves Dutch block-price decay, redact signed payloads. ERC1271 authorization and **onchain Permit2 nonce/fillability** are not validated.
- `research/uniswapx_filled_quote_screen.py`: on historically **already-filled** signed orders with a supported ERC20 route, read end-of-fill-block V3 quote via Uniswap/Pancake factories/quoters. Gross output-token quote less order required output is not filler net profit. The `0x000...000` native ETH *output* sentinel may be mapped to WETH **for indicative price modeling only**; native unwrap, reactor settlement and exact execution still unimplemented.
- `research/aave_indexed_history.py`: confirms public Blockscout no-key legacy logs API is unavailable/403 on this runner; failures distinctly recorded.

### What to build next, prioritized by evidence

1. **Accurately map the most frequent real UniswapX tokens**, native ETH routing and output requirements. Read-only historical V3/Slipstream/Aerodrome quotes; use only verified token contracts and never fake ERC20 semantics for ETH/ERC20ETH. Collect diverse settled-order samples with no raw signatures persisted. Validate expiry/exclusivity and Permit2 onchain read state for a *currently open* order when such an order exists.
2. **Aave near-liquidation model, not just event count**: for accounts approaching HF 1, read reserve config, collateral balances and debts, liquidation threshold, bonus and protocol fees; simulate a prospective route and verify positive conservative economics. Do **not** claim a liquidation candidate when HF>=1 or liquidationCall fails.
3. **Acquire legitimate archive-capable data access** (free tiers or existing RPC if possible), then replay known settled liquidation transactions at the correct pre-transaction state rather than after the block. Browser/block explorer histories are not substitutes for transaction-ordered EVM state.
4. Only build separate secure, default-paused liquidation/Filler executor when a **real and reproducible** positive pre-inclusion fork (loan → protocol action → route → loan repayment) exists, including realistic Gas and ordering assumptions.

### Strict P&L classifications

1. `API_OBSERVED`: endpoint returned an order or event, no proof of executable position.
2. `SIGNATURE_OFFCHAIN_VALID`: recovered EOA signatures, not onchain Permit2/ERC1271 state.
3. `HISTORICAL_QUOTED`: V3 quote at **end of fill block**, not a transaction inclusion price or profit.
4. `FULL_FORK_EXECUTED`: protocol action actually succeeded and repaid lender within local historical fork.
5. `FORK_ESTIMATED_AFTER_GAS_POSITIVE`: real fork + **conservative** Gas model positive. Still not a real onchain trade.
6. `LIVE_REALIZED`: chain receipt and post-fee token balances. **Never observed in this project.**

No positive full fork execution is established as of the runs recorded above.

Official references:
- [Aave V3 liquidation](https://aave.com/docs/aave-v3/markets/advanced)
- [UniswapX fillers overview](https://developers.uniswap.org/docs/liquidity/uniswapx/filling/overview)
- [UniswapX native ETH inputs](https://developers.uniswap.org/docs/trading/swapping-api/start-building/integration-guide)

## Research decision: Flash loans, legitimate market making, and capital efficiency

Date: 2026-10-16. Scope: Base and EVM. This is a research plan, not a profitable historical backtest, trading advice, or an invitation to manipulate market prices. Live execution remains OFF.

### Fundamental invariant

Aave V3 `flashLoanSimple` must repay **within the same transaction**. Three normal on-chain transactions within a block (mint LP → independent user swap → burn LP) cannot share one Aave flash-loan balance across them. Ordinary JIT liquidity therefore requires proprietary/borrowed longer-duration inventory. `flashLoan` with a variable-debt option additionally requires collateral or valid credit delegation; it is not free unsecured carry.

Uniswap v4's *flash accounting* nets token deltas within one PoolManager unlock. It does not permit external Aave debt to remain outstanding across transactions.

### Actual opportunity classes

| Strategy | Flash loan role | Full same-transaction repayment? | Independent capital | Practical next step |
| --- | --- | --- | --- | --- |
| Sustained AMM passive/active LP | Unsuitable for long-lived inventory | No | Yes | Skip under $100 experimental Gas budget |
| Conventional JIT LP surrounding somebody else's onchain transaction | Cannot bridge separate transactions with ordinary flash loan | No | LP inventory / credit | Historical analysis only |
| Authorized-order atomic liquidity provisioning + settlement + hedge | Can temporarily fund liquidity and settle authorized order IN THE SAME TX | Potentially yes | Gas / deploy; order flow required | Analyze order flow and exact execution economics |
| UniswapX permissionless filler and RFQ-style settlement | Can bridge intra-tx asset timing; callback may give user input first, eliminating need for a loan | Yes if valid signed order is filled | Gas / order competition | Priority 1 read-only order intake |
| Aave health-factor liquidation | Flash borrow debt asset, liquidate unhealthy account, sell collateral, repay | Yes | Gas / infra | Priority 1 historical liquidation event replay |
| Cross-DEX 2/3-hop swaps | Fund atomic route | Yes | Gas / infra | Already sampled: no validated positives yet |
| Manipulative price control or fake volume | Not a legitimate market making activity | Not a sustainable strategy | Legal / slippage / MEV risks | Excluded |

Sources:
- Aave: https://aave.com/docs/aave-v3/guides/flash-loans
- Aave liquidation: https://aave.com/docs/aave-v3/markets/advanced
- Uniswap Labs JIT analysis: https://blog.uniswap.org/jit-liquidity
- UniswapX filler overview (Base = 8453, Dutch_V3): https://developers.uniswap.org/docs/liquidity/uniswapx/filling/overview
- UniswapX callback settlement: https://developers.uniswap.org/docs/liquidity/uniswapx/filling/dutch-v3-chains/filling-on-dutch-v3-chains
- CoW solver qualification and auction: https://docs.cow.fi/cow-protocol/reference/core/auctions/competition-rules
- Uniswap v4 flash accounting: https://developers.uniswap.org/docs/protocols/v4/concepts/flash-accounting

### JIT profitability is quantifiable; here is a conservative decision gate

The rough *upper-bound* P&L for a fully authorized, single-tx order settlement with temporary LP inventory is:

```
LP fee income  = externally executed swap notional V × fee tier f × actual active liquidity fee share alpha
Flash fee      = borrowed USD equivalent B × flash premium r
Inventory P&L  = full end-of-tx marked-to-market asset balance change
Net estimate   = LP fee income - flash fee - hedging cost - gas - inclusion cost + inventory P&L
Break even V   = (flash fee + hedging + gas + inclusion - inventory P&L)/(f × alpha)
```

This simplification only holds if order flow is **real and external** and known to be executable **inside this very transaction**. Fee share alpha varies along the swap's tick path; do not assume a fixed value in production. The account must repay *each borrowed token* plus premium after withdrawing LP and hedging. Generated self-trading volume does not create net new economic income.

Purely illustrative scenario:
- B = $1m flash liquidity, premium 5 bps => $500 borrowing fee.
- External V = $2m swap, pool fee 5 bps => $1,000 total pool fee.
- alpha = 70% => $700 LP fees captured.
- Hedge $600k at 2 bps => $120; gas $8; inclusion $80.
- Net = 700 - 500 - 120 - 8 - 80 = **-$8**, before uncertain inventory P&L and execution risk.
- All else equal, break-even V > $2,022,857; the $1m nominal flash size itself is not revenue.

Research calculator and tests: `python -m research.flash_liquidity_economics --same-tx-order` (model, NOT profitable simulator).

### Chosen next R&D program — measured kill / proceed gates

#### Phase A: competing solvency-maintenance opportunities (highest priority)

- Read historical Aave V3 Base `LiquidationCall` events; enumerate historical debt/collateral pair, health factor, close factor, liquidation protocol fee and dynamic reserve bonus (do NOT hard-code a 5% bonus).
- On an archive-capable fork at the state immediately before a selected eligible liquidation, execute **Aave flash loan → repay borrower debt via liquidationCall → claim collateral → exact DEX route into loan asset → repay premium**.
- Save measured gas and token balance deltas; distinguish failed fork, successful token spread, and positive conservative after-Gas P&L. First target: at least 10 eligible historical events with ≥80% complete replay data, not a claimed profit count.
- Liquidation executes protocol risk management, not price manipulation. Competing liquidators may capture opportunities before the bot.

#### Phase B: authorized user orders / UniswapX filler (priority)

- Poll the official open-order endpoint for Base chain 8453, orderType Dutch_V3; respect 4 RPS documented cap and order exclusivity. Never touch keys, place orders, or claim fills.
- Decode order validation / expiry / reactor-specific signature; compare minimum user output to deterministic DEX input-to-output quotes. **The reactor's callback receives user input before the filler sources output** for many orders; skip the flash loan when unnecessary.
- Compute gross surplus, route fee, gas, priority/inclusion, fill competition, and revert probability. Only proceed to a fork if actual valid signed orders can be reproduced.
- Log precision-recall of viable candidates and API response coverage. Do not count HTTP 429 as zero opportunities.

#### Phase C: JIT LP microeconomics (separate long-horizon experimental branch)

- Collect third-party historical large swaps, pool tick liquidity and LP fee growth.
- Model estimated share of user fees by tick traversed and LP entry size; compute post-swap USDC/WETH inventory and external hedge slippage.
- Reject if conventional three-tx JIT requires uncollateralized flash liquidity. Explore authorized single-tx order flow as the only eligible Flash LP variant.
- Hypothesis gate: complete external order + same-transaction settlement access + demonstrably positive forecast after lending, inventory hedge and inclusion costs across several historical examples. No prototype execution if one gate fails.

#### Phase D: legacy 2-hop and 3-hop

Already observed live/historical read-only negative sampled net results; reduce default sampling allocation. Only invest in full 3-hop execution contracts after candidate data prove it has value.

### Base execution / sequencing constraint

Base already operates 200ms Flashblocks preconfirmations. The builder selects pending transactions during recurring 200ms loops with fee and gas-budget constraints; do not model it as an easily controlled Ethereum-mainnet JIT sandwich-style bundle. This makes large independent third-party swaps difficult to bracket and increases the appeal of *authorized single-transaction order settlement* instead. Research source: https://www.base.org/blog/flashblocks-deep-dive . Do not assume priority fees ensure placement across independent transactions.

### Constraints and security

- No live wallet signing, funding, deployment, transaction broadcasting, or opportunistic self-generated swap volume.
- No schemes to manipulate prices, fake liquidity/trades, or exploit uninformed counterparties.
- Do not equate nominal Flash Loan notional, liquidity TVL, or notional volume with managed capital, revenue or P&L.
- Distinguish real quote, simulated atomic execution, conservative after-Gas simulation, and actual transaction receipt.
- Public RPC access can be incomplete and historical state reconstruction requires archival/tracing capabilities.

Related code: `src/FlashArb.sol` currently supports two fixed V3 routers with USDC→WETH→USDC. It does NOT support liquidations, UniswapX order callbacks, LP mint/burn, v4 PoolManager, or other ERC20 tokens. A separate read-only research module is required before authoring or deploying new execution contracts.

## Strategy iteration — 2026-10-16

### Scope and safety

Private research on **Base mainnet 8453** using only public historical JSON-RPC and GitHub Actions; no signing credentials, deployments, transaction broadcasts or Gas spending. **Do not deploy or trade based on this report.**

**Verified execution profit:** none. Any positive spot or three-hop quote is only a *hypothesis*, and must not be reported as full Aave + DEX + loan repayment Fork-executed PnL.

### What has been built

- `scripts/event_microstructure.py`: anchored at pool slot0 from block B−1, applies Uniswap/Pancake V3 Swap event `sqrtPriceX96` updates in log order, assessing arbitrage spot dispersion only after **whole transaction** boundaries. Corrects for token ordering and USDC/WETH decimals. Pool discovery now uses batched RPC and fallback across public endpoints.
- Spot economic screen: best observed USDC/WETH price spread, two V3 swap fees, Aave loan premium and estimated USDC Gas; outputs exact break-even hurdle and excess/missing basis points. These are *infinitesimal spot* signals ignoring depth impact and changes to non-price pool storage.
- `scripts/triangle_backtest.py`: same historical-block pinned Uniswap/Pancake V3 QuoterV2 chaining for USDC→A→B→USDC. Starts with WETH/cbETH and DAI/USDbC token pairs; checks exact DEX outputs at each step and then deducts Aave premium and a larger three-hop modeled Gas allowance. There is **no three-hop smart-contract execution adapter yet**. No amount of quote profitability counts as verified profit.
- New offline tests for Q64.96 math, atomically grouped transaction logs, fee-aware breakeven, and three-hop quote chaining.
- GitHub Actions ran event-level diagnostics and three-hop historical quotes as separate workflows, since retired. Their artifacts preserved real evidence and no live transactions.

### Initial observed event-level results

| Run | Historical blocks with valid data | Swap event logs applied | Complete transaction boundaries | Positive spot screening |
| --- | ---: | ---: | ---: | ---: |
| [After RPC batching, first run](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38851551924) | 2 | 8 | 8 | 0 |
| [After adding break-even metrics](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38851814846) | 2 | 11 | 10 | 0 |

Second run's **best** post-transaction infinitesimal-price models:

| Block | Best spot model USDC on 1,000 USDC notional | Remaining bps short of break-even |
| --- | ---: | ---: |
| 52661039 | −0.776978 | −7.7713 |
| 52661030 | −0.677434 | −6.7784 |

Both results are after modeled swap fees, Aave premium and a small fixed Gas assumption, but **exclude price impact** and are NOT executable quotes or receipts. Failure to analyze a block due to public RPC coverage is an `UNAVAILABLE` sample rather than a negative opportunity. Both runs sampled overlapping regions; their counts are not independent trading trials.

### Actual first triangular three-swap research (October 16 UTC)

[GitHub Actions run 38852049359](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38852049359) used real historical Base QuoterV2 state, checking USDC→WETH→cbETH→USDC and USDC→DAI→USDbC→USDC (each 3 legs) at sampled blocks 52,668,097 and 52,664,129. Public RPC and market liquidity limited how many combinations could be quoted.

- 24 **complete** three-hop route/amount quotes were observed, all from the DAI/USDbC family.
- Positive estimated after-Aave-and-Gas routes: **0**.
- Best 200 USDC example (DAI→USDbC intermediary chain) returned 199.927661 USDC after all three DEX quotes, before lender/Gas. The Aave premium modeled 0.100000 USDC; conservative modeled Gas cost 0.173783 USDC; net **−0.346122 USDC**.
- Other attempts did not yield a complete three-leg quote and must be marked unavailable/incomplete, **not zero profit**. An ensuing code fix changes `TRIANGLE_QUOTED` to require ≥1 fully computed three-leg quote.
- No three-hop smart-contract executor exists; no valid fork-execution claims, deployment or real PnL.

This sparse, venue-limited sample shows no profitable three-hop paths in the checked historical state. It cannot establish that three-hop DEX arbitrage is universally unprofitable.

After correcting the completeness classifier, two immediate reruns ([first](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38852174964), [second](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38852195839)) failed the *data-coverage* CI gate: no complete three-leg candidate could be priced from the public provider responses. This does **not** overturn the 24 previously observed historical quotes; those attempts are **missing data** and must not be added to a zero-opportunity or win-rate denominator. Offline tests for the classification fix passed.


### Strategy interpretation and prioritization

1. These fee-adjusted spot metrics indicate little remaining margin in the chosen USDC/WETH pool snapshots; further increasing a sampled loan can **increase price impact** and cannot manufacture a spread above the fee-adjusted spot hurdle.
2. Assess three-hop alternative tokens and lower-fee V3 pool combinations; retain the actual QuoterV2 fee deductions rather than subtracting a guessed fee a second time. Triangle quotes still need a separate controlled executor and complete Fork repayment proof before being considered viable.
3. Intrablock spot event analysis only models pool *price*. Reproducing balances, ticks, liquidity changes and exact place-in-block execution needs a transaction-ordered fork replay or capable provider. Base mainnet's official public RPC disabled `debug`/`trace`/`txpool` on 2026-10-08, so full trace functionality requires other infrastructure. Source: https://docs.base.org/base-chain/network-information and https://status.base.org/
4. Optimize for data completeness, time-to-signal, distinct blocks and economically meaningful opportunities rather than nominal number of theoretical quotes.
5. Preserve the old two-leg executor as-is and disabled; no live deployment or signing unless a strictly positive, reproducible and externally reviewed full Fork scenario is established.

Artifacts in GitHub Actions expire according to the workflows' retention settings, so archive any evidence needed for a longer review.
