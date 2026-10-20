# 06 — Cross-chain gaps: 58 bp that only absorbs one ETH

## Question

The same asset trades on several chains. If Base sells ETH at $2,383 and Optimism at
$2,369, buying on one and selling on the other looks like 58 bp of free money. Does it
survive size?

## Method

- Quote 1 WETH → USDC through Uniswap V3's `QuoterV2` on Base, Arbitrum and Optimism, at
  the same moment, in the same 0.05% tier.
- Then increase the sell size on the two extremes and watch the average price.
- Compare that decay against the cost of moving the asset: a fast bridge for L2→L2 takes
  minutes and a few basis points, the native bridge takes days.

## Numbers

One WETH, quoted at the same time:

| Chain | 1 WETH → USDC | Implied price |
|---|---:|---:|
| Base | 2383.70 | $2,383.70 |
| Arbitrum | 2383.90 | $2,383.90 |
| Optimism | 2369.74 | $2,369.74 |

Optimism is 58 bp cheap versus Base — enough to pay a bridge fee several times over, if it
were real. Selling larger amounts into the Optimism pool:

| Sell size | Optimism average | Base average |
|---|---:|---:|
| 1 WETH | 2369.74 | 2383.69 |
| 5 WETH | **2315.11** (−2.3%) | 2383.24 |
| 20 WETH | **2081.13** (−12%) | 2381.55 |
| 100 WETH | 550.66 | 2372.45 |

Base absorbs 100 ETH with a 0.5% impact. Optimism degrades 2.3% on the second trade and
loses 12% at 20 ETH: that pool is effectively empty at the current price.

The executable size is roughly **1 ETH**, worth about **$1.4** of gross edge — less than
the cost of moving the money and the minutes of exposure, before any question of whether
the other side is still there.

## Conclusion

This is the same failure mode as every other study in this directory, one layer up: the
gap exists in proportion to the absence of depth. A cross-chain price difference that
persists does so because the pool quoting it cannot absorb the trade that would close it.

## What it does not prove

- Only Uniswap V3 0.05% pools were used. Optimism's liquidity lives mostly on Velodrome,
  which was not wired in; a deeper venue could change the size that is executable, though
  not the direction of the result.
- Bridging cost and time are described, not measured.
- Two of the three chains share an OP-stack sequencer; their prices may be coupled in ways
  three snapshots cannot reveal.

## Reproduce

```bash
python3 -m arbcheck quote --pair USDC/WETH --chain base --size 5000
python3 -m arbcheck quote --pair USDC/WETH --chain monad --size 5000   # different chain, same method
```

Only Base and Monad are wired into this repository. The Arbitrum and Optimism figures came
from direct `eth_call`s to each chain's `QuoterV2`, which is the same call the adapter
makes; porting a chain is a chain entry plus venue deployments.
