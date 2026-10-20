"""Chains this tool can talk to.

A chain entry is data only: chain id, default public RPC, and the addresses of the
tokens people actually quote against. Venue deployments live in the venue modules,
because a venue knows its own addresses.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chain:
    key: str
    label: str
    chain_id: int
    rpc_default: str
    tokens: dict[str, tuple[str, int]]  # symbol -> (address, decimals)

    def token(self, symbol_or_address: str) -> tuple[str, int]:
        """Resolve a symbol or pass through a 0x address (18 decimals assumed)."""
        if symbol_or_address.startswith("0x"):
            if len(symbol_or_address) != 42:
                raise ValueError(f"not an address: {symbol_or_address}")
            return symbol_or_address, 18
        try:
            return self.tokens[symbol_or_address.upper()]
        except KeyError:
            raise KeyError(
                f"{symbol_or_address} unknown on {self.key}; known: "
                f"{', '.join(sorted(self.tokens))}"
            ) from None


CHAINS: dict[str, Chain] = {
    "base": Chain(
        key="base",
        label="Base",
        chain_id=8453,
        rpc_default="https://mainnet.base.org",
        tokens={
            "USDC": ("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
            "WETH": ("0x4200000000000000000000000000000000000006", 18),
            "CBBTC": ("0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", 8),
            "AERO": ("0x940181a94A35A4569E4529A3CDfB74e38FD98631", 18),
            "DAI": ("0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb", 18),
        },
    ),
    "monad": Chain(
        key="monad",
        label="Monad",
        chain_id=143,
        rpc_default="https://rpc.monad.xyz",
        tokens={
            "USDC": ("0x754704Bc059F8C67012fEd69BC8A327a5aafb603", 6),
            "MUSD": ("0xacA92E438df0B2401fF60dA7E4337B687a2435DA", 6),
            "WMON": ("0x3bd359C1119dA7Da1D913D1C4D2B7c461115433A", 18),
        },
    ),
}


def get_chain(key: str) -> Chain:
    try:
        return CHAINS[key.lower()]
    except KeyError:
        raise KeyError(f"unknown chain {key!r}; known: {', '.join(sorted(CHAINS))}") from None
