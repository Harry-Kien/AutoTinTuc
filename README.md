# AutoTinTuc — bot tin tức tự động cho Telegram

Bot thu thập tin từ nguồn RSS, chấm điểm, biên tập lại bằng tiếng Việt và đăng lên
kênh Telegram [@fastnews247vn](https://t.me/fastnews247vn) ("Tin nhanh 247").

Trọng tâm: tài chính — vàng, dầu, BTC, vĩ mô.

## Điểm đáng chú ý về kỹ thuật

- **Không phụ thuộc bên ngoài.** `scripts/fastnews247_mvp.py` chỉ dùng thư viện chuẩn
  Python 3 (`urllib`, `xml.etree`, `concurrent.futures`, …). Không cần `requirements.txt`,
  không cần `pip install`.
- **Không có secret trong code.** Token khai báo qua biến môi trường trong
  `config/fastnews247.sources.json` (`tokenEnv`, `channelEnv`), không hardcode.
- **Chống trùng + chống tin cũ.** State lưu ở `storage/fastnews247/state.json`, cửa sổ
  chống trùng 72 giờ, bỏ tin quá 240 phút.
- **Khoá tiến trình.** `storage/fastnews247/worker.lock` đảm bảo hai lượt chạy không
  chồng lên nhau — chạy scheduler dày tay vẫn an toàn.
- **Cổng chất lượng.** Chỉ đăng khi điểm ≥ 4, bắt buộc có tên nguồn, link bài gốc,
  đã dịch sang tiếng Việt, và có thời gian xuất bản.

## Cấu trúc

```
config/fastnews247.sources.json   Nguồn RSS, ngưỡng chất lượng, cấu hình Telegram
prompts/fastnews247_editor.md     Prompt biên tập
scripts/fastnews247_mvp.py        Bot chính (fetch → chấm điểm → biên tập → đăng)
scripts/fastnews247_source_probe.py   Kiểm tra sức khoẻ nguồn RSS
scripts/fastnews247_finish_review.py  Chốt bản nháp đã duyệt
scripts/test_*.py                 Test chất lượng và độ tin cậy
scripts/*.cmd, *.pyw, *.ps1       Runner cho Windows Task Scheduler
skills/rss-feed-digest/           Skill OpenClaw tóm tắt RSS
deploy/                           systemd service + timer + runner cho VPS Linux
```

## Chạy

```bash
python scripts/fastnews247_mvp.py --once           # chạy thử, chỉ tạo nháp
python scripts/fastnews247_mvp.py --once --post    # chạy thật, có đăng
python scripts/fastnews247_mvp.py --test-telegram "ping"   # thử kết nối Telegram
python scripts/fastnews247_source_probe.py         # kiểm tra nguồn RSS
```

## Bốn chế độ biên tập

Chọn bằng `editorial.mode`. Mặc định là `translate` — không gọi LLM, không tốn hạn mức.

| mode | Cách hoạt động | Hạn mức tiêu tốn |
|---|---|---|
| `translate` | Google Translate + kiểm tra ngặt (như cũ) | 0 |
| `auto` | Dịch máy trước; **chỉ** gọi LLM để cứu tin sắp bị loại | Thấp |
| `openclaw` | LLM viết trước, dịch máy là dự phòng | Cao nhất |
| `ladder` | Subscription viết trước (miễn phí, có kiểm soát); lỗi thì OpenAI API viết ngay (5 sao: `gpt-5.5`, còn lại: `gpt-5.4-mini`); dịch máy là sàn | Phần lớn miễn phí; API có trần `dailyBudgetUsd` |

### `ladder` — chế độ đang chạy trên VPS

- Thứ tự theo `editorial.order` (`["subscription", "openai"]`): subscription
  được thử trước. Bị chặn, không trả lời hoặc viết sai số liệu thì API viết
  ngay; API viết sai số liệu thì dùng dịch máy.
- Subscription chỉ được gọi khi còn ≥ 2 tài khoản OAuth không bị cooldown
  (healthcheck ghi vào `subscription_quota.json` mỗi giờ) và chưa quá 10 lần
  trong giờ. Không trả lời thì tạm dừng 15 phút (`pauseMinutesAfterFailure`)
  để các tin sau đi thẳng sang API.
- Chi phí thật ghi ở `storage/fastnews247/llm_ledger.json`, theo ngày giờ Việt
  Nam. Vượt `dailyBudgetUsd` thì ngừng gọi API; tin HOT có ngân sách riêng
  `hotDailyBudgetUsd`.
- Key hỏng hoặc hết tiền (401/403/`insufficient_quota`): API tự tắt 30 phút và
  `healthcheck.sh` báo về Telegram.
- Tin trượt cổng được ghi nhớ 60 phút (`rejectRetryMinutes`) để không phải trả
  tiền viết lại mỗi 2 phút.

`auto` tồn tại vì lý do rất thực tế. LLM chạy trên hạn mức gói ChatGPT, mà
`vietnamese_editorial` được gọi cho **mọi ứng viên**, không phải chỉ bài sắp đăng:

```
timer 15 phút = 96 lượt chạy/ngày
  LLM cho mọi bài kiểm tra : 96 × maxArticleChecksPerRun(6) = 576 lượt gọi/ngày
  LLM chỉ để cứu tin bị loại: ≤ maxLlmCallsPerRun(1) × 96   =  96 lượt gọi/ngày
```

Nên có `editorial.maxLlmCallsPerRun` (mặc định 1) chặn cứng số lượt gọi mỗi lần chạy.

**LLM không được nới lỏng bất kỳ cổng kiểm tra nào.** Bản viết lại vẫn phải qua đúng
những bài kiểm tra như đường dịch máy: phải là tiếng Việt, và mọi con số trong tiêu đề
lẫn tóm tắt phải có sẵn trong nguồn. Bịa một con số là bị loại. LLM được phép viết câu
hay hơn, không được phép thêm dữ kiện.

Nội dung bài đi qua `--message-file` chứ không qua dòng lệnh — tránh giới hạn độ dài và
tránh để văn bản từ trang báo bên thứ ba chạm vào luật trích dẫn của shell.

Hỏng ở bất kỳ khâu nào (thiếu OpenClaw, quá thời gian, trả về không phải JSON) đều trả
rỗng và bỏ tin, đúng như cách đường dịch máy đang làm — không bao giờ đăng bừa.

```json
"editorial": {
  "mode": "auto",
  "maxLlmCallsPerRun": 1,
  "model": "openai/gpt-5.5",
  "sessionKey": "agent:main:fastnews247-editor",
  "timeoutSeconds": 180
}
```

`sessionKey` riêng để phiên biên tập không làm bẩn phiên chính của agent.

## Hai đường gửi Telegram

Chọn bằng trường `posting.telegram.mode` trong `config/fastnews247.sources.json`.

### `mode: "bridge"` — dùng trên Windows và khi rollback; VPS nay dùng `"direct"`

Đẩy qua OpenClaw CLI để token không bao giờ rời khỏi OpenClaw. Đây là thiết kế gốc và
là đường được khuyến nghị.

Trước đây bridge dò CLI cứng qua `%APPDATA%` nên chỉ chạy được trên Windows. Nay
`resolve_openclaw_cli()` dò theo thứ tự:

1. Biến môi trường `OPENCLAW_CLI` (ưu tiên cao nhất, dùng khi cài ở chỗ lạ)
2. Các vị trí module toàn cục: `%APPDATA%\npm\...` (Windows), `/usr/local/lib`,
   `/usr/lib`, `/opt/homebrew/lib`, `~/.npm-global/lib/...`
3. Hỏi `npm root -g`
4. Cuối cùng mới đến shim `openclaw` trên PATH

Thứ tự này có chủ ý: luôn ưu tiên `node openclaw.mjs` thay vì shim. Trên Windows shim là
`openclaw.CMD`, dùng nó sẽ đổi hành vi của bản Windows đang chạy tốt, **và** đẩy tiêu đề
tin — do nguồn bên thứ ba kiểm soát — qua luật trích dẫn của `cmd.exe`.

Nếu thiếu OpenClaw, `assert_posting_ready()` báo lỗi to và rõ ngay từ đầu, kèm hướng dẫn
cài. Còn nếu mất CLI giữa chừng thì chỉ trả `pending`, không làm sập cả lượt chạy.

### `mode: "direct"` — phương án dự phòng, không cần OpenClaw

Gọi thẳng `api.telegram.org/bot<token>/sendMessage`, token đọc từ biến môi trường khai
báo ở `tokenEnv`. Chỉ dùng thư viện chuẩn, nên VPS chỉ cần Python 3 — không cần cài
Node, không cần OpenClaw.

Giữ đúng hợp đồng của bridge: chỉ coi là `confirmed` khi Telegram trả về `message_id`
kèm `chat_id` thật. Mọi trường hợp khác (lỗi HTTP, mạng hỏng, phản hồi thiếu ack) đều là
`pending`, không bao giờ đánh dấu đã đăng khi chưa chắc chắn. Token được che trong mọi
thông báo lỗi, vì Telegram nhét token vào URL nên nó rất dễ lọt vào log.

Bật bằng cách sửa config:

```json
"telegram": {
  "enabled": true,
  "mode": "direct",
  "tokenEnv": "FASTNEWS247_TELEGRAM_BOT_TOKEN",
  "channelEnv": "FASTNEWS247_TELEGRAM_CHANNEL_ID",
  "channelId": "@fastnews247vn"
}
```

Kiểm thử offline (không gửi tin thật, không cần mạng):

```bash
python scripts/test_telegram_direct.py
```

Thử gửi một tin thật khi đã sẵn sàng:

```bash
python scripts/fastnews247_mvp.py --test-telegram "ping"
```

## Triển khai VPS — cách nhanh

Một lệnh trên VPS Debian/Ubuntu:

```bash
curl -fsSL https://raw.githubusercontent.com/Harry-Kien/AutoTinTuc/main/deploy/install.sh | sudo bash
```

Script tự làm: gói hệ thống, Node 22, OpenClaw, tài khoản `fastnews`, clone mã nguồn,
tạo thư mục và phân quyền, chạy test, cài systemd unit. Chạy lại được nhiều lần để cập
nhật — không ghi đè `.env`, không đụng `storage/`.

Nó **cố ý dừng lại** trước 3 việc cần con người: `openclaw onboard` (đăng nhập cần trình
duyệt), điền `.env`, và gửi thử một tin thật trước khi bật lịch. Script in rõ 3 bước đó
khi chạy xong.

## Triển khai VPS — làm thủ công từng bước

### Bước 1 — cài OpenClaw (cho `mode: "bridge"`)

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
sudo npm install -g openclaw

openclaw onboard                 # cấu hình kênh Telegram tại đây
openclaw channels status         # phải thấy Telegram OK
```

Kiểm tra bot tìm thấy CLI:

```bash
python3 -c "import sys; sys.path.insert(0,'scripts'); \
import fastnews247_mvp as b; print(b.resolve_openclaw_cli())"
```

Nếu cài ở vị trí không chuẩn, chỉ đường thẳng cho nó trong `.env`:

```
OPENCLAW_CLI=/duong/dan/toi/openclaw.mjs
```

### Bước 2 — cài bot

```bash
sudo useradd -r -s /usr/sbin/nologin fastnews
sudo git clone https://github.com/Harry-Kien/AutoTinTuc.git /opt/AutoTinTuc
cd /opt/AutoTinTuc

sudo cp .env.example .env
sudo nano .env                      # điền token
sudo chown -R fastnews:fastnews /opt/AutoTinTuc
sudo chmod 600 .env                 # chỉ chủ sở hữu đọc được
sudo chmod +x deploy/run_once.sh

sudo cp deploy/fastnews247.service deploy/fastnews247.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fastnews247.timer
```

Theo dõi:

```bash
systemctl list-timers fastnews247.timer
journalctl -u fastnews247.service -f
tail -f /opt/AutoTinTuc/logs/fastnews247_scheduler.log
```

## Những thứ cố ý không đưa lên Git

`storage/`, `outputs/`, `logs/` và `.env` nằm trong `.gitignore`. State chống trùng là
dữ liệu riêng của từng máy — chia sẻ giữa Windows và VPS sẽ gây xung đột và làm bot
bỏ sót hoặc đăng lại tin. Mỗi nơi tự sinh state của mình.
