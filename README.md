# stock-agent

**Investor operating system cho thị trường chứng khoán Việt Nam (HOSE · HNX · UPCoM).**

Không phải dashboard gắn AI. stock-agent là một hệ thống AI-native — AI là lõi orchestration, mọi capability đều nằm trong một vòng tuần hoàn liên tục: theo dõi watchlist → phân tích thị trường → review thesis → briefing → hành động → feedback → cập nhật watchlist.

---

## Tổng quan hệ thống

```
watchlist ──► market context ──► AI analysis ──► thesis review
    ▲                                                     │
    └──────── feedback ◄── user action ◄── briefing ◄────┘
```

Người dùng không cần mở dashboard để biết điều gì đang xảy ra. Bot Discord chủ động gửi briefing buổi sáng, cảnh báo tín hiệu watchlist, nhắc review thesis sắp hết hạn — và luôn gợi ý hành động tiếp theo rõ ràng.

---

## Tính năng chính

### Watchlist & Scan
- Theo dõi danh sách cổ phiếu với alerts theo ngưỡng giá, volume, %thay đổi
- Scan tự động mỗi phiên — tổng hợp tín hiệu thành snapshot có thể đọc ngay
- Sector rotation screening lúc 09:10 ICT mỗi ngày

### Thesis
- Tạo / cập nhật / review investment thesis cho từng cổ phiếu
- AI phản biện thesis: phát hiện assumption chưa kiểm chứng, catalyst yếu, điều kiện invalidate
- Drift detection: cảnh báo khi thesis không còn phù hợp với diễn biến thị trường
- Conviction timeline: lịch sử niềm tin và lý do thay đổi

### Briefing
- **Morning Brief** (08:45 ICT): tổng hợp watchlist + thesis + context thị trường trước phiên
- **EOD Brief** (15:05 ICT): đánh giá kết quả phiên, cập nhật thesis nếu cần
- Narrative AI-generated — không phải bảng số, mà là phân tích có cấu trúc

### Pre-trade Analysis
- `/pretrade <ticker>` — phân tích trước khi mua/bán: verdict, risk signals, confidence, next watch items
- Structured output chuẩn: `verdict · risk_signals · confidence · action · reasoning`

### Portfolio
- Theo dõi holdings và exposure
- Stress test: mô phỏng kịch bản rủi ro với portfolio hiện tại

### Decision Log
- Ghi lại mọi quyết định mua/bán kèm lý do
- Replay: nhìn lại quyết định cũ để học từ đó

---

## Kiến trúc

### Modular Monolith — Bounded Contexts

```
src/
├── ai/          # AI client, prompt packs, agents, schemas
├── api/         # FastAPI routes, DTOs, response models
├── bot/         # Discord runtime, command handlers, schedulers
├── briefing/    # Morning brief, EOD brief, narrative generation
├── market/      # Quotes, OHLCV, technical context, market adapters
├── platform/    # Config, DB, logging, bootstrap, event bus
├── portfolio/   # Holdings, exposure, performance context
├── readmodel/   # Dashboard projections, ranking, optimised UI queries
├── thesis/      # Thesis lifecycle, assumptions, catalysts, review, scoring
└── watchlist/   # Watchlist service, scans, alerts, reminders
```

### Ownership rules (bất biến)
- **bot** và **api** là adapter mỏng — không chứa domain logic
- **thesis rule** → thuộc thesis, không để trong bot/scheduler
- **watchlist rule** → thuộc watchlist, không để trong AI agent
- **read/query concern** → đưa sang readmodel
- **prompt/schema concern** → giữ trong ai
- Mỗi patch chỉ tác động 1 segment chính + tối đa 1 adapter

### Tech stack

| Layer | Công nghệ |
|-------|----------|
| API | FastAPI · Pydantic v2 · uvicorn |
| Bot | discord.py v2 · discord.ext.tasks |
| AI | Perplexity API (OpenAI-compatible) |
| DB | SQLAlchemy async · Alembic migrations |
| Runtime | Python 3.12 · Docker Compose · uvloop |
| DB (dev) | SQLite (`aiosqlite`) |
| DB (prod) | PostgreSQL (`asyncpg`) |

---

## Cài đặt

### Yêu cầu
- Docker & Docker Compose
- Discord Bot Token (cần quyền `applications.commands`, `bot`)
- Perplexity API Key

### 1. Clone và cấu hình

```bash
git clone https://github.com/luongminhphu/stock-agent.git
cd stock-agent
cp .env.example .env
```

Mở `.env` và điền các biến bắt buộc:

```env
DISCORD_TOKEN=your-discord-bot-token
PERPLEXITY_API_KEY=pplx-xxxxxxxx
OWNER_USER_ID=your-discord-user-id
```

### 2. Chạy với Docker Compose

```bash
docker compose up -d
docker compose logs -f
```

Sau khi bot online, kiểm tra `/health` trong Discord.

### 3. Chạy local (không Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Chạy DB migrations
alembic upgrade head

# Chạy API
uvicorn src.api.app:app --reload --port 8000

# Chạy bot (terminal khác)
python -m src.bot
```

---

## Cấu hình

Tất cả biến môi trường được khai báo trong `.env.example`. Các biến quan trọng:

| Biến | Bắt buộc | Mô tả |
|------|----------|------|
| `DISCORD_TOKEN` | ✅ | Bot token từ Discord Developer Portal |
| `PERPLEXITY_API_KEY` | ✅ | API key cho AI analysis |
| `OWNER_USER_ID` | ✅ | Discord User ID của owner (single-user mode) |
| `DATABASE_URL` | ✅ | SQLite (dev) hoặc PostgreSQL (prod) |
| `MORNING_CHANNEL_ID` | — | Channel nhận Morning Brief lúc 08:45 ICT |
| `EOD_CHANNEL_ID` | — | Channel nhận EOD Brief lúc 15:05 ICT |
| `DISCORD_ALERT_CHANNEL_ID` | — | Channel nhận watchlist alerts |
| `SCHEDULER_USER_ID` | — | User ID chạy scheduled briefs (thường = OWNER_USER_ID) |
| `MOCK_MARKET` | — | `true` để bỏ qua market API thật trong dev |
| `DISCORD_GUILD_ID` | — | Guild ID để sync slash commands nhanh hơn (khuyến nghị) |

---

## Slash Commands

| Command | Mô tả |
|---------|------|
| `/watchlist` | Xem và quản lý danh sách theo dõi |
| `/thesis` | Tạo, cập nhật, review investment thesis |
| `/pretrade <ticker>` | Phân tích pre-trade với AI verdict |
| `/why <ticker>` | Giải thích tín hiệu / diễn biến giá |
| `/morning_brief` | Trigger morning brief thủ công |
| `/market` | Xem thông tin thị trường |
| `/portfolio` | Xem holdings và exposure |
| `/stress_test` | Chạy stress test portfolio |
| `/decision` | Ghi / xem lịch sử quyết định |
| `/conviction_timeline` | Lịch sử niềm tin theo thesis |
| `/sector_rotation` | Screening sector rotation |
| `/health` | Kiểm tra trạng thái hệ thống |

---

## Development

### Chạy tests

```bash
pytest
# hoặc với coverage
pytest --cov=src --cov-report=term-missing
```

### Thêm slash command mới

1. Tạo file `src/bot/commands/<tên>.py` với class `<Tên>Cog`
2. Đăng ký trong `src/bot/app.py` → `_register_cogs()`
3. Bot tự sync tree lên Discord sau khi khởi động lại

### Thêm AI agent mới

1. Tạo `src/ai/prompts/<tên>.py` — định nghĩa `SYSTEM_PROMPT`, `SPEC: PromptSpec`, `build_*_prompt()`
2. Tạo `src/ai/agents/<tên>.py` — sử dụng `AIClient` và `SPEC`
3. Đăng ký agent trong segment tương ứng (thesis / briefing / watchlist...)

### Migrations

```bash
# Tạo migration mới
alembic revision --autogenerate -m "mô tả thay đổi"

# Apply
alembic upgrade head

# Rollback 1 bước
alembic downgrade -1
```

---

## Triển khai production

```bash
# Build image
docker compose build --no-cache

# Khởi động
docker compose up -d

# Kiểm tra logs
docker compose logs -f api
docker compose logs -f bot

# Restart một service
docker compose restart bot
```

**Lưu ý production:**
- Dùng PostgreSQL thay SQLite: `DATABASE_URL=postgresql+asyncpg://...`
- Set `ENVIRONMENT=production` trong `.env`
- Đặt `DISCORD_GUILD_ID` để slash commands sync ngay — không đặt thì mất ~1 giờ để propagate toàn cầu

---

## License

Private repository. All rights reserved.
