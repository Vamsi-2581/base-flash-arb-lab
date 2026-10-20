# 04 — Monad: the loss grows faster than the position

## Question

Base is a crowded chain. Monad is newer, with fewer searchers and a tenth of the gas cost.
Does the same cross-venue cycle pay there?

## Method

- **Chain**: Monad (143), public RPC.
- **Venues**: Uniswap V4, Uniswap V3 and Curve — the three families that hold the chain's
  liquidity. V4 pools have no address, so they are discovered by reconstructing hookless
  `PoolKey`s and asking `StateView` whether each one is initialised *and* holds in-range
  liquidity.
- **Pairs**: the top 8 by 24-hour volume, taken from live pool activity rather than a
  hardcoded list.
- **Costs**: two venue fees (inside each quote), gas priced from the live base fee
  (102 gwei) and MON's own USD price. Flash premium assumed 0.
- **Two notional sizes**, $100 and $500, to separate fixed costs from price impact.

## Numbers

Gas first, because Monad's fee numbers look alarming and are not:

| Item | Value |
|---|---|
| Base fee | 102 gwei |
| MON price | $0.0203 |
| 450,000 gas in USD | **$0.00093** |

Gas is 0.09 bp at $100 and 0.02 bp at $500. It is not the constraint.

Results, both sizes:

| Pair | Venues quoting | Best cycle, $100 | Best cycle, $500 |
|---|---:|---:|---:|
| MON/USDC | 4 | **−$0.063** | **−$2.019** |
| USDC/WETH | 2 | −$0.473 | −$8.290 |
| mUSD/USDC, EURW/USDC, GHO/USDC, USDC/cbBTC | 1 | single venue | single venue |
| AUSD/USDC | 3 | only depth artifacts | only depth artifacts |

**0 profitable cycles out of 8 pairs at both sizes.**

The interesting number is the scaling:

| Pair | $100 | $500 | Ratio |
|---|---:|---:|---:|
| MON/USDC | −$0.063 | −$2.019 | **32.6×** |
| USDC/WETH | −$0.473 | −$8.290 | **17.5×** |

A fixed cost — fees plus gas — would scale 5×. Seventeen to thirty-three times means the
loss is dominated by **price impact**, not by fees. Monad's pools are shallow: $100
trades near parity and $500 eats 40 bp on the deepest pair on the chain.

## Conclusion

Adding capital makes this worse per dollar, not better. On a chain whose pools are thin
relative to the size you want to trade, size is a liability. The usual advice — "start
small, scale when it works" — inverts here: it works small only in the sense of losing
less.

## What it does not prove

- The long tail is not covered. Beyond the top 8 pairs, spreads are wider and pools are
  thinner, which is exactly the trade-off this study measures.
- Hooked V4 pools are invisible to key reconstruction, so any pool with a non-zero hook is
  missing from the sample.
- Balancer V3, PancakeSwap V3, TraderJoe and the smaller Monad DEXes are not wired in.
- Single block snapshots, no continuous sampling: a window that opens and closes inside
  one block is not observable this way.

## Reproduce

```bash
python3 -m arbcheck quote --pair MON/USDC --chain monad --size 100
python3 -m arbcheck quote --pair MON/USDC --chain monad --size 500
```

Note that `MON` is the native currency and is not in the token registry; quote
`WMON/USDC` instead, and read the native pool through the V4 adapter.
