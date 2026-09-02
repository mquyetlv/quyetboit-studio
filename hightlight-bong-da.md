# Phân tích menu Highlight bóng đá

> Tên file giữ theo yêu cầu (`hight-light`); tên hiển thị đúng trong ứng dụng là **Highlight bóng đá**.

## 1. Vị trí và mục đích

Đây là mục đầu tiên của sidebar ứng dụng YouTube Helper Desktop, có khóa điều hướng `highlight_football`. Khi người dùng chọn mục này, `MainWindow` chuyển `QStackedWidget` sang `HighlightFootballPage`.

Mục đích của màn hình là tạo video highlight bóng đá ngang 16:9 từ:

- một video YouTube nguồn (dùng để lấy ảnh frame tại các mốc thời gian);
- dữ liệu JSON mô tả các phân đoạn;
- giọng đọc TTS từ CapCut hoặc Google;
- các tài nguyên hình/âm thanh/logo đi kèm ứng dụng.

Điểm quan trọng: video đầu ra **không cắt hoặc giữ lại clip động từ YouTube**. Hệ thống trích các ảnh tĩnh tại timestamp, thêm hiệu ứng chuyển động Ken Burns, rồi ghép với lời đọc TTS.

## 2. Thành phần giao diện

File chính: `ui/pages/highlight_football_page.py`.

| Nhóm | Thành phần | Hành vi |
|---|---|---|
| Audio source | `Capcut Audio`, `Google Audio` | CapCut hiện `VoiceGroupBox`; Google hiện `GoogleVoiceGroupBox` và số luồng TTS song song (1–10, mặc định 5). |
| Tùy chọn | `Phụ đề (burn-in)` | Tạo ASS từ `tts_text` và đốt trực tiếp vào từng segment. Mặc định bật. |
| Input source | `From Link Youtube`, `From Json data` | UI hỗ trợ hai radio, nhưng xử lý tạo video hiện chỉ chấp nhận `From Json data`. |
| Input JSON | ô nhập JSON + ô YouTube liên quan | JSON là danh sách các segment; URL YouTube bắt buộc để trích frame. |
| Output | thư mục lưu + Browse | Video được lưu trong một thư mục con có tên theo title video YouTube. |
| Theo dõi | progress bar, log, Start/Stop, Reset | Xử lý chạy trên `QThread`, không chặn UI; Start đổi thành Stop để yêu cầu dừng. |

Các tùy chọn được lưu tại `football_prefs.json`: thư mục xuất gần nhất, số luồng Google TTS và trạng thái phụ đề. Reset chỉ xóa dữ liệu nhập/log/thanh tiến trình; không xóa các preference đã lưu.

## 3. Điều kiện bắt đầu và xác thực UI

Khi bấm Start, trang kiểm tra lần lượt:

1. Nếu worker đang chạy, đây là thao tác Stop: đặt cờ dừng và vô hiệu hóa nút đến khi thread kết thúc.
2. Bắt buộc chọn `From Json data`. Chọn `From Link Youtube` sẽ chỉ ghi lỗi vào log; URL ở ô `youtube_input` không được dùng để tạo video.
3. JSON không được rỗng.
4. URL YouTube liên quan không được rỗng.
5. Nguồn TTS phải sẵn sàng và đã chọn voice hợp lệ.
6. Phải chọn thư mục đầu ra.

Sau khi hợp lệ, UI tạo `FootballHighlightWorker` (`ui/workers.py`). Worker gọi `football.pipeline.create_video_from_json(...)`, chuyển log/progress về UI bằng signal. Thành công sẽ thông báo toast và lưu preference; lỗi được ghi log.

## 4. Định dạng JSON đầu vào

File `football/json_input.py` yêu cầu JSON là một mảng không rỗng. Mỗi phần tử là object có đủ bốn trường:

```json
[
  {
    "match_minute": "12:34",
    "situation_type": "Bàn thắng",
    "frames": {
      "setup": "12:28",
      "finish": ["12:32", "12:34"]
    },
    "tts_text": "Một tình huống phối hợp đẹp mắt và bàn thắng được ghi."
  }
]
```

Ý nghĩa trường:

- `tts_text`: là **nguồn duy nhất** đưa vào TTS và cũng là nội dung tạo phụ đề.
- `frames`: phải là object. Giá trị chuỗi hợp lệ hoặc từng phần tử trong giá trị mảng được dùng làm timestamp theo đúng thứ tự khai báo. Giá trị như `Không xuất hiện`, `not found`, `none`, `n/a`, rỗng bị bỏ qua.
- `match_minute`: bắt buộc về cấu trúc, hiện được dùng để ghi log tiến độ; không dùng để trích frame.
- `situation_type`: dùng để nhận diện pre-match và chọn SFX theo từ khóa.

Nếu JSON sai cú pháp, không phải mảng, rỗng, thiếu trường, hoặc `frames` không phải object, pipeline dừng bằng `FootballJsonError`.

## 5. Luồng tạo video chi tiết

```text
UI HighlightFootballPage
  -> FootballHighlightWorker (QThread)
  -> parse/validate JSON
  -> yt-dlp: lấy title, tải video nguồn và thumbnail
  -> TTS cho từng segment (tuần tự hoặc song song)
  -> với từng segment:
       trộn SFX -> trích frame -> render segment 1080p
  -> tạo outro (nếu có tài nguyên/TTS)
  -> concat các segment -> watermark/logo
  -> lưu MP4 và ảnh bìa JPG
```

### 5.1. Chuẩn bị thư mục và tải nguồn

Pipeline dùng thư mục tạm `%TEMP%/yt_helper_football`:

- `cache/`: video nguồn tải từ YouTube, cache theo MD5 của URL;
- `run_<...>/audio`: MP3 TTS của lần chạy;
- `run_<...>/frames`: ảnh JPG trích xuất;
- `run_<...>/segments`: MP4 từng segment.

`YouTubeDownloader` gọi `yt-dlp` để lấy title, tải thumbnail, và tải video tối đa 1080p. Nó ưu tiên các format HLS nhằm giảm lỗi 403 của YouTube; có dùng Node/EJS runtime nếu Node có trong PATH. Download video có timeout 30 phút. Nếu cache đã có file hợp lệ thì tái sử dụng.

### 5.2. Sinh giọng đọc TTS

`TTSService` gọi `client.generate_tts(text, voice, rate=1.0)` và ghi MP3. Khi lỗi, một segment TTS thử tối đa 6 lần với khoảng chờ tăng dần 5, 10, 15, 20, 25 giây.

- CapCut luôn chạy TTS tuần tự (`tts_parallel = 1`).
- Google có thể sinh trước toàn bộ segment theo song song, số worker do UI cấu hình.

Audio sau đó có thể được trộn SFX tại mốc 300 ms, âm lượng SFX 50%. Mapping dựa trên `situation_type`: bàn thắng, thẻ vàng, thẻ đỏ, penalty, bỏ lỡ/cơ hội, và bắt đầu/hiệp. Nếu không có keyword hoặc file âm thanh tương ứng, hệ thống giữ nguyên audio TTS.

### 5.3. Trích frame

Với mỗi timestamp hợp lệ từ `frames`, `FrameExtractor` gọi FFmpeg với `-ss <timestamp> -frames:v 1` để tạo JPG. Một frame lỗi chỉ sinh warning và không làm hỏng toàn bộ segment.

Không có frame hợp lệ thì segment vẫn được tạo bằng background màu `#0f172a`.

### 5.4. Render một segment

`SegmentBuilder` tạo MP4 1920×1080, 30 fps, H.264 (`libx264`, `veryfast`) và AAC 192 kbps:

- Chia thời lượng audio đều cho các ảnh (tối thiểu 0,2 giây/ảnh).
- Mỗi ảnh được scale/pad, hiệu ứng Ken Burns zoom/pan với bốn preset luân phiên, tăng nhẹ contrast/saturation và vignette.
- Các ảnh được nối bằng `concat`; code hiện dùng hard cut, không có xfade dù phần mô tả module từng nhắc transition.
- Nếu bật phụ đề, tạo file ASS từ `tts_text`: tách câu/dòng, tính thời lượng tương đối theo độ dài/từ, rồi overlay lên video.
- Thời lượng video giới hạn theo audio (`-shortest`).

Segment được xem là Pre-match khi `situation_type` chứa các từ như `pre-match`, `trước trận`, `tổng quan`, `giới thiệu`, `intro`, `overview`; nếu không tìm thấy thì segment đầu tiên được chọn. Nếu tồn tại `assets/intro.png`, ảnh này được chèn đầu segment đó tối đa 3 giây, phần thời gian còn lại phân bổ cho các frame.

### 5.5. Outro, ghép và hoàn thiện

Pipeline cố sinh một outro bằng `assets/outro.png` và lời kêu gọi đăng ký kênh cố định. Audio outro được cache lâu dài trong `outro_cache/` theo voice. Nếu lỗi TTS outro hoặc thiếu ảnh outro, hệ thống ghi warning và bỏ qua outro, không làm hỏng video chính.

`VideoMerger` ghép danh sách segment bằng FFmpeg concat demuxer. Nó thử stream copy trước, thất bại thì re-encode. Bước cuối luôn re-encode để gắn:

- `assets/football/logo.png` ở góc trái dưới (nếu có);
- `assets/football/logo-horizontal.png` ở góc phải trên, có nền mờ (nếu có);
- dòng `Nguồn: Sưu tầm` ở góc phải dưới.

Tên video lấy từ title YouTube, loại ký tự không hợp lệ trên Windows. Kết quả được lưu dạng `<output-folder>/<title>/<title>.mp4`; thumbnail được copy cạnh nó thành `<title>.jpg`. Nếu thư mục trùng tên, hệ thống thêm ` (1)`, ` (2)`, … để không ghi đè.

## 6. Dừng, lỗi và các phụ thuộc

Người dùng có thể yêu cầu dừng qua nút Stop. Cờ dừng được kiểm tra tại các mốc lớn (trước/sau download, khi sinh TTS song song, giữa các segment và trước merge). Tuy nhiên các lệnh FFmpeg/yt-dlp trong pipeline hiện không nhận `stop_event`, nên thao tác dừng có thể phải chờ lệnh ngoài đang chạy kết thúc/timeout.

Phụ thuộc runtime chính:

- `ffmpeg`, `ffprobe`, `yt-dlp` phải có trong PATH (hoặc `D:\ffmpeg\bin`, `C:\ffmpeg\bin` cho FFmpeg);
- Node là tùy chọn nhưng hữu ích cho yt-dlp/YouTube JS runtime;
- client CapCut hoặc Google TTS phải khả dụng;
- các assets: intro/outro, SFX và logo là tùy chọn theo từng bước nhưng ảnh hưởng trực tiếp đến thành phẩm.

## 7. Điểm cần lưu ý / lệch hiện tại

1. **Luồng “From Link Youtube” chưa được triển khai.** Radio này là trạng thái mặc định và ô nhập vẫn hiển thị, nhưng Start chủ động từ chối nó. Nên ẩn/disable hoặc triển khai đúng để tránh hiểu nhầm.
2. **`match_minute` và `situation_type` không được overlay lên video.** Comment trong `json_input.py` nói hai trường “vẫn dùng cho text overlay”, nhưng code render không có `drawtext` cho chúng. Thực tế `match_minute` chỉ phục vụ log, còn `situation_type` phục vụ SFX/pre-match.
3. **Comment về xfade không khớp implement.** `segments.py` mô tả fade/slide/wipe nhưng filtergraph đang concat hard cut.
4. **Dừng chưa hủy ngay tiến trình con.** Hàm `fftools.run` có hỗ trợ `stop_event`, nhưng các lời gọi từ pipeline không truyền cờ này.
5. **Một số lỗi ngoài segment có thể dừng cả job.** Ví dụ download nguồn, merge, TTS chính thất bại sẽ dừng toàn bộ; trái lại frame và outro có cơ chế degrade mềm hơn.

## 8. Bản đồ mã nguồn

| Vai trò | File |
|---|---|
| Điều hướng sidebar và đổi trang | `ui/sidebar.py`, `ui/main_window.py` |
| UI Highlight và validation trước khi chạy | `ui/pages/highlight_football_page.py` |
| Worker nền / signal progress, log, success, failure | `ui/workers.py` (`FootballHighlightWorker`) |
| Orchestrator chính | `football/pipeline.py` |
| Kiểm tra JSON / lấy TTS text / timestamp | `football/json_input.py` |
| yt-dlp, cache, title, thumbnail | `football/downloader.py` |
| TTS retry | `football/tts.py` |
| Frame extraction | `football/frames.py` |
| Render segment và subtitle | `football/segments.py`, `football/subs.py` |
| SFX | `football/sfx.py` |
| Outro | `football/outro.py` |
| Concat và watermark | `football/merger.py` |
| FFmpeg/ffprobe/yt-dlp wrapper | `football/fftools.py` |
| Preference | `football/prefs.py`, `football_prefs.json` |
