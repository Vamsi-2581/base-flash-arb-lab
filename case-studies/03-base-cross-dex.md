# 03 — Base cross-DEX flash cycles: the loss equals the pool fee

## Question

Borrow USDC in a flash loan, buy WETH on one DEX, sell it on another, repay in the same
transaction. If two pools disagree on price, the cycle prints. How often do two Base pools
disagree by more than the cost of crossing both?

## Method

- **Venues**: Uniswap V3 and PancakeSwap V3 (plus Uniswap V4 once the adapter existed),
  discovered through their factories across every fee tier.
- **Quotes**: exact, from each venue's own quoter at a pinned block. The sell leg is
  re-quoted against the *quantity the buy leg returned*, not against the same dollar
  size, because that is what a real cycle does.
- **Costs**: two pool fees (inside the quote), plus gas measured from the live base fee
  and the OP-stack L1 data component. Flash-loan premium: Balancer V2 charges 0 on Base.
- **Pool quality**: a pool is dropped when its own round trip loses more than 3%, and any
  quote more than 5% away from the median is reported as excluded rather than ranked.
  Both filters exist because a nearly-empty pool quotes an absurd price.
- **Sample**: 8 rounds across 13 tokens, at $100 and $500 notional.

## Numbers

Gas on Base is not the obstacle:

| Item | Value |
|---|---|
| Gas price observed | ~0.006 gwei |
| 450,000 gas in USD | **$0.0057** |
| Flash premium | 0 |

Quotes for 5,000 USDC → WETH, one block:

| Pool | Output (WETH) | vs best |
|---|---:|---:|
| Pancake V3 0.01% | 2.05460 | 0.0 bp |
| Uniswap V3 0.05% | 2.05370 | −4.4 bp |
| Uniswap V3 0.01% | 2.05307 | −7.5 bp |
| Pancake V3 0.05% | 2.05255 | −10.0 bp |
| Uniswap V3 0.3% | 2.04654 | −39.3 bp |
| Uniswap V4 0.3% | 2.03825 | −79.6 bp |
| Uniswap V3 1% | 2.01860 | −175.2 bp |

The best cycle — buy the cheapest pool, sell into the dearest — loses **15.0 bp** after
gas, and three pools were excluded as depth artifacts before ranking.

That pattern repeats. Across the sample:

| Notional | Rounds | Tokens | Profitable cycles |
|---|---:|---:|---:|
| $100 | 3 | 13 | **0** |
| $500 | 5 | 11 | **0** |

Best case per round was −3.3 bp at $100 and −3.8 bp at $500, both on WETH, and in both
cases the loss was approximately **the sum of the two pool fees**. The quoted prices were
not wrong; they were simply closer to each other than the cost of walking between them.

## Conclusion

On Base, at retail sizes, cross-DEX price differences on liquid pairs have been arbitraged
below the fee it costs to cross both pools. The market is not inefficient; it is efficient
to a fraction of a basis point, and the fee schedule is the floor.

## What it does not prove

- Long-tail tokens and brand-new pools are not covered here. Dislocations are larger there,
  and so is the probability that the pool is empty and the quote is meaningless.
- Priority fees, builder tips and failed-transaction costs are not modelled. All three make
  the real number worse, not better.
- A searcher with a private orderflow path and zero fees has a different cost structure.

## Reproduce

```bash
python3 -m arbcheck quote --pair USDC/WETH --size 5000
python3 -m arbcheck sweep --pair USDC/WETH --sizes 1000,5000,20000 --blocks 3
```
