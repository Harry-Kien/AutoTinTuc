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

## ⚠️ Trước khi triển khai lên VPS — đọc phần này

Bot **chưa chạy được trên Linux nguyên trạng**. Đây là hạn chế đã biết, không phải bug ngẫu nhiên.

Đường đăng Telegram cố tình đi qua OpenClaw bridge để token không bao giờ rời khỏi
OpenClaw (`scripts/fastnews247_mvp.py`):

```python
raise RuntimeError("Only the credential-owning OpenClaw bridge is supported.")
```

Và bridge gọi CLI qua đường dẫn chỉ có trên Windows:

```python
cli = Path(os.environ.get("APPDATA", "")) / "npm/node_modules/openclaw/openclaw.mjs"
```

Trên Linux `APPDATA` không tồn tại → không tìm thấy CLI → không đăng được.

Có hai hướng xử lý, chọn một:

**Hướng A — giữ nguyên mô hình bảo mật.** Cài OpenClaw trên VPS, cấu hình kênh
Telegram ở đó, rồi sửa phần dò đường dẫn CLI cho đa nền tảng. Token vẫn nằm trong
OpenClaw. Nặng hơn nhưng giữ đúng thiết kế ban đầu.

**Hướng B — thêm transport gọi thẳng Telegram API.** Bổ sung nhánh
`mode: "direct"` gọi `api.telegram.org/bot<token>/sendMessage`, đọc token từ biến môi
trường. VPS gọn nhẹ, nhưng token nằm trong `.env` trên máy chủ — khác với chủ ý ban đầu
của tác giả.

Phần `deploy/` trong repo này đã dựng sẵn cho **hướng B** (systemd đọc `.env`). Nếu chọn
hướng A thì `EnvironmentFile` không còn cần thiết.

## Triển khai VPS (sau khi đã chọn hướng ở trên)

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
