# Contributing

This repository is a research log as much as a codebase. The bar for a change is
**evidence**, not preference.

## Ground rules

- **Measurements over opinions.** A claim about an opportunity needs a block number, a
  quote, a run, or a failing test behind it. "This should be profitable" is not a finding.
- **Negative results are welcome.** Most of this repository is negative results, and they
  are the most useful part. Do not delete an honest failure to make the project look better.
- **Never claim realised profit** from a quote, a fork replay or a backtest. Use the
  vocabulary already in the docs: `QUOTED_NOT_FORK_VERIFIED`, `POST_FILL_BLOCK_QUOTE_ONLY`,
  `FORK_ESTIMATED_AFTER_GAS_POSITIVE`.
- **No secrets, ever.** No private keys, mnemonics, API keys, RPC tokens or funded addresses
  in code, tests, fixtures, logs, issues or pull requests.
- **Fail closed.** If a price, a gas figure or a pool is missing, return an error or skip the
  candidate. Code that quietly substitutes a default is rejected.

## Local setup

```bash
python3 -m unittest discover -s tests -v   # 88 tests, zero package dependencies
forge build
forge test -vv
```

Requires Python 3.10+ and Foundry. No `pip install` step exists on purpose.

## Pull requests

1. Keep the change scoped to one claim or one fix.
2. Add or update a test. Solidity changes need a Foundry test; Python changes need a
   `unittest` case under `tests/`.
3. If the change alters a documented number, update the document that states it.
4. State in the description what you measured and on which block, or why the behaviour
   could not be measured.
5. Run the unit tests and `forge test` locally and say so.

## What will be declined

- Strategies that only work by harming other users (sandwiching, front-running retail flow).
- Profitability claims without an on-chain artifact, a reproducible block and a cost model.
- Changes that weaken a safety guard: removing `paused`, widening the callback check,
  allowing arbitrary targets, or loosening the freshness or loss-budget caps.
- Dependency additions without a clear reason. The Python side deliberately has none.

## Security

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).
