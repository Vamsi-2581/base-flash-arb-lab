# Operations and go/no-go

What it would take to move from research to a live transaction, and the decision that was
actually made: **do not deploy**. The contract is not audited, and the measured edge was
negative before any of these gates were reached.

| Section | Question it answers |
|---|---|
| Live readiness review | Which blockers stood between this code and real money? |
| Acceptance gate | What does "accepted" mean for each claim here? |

## Base Aave Flash Liquidation — Live Readiness Review

**Review date:** 2026-10-16 (UTC). **Verdict: NO-GO for deployment / signing / real money trading.**  
**Approved phase:** *Shadow mode / read-only current-mainnet accounts / strictly local EVM simulation.*

### Decision rationale

- Historical example `15066218` was reproduced in a real Base-state Foundry fork: 1,468.185640 USDC Aave flash borrow, protocol liquidation, WETH swap and lender repayment. Two distinct CI jobs modeled net about +45.42 USDC after conservative Gas. Both replays used **the same historical position/state** and Base official RPC, not different market opportunities.
- Historical transaction was already mined. The preceding fork used block B−1 rather than transaction-index-exact state. Real-time liquidation competition and execution inclusion remain unmeasured.
- Real-money experimental budget: **$100**. The single historical example held ~19.70 USDC of modeled gas and L1/inclusion buffer; 5 similar worst-case losses could consume almost that entire budget. This cost estimate is *not* a validated loss distribution.
- The correct go/no-go criterion is not "one positive historical fork", but **a currently eligible token-specific borrower, same-state full successful repayment, conservative net above fee risks, and strict verified loss caps**.
- No wallet or private keys have been connected. No contract is deployed by this workflow. No signatures or transactions are broadcast. No gas is spent onchain.

### Already implemented and tested

| Layer | Implementation | Contract |
| --- | --- | --- |
| Live chain & registry | `research/live_liquidation_gate.py` | Checks Base 8453, fresh block age, `getPool()`, reserve paused/active/flashloan flags and onchain `FLASHLOAN_PREMIUM_TOTAL` |
| Borrower observations | `research/aave_borrower_health.py` | Only samples recent `Borrow` events; incomplete universe explicitly reported |
| Asset-specific Aave data | `getPoolDataProvider()` → `getUserReserveData(USDC/WETH,borrower)` | Confirms actual USDC debt, WETH aToken balance and collateral-enabled flag for near-critical accounts; failures remain unavailable, not zero |
| Cost and risk filter | `evaluate_financial_gate()` | Default loan ≤5,000 USDC, stressed output −30 bps, net ≥10 USDC, max single failed attempt ≤2 USDC, daily ≤5 USDC, total ≤20 USDC; requires explicit L1 fee bound and atomic simulation |
| Hard contract stops | `src/FlashLiquidator.sol` | Pause true at deploy; owner-only, loan ≤5,000 USDC, minimum token profit 5 USDC, max `tx.gasprice` 5 gwei, deadline ≤45 seconds, premium ≤20 bps, allowlisted V3 fees |
| Opt-in paper candidate builder | `research/live_shadow_candidates.py` | Emits local-only pinned-block USDC-debt/WETH-collateral candidates iff HF<1 and relevant Aave balances verified |
| Actual current-block local simulation | `test/LiveShadowFork.t.sol` | Default test skips; only when gated candidate exists does Forge locally attempt Aave FlashLoan→liquidationCall→Uni swap→repayment; never broadcasts. A positive USDC return does not establish full L1/L2 after-Gas profitability |
| Periodic readiness CI | retired | The cron-driven readiness jobs were removed: they re-ran unit tests that `ci.yml` already covers, and nothing was ever promoted from them. The manual readiness path below is unchanged. |
| Opt-in local daemon | `python -m scripts.run_shadow_monitor` | One pass by default; for explicit long run use `--iterations 0 --interval 300`; outputs JSONL local evidence, no wallet, never broadcasts |

Relevant CI: [unit and Foundry tests](https://github.com/starky2000/base-flash-arb-lab/actions/workflows/ci.yml), [historical backtest](https://github.com/starky2000/base-flash-arb-lab/actions/workflows/historical-backtest.yml), [read-only mainnet smoke](https://github.com/starky2000/base-flash-arb-lab/actions/workflows/live-validation.yml).

### First real mainnet shadow observation

2026-10-16 [CI run 38882263737](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38882263737):
- Onchain Base 8453 and official Aave Pool were successfully verified at one live block.
- Flash-loan premium read from Pool: **5 bps** (do not hardcode; governance may change).
- **20** recently sampled addresses revalidated at a single new pinned block.
- **Zero** in this sample with account-wide HF below 1; **zero** confirmed live liquidatable token-pair candidates.
- Current same-state atomic replay opportunities: **zero**; chain transactions signed/broadcast: **zero**.

The monitored sample is not a comprehensive list of all Aave Base debt positions. The absence of a positive result here cannot be used as an opportunity-frequency estimate.

### Unresolved blockers before a live deployment

1. **Actual full-width borrower discovery**: collect all relevant USDC borrowers from backfilled active debt events / a legitimate indexer, not only the most recent 20–40 Borrow emitters. Trigger reevaluations on price/oracle updates as well as debt/collateral changes; handle chain reorganizations.
2. **Exact current token-specific liquidation state**: confirm variable/stable USDC debt, WETH aToken collateral, user collateral flag, eMode / liquidation thresholds, current Aave protocol liquidation bonus and close factor. Aave V3 can use a 50% or 100% close factor depending on health/size, and dynamic reserve/protocol fees. Do not infer seized WETH from account total collateral.
3. **Current-state exact full transaction simulation**: evaluate *actual* amount of seized WETH from `liquidationCall`, DEX route reserves and swap, Aave premium and final USDC balance; stress price changes before inclusion. The current opt-in Forge shadow test can do a first atomic test but doesn't guarantee pending ordering.
4. **Complete failure-risk accounting**: obtain real Base L1 data fee via proper chain GasPriceOracle (or conservative verifiable upper bound) on the **actual serialized transaction**, EIP-1559 fee bounds, current L2 gas and inclusion bid; missing fee data means **REJECT**.
5. **Durability and competitiveness**: replay multiple *distinct* liquidations on independent state snapshots and observe enough shadow opportunities to estimate candidate latency, probability of being first, reverts, worst-case spent Gas and P&L. Multiple reruns of one historical event do not count as new opportunities.
6. **Security and operations**: external audit plus reviewed source/bytecode, EOA/multisig with hardware/keystore handling, segregated trading wallet, paused deployment, permissions validation, nonce management, halt-on-first-failure, alerts, emergency rescues, regression testnet/paper executions.
7. **Economic pilot gate**: until the shadow ledger can demonstrate conservatively positive **expected net** and max failed-attempt cost ≤2 USDC for the intended $100 budget, do not even begin a micro-funded pilot. A single +45 USDC old case is insufficient.

### Execution stages

**Stage 0 (completed):** Historical real-protocol Fork positive and independently repeated in CI; no trade.

**Stage 1 (implemented, ongoing):** Current-mainnet read-only chain, reserve, borrower and per-asset verification; paper candidates; 6-hour CI snapshots; optional 300-second local daemon. This is *not fast enough to compete for sub-second liquidation ordering*.

**Stage 2 (next):** Build precise price/oracle-aware account watcher and transaction-index/builder-compatible EVM/Block simulations. Budget for up-to-date RPC, alerting and state pinning. Optimize loan sizing and DEX routing using **real** collateral seized.

**Stage 3 (conditional only):** User-approved *separate* paused contract deployment and wallet infrastructure. No automatic unpause or transaction signing. Confirm bytecode, owner and limits onchain; separately review small-pilot thresholds and Gas reserve.

### Run Shadow locally

```bash
python -m unittest discover -s tests -v
# One pass, no keys, one newest window of Borrow events:
python -m scripts.run_shadow_monitor --iterations 1 --interval 300 --out validation/shadow-monitor
# Only on a machine you deliberately operate, still no signing:
python -m scripts.run_shadow_monitor --iterations 0 --interval 300 --out validation/shadow-monitor
```

Monitoring JSON is written to `validation/shadow-monitor/latest_snapshot.json`,
`latest_summary.json` and `samples.jsonl`; manage log retention and avoid putting the files in publicly served web directories. Respect third-party RPC rate limits.

**No-Go remains in force** until the full current-state simulation, cost model, prospective chance of success, and audit criteria are met. Do not commit wallet keys or depend on `--broadcast` in the research CI.

Protocol verification: [Aave Pool](https://aave.com/docs/aave-v3/smart-contracts/pool), [Aave PoolAddressesProvider](https://aave.com/docs/aave-v3/smart-contracts/pool-addresses-provider), [Aave flash loans](https://aave.com/docs/aave-v3/guides/flash-loans), [Base RPC/Flashblocks](https://docs.base.org/).

## Acceptance Gate — Oct 15, 2026

| Original gate | Implemented in code | Locally verified | Before production |
| --- | --- | --- | --- |
| 1. Base/Arbitrum real V3 pools | **Base** Uniswap V3 + PancakeSwap V3 with pinned-block QuoterV2 | **Real Base RPC probe passed**, 3 small pinned-block quotes; full scan discovered 8 pools ([evidence](RESULTS.md)) | Full production RPC / Arbitrum adapter not yet implemented. |
| 2. Multi-pool quotations, loan sizing, cost model | Fixed sampled loan-size grid, batch RPC, fees and slippage stress | **Real sampled scan completed** (112 combinations, 8.97 s, 5 blocks stale); Python tests passed | Reduce quote latency; optimization remains sampled-grid; verify exact execution costs |
| 3. Flash Loan Solidity and tests | USDC Aave V3 executor, mocked success/fail tests | **GitHub Actions passed** `forge build` and `forge test -vv` on commit `b1cf94d` | Base Fork tests and external audit remain outstanding |
| 4. Foundry Fork historical replay | `ForkProof.s.sol` + `bot.replay` | **Historical Base fork connectivity passed**, but no full opportunity replay (no positive candidate) | Run candidate-specific actual Aave/DEX execution replay with a valid positive candidate |
| 5. Automated simulation and risk gates | `bot.preflight`, `bot.live` opt-in; max age, cost and Gas controls | Mock tests only | Test with deployed paused/working contract, hardware owner, funded operator; no unattended mode |
| 6. Paper stats, success and opportunity persistence | `bot.analytics` quote stats + Fork replay outcome ledger | Python tests plus one real scanned block; no verified PnL | Many observations, persistence stats and candidate-specific Fork outcomes remain |
| 7. Small real Gas live trial | Explicit gated `cast send` integration | **No funds spent, no transactions sent** | User authorization, successful audit/replay and carefully limited funded Base wallet |

### Real RPC and Fork evidence

[Live validation report and exact limitations](RESULTS.md). The second GitHub Actions run validated read-only Base QuoterV2 integration and historical Fork connectivity. It scanned 112 combinations across 8 pools but finished 5 blocks stale and found zero qualifying candidates in the sampled grid. No positive arbitrage transaction has been replayed; no contract was deployed or funds used.

### GitHub repository and CI verification

The repository [starky2000/base-flash-arb-lab](https://github.com/starky2000/base-flash-arb-lab) was populated (source commit `b1cf94d28c7eb0e1e04d6ea89648eff0d0ec2afc`). GitHub Actions [run 38775446998](https://github.com/starky2000/base-flash-arb-lab/actions/runs/38775446998) completed successfully on the initial push: Python unit tests, `forge build`, and `forge test -vv` all passed. This is **not** live RPC, historical fork, deployment, or production verification.

**Security posture:** All asset tokens and external routers allowlisted by immutable addresses; owner vs operator separation; paused on deploy; no arbitrary call data; profit transfer to owner; failure rolls back loan operations, not transaction Gas. This is experimental, not audited. Never store keys in source files.
