<div align="center">

# Flash Arb Lab

**Base flash-loan arbitrage research — including the part where it does not work.**

![license](https://img.shields.io/badge/license-AGPL--3.0-blue)
![python](https://img.shields.io/badge/python-3.10%2B-3776AB)
![solidity](https://img.shields.io/badge/solidity-0.8.x-363636)
![status](https://img.shields.io/badge/status-research%20concluded-lightgrey)
![audit](https://img.shields.io/badge/audit-none-red)

</div>

> **Research concluded. No profitable strategy was found, and none is claimed.** The code is
> experimental and not audited. No contract is deployed. Broadcasting is disabled by default
> and needs two explicit opt-ins. Everything in this repository is either a reproducible
> measurement or a negative result.

---

## Findings

Four lines of investigation on Base (chain 8453). Each row links to the document that
proves it.

| Investigation | Scope | Result |
|---|---|---|
| **Cross-DEX flash cycle** — USDC → WETH → USDC on Uniswap V3 + PancakeSwap V3, 200–20,000 USDC | 992 valid quote combinations, 7 historical blocks | ❌ **0 positive** after DEX and Aave fees. Best case −0.1347 USDC, −0.2503 after modeled gas |
| **Aave V3 liquidations** — borrower health | 40 sampled accounts | ❌ One below HF 1.05, **none below 1**. No liquidatable position at that time |
| **UniswapX fills** — re-quoted at fill blocks | 25 already-filled Dutch V3 orders | ❌ 0 profitable. The one positive gross quote (+0.000172 ETH) turns negative under a 15 bps haircut |
| **Contract-level fork replay** — borrow → liquidate → swap → repay | 1 historical state | ⚠️ 65.1334 USDC gross, 45.4286 net after modeled gas — but that transaction was **already mined by someone else** |

Two conclusions that generalise well beyond this repository:

1. **A flash loan removes the capital requirement, not the competition requirement.**
   Every cycle above is capital-free and still negative, because the spread is smaller than
   the fees it costs to cross.
2. **A flash loan cannot fund market making across transactions.** Aave requires repayment
   inside the same transaction, so ordinary JIT liquidity needs real inventory. Uniswap V4
   flash accounting does not change that.

Full evidence: **[RESULTS.md](RESULTS.md)** · reasoning: **[RESEARCH_LOG.md](RESEARCH_LOG.md)** ·
go/no-go: **[OPERATIONS.md](OPERATIONS.md)**.

## Use it

`arbcheck` answers one question: **does this route pay after fees and gas, right now,
at this block?** It never guesses — every number is a venue's own quoter response.

```bash
python3 -m arbcheck venues                                # chains and registered venues
python3 -m arbcheck quote --pair USDC/WETH --size 5000    # exact quotes + best cycle
python3 -m arbcheck quote --pair MUSD/USDC --size 5000 --chain monad --venues uniswap-v4
python3 -m arbcheck sweep --pair USDC/WETH --sizes 1000,5000,20000 --blocks 3
```

Real output, Base, one block:

```text
USDC/WETH on Base @ block latest | 5000 in
  venue           pool                               out   vs best
  pancake-v3      pancake-v3 0.01%            2.05460363      0.0bp
  uniswap-v3      uniswap-v3 0.05%            2.05370374     -4.4bp
  uniswap-v3      uniswap-v3 0.01%            2.05306709     -7.5bp
  pancake-v3      pancake-v3 0.05%            2.05254958    -10.0bp
  uniswap-v3      uniswap-v3 0.3%             2.04653654    -39.3bp
  uniswap-v4      uniswap-v4 0.3%             2.03824824    -79.6bp
  uniswap-v3      uniswap-v3 1%               2.01859941   -175.2bp
  excluded 3 quote(s) more than 5% from the median (depth artifact)

best cycle: pancake-v3 0.01% -> pancake-v3 0.05%
  gross   $-7.4940  (-15.0 bp)
  gas     $0.0071 (450000 gas)
  net     $-7.5011  (-15.0 bp)
  verdict not profitable
```

Three things the design guarantees:

- pools are discovered per venue, and a pool with no in-range liquidity is dropped
  rather than quoted at a nonsense price;
- quotes more than 5% from the median are reported as excluded, because that is a
  depth artifact, not an opportunity;
- if gas cannot be priced, the tool says so and refuses to print a net figure.

## What it implements

- **One adapter per venue**, in `venues/`: Uniswap V3, PancakeSwap V3 and Uniswap V4 on
  Base, with Uniswap V3 and V4 also wired for Monad. Adding a venue is one module and one
  registration — see [ADD_A_VENUE.md](ADD_A_VENUE.md).
- **`arbcheck`**, a CLI that turns those adapters into a verifier: exact quotes per pool,
  the best cross-venue cycle, and a verdict, refusing to invent a net figure when gas
  cannot be priced.
- Resolves the Aave V3 Pool from its `PoolAddressesProvider` and reads the live
  `FLASHLOAN_PREMIUM_TOTAL()`. Conservative gas model, slippage stress and an L1 data-fee
  cushion. Fails closed on chain mismatch, unpriced gas or too few pools.
- `FlashArb.sol` — single-asset USDC flash loan, two router types, restricted callback,
  owner/operator split, pinned addresses, no arbitrary calls, deadline, per-leg `minOutput`,
  profit floor, safe approvals. **Deploys paused.**
- Off-chain `eth_call` / `eth_estimateGas` preflight with a freshness cap, gas-price and
  cost caps, and an operator balance check.
- Single-candidate broadcaster behind an environment flag, a CLI confirmation and an
  external Foundry keystore, with persistent **$2/day and $20 total** gas-loss reservations.

**Deliberately not included:** MEV builder integration, sequencer co-location, V4 hooks,
multihop graphs, an audit, a deployment, or any claim that a quote is executable.

## Documentation

| Document | What it answers |
|---|---|
| **[RESULTS.md](RESULTS.md)** | Every measurement: the 992-quote backtest, extended runs, live validation, the fork replay |
| **[RESEARCH_LOG.md](RESEARCH_LOG.md)** | Why the code looks the way it does: P0/P1 phases, market-making analysis, strategy iteration |
| **[OPERATIONS.md](OPERATIONS.md)** | Live readiness review, the no-go decision, acceptance criteria |
| **[SECURITY.md](SECURITY.md)** | Scope, reporting channel, safe-testing rules |
| **[CONTRIBUTING.md](CONTRIBUTING.md)** | Evidence ground rules, local setup, what gets declined |
| **[ADD_A_VENUE.md](ADD_A_VENUE.md)** | The adapter contract, the five non-negotiable rules, and the PR checklist |

## Quick start

Python 3.10+ and [Foundry](https://getfoundry.sh/). **Zero Python package dependencies** —
there is no `pip install` step on purpose.

```bash
# Linux / macOS
python3 -m unittest discover -s tests -v    # 88 tests
forge build
forge test -vv
```

```powershell
# Windows PowerShell
python -m unittest discover -s tests -v
forge build
forge test -vv
```

## How a candidate flows through the system

```text
scan ──► replay ──► preflight ──► (optional) one live transaction
 │         │            │                        │
 free RPC  fork only   real eth_call/estimate   env flag + CLI confirm + keystore
 no keys   no broadcast  fails closed on stale    reserves the loss BEFORE sending
```

<details>
<summary><b>Scan for candidate quotes</b> — free RPC, zero gas</summary>

```bash
export BASE_RPC_URL='https://mainnet.base.org'
python3 -m bot.searcher
python3 -m bot.searcher --interval 20 --min-profit-usdc 2 --gas-units 850000
python3 -m bot.analytics
```

`candidates.jsonl` records `QUOTED_NOT_FORK_VERIFIED`. These are **math snapshots, not
trades**. The gas model is a deliberately generous upper bound; if the provider or the quote
fails, the scanner records nothing rather than fabricating a result.

</details>

<details>
<summary><b>Replay on a fork</b> — no broadcast, ever</summary>

```bash
python3 scripts/choose_candidate.py --line -1 --output fork_candidate.json
export CANDIDATE_PATH="$(pwd)/fork_candidate.json"
export BASE_RPC_URL='https://your-base-archive-rpc.example'
forge script script/ForkProof.s.sol:ForkProof -vvv

# Batch replay of historical candidates:
python3 -m bot.replay --input candidates.jsonl --limit 10
```

`ForkProof.s.sol` never calls `vm.startBroadcast`. It forks to the candidate's block, deploys
the executor in the local EVM, borrows from Aave, runs both real DEX routes and asserts USDC
profit. A failing script means the candidate is **not** verified. Needs an archive endpoint.

</details>

<details>
<summary><b>Deployment gate</b> — only after tests and an independent review</summary>

```bash
export OWNER_ADDRESS='0xYourOwnerAddress'
forge script script/DeployBase.s.sol:DeployBase --rpc-url "$BASE_RPC_URL" -vv   # dry run

cast send "$EXECUTOR_ADDRESS" 'setOperator(address)' "$OPERATOR_ADDRESS" --rpc-url "$BASE_RPC_URL" --account <owner-keystore>
cast send "$EXECUTOR_ADDRESS" 'setPaused(bool)' false --rpc-url "$BASE_RPC_URL" --account <owner-keystore>
python3 -m bot.preflight --candidate candidate.json --executor "$EXECUTOR_ADDRESS" --sender "$OPERATOR_ADDRESS"
```

The contract lands **paused**. Only a fresh candidate (≤2 blocks old by default) passes
preflight. Full checklist and the live-send path: [OPERATIONS.md](OPERATIONS.md).

</details>

## Code map

| Path | Purpose |
|---|---|
| `arbcheck/` | CLI: `venues`, `quote`, `sweep`, `replay` |
| `venues/` | Venue adapters (Uniswap V3, PancakeSwap V3, Uniswap V4) and the chain registry |
| `bot/searcher.py` | Legacy fixed-block pool discovery and V3 quote comparison |
| `bot/networks.py` | Base Aave/DEX/token address registry |
| `bot/preflight.py` | Point-in-time `eth_call` / `eth_estimateGas` against real contracts |
| `bot/live.py`, `bot/policy.py` | One-time live send with budget reservations |
| `bot/replay.py`, `bot/historical.py` | No-broadcast fork replay and historical scans |
| `src/FlashArb.sol`, `src/FlashLiquidator.sol` | Atomic executors, paused by default |
| `test/` · `tests/` | Foundry tests · Python unit tests |
| `script/` | Fork proof and Base deployment script |
| `research/` | Read-only Aave/UniswapX/flash-liquidity research |

## Safety invariants

- Contracts deploy **paused**; unpausing is a separate owner transaction.
- The flash-loan callback accepts only the configured initiator and pool.
- No arbitrary target, token or calldata is ever executed.
- The broadcaster reserves its worst-case loss **before** sending and is not a bot.
- Every quote path fails closed rather than substituting a default.

## License

[GNU Affero General Public License v3.0](LICENSE). Copyleft with a network clause: running a
modified version as a network service means offering that service's users the source.
Published as research, with no warranty and no claim of profitability.

## Contact

- WeChat `starky2000`
- QQ `156297707`
- Email [chenghanmic@outlook.com](mailto:chenghanmic@outlook.com)

## Support

This is unpaid research with no sponsorship. Nothing here is paywalled and nothing is
expected — but if the negative results saved you a week of work, a tip is welcome.

| Chain | Address |
|---|---|
| Ethereum · BNB Chain | `0x00b55399835E9b1Fd800eE0BeB2aC584F854342B` |
| Bitcoin | `bc1q06raapgh35tt099ty9czcwsdrk5llyl7rzs8qe` |
| Solana | `4KTtyJoeGyA1AExFLeVJMtZWFxvEMVSaPwHyfoXVjSNk` |

Ethereum and BNB Chain share the same EVM address. These are the only addresses this
project publishes. If a fork, an issue or a DM shows a different one, it did not come
from here — check it against this file before sending anything.
