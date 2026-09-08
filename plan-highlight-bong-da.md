# Kế hoạch triển khai Highlight bóng đá

## Tiến độ cập nhật ngày 2026-09-06

- Hoàn thành: **23/26 task (88,46%)**.
- Đang làm/đã có một phần: **2/26 task (7,69%)**.
- Chưa triển khai: **0/26 task (0,00%)**.
- Chờ credential/live test: **1/26 task (3,85%)**.

## Mốc 1 — Nền tảng UI và dữ liệu

- [x] Thêm menu đầu sidebar **Highlight bóng đá** và trang cấu hình riêng.
- [x] Chuẩn hóa/kiểm tra JSON `match_minute`, `situation_type`, `frames`, `content`, `tts_text`; phụ đề chỉ hiển thị `content`, voice/cache dùng `tts_text`, timeline theo duration voice thật; chỉ chấp nhận HTTPS YouTube.
- [x] Dùng lại dialog catalog voice của Mini game: chọn provider, lọc, nghe thử và lưu metadata voice không nhạy cảm.
- [-] Lưu cấu hình không nhạy cảm trong project; UI và contract đã lưu voice, tốc độ đọc, phụ đề và zoom Ken Burns bắt đầu/kết thúc, đã có cache receipt cho render, chưa có manifest trạng thái toàn job.
- [x] Lưu lại cấu hình Highlight không nhạy cảm lần gần nhất (gồm voice, output, Ken Burns, chữ đầu video và phụ đề) bằng `LastUsedSettingsStore`; project mới khôi phục được default này.

## Mốc 2 — Pipeline tạo video

- [x] Tạo `services/youtube/` dùng yt-dlp Python API: đã tải nguồn 1080p; cache theo hash URL đầu vào + format, checksum; kiểm tra URL webpage cuối trước tải media. URL khác nhau của cùng video có thể có cache riêng.
- [x] yt-dlp tải thumbnail của YouTube cùng metadata; pipeline tạo thư mục output an toàn theo tiêu đề video, đặt `highlight.mp4` và `thumbnail.<ext>` trong đó, tự tăng suffix khi thư mục đã có dữ liệu.
- [-] Tạo `pipeline/football.py` điều phối worker, `CancellationToken`, progress thực; đã có resume bằng receipt/checksum của source/frame/voice/segment; chưa có manifest toàn job hoặc UI pause riêng.
- [x] Trích frame bằng FFmpeg argument list; chặn timestamp vượt duration và segment không có ảnh; báo lỗi rõ thay vì âm thầm xuất nền trơn; cache frame theo source checksum + timestamp.
- [x] TTS CapCut/Google chạy ngoài Qt main thread, cache theo text/voice/cấu hình, retry chỉ lỗi provider tạm thời và không ghi credential.
- [x] Đo duration voice thật bằng ffprobe để render segment.
- [x] Render segment 1920×1080 với Ken Burns easing cosine theo frame đầu ra 30 fps ở delivery resolution; crop theo focus người dùng (mặc định tâm), dùng đúng toàn bộ khoảng zoom người dùng chọn (mặc định `1.00`→`1.15`) và `xfade` mượt giữa frame, burn-in ASS theo duration voice thật từng câu/cụm; tự xuống dòng theo font metrics khi cần.
- [x] Ghép segment bằng cut để không chồng voice/phụ đề; giữ fade giữa ảnh bên trong segment và bù đủ overlap, `.partial` → ffprobe → rename atomic, không ghi đè output cũ.
- [x] Cấu hình text đầu video: text, cỡ chữ, margin, màu chữ, màu nền và opacity nền; render bằng `drawbox`/`drawtext` theo cùng quy ước Layer Studio VietSub.
- [x] Cấu hình phụ đề: top/bottom, cỡ chữ, margin, màu chữ, nền và opacity nền; nền ASS theo kích thước câu/cụm, multiline neo trong khung; dùng cùng font với chữ đầu video và tracking giảm nhẹ.

## Mốc 3 — Độ tin cậy và nghiệm thu

- [x] Stop hủy FFmpeg bằng `CancellationToken`; tải/TTS trong process riêng, hủy cả process tree, chờ process kết thúc; cleanup staging/partial theo phạm vi job. Test process chặn và QThread shutdown không cần Qt main loop.
- [x] Regression parser/cache/UI/provider và integration FFmpeg: voice 6 giây + 3 ảnh giữ đủ 6 giây, ghép hai đoạn đủ 12 giây không chồng audio, motion/music ducking, Stop giữ output cũ và cleanup partial.
- [x] Live-test lịch sử ngày 2026-09-02 (implementation cũ), URL YouTube + CapCut TTS: source tải bằng Android player fallback, render 11 segment, output 1920×1080 có audio/video, ffprobe hợp lệ và không còn `.partial`.
- [x] Tạo handoff kỹ thuật `feature/hightlight-bong-da.md`: kiến trúc, contract, pipeline, media filter, workspace, persistence, an toàn, test và playbook mở rộng.

## Mốc 4 — Sửa lỗi và cải thiện ngày 2026-09-06

- [x] Hiện phút thi đấu/loại tình huống từ JSON; tăng thời lượng ảnh nhóm finish/goal/celebration. Không tự suy đoán đội hoặc tỷ số.
- [x] TTS theo các câu/cụm có dấu câu tương ứng, phụ đề lấy `content` và duration thật; fallback một cue toàn segment khi dấu câu không khớp, không ước lượng word timestamp. Chuẩn hóa loudness và giữ volume đã chọn.
- [x] Chế độ clip tối đa 3 giây từ mỗi timestamp, giữ hình phần còn lại; cấu hình focus dùng cho cả ảnh/clip. Không dùng audio nguồn.
- [x] Chọn file nhạc nền riêng, loop và sidechain ducking theo voice; không cần thay JSON, không chọn nhạc thì chỉ có TTS.
- [x] Xem thử tình huống theo số thứ tự ngoài Qt main thread; snapshot voice/config khi bắt đầu và khóa form trong lúc chạy; validate màu/margin, xử lý JSON sai type.
- [x] Xuất `cover.jpg` từ frame đã render, cùng `highlight.mp4` và thumbnail nguồn; validate media trước rename atomic, font/path escape và GPU→CPU fallback có test.
- [!] Live-test lại YouTube + CapCut/Google trên implementation mới: chưa gọi dịch vụ thật trong phiên sửa này; cần nghe đánh giá độ tự nhiên của voice chia cụm bằng video thật.

### Kết quả kiểm thử phiên 2026-09-06

- Toàn repo: 265 pass, 2 fail. Hai lỗi `test_review_visual_scene_sequence_matches_timeline_boundaries` và `test_english_menu_has_three_placeholder_pages` đã tái hiện trên HEAD gốc trong thư mục tạm; không sửa/bỏ test ngoài phạm vi Highlight.
- 29 test thuộc Highlight (parser, pipeline, regression và UI) pass trong lượt toàn repo.
- Ruff, compileall và `git diff --check` pass; không còn FFmpeg thuộc các fixture sau test.
- Chỉ dùng media tổng hợp/provider doubles; không gọi YouTube/CapCut/Google thật.
