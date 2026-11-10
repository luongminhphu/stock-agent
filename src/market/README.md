# market

Owner: market data.

**Boundary:** symbol registry (HOSE/HNX/UPCoM), quote service, OHLCV history, technical/context/news service, market data adapters.

**Contract:** Exposes `QuoteService`, `OHLCVService`, and `SymbolRegistry` as the public API. Adapters are internal implementation details.

**Do NOT put here:** thesis logic, watchlist state, AI prompts.

## Nguồn dữ liệu

| Vai trò | Adapter | Endpoint | Ghi chú |
|---|---|---|---|
| Bảng giá — chính | `VCIAdapter` | trading.vietcap.com.vn `price/symbols/getList` | realtime, VND |
| Bảng giá — dự phòng 1 + nguồn đối chứng | `KBSAdapter` | kbbuddywts.kbsec.com.vn `stock/iss` | realtime, VND |
| Bảng giá — dự phòng 2 | `VNDirectAdapter` | finfo-api.vndirect.com.vn `v4/stock_prices` | theo ngày, API trả nghìn đồng → ×1000 |
| Nến ngày (OHLCV) | `VCIOHLCVAdapter` | trading.vietcap.com.vn `chart/OHLCChart/gap-chart` | ngày nến tính theo giờ ICT |

`ChainedAdapter` thử lần lượt VCI → KBS → VNDirect; mã nào nguồn trước không trả về
thì lấy từ nguồn sau. Mỗi mã chỉ lấy dữ liệu từ một nguồn (không trộn trường).

## Đồng nhất giá mở/đóng cửa với CTCK

- `price_rules`: bước giá theo sàn (HOSE 10/50/100đ, HNX/UPCoM 100đ) và giờ chốt giá đóng
  cửa (HOSE/HNX 14:45 sau ATC, UPCoM 15:00 vì không có ATC).
- `price_reconciliation.SessionPriceReconciler`: khi chốt phiên, mỗi mã lấy open/close theo
  thứ tự ưu tiên (1) nến ngày chính thức của đúng phiên, (2) đồng thuận bảng giá VCI–KBS,
  (3) nguồn chính; sau đó làm tròn về bước giá và kiểm tra biên trần/sàn. Bất thường được log
  `price_reconcile.flagged` (lệch nến / lệch nguồn đối chứng / lệch bước giá / ngoài biên).
- `QuoteService.capture_eod_close(now=..., reconciler=...)` chỉ chốt mã có sàn đã đóng giá;
  mã còn lại trả về `pending`. Bot chạy lúc 14:50 (HOSE/HNX) và 15:05 ICT (UPCoM + phần còn lại).
