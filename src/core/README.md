# core

**Owner:** `core` segment
**Role:** Khép vòng phản hồi hành động của nhà đầu tư.

Chỉ còn `UserActionFeedbackListener`: nhận `UserActionEvent` (SELL, BUY, IGNORE_ALERT,
MARK_REVIEWED, DEFER) và chuyển tác dụng phụ sang segment sở hữu (thesis, watchlist,
ai.memory). Không chứa luật nghiệp vụ — chỉ định tuyến.

Luật ranh giới: `core` được import mọi segment, không segment nào được import `core`.
