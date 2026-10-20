# Case studies

Six measurements of on-chain and off-chain arbitrage, kept because the answer was
**no** and the route to that answer is reusable.

Every study follows the same structure, because a result without its method is an
opinion:

| Section | What it must contain |
|---|---|
| **Question** | The decision this study was supposed to settle |
| **Method** | Venues, block range, data source, cost model, and what was excluded |
| **Numbers** | The measurement, with the unit and the sample size |
| **Conclusion** | What the numbers settle |
| **What it does not prove** | The gap between the measurement and a trade |
| **Reproduce** | The command or artifact that produced it |

## The studies

| # | Study | One-line result |
|---|---|---|
| [01](01-cex-funding-differential.md) | Cross-venue funding-rate differentials | A trailing differential has no predictive power: slope −0.14 over 21,792 observations |
| [02](02-prediction-market-negative-sum.md) | Prediction-market negative sum | Structurally impossible: the protocol lets anyone mint at $1, so YES+NO never clears below 1.0010 |
| [03](03-base-cross-dex.md) | Base cross-DEX flash cycles | 0 profitable out of 8 rounds; the loss equals the sum of the two pool fees |
| [04](04-monad-cross-venue.md) | Monad cross-venue | 0 profitable; the loss scales 17–33× when the notional only goes up 5× |
| [05](05-monad-v4-zero-fee-pool.md) | A zero-fee V4 pool, and two quoting traps | The flagship stable pool pays liquidity providers nothing, and the quoter ABI differs per chain |
| [06](06-cross-chain-depth-artifact.md) | Cross-chain price gaps | A 58 bp gap that only absorbs 1 ETH: the edge is the absence of depth |

Studies 01 and 02 leave the on-chain scope on purpose. The lab's standard — does the
strategy pay after every cost, and what is the evidence — is the same either way, and
two of the most repeated "risk-free" claims in this space deserve the same treatment.

## The pattern

Five of the six studies end the same way: **the apparent edge is inversely
proportional to the depth available at that edge.**

- A 3.9% funding differential persists because the asset cannot be moved to capture it.
- A 793 bp cross-venue spread exists because the venue has deposits and withdrawals
  disabled — we measured that directly before rejecting it.
- A 58 bp cross-chain gap absorbs one ETH before collapsing 2.3%.
- A cross-DEX cycle loses exactly the pool fee, because the price difference has been
  arbitraged down to below the cost of crossing.

That is not bad luck. It is what a market looks like when the people who capture these
gaps have better latency and lower fees than you do. The useful skill is not finding
gaps; it is measuring whether a gap survives costs and depth, quickly and honestly.

## Scope and honesty notes

- None of these are realised trades. Every figure is a quote, a backtest or a fork
  simulation, labelled as such.
- The CEX studies ran from a server in the United States: Binance returned HTTP 451 and
  Bybit returned HTTP 403, so six other venues were used. That is a coverage limit, not
  a claim about Binance.
- Numbers were produced in October 2026. Markets move; the *method* is what transfers.
