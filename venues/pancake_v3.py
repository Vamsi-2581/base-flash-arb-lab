"""PancakeSwap V3 on Base.

Same quoter ABI as Uniswap V3, different factory, quoter and fee ladder (2500 ppm
instead of 3000). The addresses are the ones already pinned in `bot/networks.py`.
"""
from __future__ import annotations

from venues import register
from venues.uniswap_v3 import V3Deployment, V3Venue

pancake_v3 = register(V3Venue(
    "pancake-v3", "PancakeSwap V3",
    {
        "base": V3Deployment(
            factory="0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",
            quoter="0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997",
            fees=(100, 500, 2500, 10000),
        ),
    },
))
