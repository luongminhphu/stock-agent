# stock-agent

AI-native stock analysis platform for Vietnamese equity markets (HOSE, HNX, UPCoM).

## Architecture

Modular monolith with micro-segment boundaries. AI is the orchestration core, not a plugin.

```
src/
├── platform/    # config, db, logging, bootstrap
├── ai/          # perplexity client, prompt packs, agents, structured schemas
├── market/      # symbol registry, quotes, OHLCV, adapters
├── thesis/      # thesis lifecycle, assumptions, catalysts, scoring, invalidation
├── watchlist/   # watchlist, scan service, alerts, reminders
├── briefing/    # morning/EOD brief, narrative generation
├── readmodel/   # dashboard, leaderboard, read queries (read-only)
├── bot/         # discord adapter, command handlers, scheduler (thin)
└── api/         # fastapi app, routes, DTOs (thin)
```

Each segment has a `README.md` defining its boundary and contract.

## Principles

- **AI-native**: AI is the orchestration core for analysis, thesis review, briefing, and decision support.
- **Segment ownership**: Every rule lives in exactly one segment. Bot and API are adapters only.
- **Read/write separation**: `readmodel` is strictly read-only. Writes go through domain services.
- **Natural language intent**: System is designed around investor intents, not filter/report patterns.

## Setup

```bash
cp .env.example .env
# Fill in .env values
pip install -e '.[dev]'
```

## Development

```bash
pytest          # run tests
ruff check .    # lint
mypy src/       # type check
```

## Discord Commands

All commands are Discord slash commands. Bot responds ephemerally (private) unless noted.

### Market
| Command | Description |
|---|---|
| `/quote <ticker>` | Live quote for a single ticker (public response) |
| `/quote_bulk <tickers>` | Quotes for up to 10 comma-separated tickers (public) |

### Watchlist
| Command | Description |
|---|---|
| `/watchlist add <ticker> [note]` | Add ticker to your watchlist |
| `/watchlist remove <ticker>` | Remove ticker from your watchlist |
| `/watchlist list` | Show watchlist with live prices |
| `/watchlist scan` | Run signal + alert scan across watchlist |
| `/watchlist alert <ticker> <condition> <threshold>` | Set price/volume/change alert |

### Thesis
| Command | Description |
|---|---|
| `/thesis add <ticker> <title> <entry> <target> <stop>` | Create investment thesis |
| `/thesis list [status]` | List theses (active / paused / closed / invalidated / all) |
| `/thesis close <id> <reason>` | Close or invalidate a thesis |
| `/review_thesis <id>` | Run AI review on a thesis (verdict + risks + confidence) |

### Briefing
| Command | Description |
|---|---|
| `/morning_brief` | Generate morning market brief (public, watchlist-aware) |
| `/eod_brief` | Generate end-of-day brief (public, watchlist-aware) |

- **Wave 1** (current): Scaffold + platform + ai client + domain model skeletons
- **Wave 2**: Market adapters + thesis CRUD + watchlist CRUD + bot commands
- **Wave 3**: Briefing generation + thesis review agent + scan service
- **Wave 4**: Readmodel projections + API routes + scoring/leaderboard
