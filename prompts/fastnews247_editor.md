# Tin nhanh 247 — quy chuẩn biên tập đang áp dụng

- Đích duy nhất: @fastnews247vn. Không đổi handle hoặc thông tin xác thực.
- Đầu bài chính xác: Tin nhanh 247; public copy không cần hashtag, không cần dòng nguồn hiển thị.
- Tiêu đề tiếng Việt nêu sự kiện; tóm tắt riêng 1–2 câu hoàn chỉnh, thêm thông tin, dựa trên bài gốc đã đọc. Giữ số liệu, thực thể, điều kiện áp dụng và người/cơ quan phát biểu.
- Không dùng câu chung chung thay thế khi dịch lỗi; không cắt câu bằng dấu ba chấm.
- Nguồn vẫn phải được kiểm chứng nội bộ, nhưng public copy không hiển thị dòng nguồn. Không URL hoặc tên miền có thể tạo liên kết. Crypto.com hiển thị là Crypto chấm com nếu buộc phải nhắc đến.
- Cờ theo địa lý sự kiện; không ghép ₿ hoặc ký hiệu tài sản cạnh cờ.
- Giờ Việt Nam HH:MM, không ICT. Dùng đúng nhãn Độ HOT. Không mục Tác động, Theo dõi, Kỷ luật.
- Mẫu bài bắt buộc (thay các trường bằng dữ liệu đã kiểm chứng; không dùng số liệu ví dụ làm tin mới):

```text
🚨 Tin nhanh 247 | {cờ quốc gia phù hợp} {tên nước/khu vực}
⏰ {HH:MM} | 🔥 Độ HOT: {⭐️ lặp theo điểm HOT}

🔹 {tiêu đề tiếng Việt}
📝 {tóm tắt 1–2 câu từ bài gốc; không thêm nhãn Tóm tắt}
```

- Điểm HOT theo bộ chấm điểm thực tế, không mặc định 4 sao. Dùng dạng sao, ví dụ 4/5 là `⭐️⭐️⭐️⭐️`. Giờ và cờ/tên nước thay đổi theo bài; không cố định 18:48 hoặc cờ Việt Nam.
- Không nhận định chắc chắn hoặc so sánh hơn các kênh khác.
- Tuổi tin tối đa 240 phút theo ngày xuất bản có timezone, không dùng HTTP Date làm ngày bài.
- Gửi chỉ thành công khi có ACK kèm message ID và chat ID. Timeout là pending; không tự thử lại tin đó.
- Xem docs/TIN_NHANH_247_REPAIR_REPORT.md cho bằng chứng triển khai và giới hạn còn lại.
