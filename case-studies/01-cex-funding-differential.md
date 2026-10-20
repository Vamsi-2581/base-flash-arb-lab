# 01 — Cross-venue funding-rate differentials have no predictive power

## Question

Perpetual funding is paid every few hours. If venue A pays longs 8% a year and venue B
pays 12%, going long A and short B collects the 4% difference with no price exposure.
The standard screen is: rank vertices by trailing funding, hold the widest spread. Does
that rule actually predict the next period?

## Method

- **Venues**: OKX, Hyperliquid, Bitget, Gate, KuCoin, MEXC. Binance and Bybit were
  unreachable from the host (HTTP 451 / 403), so they are absent from every number here.
- **Data**: 101 settled funding series over 20 symbols, roughly 45 days. All series are
  collapsed onto a common 8-hour grid; Hyperliquid settles hourly, so eight consecutive
  hourly rows are required before a bucket counts. A bucket with a hole is dropped, never
  filled with zero.
- **Panel**: for every (symbol, ordered venue pair, bucket) with a *complete* trailing
  seven-day window, compare the trailing mean differential with the differential actually
  realised over the next period. 21,792 observations.
- **Cost model**: public non-VIP taker fees per venue, plus the measured median bid/ask
  spread per venue. A round trip is four fills.
- **Walk-forward**: at each 8-hour boundary, pick the pair with the highest trailing
  differential, hold up to seven days, exit when the carry turns negative. Selection uses
  only data settled at or before that boundary.

## Numbers

**Predictive power.** Regressing the next-period differential on the trailing one:

| Metric | Value |
|---|---|
| Observations | 21,792 |
| Slope | **−0.140** |
| Correlation | **−0.022** |

A slope at or below zero means the screen has no signal. Bucketing by trailing
differential makes the failure visible:

| Trailing decile | Trailing range (bp/day) | Realised next period (bp/day) |
|---|---|---|
| d1 | 0.00 – 0.13 | −0.128 |
| d5 | 0.61 – 0.81 | +0.818 |
| d8 | 1.31 – 1.70 | **+1.159** |
| d9 | 1.70 – 2.47 | +0.472 |
| d10 | 2.47 – 9.97 | **−0.148** |

The middle of the distribution carries a little (d8 gets 1.16 bp/day). The extreme does
not: the widest deciles revert. Applying a 1.5 bp/day threshold, which is roughly what it
takes to be interesting, leaves 2,714 observations whose realised mean is **0.367 bp/day**
— about a quarter of the threshold, with the wrong sign in the tail.

**Cost floor.** One round trip on $500 notional, using measured spreads:

| Pair | Round trip |
|---|---:|
| Gate / Hyperliquid | 20.00 bp |
| Gate / OKX | 22.05 bp |
| Bitget / Gate | 23.77 bp |
| Bitget / MEXC | 25.82 bp |

At 0.367 bp/day realised, the cheapest venue pair needs **54 days** of carry to pay for
one round trip. The seven-day holding constraint is not close.

**Walk-forward.** Twelve trades at $500 notional:

| Component | USD |
|---|---:|
| Funding collected | +0.850 |
| Basis (spread convergence) | +4.588 |
| Costs | −17.795 |
| **Net** | **−12.357** |

Two of twelve trades were positive. Note where the money came from: the basis term
out-earned funding four to one. If there is an edge in this family, it is not the funding.

## Conclusion

The trailing funding differential is not a signal. Screening on it selects the pairs most
likely to revert. Even the modest, real persistence in the middle of the distribution is
an order of magnitude too small to clear a 20 bp round trip inside a week.

## What it does not prove

- Nothing about venues not measured, or about fee tiers better than the public ones. A
  market maker paying 0 bp and earning a rebate has a different problem.
- The walk-forward used 8-hour closes for the basis term; it cannot see intra-period
  dislocations.
- Twelve trades is a small sample. It is consistent with the panel result, and the panel
  is the part that carries the conclusion.

## Reproduce

```bash
# the panel and cost tables, from the archived scan
node scripts/analyze-cross-venue-funding.mjs
```

The raw settled funding history, the venue tickers and the per-trade ledger are archived
alongside the analysis. Every figure above is recomputed offline from those files.
