# portfolio segment

## Owner
`portfolio` segment.

## Responsibility
Tracks the investor's open positions, trade history, and P&L.
This is the write-side and read-side for everything the investor *owns*.

## Boundary rules
- **Owns**: `Position`, `Trade` lifecycle (open, close, correct).
- **Consumes**: `market.QuoteService` for realtime prices (best-effort only).
- **Does NOT own**: thesis logic — `thesis_id` is an optional FK reference only.
- **Does NOT send**: Discord notifications — that is `bot` adapter concern.
- **Does NOT import from**: `ai`, `thesis`, `watchlist`, `briefing`, `bot`, `api`, `readmodel`.

## Dependency direction
```
bot / briefing / ai
        ↓
  portfolio  ←  market (QuoteService, prices only)
        ↓
    platform
```

## Public surface (import from `src.portfolio`)

| Symbol | Type | Description |
|---|---|---|
| `PortfolioService` | write-side service | buy, sell, correct_trade, list_open |
| `PnlService` | read-side service | unrealized PnL, realized summary, history |
| `get_portfolio_context()` | async factory | **single entry point for AI / briefing** |
| `PortfolioContext` | dataclass | immutable snapshot returned by factory |
| `PositionSummary` | dataclass | per-position data inside PortfolioContext |
| `PortfolioPnl` | dataclass | full PnL snapshot from PnlService |
| `PositionPnl` | dataclass | per-position PnL |
| `RealizedSummary` | dataclass | realized P&L summary |

## AI / briefing integration

Downstream segments (`ai`, `briefing`) **must** use `get_portfolio_context()` — 
do not import `Position` or `Trade` ORM models directly from outside this segment.

```python
from src.portfolio import get_portfolio_context, PortfolioContext

async with AsyncSessionLocal() as session:
    ctx: PortfolioContext = await get_portfolio_context(
        session,
        user_id="user_123",
        include_prices=False,   # True for briefing; False for hot AI paths
    )
```

`get_portfolio_context()` always succeeds — returns an empty `PortfolioContext`
(`has_positions=False`) when the user has no open positions.

## Internal structure

```
portfolio/
  __init__.py          ← public surface + get_portfolio_context() factory
  models.py            ← Position, Trade ORM models + PortfolioContext dataclass
  service.py           ← write-side: buy, sell, correct_trade, list_open
  pnl_service.py       ← read-side: PnL calculations
  repository.py        ← DB queries (used by service.py and pnl_service.py)
```

## Not yet implemented (Wave 4+)
- `performance.py` — portfolio-level analytics (sector exposure, win rate, Sharpe)
- Multi-user support — currently `user_id` is passed through but scheduler is single-user
