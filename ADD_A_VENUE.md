# Adding a venue

A venue adapter is the smallest useful contribution to this repository: one module,
two methods, one registration. If you can read a contract's quoting function, you can
add a venue in an afternoon.

Read this alongside `venues/uniswap_v3.py` — it is about 70 lines and is the reference
implementation.

## The contract

Every adapter implements two methods and declares which chains it exists on:

```python
class MyVenue:
    id = "my-venue"            # used on the command line: --venues my-venue
    label = "My Venue"         # human name
    supports = ("base",)       # chains from venues/chains.py

    def discover(self, rpc, chain, token_a, token_b, block) -> list[PoolRef]:
        """Return every pool for the pair. Empty list means 'no pool', not an error."""

    def quote(self, rpc, chain, pool, token_in, amount_in, block) -> int:
        """Exact amount out for `amount_in` of `token_in`, in the pool's output units."""
```

`rpc` is `bot.rpc.RPC` with `eth_call(to, data, block)`. `block` is a pinned block tag
such as `"latest"` or `"0x3234e2a"`; always pass it through rather than letting a
venue read a different block.

`PoolRef` carries what the quoter needs:

| Field | Meaning |
|---|---|
| `venue`, `chain` | identity |
| `fee` | pool fee in parts per million |
| `token0`, `token1` | the pair in the venue's own ordering |
| `address` | pool contract, or `None` when the venue keeps pools in a singleton |
| `extra` | anything else the quoter needs, e.g. tick spacing and hooks |

## Rules that are not optional

1. **Quote through the venue's own lens.** Do not estimate a price from reserves.
   Pool fee and price impact must be inside the returned integer.
2. **Fail closed.** If a call reverts, a result is short, or a price is zero, raise
   `VenueError`. Never substitute a default and never return `0` as "nothing traded".
3. **Drop empty pools at discovery.** A pool that exists but holds no in-range
   liquidity quotes as an absurd price. Check liquidity, not just existence.
4. **Sort before hashing or calling.** Several venues identify a pool by a hash of an
   ordered pair; use `sort_pair()` so every caller agrees on the order.
5. **Pin the addresses.** Copy them from the venue's official deployment page and
   cross-check on the explorer. Put them in the adapter, not in user config.

## Step by step

1. Create `venues/<name>.py`.
2. Implement `discover()` and `quote()`. Use `bot.abi.encode`, `word`, `result_uint`
   and `result_address` — the package has no external dependencies and neither
   should your adapter.
3. Register it at the bottom of the module:
   ```python
   from venues import register
   my_venue = register(MyVenue())
   ```
4. Add the module to the import list at the bottom of `venues/__init__.py`.
5. Add tests in `tests/test_venues.py` using the `FakeRpc` helper. Cover:
   - discovery skips a zero address / an empty pool,
   - a quote returns the expected word,
   - an unsupported chain raises `VenueError`.
6. Run `python3 -m unittest discover -s tests` and `python3 -m arbcheck venues`.

## A golden test is better than a unit test

If you can, pin one real pool and one real block, and assert the quoted output to the
last wei. That catches fee-tier mistakes, decimal mistakes and ABI mistakes in one
assertion. Put the block number in the test name so a future reader knows it was
verified against chain state, not against another mock.

## Checklist for a pull request

- [ ] Addresses copied from the official deployment page and cross-checked on an explorer
- [ ] `discover()` filters pools with no in-range liquidity
- [ ] `quote()` raises instead of guessing
- [ ] `supports` lists only chains where the venue is actually deployed
- [ ] Tests added; `python3 -m unittest discover -s tests` passes
- [ ] `python3 -m arbcheck quote --pair USDC/WETH --venues <yours>` returns a sane price
- [ ] The README code map lists the new module
