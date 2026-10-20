"""Persisted experimental Gas-loss reservations, intentionally fail-closed."""
import json
from datetime import datetime, timezone
from pathlib import Path


class LimitExceeded(RuntimeError):
    pass


def reserve_budget(path: Path, amount_usdc: int,
                   *, daily_usdc: int = 2_000_000,
                   lifetime_usdc: int = 20_000_000) -> dict:
    """Reserve full worst-case loss *before* an attempted broadcast.

    This is intentionally conservative: reservations are never refunded automatically.
    Only run one broadcaster per wallet; atomic multi-process locking is NOT supported.
    """
    if amount_usdc <= 0 or daily_usdc <= 0 or lifetime_usdc <= 0:
        raise ValueError("Positive limits required")
    now = datetime.now(timezone.utc).date().isoformat()
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
    else:
        state = {"day": now, "day_reserved_usdc": 0, "lifetime_reserved_usdc": 0}
    if state["day"] != now:
        state["day"] = now
        state["day_reserved_usdc"] = 0
    if (state["day_reserved_usdc"] + amount_usdc > daily_usdc or
        state["lifetime_reserved_usdc"] + amount_usdc > lifetime_usdc):
        raise LimitExceeded("Daily or total Gas-risk budget exhausted")
    state["day_reserved_usdc"] += amount_usdc
    state["lifetime_reserved_usdc"] += amount_usdc
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)
    return state
