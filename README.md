# stock-agent

**Công cụ ra quyết định cho danh mục và watchlist cổ phiếu Việt Nam, dựa trên thống kê.**

Không dự báo. Hệ thống quan sát trạng thái hiện tại của từng mã, đối chiếu với các lần
**chính mã đó** từng ở trạng thái tương tự, rồi cho biết sau 60/120 phiên kết quả ra sao:
xác suất lãi, lãi/lỗ trung vị, mức sụt thường gặp, số đợt làm bằng chứng. AI chỉ làm
người diễn giải các con số đã tính — không tự chấm điểm, không đoán giá.

---

## Tính năng chính

### Bảng Danh mục & Watchlist
- Mỗi mã: giá, lãi/lỗ, tỉ trọng; trạng thái hiện tại (định giá, xu hướng, độ nóng,
  dòng tiền, tăng trưởng lợi nhuận); xác suất lãi sau 60/120 phiên so với mức nền của mã;
  độ tin cậy theo số đợt độc lập; gợi ý *nghiêng mua / nghiêng tránh / trung tính*.
- Cảnh báo khi mức lỗ hiện tại sâu hơn mức sụt thường gặp của các tiền lệ.

### Kế hoạch nắm giữ
- 4 câu hỏi: vì sao mua (chọn các "điều tôi tin" hệ thống tự kiểm được bằng số liệu),
  sai ở đâu thì bán, mục tiêu, khi nào xem lại — có gợi ý mức từ tiền lệ của mã.
- Đèn xanh / vàng / đỏ tự kiểm sau mỗi phiên (15:30 ICT) kèm lý do bằng lời thường;
  nút "Giải thích" để AI diễn giải khi cần.

### Phái sinh VN30F
- Sổ vị thế Long/Short: lãi/lỗ, ký quỹ, ngày đáo hạn, nhắc roll.
- Phòng hộ danh mục theo beta, basis & thống kê basis, tiền lệ của VN30.

### Briefing & nhật ký quyết định
- Morning brief / EOD brief trên Discord.
- `/pretrade`, nhật ký quyết định và replay để học từ quyết định cũ.

### Dữ liệu thị trường
- Nến ngày lưu DB (chỉ tải phần thiếu), giá đóng cửa đối soát theo giờ chốt của từng sàn.
- Mỗi nguồn API của CTCK đi qua giới hạn tốc độ + tạm nghỉ khi bị 429/403; chỉ gọi cho
  các mã trong danh mục ∪ watchlist.

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
├── readmodel/   # Dashboard projections, bảng danh mục & watchlist
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
| DB | PostgreSQL (`asyncpg`); test dùng SQLite in-memory |

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
| `DATABASE_URL` | ✅ | PostgreSQL, vd `postgresql+asyncpg://user:pass@host/db` |
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
| `/watchlist add·remove·list·scan` | Quản lý danh sách theo dõi |
| `/thesis add·list·close`, `/review_thesis` | Kế hoạch nắm giữ / thesis |
| `/pretrade <ticker>` | Phân tích trước khi mua/bán |
| `/buy`, `/sell`, `/correct_trade`, `/dividend` | Ghi giao dịch |
| `/portfolio`, `/history` | Danh mục, lịch sử giao dịch |
| `/quote`, `/quote_bulk` | Giá |
| `/morning_brief`, `/eod_brief` | Gọi brief thủ công |
| `/log_decision`, `/replay`, `/lessons` | Nhật ký quyết định |
| `/health`, `/help` | Trạng thái hệ thống, trợ giúp |

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

Lịch sử migration đã được gộp thành một baseline duy nhất `0001_baseline` (sinh từ model hiện tại).
DB tạo trước khi gộp không nâng cấp tại chỗ được — chạy `scripts/reset_db.sh` (xoá schema `public`,
dựng lại từ baseline; chặn khi `ENVIRONMENT=production`). CI kiểm tra `alembic check` để model và
migration không lệch nhau.

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
