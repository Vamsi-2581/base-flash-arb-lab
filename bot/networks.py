"""Verified Base addresses; always re-check against official deployments on live use."""
from dataclasses import dataclass

BASE_CHAIN_ID = 8453
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH = "0x4200000000000000000000000000000000000006"
AAVE_PROVIDER = "0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D"
# Fetched dynamically through AAVE_PROVIDER.getPool() for upgrade safety.
UNI_V3_FACTORY = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"
UNI_QUOTER_V2 = "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a"
UNI_SWAP_ROUTER_02 = "0x2626664c2603336E57B271c5C0b26F421741e481"
PANCAKE_V3_FACTORY = "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"
PANCAKE_QUOTER_V2 = "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997"
PANCAKE_V3_SWAP_ROUTER = "0x1b81D678ffb9C0263b24A97847620C99d213eB14"


@dataclass(frozen=True)
class Exchange:
    label: str
    dex_id: int
    factory: str
    quoter: str
    router: str
    fees: tuple[int, ...]


EXCHANGES = (
    Exchange("UniswapV3", 0, UNI_V3_FACTORY, UNI_QUOTER_V2,
             UNI_SWAP_ROUTER_02, (100, 500, 3000, 10000)),
    Exchange("PancakeV3", 1, PANCAKE_V3_FACTORY, PANCAKE_QUOTER_V2,
             PANCAKE_V3_SWAP_ROUTER, (100, 500, 2500, 10000)),
)
