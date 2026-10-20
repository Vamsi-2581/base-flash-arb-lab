# 05 — A zero-fee V4 pool, and two quoting traps

## Question

How much does it pay to provide liquidity to the flagship stablecoin pool on Monad? The
pool shows $3.4M of reserves and $11.6M of daily volume, so the answer looked like
"volume × fee × your share". That arithmetic has a hidden assumption.

## Method

1. Read the pool's `slot0` and `liquidity` through Uniswap V4's `StateView`.
2. Recover the pool's `PoolKey` — V4 pools have no address, only `keccak(PoolKey)`.
3. Quote both directions through the venue's own `V4Quoter`.
4. Compare the quoted fee against the fee the arithmetic assumed.

The `PoolKey` is not published anywhere convenient, so it was recovered from the
`PoolManager` `Initialize` event: take the pool's creation timestamp from the indexer,
bisect the chain for the block at that timestamp, then filter `eth_getLogs` for the event
whose indexed `id` matches the pool. The recovered key hashes back to the on-chain id,
which is the check that the recovery is correct.

## Numbers

Pool: MUSD/USDC on Uniswap V4, Monad.

| Field | Value |
|---|---|
| `currency0` / `currency1` | USDC / MUSD |
| **fee** | **0** |
| **`lpFee` from StateView** | **0** |
| tick spacing | 1 |
| hooks | none |
| reserves | $3,427,933 |
| 24h volume | $11,568,868 |
| MUSD price | 1.00003 USDC |

The pool charges nothing. Liquidity providers earn **$0** in fees, regardless of volume.

An earlier estimate, made before the key was recovered, assumed the standard 0.05% tier
and projected $10–$50 per month on a $1,000 position. That estimate was wrong by the
entire fee. The lesson is not "stablecoin LP is bad"; it is that the fee tier is a property
of the individual pool in V4 and cannot be assumed from the pair, the venue, or the volume.

## Two traps that cost real time

**1. The quoter parameter is a struct containing a dynamic member.** The argument head
therefore starts with an offset to that struct. Omit it and every call reverts with empty
`0x` data — which is exactly what an uninitialised pool looks like. The encoding is:

```text
selector | offset(0x20) | currency0 | currency1 | fee | tickSpacing | hooks
         | zeroForOne | amountIn | bytesOffset(0x100) | length | payload
```

Nine words of head, not eight. A test asserts the leading offset so this cannot regress.

**2. The ABI differs per deployment.** Both Monad and Base expose selector `0xaa9d21cb`,
the variant whose last parameter is `bytes hookData`. The canonical v4-periphery takes
`uint160 sqrtPriceLimitX96` instead, in two signature spellings that encode identically.
An adapter that assumes one shape works on one chain and silently reverts on the other, so
the V4 adapter probes and caches the working shape per chain.

## Conclusion

Volume is not income. Before modelling a liquidity position, read the pool's fee, and
before trusting a V4 quote, verify the ABI and the struct offset. Both are cheap; assuming
either is expensive.

## What it does not prove

- Nothing about other V4 pools. This is one pool on one chain, and its zero fee may be a
  deliberate design choice for a stable pair.
- External incentives are not measured. A zero-fee pool can still pay through a points or
  subsidy programme, but that is a different revenue line with a different risk.
- The pool may have a hook that changes behaviour; the recovered key says none, which is
  why the pool was discoverable at all.

## Reproduce

```bash
python3 -m arbcheck quote --pair MUSD/USDC --chain monad --venues uniswap-v4 --size 5000
```

The pool-id derivation is asserted in `tests/test_venues.py` against the value recovered
from the `Initialize` event, so a change in the encoding breaks the test rather than
silently returning a different pool.
