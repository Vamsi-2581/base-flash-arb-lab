"""Uniswap V3 and its forks: one pool per fee tier, quoted through QuoterV2.

Adding a V3 fork is three verified addresses and a fee list. That is the whole
adapter, which is why this module serves Uniswap V3 and PancakeSwap V3 from one
class.
"""
from __future__ import annotations

from dataclasses import dataclass

from venues import PoolRef, VenueError, encode_v3_quote, register, sort_pair
from bot.abi import encode, result_address, result_uint, word

ZERO = "0x0000000000000000000000000000000000000000"
GET_POOL = "getPool(address,address,uint24)"


@dataclass(frozen=True)
class V3Deployment:
    factory: str
    quoter: str
    fees: tuple[int, ...]


class V3Venue:
    """A Uniswap-V3-shaped venue on one or more chains."""

    def __init__(self, venue_id: str, label: str,
                 deployments: dict[str, V3Deployment]) -> None:
        self.id = venue_id
        self.label = label
        self._deployments = deployments
        self.supports = tuple(sorted(deployments))

    @property
    def deployments(self) -> dict[str, "V3Deployment"]:
        """Read-only view for code that builds its own registry (e.g. bot/networks)."""
        return dict(self._deployments)

    def _deployment(self, chain: str) -> V3Deployment:
        try:
            return self._deployments[chain]
        except KeyError:
            raise VenueError(f"{self.id} is not deployed on {chain}") from None

    def discover(self, rpc, chain: str, token_a: str, token_b: str, block: str) -> list[PoolRef]:
        deployment = self._deployment(chain)
        token0, token1 = sort_pair(token_a, token_b)
        pools: list[PoolRef] = []
        for fee in deployment.fees:
            raw = rpc.eth_call(deployment.factory,
                               encode(GET_POOL, token0, token1, fee), block)
            address = result_address(raw)
            if address == ZERO:
                continue
            pools.append(PoolRef(venue=self.id, chain=chain, fee=fee,
                                 token0=token0, token1=token1, address=address))
        return pools

    def quote(self, rpc, chain: str, pool: PoolRef, token_in: str,
              amount_in: int, block: str) -> int:
        if amount_in <= 0:
            raise VenueError("amount_in must be positive")
        if token_in.lower() not in (pool.token0, pool.token1):
            raise VenueError(f"{token_in} is not part of pool {pool.address}")
        token_out = pool.token1 if token_in.lower() == pool.token0 else pool.token0
        deployment = self._deployment(chain)
        # The quoter takes the fee tier, not the pool address: the factory guarantees
        # exactly one pool per (pair, fee), so the pair plus fee is unambiguous.
        calldata = encode_v3_quote(token_in, token_out, amount_in, pool.fee)
        raw = rpc.eth_call(deployment.quoter, calldata, block)
        return result_uint(raw, 0)


uniswap_v3 = register(V3Venue(
    "uniswap-v3", "Uniswap V3",
    {
        "base": V3Deployment(
            factory="0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
            quoter="0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a",
            fees=(100, 500, 3000, 10000),
        ),
        "monad": V3Deployment(
            factory="0x204faca1764b154221e35c0d20abb3c525710498",
            quoter="0x661e93cca42afacb172121ef892830ca3b70f08d",
            fees=(100, 500, 3000, 10000),
        ),
    },
))
