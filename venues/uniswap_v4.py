"""Uniswap V4.

Two things make V4 different from V3:

1. A pool has no address. It is identified by `keccak(PoolKey)`, so discovery means
   reconstructing candidate keys and asking `StateView` whether they are initialised
   with liquidity.
2. The quoter takes `bytes hookData`, not `uint160 sqrtPriceLimitX96`. Encoding the
   V3-shaped struct reverts with empty data and looks exactly like an empty pool.

Pools with a non-zero hook cannot be found by key reconstruction, so this adapter
only sees hookless pools and says so instead of guessing.
"""
from __future__ import annotations

from dataclasses import dataclass

from bot.abi import encode, result_uint, word
from venues import (PoolRef, VenueError, QUOTE_V4_STATIC_BODY, encode_pool_id,
                    encode_v4_quote, encode_v4_quote_static, register, sort_pair)

ZERO_HOOKS = "0x0000000000000000000000000000000000000000"
FEE_CANDIDATES = (0, 100, 500, 3000, 10000)
TICK_SPACINGS = (1, 10, 20, 50, 60, 100, 200)


@dataclass(frozen=True)
class V4Deployment:
    quoter: str
    state_view: str
    pool_manager: str


class V4Venue:
    id = "uniswap-v4"
    label = "Uniswap V4"

    def __init__(self, deployments: dict[str, V4Deployment]) -> None:
        self._deployments = deployments
        self.supports = tuple(sorted(deployments))
        # Which calldata shape this chain's quoter accepts, discovered once and
        # reused. None means "not probed yet".
        self._selector: dict[str, str | None] = {}

    def _deployment(self, chain: str) -> V4Deployment:
        try:
            return self._deployments[chain]
        except KeyError:
            raise VenueError(f"uniswap-v4 is not deployed on {chain}") from None

    def discover(self, rpc, chain: str, token_a: str, token_b: str, block: str) -> list[PoolRef]:
        deployment = self._deployment(chain)
        token0, token1 = sort_pair(token_a, token_b)
        pools: list[PoolRef] = []
        for fee in FEE_CANDIDATES:
            for tick_spacing in TICK_SPACINGS:
                key = (token0, token1, fee, tick_spacing, ZERO_HOOKS)
                pool_id = encode_pool_id(key)
                # word() encodes ints, so the 32-byte pool id goes in as an integer.
                pool_id_int = int.from_bytes(pool_id, "big")
                slot = rpc.eth_call(deployment.state_view,
                                    encode("getSlot0(bytes32)", pool_id_int), block)
                sqrt_price = result_uint(slot, 0)
                if sqrt_price == 0:
                    continue  # key never initialised
                liquidity_raw = rpc.eth_call(
                    deployment.state_view,
                    encode("getLiquidity(bytes32)", pool_id_int), block)
                if result_uint(liquidity_raw, 0) == 0:
                    # Initialised but empty: quoting it returns a meaningless number.
                    continue
                pools.append(PoolRef(venue=self.id, chain=chain, fee=fee,
                                     token0=token0, token1=token1, address=None,
                                     extra=(tick_spacing, ZERO_HOOKS, "0x" + pool_id.hex())))
        return pools

    def quote(self, rpc, chain: str, pool: PoolRef, token_in: str,
              amount_in: int, block: str) -> int:
        if amount_in <= 0:
            raise VenueError("amount_in must be positive")
        if token_in.lower() not in (pool.token0, pool.token1):
            raise VenueError(f"{token_in} is not part of pool {pool.extra[-1]}")
        tick_spacing, hooks, _pool_id = pool.extra
        zero_for_one = token_in.lower() == pool.token0
        key = (pool.token0, pool.token1, pool.fee, tick_spacing, hooks)
        deployment = self._deployment(chain)
        attempts = []
        if chain in self._selector and self._selector[chain]:
            attempts.append(self._selector[chain])
        else:
            attempts = [None, *QUOTE_V4_STATIC_BODY]  # None = the hookData shape

        last_error: Exception | None = None
        for candidate in attempts:
            calldata = (encode_v4_quote(key, zero_for_one, amount_in) if candidate is None
                        else encode_v4_quote_static(key, zero_for_one, amount_in, candidate))
            try:
                raw = rpc.eth_call(deployment.quoter, calldata, block)
            except Exception as exc:  # a wrong selector looks exactly like an empty pool
                last_error = exc
                continue
            self._selector[chain] = candidate
            return result_uint(raw, 0)
        raise VenueError(f"{self.id} on {chain}: no accepted quoter ABI ({last_error})")


uniswap_v4 = register(V4Venue({
    "base": V4Deployment(
        quoter="0x0d5e0f971ed27fbff6c2837bf31316121532048d",
        state_view="0xa3c0c9b65bad0b08107aa264b0f3db444b867a71",
        pool_manager="0x498581ff718922c3f8e6a244956af099b2652b2b",
    ),
    "monad": V4Deployment(
        quoter="0xa222dd357a9076d1091ed6aa2e16c9742dd26891",
        state_view="0x77395f3b2e73ae90843717371294fa97cc419d64",
        pool_manager="0x188d586ddcf52439676ca21a244753fa19f9ea8e",
    ),
}))
