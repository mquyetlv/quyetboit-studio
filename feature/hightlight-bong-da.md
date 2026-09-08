# Highlight bóng đá — tài liệu kỹ thuật

Cập nhật 2026-09-06. Tài liệu này mô tả implementation hiện tại; `hightlight-bong-da.md` ở root là thiết kế lịch sử.
Trạng thái nghiệm thu nằm trong `plan-highlight-bong-da.md`.

## Flow và contract

YouTube HTTPS + JSON → kiểm tra nguồn → trích ảnh theo timestamp → TTS theo câu/cụm →
phụ đề theo duration thật → render ảnh/clip + thông tin tình huống → ghép bằng cut →
trộn nhạc tùy chọn → validate/rename MP4 → xuất cover.

JSON hiện tại giữ nguyên, không thêm trường bắt buộc:

```json
[
  {
    "match_minute": "12:34",
    "situation_type": "Bàn thắng",
    "frames": {"setup": "00:28", "finish": ["00:32", "00:34"]},
    "content": "Manchester United mở tỷ số. Bruno Fernandes kiến tạo.",
    "tts_text": "Man-chét-tơ Yu-nai-tịt mở tỷ số. Bru-nô Phéc-nan-đét kiến tạo."
  }
]
```

- `frames` là thời gian trong **video nguồn**, không phải đồng hồ trận đấu. `match_minute` chỉ dùng để hiển thị.
- Giữ thứ tự object/mảng frames của JSON; không tự sắp xếp hoặc suy đoán diễn biến.
- Các sentinel `not found`, `không xuất hiện`, `none`, `n/a`, chuỗi rỗng được bỏ qua; nếu toàn đoạn không còn timestamp thì báo lỗi.
- Timestamp ngoài duration và lỗi trích ảnh được báo rõ; không thay bằng nền trơn rồi báo thành công.
- Tên nhóm chứa `finish`, `goal`, `celebration`, `dứt`, `bàn` có trọng số thời lượng 1,6; nhóm khác 1,0. Có thể tắt trong UI.
- Không tự tạo tên đội/tỷ số vì schema chưa có các trường đó.

## Phụ đề và voice

`services/subtitles/football.py::paired_clauses` ghép câu/cụm theo dấu câu tương ứng giữa
`content` và `tts_text`. Mỗi cụm được TTS riêng, normalize loudness, ffprobe rồi ghép PCM.
Phụ đề hiển thị nguyên tên từ `content`, timestamp là tổng duration của các clip voice thật.
Không chia timestamp theo số ký tự và không giả lập căn từng từ.

Nếu số cụm/câu hai phía không khớp, giữ một clip/cue cho toàn segment để không bỏ hoặc
đoán nội dung. Nên giữ dấu câu tương ứng giữa hai trường để có cue ngắn. Độ tự nhiên khi
TTS chia cụm cần nghe lại trên provider thật; có thể tăng số request so với TTS cả đoạn.

Loudness mục tiêu -16 LUFS, true peak -1,5 dB trước khi áp dụng volume người dùng.
Phụ đề tự xuống dòng theo font metrics; câu dài neo theo cạnh trên/dưới trong khung.
Nền phụ đề theo khung chữ, màu/opacity vẫn lấy cấu hình cũ. Tiêu đề dùng textfile với
`expansion=none`, không nội suy `%`, dấu nháy hoặc cú pháp FFmpeg từ nội dung người dùng.

## Dựng hình và âm thanh

- Output 1920×1080, 30 fps, H.264 High/yuv420p + AAC, faststart.
- Ảnh dùng Ken Burns easing cosine, khoảng zoom từ UI; focus ngang/dọc mặc định 50%/50%.
- Tổng thời lượng input ảnh = thời lượng voice + tổng overlap; cuối hình được pad/trim để giữ đủ voice.
- Chuyển ảnh trong segment bằng xfade; giữa các segment dùng cut, không trộn chồng lời/phụ đề.
- Chế độ clip: mỗi timestamp bắt đầu tối đa 3 giây chuyển động, sau đó giữ frame cuối cho đủ thời lượng được phân bổ. Audio nguồn tắt; không suy đoán khoảng bắt đầu/kết thúc pha bóng ngoài timestamp.
- Nhạc nền là file riêng người dùng chọn trong UI, không phải trường JSON. Loop, normalize nền, sidechain ducking theo voice, giới hạn peak. Không chọn file thì output chỉ có voice.
- Encoder ưu tiên NVENC → QSV → AMF → libx264 theo preflight. GPU lỗi chuyển sang CPU một lần và giữ CPU cho phần còn lại; cancel không fallback.
- Kiểm tra duration của cả stream audio/video trước rename để phát hiện output hụt lời đọc.

## UI, preview và Stop

`HighlightFootballPage` chỉ quản lý form/state, không xây command hoặc gọi HTTP.
Snapshot voice/cấu hình khi bấm chạy; worker không đọc widget. Form bị khóa trong lúc chạy.
Các tùy chọn mới được lưu cùng cấu hình project/default gần nhất, tương thích project cũ.
Đường dẫn Google service-account chỉ giữ trong phiên; không ghi vào project/cache.

**Xem thử tình huống #N** dùng cùng pipeline và cache, chỉ lấy đoạn N. Output nằm trong
`<workspace>/previews/<title>/highlight.mp4` và được mở bằng trình phát mặc định khi xong.
Preview không cần chọn thư mục output cuối. Khởi tạo dịch vụ, tải, TTS và render đều ngoài main thread.

`FootballWorker` chạy trên QThread. `finished → thread.quit` dùng DirectConnection để
`shutdown().wait()` không phụ thuộc Qt main loop đang bị chặn lúc đóng cửa sổ.
FFmpeg dùng cancellation watcher sẵn có. Download/TTS chạy trong process Python riêng:
Stop kết thúc process tree thuộc job và chờ process thoát. Không hủy process không thuộc job.
Worker IPC chỉ trao đổi metadata cần thiết, progress và lỗi an toàn; stderr của provider không được log.
Credential được đọc trong process con từ các cơ chế hiện có, không truyền nội dung credential qua IPC.

## Cache, workspace và output

- Cache nguồn: hash URL đầu vào + format/version; metadata chỉ title an toàn/tên file nội bộ. Xác thực webpage URL cuối là HTTPS YouTube trước download media. Những URL khác nhau trỏ cùng video có thể có cache riêng.
- Cache ảnh: source checksum + timestamp/version.
- Cache voice: hash text + provider/voice/language/rate/volume + phiên bản normalize.
- Cache segment: checksum frame/audio/ASS + cấu hình hình ảnh + timestamp/weights/source. Đổi nhạc không render lại segment.
- Receipt `.json` lưu key/size/checksum, ghi atomically; cache hit chỉ khi cả ba khớp. Không tái sử dụng cache cũ chỉ dựa vào tên file.
- Receipt cho phép chạy lại từ artifact hợp lệ. Chưa có manifest trạng thái toàn job hoặc UI pause/resume riêng.
- Download staging tạo trong cache root đã resolve và chỉ cleanup staging của lần chạy đó; không xóa artifact/output khác.
- MP4/WAV ghi partial → ffprobe → rename; lỗi/cancel giữ output cũ. Raw TTS và file partial của nó được dọn trong finally.
- Output cuối nằm trong `<thư mục xuất>/<title an toàn>/highlight.mp4`, có `cover.jpg` từ frame đã render và thumbnail nguồn nếu tải được. Tên thư mục tăng suffix, không ghi đè output cũ.

## Bản đồ mã nguồn

| Phần | File |
|---|---|
| Model/validation/config | `src/quyetboit_studio/core/football.py` |
| UI và QThread | `src/quyetboit_studio/football.py` |
| Điều phối/caching voice/segment | `src/quyetboit_studio/pipeline/football.py` |
| Render/filter/mix/frame/cover | `src/quyetboit_studio/services/media/football.py` |
| Cache receipt | `src/quyetboit_studio/services/media/football_cache.py` |
| Ghép content/tts_text | `src/quyetboit_studio/services/subtitles/football.py` |
| Process provider/Stop | `src/quyetboit_studio/services/football_worker.py` |
| Download/cache YouTube | `src/quyetboit_studio/services/youtube/downloader.py` |
| Regression và integration | `tests/test_football_regressions.py`, `tests/test_football_pipeline.py`, `tests/test_football.py`, test Highlight trong `tests/test_ui.py` |

## Kiểm chứng

Test mặc định dùng media tổng hợp và provider doubles, không gọi dịch vụ thật.
Các regression kiểm tra duration 6 giây/12 giây, phổ audio quanh ranh giới segment,
checksum invalidation, duration cue đo được, motion + music ducking, đường dẫn có dấu nháy,
preview/config snapshot, QThread shutdown, process cancellation và giữ output khi lỗi.

Live-test 11 segment ngày 2026-09-02 thuộc implementation trước đợt sửa này.
Chưa live-test lại YouTube/CapCut/Google của implementation 2026-09-06; không coi fixture
âm thanh sine là bằng chứng giọng nói thật tự nhiên hoặc nghiệm thu video trận đấu thật.
