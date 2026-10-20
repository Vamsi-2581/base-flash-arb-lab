# Quoting Uniswap V4 from off-chain code

Everything here was learned by getting it wrong first. If your V4 quote reverts with
`execution reverted` and **no data**, this page is probably what you are looking for.

V3 quoting is one call: `QuoterV2.quoteExactInputSingle(tokenIn, tokenOut, amount, fee, 0)`.
V4 changes three things at once, and each one fails in a way that looks like an empty pool.

| | Uniswap V3 | Uniswap V4 |
|---|---|---|
| Pool identity | a contract address | `keccak256(PoolKey)`, no address |
| State read | `pool.slot0()` | `StateView.getSlot0(poolId)` |
| Quote call | `QuoterV2` | `V4Quoter`, different ABI per deployment |
| Empty pool looks like | zero reserve | **also** a revert with no data |

This repository implements all of it in `venues/uniswap_v4.py`, with the encoders in
`venues/__init__.py` and tests in `tests/test_venues.py`.

## 1. A pool is a hash, not an address

```text
PoolKey = (currency0, currency1, fee, tickSpacing, hooks)
poolId  = keccak256(abi.encode(currency0, currency1, fee, tickSpacing, hooks))
```

`currency0` is always the numerically smaller address — sort before hashing, or every
lookup misses.

There is no factory to ask "does this pair exist". You reconstruct candidate keys and ask
the state lens whether each one is live:

```text
StateView.getSlot0(poolId)      -> (sqrtPriceX96, tick, protocolFee, lpFee)
StateView.getLiquidity(poolId)  -> uint128
```

A non-zero `sqrtPriceX96` means the key was initialised. That is **not** the same as "has
liquidity": a pool can be initialised and hold nothing at the current tick, and quoting it
returns a meaningless number rather than an error. Require `getLiquidity(poolId) > 0`
before you treat a pool as quotable.

Discovery therefore looks like a small brute force over plausible keys — fee in
`{100, 500, 3000, 10000}`, tick spacing in `{1, 10, 20, 50, 60, 100, 200}`, hooks zero —
which is fine for a CLI and wrong for anything latency-sensitive. **A pool with a
non-zero hook is invisible to this method**, and that is a coverage limit, not a bug.

### Recovering a key you cannot guess

If a pool has a hook, or an unusual fee, the key is in the `PoolManager` `Initialize`
event:

```solidity
event Initialize(PoolId indexed id, Currency indexed currency0, Currency indexed currency1,
                 uint24 fee, int24 tickSpacing, IHooks hooks, uint160 sqrtPriceX96, int24 tick);
```

`id`, `currency0` and `currency1` are indexed (topics 1–3); `fee`, `tickSpacing`, `hooks`,
`sqrtPriceX96` and `tick` are in the data, in that order. A public RPC usually caps
`eth_getLogs` at a narrow block range, so bisect by timestamp to find the creation block,
then ask for a window around it.

Always re-hash the recovered key and compare it to the pool id you started from. That
comparison is the only proof that you recovered the right pool.

## 2. The quoter ABI is not the same on every chain

Three spellings are in the wild. The first two encode to identical calldata and differ
only in selector, so probing all three costs nothing:

| Signature | Selector | Seen on |
|---|---|---|
| `quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes))` | `0xaa9d21cb` | Base, Monad |
| `quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,uint160))` | `0x97d73bdf` | canonical v4-periphery |
| `quoteExactInputSingle((address,address,uint24,int24,address,bool,uint128,uint160))` | `0x627bfac4` | flat-parameter variant |

Casting the wrong one reverts with `0x`. The reference implementation probes, caches the
working shape per chain, and only raises once every shape has failed.

You can check which selector a deployment supports before writing any code: fetch the
runtime bytecode and scan for `PUSH4` (`0x63`) followed by four bytes, or reverse-lookup a
selector in `4byte.directory`. Confirming `poolManager()` returns the PoolManager you
expect is a good sanity check that you are talking to a V4 periphery contract at all.

## 3. The calldata trap: a struct with a dynamic member

This is the one that costs an afternoon.

The parameter is a **struct** that contains `bytes hookData`. A struct with a dynamic
member is itself dynamic, so the argument head begins with an offset *to the struct*.
Inside the struct, the static members are inlined and `hookData` gets its own offset.

```text
word 0   0x20            offset to the struct, from the start of the arguments
word 1   currency0
word 2   currency1
word 3   fee             uint24
word 4   tickSpacing     int24
word 5   hooks
word 6   zeroForOne      bool
word 7   exactAmount     uint128
word 8   0x100           offset to hookData, from the start of the struct
word 9   length          bytes length
word 10+ payload
```

Nine words of head, not eight. Omit word 0 and the call reverts with `0x` on a pool that
is perfectly healthy — which is why this failure gets misdiagnosed as "no liquidity".

The same shape applies to the canonical `uint160` variants; only the selector changes.

## 4. Diagnosing an empty revert

Work down this list; each step is cheap.

1. **Is the pool initialised?** `StateView.getSlot0(poolId).sqrtPriceX96 > 0`.
2. **Does it hold liquidity?** `StateView.getLiquidity(poolId) > 0`. If zero, stop: the
   quote is meaningless, and this is the most common "the price is absurd" cause.
3. **Is the quoter real?** `poolManager()` on the quoter address should return the
   PoolManager for that chain.
4. **Is the selector deployed?** Scan the bytecode for `PUSH4` selectors, or look up your
   selector in `4byte.directory`.
5. **Is the struct offset present?** Check that byte 0 of the arguments is `0x20` and word
   8 equals `0x100` for empty hook data.

If all five pass and it still reverts, the hook is probably not the zero address and the
pool needs hook data you do not have.

## 5. Reading a quote

`V4Quoter` returns `(uint256 amountOut, uint256 gasEstimate)`. Use the first word. The
returned amount already includes the pool fee and price impact; there is nothing left to
subtract. Fees live in the `PoolKey` (or come from a hook for dynamic-fee pools) — do not
assume a fee tier from the pair or the venue. A pool can be configured with `fee = 0`,
which is exactly what the MUSD/USDC pool on Monad is: `case-studies/05` has the walkthrough
of a liquidity-provider estimate that was wrong by the entire fee because of that
assumption.

## Working implementation

```bash
python3 -m arbcheck quote --pair MUSD/USDC --chain monad --venues uniswap-v4 --size 5000
```

- `venues/__init__.py` — `encode_pool_id`, `encode_v4_quote`, `encode_v4_quote_static`
- `venues/uniswap_v4.py` — discovery, liquidity filter, per-chain ABI probing
- `tests/test_venues.py` — asserts the pool id against a value recovered from a real
  `Initialize` event, and pins all three selectors

Deployment addresses used (verified against the Uniswap deployment list and by reading
`poolManager()` back from the quoter):

| Chain | Quoter | StateView |
|---|---|---|
| Base (8453) | `0x0d5e0f971ed27fbff6c2837bf31316121532048d` | `0xa3c0c9b65bad0b08107aa264b0f3db444b867a71` |
| Monad (143) | `0xa222dd357a9076d1091ed6aa2e16c9742dd26891` | `0x77395f3b2e73ae90843717371294fa97cc419d64` |
