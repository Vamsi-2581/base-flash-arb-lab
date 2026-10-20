"""Verified Base addresses; always re-check against official deployments on live use."""
"""Base addresses for the legacy research pipeline.

Venue addresses are **not** duplicated here: they come from the `venues/` adapters,
which are the single source of truth. This module only adds the numeric ids and the
routers that the deployment and execution paths still need.

The supported entry point for new work is `arbcheck`; this pipeline is kept because
the fork replay, preflight and live-send paths depend on it.
"""
from dataclasses import dataclass

from venues.pancake_v3 import pancake_v3
from venues.uniswap_v3 import uniswap_v3

BASE_CHAIN_ID = 8453
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH = "0x4200000000000000000000000000000000000006"
AAVE_PROVIDER = "0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D"

_UNISWAP = uniswap_v3.deployments["base"]
_PANCAKE = pancake_v3.deployments["base"]

# Routers are only used by the execution path; quoting never needs them.
UNI_SWAP_ROUTER_02 = "0x2626664c2603336E57B271c5C0b26F421741e481"
PANCAKE_V3_SWAP_ROUTER = "0x1b81D678ffb9C0263b24A97847620C99d213eB14"

# Backwards-compatible aliases: existing callers import these names directly.
UNI_V3_FACTORY = _UNISWAP.factory
UNI_QUOTER_V2 = _UNISWAP.quoter
PANCAKE_V3_FACTORY = _PANCAKE.factory
PANCAKE_QUOTER_V2 = _PANCAKE.quoter


@dataclass(frozen=True)
class Exchange:
    label: str
    dex_id: int
    factory: str
    quoter: str
    router: str
    fees: tuple[int, ...]


EXCHANGES = (
    Exchange("UniswapV3", 0, _UNISWAP.factory, _UNISWAP.quoter,
             UNI_SWAP_ROUTER_02, _UNISWAP.fees),
    Exchange("PancakeV3", 1, _PANCAKE.factory, _PANCAKE.quoter,
             PANCAKE_V3_SWAP_ROUTER, _PANCAKE.fees),
)
