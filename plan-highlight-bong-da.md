# Kế hoạch triển khai Highlight bóng đá

## Tiến độ cập nhật ngày 2026-09-02

- Hoàn thành: **10/15 task (66,67%)**.
- Đang làm/đã có một phần: **5/15 task (33,33%)**.
- Chưa triển khai: **0/15 task (0,00%)**.
- Chờ credential/live test: **0/15 task (0,00%)**.

## Mốc 1 — Nền tảng UI và dữ liệu

- [x] Thêm menu đầu sidebar **Highlight bóng đá** và trang cấu hình riêng.
- [x] Chuẩn hóa/kiểm tra JSON `match_minute`, `situation_type`, `frames`, `tts_text`; chỉ chấp nhận HTTPS YouTube.
- [x] Dùng lại dialog catalog voice của Mini game: chọn provider, lọc, nghe thử và lưu metadata voice không nhạy cảm.
- [-] Lưu cấu hình không nhạy cảm trong project; UI và contract đã lưu voice, phụ đề và zoom Ken Burns bắt đầu/kết thúc, chưa có manifest/checkpoint render.

## Mốc 2 — Pipeline tạo video

- [-] Tạo `services/youtube/` dùng yt-dlp Python API: đã tải nguồn 1080p; cache theo URL cuối + format và kiểm tra URL cuối còn thiếu.
- [-] Tạo `pipeline/football.py` điều phối worker, `CancellationToken`, progress thực; manifest/checkpoint/cache còn thiếu.
- [x] Trích frame bằng FFmpeg argument list; frame lỗi bỏ qua và segment không frame có background fallback.
- [x] TTS CapCut/Google chạy ngoài Qt main thread, cache theo text/voice/cấu hình, retry chỉ lỗi provider tạm thời và không ghi credential.
- [x] Đo duration voice thật bằng ffprobe để render segment.
- [x] Render segment 1920×1080 với Ken Burns nội suy theo frame đầu ra 30 fps, canvas 2× rồi Lanczos downsample để tránh giật; zoom bắt đầu/kết thúc cấu hình được (mặc định 1.00→1.15), pan luân phiên theo từng ảnh, `xfade` mượt giữa frame và burn-in ASS kiểu SRT: một cue/một dòng, timed theo duration voice.
- [x] Ghép segment với video/audio crossfade, `.partial` → ffprobe → rename atomic, không ghi đè output cũ.

## Mốc 3 — Độ tin cậy và nghiệm thu

- [-] Stop hủy FFmpeg đang chạy qua `CancellationToken`; yt-dlp/TTS và cleanup toàn bộ `.partial` còn cần tăng cường.
- [-] Có unit test parser và integration FFmpeg fixture với output audio/video 1920×1080/crossfade; chưa kiểm tra đầy đủ cancel/process mồ côi.
- [x] Live-test URL YouTube + CapCut TTS: source tải bằng Android player fallback, render 11 segment, output 1920×1080 có audio/video, ffprobe hợp lệ và không còn `.partial`.
- [x] Tạo handoff kỹ thuật `feature/hightlight-bong-da.md`: kiến trúc, contract, pipeline, media filter, workspace, persistence, an toàn, test và playbook mở rộng.
