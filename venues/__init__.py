"""Venue adapters: one module per DEX, one uniform interface.

The point of this package is that adding a venue is a bounded, testable change:
implement `discover()` and `quote()` and register the class. Nothing else in the
codebase needs to know the venue exists.

Every quote comes from the venue's own on-chain lens (QuoterV2, V4Quoter, ...), so
pool fee and price impact are already inside the returned amount. Nothing here
estimates a price from reserves.

Read-only: no keys, no signing, no transactions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from bot.abi import keccak256, selector, word  # noqa: F401  (re-exported for adapters)


class VenueError(RuntimeError):
    """A venue could not be discovered or quoted. Always fail closed."""


@dataclass(frozen=True)
class PoolRef:
    """A tradable pool on one venue.

    `address` is set for pool-per-contract venues (Uniswap V3). Venues that keep
    pools inside a singleton (Uniswap V4) leave it None and carry everything the
    quoter needs in `extra`.
    """
    venue: str
    chain: str
    fee: int
    token0: str
    token1: str
    address: str | None = None
    extra: tuple[Any, ...] = field(default=())

    def label(self) -> str:
        return f"{self.venue} {self.fee / 10_000:g}%"


@runtime_checkable
class Venue(Protocol):
    id: str
    label: str
    supports: tuple[str, ...]

    def discover(self, rpc, chain: str, token_a: str, token_b: str, block: str) -> list[PoolRef]:
        ...

    def quote(self, rpc, chain: str, pool: PoolRef, token_in: str, amount_in: int, block: str) -> int:
        ...


_REGISTRY: dict[str, Venue] = {}


def register(venue: Venue) -> Venue:
    if venue.id in _REGISTRY:
        raise ValueError(f"venue already registered: {venue.id}")
    _REGISTRY[venue.id] = venue
    return venue


def get_venue(venue_id: str) -> Venue:
    try:
        return _REGISTRY[venue_id]
    except KeyError:
        raise KeyError(f"unknown venue {venue_id!r}; known: {', '.join(sorted(_REGISTRY))}") from None


def all_venues() -> tuple[Venue, ...]:
    return tuple(_REGISTRY[k] for k in sorted(_REGISTRY))


def reset_registry() -> None:
    """Tests only: forget every registered venue."""
    _REGISTRY.clear()


# V3-style venues share one quoter ABI, so the selector lives here rather than in
# each adapter.
QUOTE_V3_SIGNATURE = "quoteExactInputSingle((address,address,uint256,uint24,uint160))"
QUOTE_V3_SELECTOR = selector(QUOTE_V3_SIGNATURE)

# Uniswap V4's quoter takes `bytes hookData`, not a sqrt price limit. Encoding the
# uint160 version reverts with empty data and looks exactly like an empty pool.
# The V4 quoter ABI is not the same on every deployment:
#   * Monad ships a variant whose last parameter is `bytes hookData`,
#   * the canonical v4-periphery takes `uint160 sqrtPriceLimitX96`, and its
#     signature is deployed both as a nested struct and as flat parameters.
# The last two encode to identical calldata and differ only in the selector, so an
# adapter can probe all three with one body. Getting this wrong reverts with empty
# data, which is indistinguishable from an empty pool.
QUOTE_V4_SIGNATURE = (
    "quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes))"
)
QUOTE_V4_SELECTOR = selector(QUOTE_V4_SIGNATURE)
QUOTE_V4_SELECTOR_STRUCT = selector(
    "quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,uint160))")
QUOTE_V4_SELECTOR_FLAT = selector(
    "quoteExactInputSingle((address,address,uint24,int24,address,bool,uint128,uint160))")
QUOTE_V4_STATIC_BODY = (QUOTE_V4_SELECTOR_STRUCT, QUOTE_V4_SELECTOR_FLAT)


def encode_v4_quote_static(pool_key: tuple[str, str, int, int, str], zero_for_one: bool,
                           amount_in: int, selector_: str) -> str:
    """Calldata for the sqrtPriceLimit-shaped quoter (nested or flat signature)."""
    currency0, currency1, fee, tick_spacing, hooks = pool_key
    if not 0 <= amount_in < (1 << 128):
        raise ValueError("amount_in must fit uint128")
    return "0x" + selector_ + "".join([
        word(currency0), word(currency1), word(fee), word(tick_spacing), word(hooks),
        word(1 if zero_for_one else 0), word(amount_in), word(0),
    ])


def encode_v4_quote(pool_key: tuple[str, str, int, int, str], zero_for_one: bool,
                    amount_in: int, hook_data: bytes = b"") -> str:
    """Calldata for V4Quoter.quoteExactInputSingle.

    The single parameter is a struct that contains a dynamic member, so the argument
    head starts with an offset to that struct. Inside the struct the first eight words
    are static (five for PoolKey, then bool, uint128 and the bytes offset) and the tail
    carries the bytes length and payload. Omitting the leading offset reverts with no
    data, which is easy to misread as an empty pool.
    """
    currency0, currency1, fee, tick_spacing, hooks = pool_key
    if not 0 <= amount_in < (1 << 128):
        raise ValueError("amount_in must fit uint128")
    inner_offset = 8 * 32                      # bytes offset, relative to the struct
    padded = ((len(hook_data) + 31) // 32) * 32
    body = hook_data.hex().ljust(padded * 2, "0")
    return "0x" + QUOTE_V4_SELECTOR + "".join([
        word(32),                              # offset to the struct itself
        word(currency0), word(currency1), word(fee), word(tick_spacing), word(hooks),
        word(1 if zero_for_one else 0), word(amount_in), word(inner_offset),
        word(len(hook_data)), body,
    ])


def encode_pool_id(pool_key: tuple[str, str, int, int, str]) -> bytes:
    """Uniswap V4 identifies a pool by keccak of its key, not by an address."""
    currency0, currency1, fee, tick_spacing, hooks = pool_key
    encoded = "".join([word(currency0), word(currency1), word(fee),
                       word(tick_spacing), word(hooks)])
    return keccak256(bytes.fromhex(encoded))


def encode_v3_quote(token_in: str, token_out: str, amount_in: int, fee: int) -> str:
    """Calldata for the QuoterV2 entry point shared by every V3-shaped venue."""
    return "0x" + QUOTE_V3_SELECTOR + "".join(
        word(a) for a in (token_in, token_out, amount_in, fee, 0))


def sort_pair(token_a: str, token_b: str) -> tuple[str, str]:
    """V4 orders currencies by address; every caller must agree on that order."""
    a, b = token_a.lower(), token_b.lower()
    return (a, b) if int(a, 16) < int(b, 16) else (b, a)


# Importing the adapters registers them. Kept at the bottom so the helpers above
# are defined first.
from venues import pancake_v3, uniswap_v3, uniswap_v4  # noqa: E402,F401
