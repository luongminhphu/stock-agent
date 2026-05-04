"""Help command — data definitions.

Owner: bot segment.
File này là source of truth cho toàn bộ nội dung /help.
Khi thêm/bớt/sửa command, chỉ cần chỉnh sửa file này.
Không chứa Discord UI logic.
"""

from __future__ import annotations

from typing import TypedDict


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class CommandEntry(TypedDict):
    usage: str
    description: str
    example: str | None


class GroupEntry(TypedDict):
    label: str   # tên hiển thị trên dropdown
    emoji: str
    colour: int  # discord.Color int
    intro: str   # 1-line mô tả nhóm
    commands: list[CommandEntry]


# ---------------------------------------------------------------------------
# Data — cập nhật đây khi thêm/bớt command
# ---------------------------------------------------------------------------

HELP_DATA: dict[str, GroupEntry] = {
    "market": {
        "label": "Market · Giá cổ phiếu",
        "emoji": "📈",
        "colour": 0x0078D7,
        "intro": "Tra giá realtime cho một hoặc nhiều mã cổ phiếu.",
        "commands": [
            {
                "usage": "/quote <ticker>",
                "description": "Giá realtime cho một mã (HOSE/HNX/UPCoM).",
                "example": "/quote HPG",
            },
            {
                "usage": "/quote_bulk <tickers>",
                "description": "Giá cho nhiều mã cùng lúc, cách nhau dấu phẩy (tối đa 10).",
                "example": "/quote_bulk HPG,VNM,FPT",
            },
        ],
    },
    "watchlist": {
        "label": "Watchlist · Danh sách theo dõi",
        "emoji": "👁️",
        "colour": 0x00B96B,
        "intro": "Quản lý danh sách theo dõi, cảnh báo giá, và quét tín hiệu.",
        "commands": [
            {
                "usage": "/watchlist add <ticker> [note]",
                "description": "Thêm mã vào watchlist, kèm ghi chú tuỳ chọn.",
                "example": "/watchlist add VNM Theo dõi breakout",
            },
            {
                "usage": "/watchlist remove <ticker>",
                "description": "Xoá mã khỏi watchlist.",
                "example": "/watchlist remove VNM",
            },
            {
                "usage": "/watchlist list",
                "description": "Hiển thị toàn bộ watchlist kèm giá realtime.",
                "example": None,
            },
            {
                "usage": "/watchlist scan",
                "description": "Quét tín hiệu kỹ thuật và kiểm tra cảnh báo đang active trên toàn bộ watchlist.",
                "example": None,
            },
            {
                "usage": "/watchlist alert <ticker> <condition> <threshold>",
                "description": (
                    "Đặt cảnh báo giá/thay đổi/volume. Condition: "
                    "`price_above`, `price_below`, `change_pct_up`, `change_pct_down`, `volume_spike`."
                ),
                "example": "/watchlist alert HPG price_above 55000",
            },
        ],
    },
    "portfolio": {
        "label": "Portfolio · Danh mục đầu tư",
        "emoji": "💼",
        "colour": 0x2ECC71,
        "intro": "Ghi nhận giao dịch, theo dõi P&L realtime và conviction từ thesis.",
        "commands": [
            {
                "usage": "/buy <ticker> <qty> <price> [note]",
                "description": "Ghi nhận lệnh mua vào portfolio. Tự động tính giá vốn trung bình khi mua thêm.",
                "example": "/buy VCB 1000 85000",
            },
            {
                "usage": "/sell <ticker> <qty> <price> [note]",
                "description": "Ghi nhận lệnh bán. Hiển thị realized P&L ngay sau khi bán.",
                "example": "/sell VCB 500 92000",
            },
            {
                "usage": "/correct_trade <trade_id> <new_price>",
                "description": (
                    "Sửa giá mua sai của một BUY trade và tính lại giá vốn trung bình (VWAP). "
                    "Lấy trade_id từ `/history` (hiển thị `#ID` cạnh mỗi BUY) "
                    "hoặc footer của lệnh `/buy`. "
                    "Chỉ áp dụng cho BUY trade trên vị thế đang mở."
                ),
                "example": "/correct_trade 42 85000",
            },
            {
                "usage": "/portfolio [ticker] [view]",
                "description": (
                    "Xem danh mục hiện tại. "
                    "**view=trades** (mặc định): P&L unrealized theo giao dịch thực tế, "
                    "có thể lọc theo `ticker` cụ thể. "
                    "**view=thesis**: góc nhìn conviction — verdict AI (🐂 BULLISH / 🐻 BEARISH / ⚖️ NEUTRAL / 👁 WATCHLIST), "
                    "health score, P&L tính từ entry_price thesis và giá realtime."
                ),
                "example": "/portfolio view:thesis",
            },
            {
                "usage": "/history [ticker]",
                "description": (
                    "Lịch sử giao dịch đã thực hiện (20 lệnh gần nhất). "
                    "BUY trade hiển thị `#ID` để dùng với `/correct_trade`. "
                    "Kèm tổng kết realized P&L và win rate."
                ),
                "example": "/history VCB",
            },
        ],
    },
    "thesis": {
        "label": "Thesis · Investment thesis",
        "emoji": "📝",
        "colour": 0x7B2FBE,
        "intro": "Tạo, theo dõi, và review AI cho investment thesis của bạn.",
        "commands": [
            {
                "usage": "/thesis add <ticker> <title> <entry_price> <target_price> <stop_loss> [summary]",
                "description": "Tạo investment thesis mới với giá vào, mục tiêu, và stop-loss.",
                "example": "/thesis add HPG Thesis Q2 45000 60000 40000",
            },
            {
                "usage": "/thesis list [status]",
                "description": "Xem danh sách thesis. Status: `active` (default), `paused`, `closed`, `invalidated`, `all`.",
                "example": "/thesis list active",
            },
            {
                "usage": "/thesis close <thesis_id> <reason>",
                "description": "Đóng thesis (`closed` = đạt target/exit) hoặc huỷ (`invalidated` = thesis không còn valid).",
                "example": "/thesis close 12 closed",
            },
            {
                "usage": "/review_thesis <thesis_id>",
                "description": "Chạy AI review: verdict (Bullish/Bearish/Neutral/Watchlist), risk signals, next watch items, confidence score.",
                "example": "/review_thesis 12",
            },
            {
                "usage": "/conviction <ticker> [limit]",
                "description": (
                    "Xem Conviction Score Timeline: lịch sử health score qua các snapshot, "
                    "trend (📈 Improving / 📉 Declining / ➡️ Stable), breakdown 4 dimensions "
                    "(Assumptions · Catalysts · Risk/Reward · AI Confidence), "
                    "và verdict + confidence của AI review gần nhất. "
                    "`limit` = số snapshot tối đa (5–50, mặc định 20)."
                ),
                "example": "/conviction VCB",
            },
        ],
    },
    "decision": {
        "label": "Decision · Ghi nhận & học từ quyết định",
        "emoji": "🧠",
        "colour": 0x2980B9,
        "intro": "Ghi lại quyết định đầu tư và để AI phân tích outcome sau horizon để rút ra bài học.",
        "commands": [
            {
                "usage": "/log_decision <thesis_id> <action> <rationale> [horizon_days]",
                "description": (
                    "Ghi lại quyết định BUY/SELL/HOLD/ADD/REDUCE tại thời điểm thực tế. "
                    "Hệ thống đóng băng giá và thesis score hiện tại. "
                    "Sau `horizon_days` ngày (mặc định 30), outcome sẽ được evaluate tự động."
                ),
                "example": "/log_decision 5 BUY Breakout volume xác nhận, thesis còn valid 30",
            },
            {
                "usage": "/replay <decision_id>",
                "description": (
                    "AI phân tích một quyết định đã qua horizon: so sánh giá vào với giá thực tế, "
                    "verdict CORRECT/INCORRECT/MIXED, những gì đúng/sai, key lesson, pattern phát hiện, "
                    "và gợi ý điều chỉnh cho lần sau. "
                    "Lấy `decision_id` từ output của `/log_decision`."
                ),
                "example": "/replay 3",
            },
            {
                "usage": "/lessons [ticker] [limit]",
                "description": (
                    "Xem tổng hợp bài học AI đã rút ra từ các quyết định đã replay. "
                    "Mỗi bài học kèm verdict (CORRECT/INCORRECT/MIXED), pattern phát hiện, "
                    "và ngày ra quyết định. "
                    "`ticker` để lọc theo mã cụ thể. `limit` tối đa 50, mặc định 10."
                ),
                "example": "/lessons VCB 5",
            },
        ],
    },
    "stress_test": {
        "label": "Stress-Test · Kiểm tra sức chịu đựng thesis",
        "emoji": "🔬",
        "colour": 0xE74C3C,
        "intro": "AI chạy stress-test toàn bộ assumptions của thesis trước các kịch bản bất lợi.",
        "commands": [
            {
                "usage": "/stress_test <ticker>",
                "description": (
                    "Stress-test thesis đang active của mã: tạo kịch bản bất lợi, "
                    "đánh giá từng assumption (🟢 INTACT / 🟡 WEAKENED / 🔴 BROKEN), "
                    "xác suất invalidation, triggers cần theo dõi, và rủi ro vĩ mô. "
                    "Output: verdict + confidence. Không thay đổi trạng thái thesis."
                ),
                "example": "/stress_test VCB",
            },
        ],
    },
    "briefing": {
        "label": "Briefing · Bản tin thị trường",
        "emoji": "📰",
        "colour": 0xF5A623,
        "intro": "Bản tin AI tổng hợp thị trường, cá nhân hoá theo watchlist của bạn.",
        "commands": [
            {
                "usage": "/morning_brief",
                "description": "Bản tin buổi sáng: tổng quan thị trường, watchlist highlight, macro/sector context.",
                "example": None,
            },
            {
                "usage": "/eod_brief",
                "description": "Bản tin cuối phiên: diễn biến ngày, phân tích watchlist, điểm cần theo dõi ngày mai.",
                "example": None,
            },
        ],
    },
    "pretrade": {
        "label": "Pre-trade · Kiểm tra trước khi vào lệnh",
        "emoji": "🎯",
        "colour": 0x01696F,
        "intro": "AI kiểm tra thesis, tín hiệu, và brief trước khi bạn vào lệnh.",
        "commands": [
            {
                "usage": "/pretrade <ticker>",
                "description": (
                    "Kiểm tra toàn diện trước khi đặt lệnh: alignment với thesis đang active, "
                    "tín hiệu scan watchlist, đề cập trong brief gần nhất. "
                    "Output: verdict (BUY / HOLD / AVOID / REVIEW) + risk flags + confidence."
                ),
                "example": "/pretrade VCB",
            },
        ],
    },
    "analysis": {
        "label": "Analysis · Phân tích biến động",
        "emoji": "🔍",
        "colour": 0xE8534A,
        "intro": "AI phân tích nguyên nhân tăng/giảm đột biến của một mã cổ phiếu.",
        "commands": [
            {
                "usage": "/why <ticker>",
                "description": (
                    "Giải thích nguyên nhân tăng/giảm đột biến: "
                    "nguyên nhân kỹ thuật, cơ bản, macro context, risk flags và độ tin cậy phân tích."
                ),
                "example": "/why HPG",
            },
        ],
    },
    "system": {
        "label": "System · Trạng thái hệ thống",
        "emoji": "🖥️",
        "colour": 0x7F8C8D,
        "intro": "Kiểm tra trạng thái kết nối và sức khoẻ của bot và các dịch vụ phụ thuộc.",
        "commands": [
            {
                "usage": "/health",
                "description": (
                    "Kiểm tra trạng thái hệ thống: kết nối database, market data adapter, "
                    "AI client, và Discord bot runtime. "
                    "Hiển thị ✅ / ⚠️ / ❌ cho từng thành phần kèm latency."
                ),
                "example": None,
            },
        ],
    },
    "owner": {
        "label": "Owner · Công cụ quản trị",
        "emoji": "⚙️",
        "colour": 0x95A5A6,
        "intro": "Các lệnh dành riêng cho bot owner. Người dùng thường không thể chạy.",
        "commands": [
            {
                "usage": "/run_replay_scheduler",
                "description": (
                    "Kích hoạt DecisionReplayScheduler thủ công ngoài giờ cron. "
                    "Tìm tất cả decision đã đến hạn horizon, evaluate outcome, chạy ReplayAgent, "
                    "và lưu lesson. Hữu ích để test hoặc recovery sau downtime."
                ),
                "example": None,
            },
        ],
    },
}

_OVERVIEW_COLOUR: int = 0x4F8EF7
