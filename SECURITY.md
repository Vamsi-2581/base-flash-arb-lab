# Security policy

## Status

This repository is **research code**. Nothing here is audited, nothing is deployed, and
no funds are held by any contract in this repository. Both executor contracts
(`src/FlashArb.sol`, `src/FlashLiquidator.sol`) start **paused** and are intended for
fork tests and local review only.

There is no bug bounty and no guaranteed response time.

## What is in scope

Reports that would matter **if someone deployed this code with real funds**:

- Ways to bypass the `paused` / `onlyOwner` / `active` (re-entrancy) guards.
- Ways to make the flash-loan callback accept an unauthorised initiator or pool.
- Ways to make the executor call an arbitrary target, token or calldata payload.
- Arithmetic or rounding errors that turn a checked "minimum out" / "minimum profit"
  into an unchecked one.
- Approval or accounting bugs that could leave residual allowance or misattribute the
  profit balance.
- Any secret, credential or private key committed to this repository or its history.
- Supply-chain problems in pinned dependencies or CI workflows.

## What is out of scope

- **The absence of a profitable strategy.** The documentation reports negative results
  on purpose; "this does not make money" is a finding, not a vulnerability. See
  `RESULTS.md`.
- Economic outcomes, market risk, MEV/ordering losses, or gas costs.
- Vulnerabilities in third-party protocols (Aave, Uniswap, PancakeSwap) or in public RPC
  providers. Report those upstream.
- Findings that require deploying to mainnet or spending funds, or any testing against
  live contracts.

## How to report

Use GitHub's **private vulnerability reporting** on this repository (Security tab →
"Report a vulnerability"). If that is unavailable, open a minimal issue that says only
that you have a security report and how to reach you — do not include exploit details in
a public issue.

Please include: affected file and function, a description of the failure mode, and a
minimal reproduction (a failing test is ideal).

## Safe testing rules

- Work against a local fork or the Foundry unit tests only.
- Do not deploy to any network with real value.
- Do not test against other people's contracts or accounts.
- Do not include private keys, API keys or funded addresses in a report or a commit.
