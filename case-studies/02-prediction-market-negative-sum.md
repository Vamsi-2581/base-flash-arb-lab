# 02 — Prediction-market negative sum is structurally impossible

## Question

A binary market resolves to $1 on one side and $0 on the other. If YES can be bought for
$0.48 and NO for $0.49, buying both costs $0.97 and pays $1 whichever way it resolves.
Bots advertise exactly this. Does the window exist?

## Method

- Read 600 active, order-book-enabled binary markets from the public Polymarket API,
  ordered by 24-hour volume.
- For each market, fetch both order books (YES and NO tokens) and take the best ask on
  each side, plus the size resting at that price.
- Cost of a guaranteed $1 is `best_ask_YES + best_ask_NO`. Anything below 1.0 is a
  negative-sum opportunity.
- 534 markets returned two usable books; the rest were missing one side.

## Numbers

| Metric | Value |
|---|---|
| Markets with two usable books | 534 |
| Markets with ask sum below 1.0 | **0** |
| Minimum ask sum observed | **1.0010** |
| Median ask sum | 1.0100 |
| 90th percentile | 1.0200 |

The floor is not a coincidence of sampling. It sits exactly one tick above parity, and it
holds across the highest-volume markets on the venue.

## Why the floor exists

Polymarket lets anyone split 1 USDC into one YES and one NO token, and merge a matched
pair back into 1 USDC. That mint/merge path is a standing arbitrage against any ask sum
below 1: instead of buying both sides in the book, a participant mints a pair for exactly
$1 and sells whichever leg is expensive. The protocol enforces the floor; it does not
depend on a bot being fast enough.

This is why the "YES + NO < $1" bots are selling a window that cannot open. The same
mechanism exists across multi-outcome (neg-risk) markets through the converter, which
keeps the sum of complementary positions at or above parity for the same reason.

## Conclusion

Within a single venue that supports minting, negative-sum is not a strategy, it is a
protocol impossibility. Any claim otherwise is either about a venue without that
mechanism, or about a stale screenshot.

## What it does not prove

- Nothing about **cross-venue** gaps. Two venues with separate order books and separate
  resolution processes have no shared mint path, so their prices can genuinely diverge.
  Access, KYC and settlement risk then become the binding constraints rather than speed.
- Nothing about fees and gas on venues that do charge them.
- The snapshot is one moment per market. It is enough to establish the floor, because the
  floor is structural, but it is not a time series.

## Reproduce

The scan read `gamma-api.polymarket.com/markets` for the market list and
`clob.polymarket.com/books` in batches of 50 for the books. Both endpoints are public and
need no key. The list price is not used: only the best ask on each side counts.
