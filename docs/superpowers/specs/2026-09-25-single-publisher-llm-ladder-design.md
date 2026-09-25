# Thiết kế: một nguồn đăng duy nhất + thang LLM cho Tin nhanh 247

Ngày: 2026-09-25 · Trạng thái: chờ duyệt

## 1. Bối cảnh (số liệu đo trên VPS ngày 2026-09-25)

- Hai hệ thống cùng đăng vào kênh @fastnews247vn (`-1004328346315`), mỗi bên giữ
  danh sách tin đã đăng riêng:
  - bot `AutoTinTuc` (systemd, mỗi 10 phút);
  - cron OpenClaw `fastnews247:coin369-autopost` (mỗi 2 phút, agent tự lấy tin,
    tự lọc trùng, tự viết bài).
- Cron tốn trung vị **386 nghìn token/lượt** (50k input mới + ~330k cache +
  2k output) × ~720 lượt/ngày. Nó làm cạn cả 5 tài khoản ChatGPT (đang cooldown
  từ 22 giờ đến 5 ngày) và lỗi 100% từ 2026-09-24 15:28.
- Bot gọi LLM trong một phiên cố định `agent:main:fastnews247-editor`, phiên này
  đã tích 122k token. Mỗi lần viết lại một tin đều gửi kèm toàn bộ lịch sử đó.
- Gateway đang dùng 4,4 GB RSS, trả lời HTTP mất 13,6 giây. Bot đăng qua
  `bridge`, nên việc đăng bài phụ thuộc vào gateway.
- Bot đăng 68–82 tin/ngày (21–24/9); chỉ 1–3 lượt/ngày chạm trần 3 tin/lượt.
  Số tin bị giới hạn bởi nguồn và cổng chất lượng, không phải bởi nhịp quét.
- Tin đã đăng: 138 tin 4 sao, 85 tin 5 sao (**38% là 5 sao**).
- Lượt chạy gần nhất: 19 ứng viên bị loại vì dịch máy hỏng, 23 bị loại vì hết
  lượt kiểm tra bài (`article-check-budget-exhausted`).
- API key `openai:default` trả về 401 (hỏng).
- Laptop vẫn chạy task `OpenClaw Fast News 247` mỗi 5 phút, cố đăng qua bridge
  lên cùng kênh (31 lần treo, 1 lần gửi thành công). Task `OpenClaw Gateway` (chạy
  khi đăng nhập) vẫn bật và đã chạy lúc 11:15 hôm nay; khi chạy, nó tranh
  `getUpdates` với gateway trên VPS.
- `openclaw status --usage` trả về `OpenAI: Unsupported provider`: không đọc được
  hạn mức tuần của subscription.
- Coin369 là tin nhanh **tiếng Việt** (87–600 ký tự/bài, có tiền tố `🔹 10:35:`).

## 2. Mục tiêu

1. Kênh đăng liên tục, không dừng vì LLM, quota hay gateway.
2. Tin lên nhanh: quét mỗi 2 phút, đăng mọi tin đạt chuẩn, không trần theo giờ.
3. LLM viết mọi bài; tin 5 sao dùng model mạnh hơn.
4. Chi phí dự đoán được, có trần cứng theo ngày.
5. Subscription dành cho agent OpenClaw; bot chỉ dùng nó làm dự phòng có kiểm soát.

**Không thuộc đợt này:** gỡ `NOPASSWD` sudo, cập nhật OpenClaw 2026.9.6, sửa cron
heartbeat/memory-dreaming/skill-review, reboot kernel.

## 3. Kiến trúc

```
fastnews247.timer (2 phút)
  └─ fastnews247_mvp.py --once --post          (worker.lock chống chạy chồng)
       1. Tải 49 RSS + Coin369      ← GET có điều kiện (ETag / Last-Modified)
       2. Chấm điểm, độ mới, chống trùng          (giữ nguyên)
       3. Lấy nội dung bài gốc                     (giữ nguyên; Coin369 dùng chính bài Telegram)
       4. Thang biên tập (mode "ladder"):
            a. OpenAI API   — 5 sao: gpt-5.5 · còn lại: gpt-5.4-mini
            b. Subscription — `openclaw agent exec`, chỉ khi (a) không dùng được
            c. Dịch máy     — hiện có
          mọi bậc đi qua CÙNG bộ cổng sự thật và chất lượng hiện có
       5. Đăng thẳng Telegram Bot API (mode "direct", có xử lý retry_after)

healthcheck.sh (cron mỗi giờ)
  ├─ DM cảnh báo qua Bot API (không qua gateway)
  └─ ghi hạn mức subscription vào storage/fastnews247/subscription_quota.json
```

Sau thay đổi này, việc đăng bài không còn phụ thuộc vào gateway OpenClaw. Gateway
chỉ còn phục vụ agent chat và làm bậc dự phòng (b).

## 4. Thành phần

### A. Chế độ biên tập `ladder`

Cấu hình mới trong `config/fastnews247.sources.json`:

```json
"editorial": {
  "mode": "ladder",
  "openai": {
    "apiKeyEnv": "OPENAI_API_KEY",
    "model": "gpt-5.4-mini",
    "hotModel": "gpt-5.5",
    "hotMinScore": 5,
    "reasoningEffort": "low",
    "timeoutSeconds": 45,
    "dailyBudgetUsd": 3.0,
    "hotDailyBudgetUsd": 1.5,
    "pricesPerMTok": {
      "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
      "gpt-5.5":      {"input": 5.00, "output": 30.00}
    }
  },
  "subscription": {
    "enabled": true,
    "model": "openai/gpt-5.5",
    "maxCallsPerHour": 10,
    "minUsableProfiles": 2,
    "quotaCacheMaxAgeMinutes": 120,
    "timeoutSeconds": 180
  }
}
```

Các chế độ cũ `translate`, `auto`, `openclaw` vẫn giữ để chạy được trên Windows và
để quay lui nhanh.

**Gọi OpenAI:**
- Gọi thẳng HTTPS bằng `urllib`, dùng thư viện chuẩn như phần còn lại của bot,
  không thêm phụ thuộc.
- Mỗi lần gọi độc lập, không có phiên, không kèm lịch sử.
- Yêu cầu model trả JSON đúng schema `{title, summary}` (Structured Outputs).
  Endpoint và tên tham số cụ thể sẽ kiểm tra lại với tài liệu OpenAI khi triển khai.
- Prompt dùng lại `EDITORIAL_PROMPT` hiện có.
- Chi phí tính từ số `input_tokens` / `output_tokens` API trả về, nhân với bảng
  giá trong config.

**Thứ tự chọn bậc cho mỗi tin:**

| Điều kiện | Bậc được dùng |
|---|---|
| điểm ≥ `hotMinScore`, đã chi cho tin HOT < `hotDailyBudgetUsd`, tổng < `dailyBudgetUsd` | `hotModel` |
| tổng đã chi < `dailyBudgetUsd` | `model` |
| API bị tắt, hoặc vượt trần | subscription (nếu qua được điều kiện chặn) |
| subscription không dùng được | dịch máy |

- Ngày tính theo giờ Việt Nam. Trần có thể bị vượt tối đa bằng chi phí của đúng
  một lần gọi (khoảng 0,03 USD).

**Kiểm tra kết quả:**
- Kết quả LLM đi qua đúng các cổng hiện có: `_fact_numbers` phải là tập con của
  nguồn, `looks_vietnamese`, `headline_quality_issues`, `summary_quality_issues`.
- LLM trượt cổng thì chuyển sang dịch máy; dịch máy cũng trượt thì bỏ tin.
- Mỗi tin tối đa một lần gọi LLM thành công, cộng tối đa một lần thử lại khi lỗi
  đường truyền.

**Sổ ghi `storage/fastnews247/llm_ledger.json`** (ghi nguyên tử bằng `save_json`):

```json
{"days": {"2026-09-25": {"spendUsd": 0.0, "hotSpendUsd": 0.0, "calls": 0,
                          "byTier": {}, "errors": {}}},
 "subscriptionCalls": {"2026-09-25T14": 0},
 "apiDisabledUntil": 0, "lastApiError": ""}
```

- Chỉ giữ 14 ngày gần nhất.

### B. Bậc dự phòng bằng subscription

- Gọi `openclaw agent exec --message-file <tmp> --model openai/gpt-5.5 --thinking low
  --json --timeout 180 --cwd <thư mục tạm>`. Lệnh này chạy một lượt agent độc lập.
  Khi triển khai sẽ kiểm tra nó không để lại session. Nếu có để lại, thêm
  `--state-dir` tạm và xoá sau khi chạy.
- Bỏ hẳn cách gọi cũ `openclaw agent --session-key agent:main:fastnews247-editor`
  ở chế độ `ladder`.
- **Điều kiện chặn:** chỉ dùng khi đủ cả ba điều sau:
  - file `subscription_quota.json` được ghi trong vòng 120 phút;
  - còn ≥ 2 tài khoản OAuth **không** bị cooldown (`usableProfiles`);
  - số lần gọi trong giờ hiện tại < 10.

  Thiếu file hoặc không đọc được thì coi như không dùng được. Lý do không dùng
  "hạn mức tuần ≥ 50%": OpenClaw không đọc được hạn mức tuần của OpenAI. Số tài
  khoản còn dùng được là tín hiệu thay thế đo được, và giữ lại ít nhất một tài
  khoản cho agent.

### C. Nguồn Coin369 (loại nguồn `telegram_public`)

```json
{"name": "Coin369", "type": "telegram_public",
 "url": "https://t.me/s/coin369channel", "category": "world_macro",
 "priority": 2, "sourceTier": "repost", "minimumTextChars": 80}
```

Kênh đăng cả tin vĩ mô, địa chính trị và thị trường, không chỉ crypto, nên để
category `world_macro`. Ngưỡng độ dài để 80 ký tự vì nhiều bài chỉ dài 87–130 ký
tự.

**Bộ đọc trang** (dùng `HTMLParser` của thư viện chuẩn):
- Đọc các khối `tgme_widget_message`:
  - `data-post` → mã bài và link `https://t.me/coin369channel/<id>`;
  - `tgme_widget_message_text` → nội dung;
  - `time[datetime]` → thời gian đăng.
- Tiêu đề là câu hoặc dòng đầu tiên của nội dung, tối đa 200 ký tự.

**Xử lý trong `run_once`:**
- Tin có `inline_article` dùng luôn nội dung đó làm `article_text`, không tải
  trang bài.
- Ngưỡng độ dài lấy từ `minimumTextChars` của nguồn này.

**Quan hệ với các nguồn gốc:**
- Priority 2 cho điểm thấp hơn nguồn gốc, nên trong cùng một lượt tin nguồn gốc
  được xếp và biên tập trước.
- `same_event` so sánh từ ngữ trong tiêu đề, nên **không** nhận ra một tin tiếng
  Việt và một tin tiếng Anh nói cùng một sự kiện. Vì vậy thêm chống trùng theo
  tiếng Việt (mục D2).
- Bài quảng cáo không khớp tài sản nào nên được 1 điểm và tự bị loại.
- Nếu Telegram đổi HTML, bộ đọc trả về 0 tin, ghi cảnh báo nguồn, các nguồn khác
  vẫn chạy bình thường.

### D. Quét nhanh

**Timer:** `OnBootSec=2min`, `OnUnitActiveSec=2min`, `AccuracySec=10s`; sửa lại
Description cho đúng.

**GET có điều kiện:**
- `storage/fastnews247/feed_cache.json` lưu theo từng URL: ETag, Last-Modified và
  **danh sách tin đã parse**.
- Nếu server trả 304 thì dùng lại danh sách tin đã lưu. Không được trả về rỗng,
  vì tin chưa kịp xử lý ở lượt trước (do hết lượt kiểm tra bài hoặc chạm
  `maxPostsPerRun`) sẽ bị mất.

**Giới hạn mỗi lượt:**
- `maxPostsPerRun` tăng từ 3 lên 6.
- `maxArticleChecksPerRun` giữ 30.
- `maxPostsPerSourcePerRun` giữ 1.

### D2. Chặn chi phí và trùng lặp trong `run_once`

- **Nhớ tin bị loại:** tin trượt cổng chất lượng được ghi vào `state["rejected"]`
  và bỏ qua trong 60 phút (`posting.rejectRetryMinutes`). Nếu không có bước này,
  cùng một tin hỏng bị viết lại, và phải trả tiền, mỗi 2 phút cho tới khi hết hạn
  8 giờ.
- **Giới hạn tin mỗi nguồn kiểm tra trước khi viết bài:** trước đây bước này nằm
  sau `draft_post`, nên bot trả tiền viết những tin mà sau đó bị bỏ đi.
- **Chống trùng theo tiếng Việt:** mỗi bài đã đăng lưu thêm `postedTitle` (tiêu đề
  tiếng Việt đã đăng). Một tin bị coi là trùng nếu `same_event` khớp giữa tiêu đề
  tiếng Việt của nó với một `postedTitle` trong 72 giờ, hoặc với tin khác đã chọn
  trong cùng lượt. Việc so sánh diễn ra:
  - **trước khi viết bài**, với tin nguồn có tiêu đề sẵn tiếng Việt (Coin369,
    CafeF…), nên không tốn tiền;
  - **sau khi viết bài**, với tin nguồn tiếng Anh.
- **LLM được đọc nhiều bài gốc hơn:** ở chế độ `ladder`, LLM nhận tối đa 3.000 ký
  tự bài gốc đã xác minh (`openai.sourceChars`) thay vì chỉ 2 câu đã chọn. Số liệu
  vẫn chỉ được lấy từ đúng phần văn bản đã đưa cho LLM.

### E. Đăng thẳng qua Telegram

- `posting.telegram.mode` đổi từ `bridge` sang `direct`. Token và channel đã có
  trong `.env`.
- Khi Telegram trả HTTP 429: đọc `parameters.retry_after`. Nếu ≤ 30 giây thì chờ
  rồi thử lại một lần; nếu không thì trả `pending/telegram-rate-limited`. Cơ chế
  ghi trước (write-ahead) hiện có sẽ lo phần tin chưa chắc đã gửi.
- Giữ nguyên code `bridge` cho Windows.
- Bot chỉ gọi `sendMessage` nên không tranh `getUpdates` với gateway.

### F. Healthcheck

**Gửi cảnh báo:**
- DM gửi qua Bot API `sendMessage`, token lấy từ `.env`.
- Nếu Bot API thất bại thì mới dùng `openclaw message send`.

**Ngưỡng và cảnh báo mới:**
- `QUIET_HOURS_LIMIT` giảm từ 12 xuống 3.
- Thêm cảnh báo:
  - `apiDisabledUntil` còn hiệu lực, hoặc `lastApiError` là 401/403/`insufficient_quota`;
  - chi tiêu hôm nay ≥ `dailyBudgetUsd`.

**Ghi trạng thái subscription:**
- Ghi `subscription_quota.json` gồm `{"at": <epoch>, "usableProfiles": N}`. N là
  số profile `openai:*=OAuth (...)` không có `[cooldown ...]` trong kết quả
  `openclaw models status` mà script đã đọc sẵn.
- Thêm cảnh báo khi thiếu `OPENAI_API_KEY`.

### G. Dọn OpenClaw (thao tác vận hành, không sửa code)

1. Sao lưu: `~/bin/openclaw-backup.sh`.
2. `openclaw cron disable`: tắt `coin369-autopost` và `fastnews247:watchdog`
   (chỉ tắt, không xoá, để có thể bật lại).
3. Xoá session của cron cũ và reset phiên `fastnews247-editor`. Lệnh cụ thể sẽ
   kiểm tra khi thực hiện.
4. Bỏ `openai/gpt-5.4-pro` và `custom-localhost-20128/openclaw` khỏi danh sách
   fallback mặc định.
5. Restart gateway, rồi đo lại RAM và thời gian phản hồi.
6. Trên laptop: tắt task `OpenClaw Fast News 247` và `OpenClaw Gateway` (chỉ tắt,
   không xoá). Cần người dùng đồng ý trước.

## 5. Xử lý lỗi

| Tình huống | Hành vi |
|---|---|
| OpenAI 429 / 5xx / timeout | thử lại 1 lần (tôn trọng `Retry-After` ≤ 10 giây) → subscription → dịch máy |
| OpenAI 401 / 403 / `insufficient_quota` | tắt API 30 phút, ghi sổ, healthcheck cảnh báo → subscription → dịch máy |
| Vượt trần ngày | subscription (nếu qua điều kiện chặn) → dịch máy |
| Gateway chết | đăng bài không bị ảnh hưởng; bỏ qua bậc subscription |
| Telegram 429 | chờ `retry_after` ≤ 30 giây rồi thử lại 1 lần; nếu không thì ghi `pending` |
| Coin369 đổi HTML | 0 tin + cảnh báo nguồn; các nguồn khác chạy bình thường |
| RSS trả 304 | dùng lại danh sách tin đã lưu |

## 6. Chi phí dự kiến

**Giả định:**
- Mỗi lần gọi khoảng 2.000 token vào và 500 token ra.
- Trung bình 1,3 lần gọi cho mỗi bài.
- 38% số bài là 5 sao.

**Ở mức 150 tin/ngày (khoảng 195 lần gọi):**
- Không có ngân sách riêng cho tin HOT: 74 × 0,025 + 121 × 0,00375 ≈ **2,30 USD/ngày**.
- Với ngân sách HOT 1,5 USD: khoảng 60 lần gọi `gpt-5.5`, số còn lại dùng
  `gpt-5.4-mini`, tổng ≈ **2,0 USD/ngày, khoảng 60 USD/tháng**.

**Chặn chi phí:**
- Trần cứng 3 USD/ngày, nên tối đa khoảng 90 USD/tháng.
- Người dùng đặt thêm giới hạn ngân sách tháng trên platform.openai.com.

**Lưu ý:** ước tính 1,35 USD/ngày đưa ra trong chat giả định 15% tin là 5 sao. Số
đo thực tế là 38%.

## 7. Kiểm thử

Test offline, theo đúng kiểu `scripts/test_*.py` hiện có (hàm `check()`, stub,
không mạng, không subprocess thật):

- `test_editorial_ladder.py`:
  - request đúng model theo bậc và không có session;
  - `usage` được ghi đúng vào sổ;
  - hạ bậc theo đúng thứ tự hot → mini → subscription → dịch máy;
  - 401 tắt API;
  - 429 thử lại đúng 1 lần;
  - số liệu LLM tự bịa bị cổng chặn;
  - điều kiện chặn subscription (file cũ, quota thấp, vượt số lần/giờ).
- `test_telegram_public.py`:
  - HTML mẫu parse ra đúng mã bài, thời gian, nội dung, link;
  - `inline_article` bỏ qua bước tải trang;
  - bài quảng cáo được điểm thấp.
- `test_feed_cache.py`: gửi đúng ETag / Last-Modified; 304 trả lại tin đã lưu.
- Bổ sung vào `test_telegram_direct.py`: 429 có `retry_after`.
- Chạy lại toàn bộ `scripts/test_*.py` hiện có.

**Chạy thử trên VPS:**
- `--once` không kèm `--post`, với key thật: xem bản nháp và sổ chi phí.
- So sánh 20 tin viết bằng `gpt-5.4-mini` và `gpt-5.5`.

## 8. Triển khai và quay lui

**Triển khai:**
1. Sao lưu.
2. Tắt cron OpenClaw.
3. Commit, push GitHub, `git pull` trên VPS.
4. Chạy thử không đăng.
5. Đổi timer sang 2 phút và bật chế độ `direct` + `ladder`.
6. Theo dõi 2 giờ: journal, sổ chi phí, kênh.
7. Chạy healthcheck.

**Quay lui:**
- `git checkout 4e9f76e` trên VPS.
- Trả timer về 10 phút, config về `bridge` + `auto`.

## 9. Việc người dùng tự làm

1. Tạo API key OpenAI mới, nạp credit, đặt giới hạn ngân sách tháng (đề xuất 90 USD).
2. Tự dán dòng `OPENAI_API_KEY=...` vào `~/AutoTinTuc/.env` trên VPS.
