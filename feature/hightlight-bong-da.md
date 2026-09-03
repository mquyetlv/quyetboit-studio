# Highlight bóng đá — tài liệu kỹ thuật và handoff phát triển

> **Nguồn tham chiếu cho phần Highlight bóng đá.** Cập nhật theo mã nguồn ngày 2026-09-02.
> Khi mở rộng tính năng, đọc tài liệu này trước; chỉ mở các file được nêu trong mục “Bản đồ mã nguồn” của hạng mục đang sửa. File gốc ở repository root `hightlight-bong-da.md` mô tả một kiến trúc cũ và **không còn phản ánh implementation hiện tại**.

## 1. Mục tiêu và phạm vi hiện tại

Menu **Highlight bóng đá** tạo video ngang 16:9 từ một video YouTube, một mảng JSON mô tả các tình huống và TTS tiếng Việt/Google. Video nguồn chỉ dùng để trích **ảnh** tại timestamp; output không tái sử dụng clip động hoặc audio gốc.

Mỗi tình huống được render thành một segment gồm ảnh trích xuất + hiệu ứng Ken Burns + voice + phụ đề tùy chọn. Các segment có `xfade` video và `acrossfade` audio, sau đó được xuất MP4 tương thích Windows Media Player. yt-dlp cũng tải thumbnail của video nguồn.

Kết quả của mỗi lần chạy nằm trong `<Thư mục xuất>/<tiêu đề YouTube đã sanitize>/`: `highlight.mp4` và `thumbnail.<ext>`. Nếu thư mục tiêu đề đã có dữ liệu, pipeline tạo thư mục có suffix tăng dần; không ghi đè output cũ.

Không thuộc phạm vi hiện tại:

- cắt clip động từ YouTube;
- timeline/editor thủ công, nhạc nền, logo/SFX hoặc outro;
- manifest/checkpoint render hoàn chỉnh;
- chọn format/độ phân giải nguồn hoặc live preview;
- cookie YouTube trong UI.

## 2. Bản đồ mã nguồn

| Trách nhiệm | File / class chính |
|---|---|
| Điều hướng menu, nạp/lưu project | `src/quyetboit_studio/main_window.py` — `MainWindow` |
| Giao diện và QThread worker | `src/quyetboit_studio/football.py` — `HighlightFootballPage`, `FootballWorker` |
| Contract JSON và config hình ảnh | `src/quyetboit_studio/core/football.py` — `FootballHighlightInput`, `FootballSegment`, `FootballRenderConfig` |
| Điều phối media | `src/quyetboit_studio/pipeline/football.py` — `FootballHighlightPipeline` |
| Tải YouTube | `src/quyetboit_studio/services/youtube/downloader.py` — `YouTubeDownloader` |
| FFmpeg/ffprobe, cancellation, progress | `src/quyetboit_studio/services/media/ffmpeg.py` — `FFmpegRunner` |
| TTS dùng chung với Mini game | `src/quyetboit_studio/services/minigame_tts.py` — `CapCutEnglishSpeechService`, `GoogleCloudSpeechService` |
| Voice dialog dùng lại | `src/quyetboit_studio/minigame_voice.py` — `MinigameVoiceDialog` |
| Persistence project | `src/quyetboit_studio/models.py`, `src/quyetboit_studio/persistence.py` |
| Regression test contract/pipeline | `tests/test_football.py`, `tests/test_football_pipeline.py` |
| Regression UI/persistence | `tests/test_ui.py` (`test_highlight_football_*`) |

## 3. Kiến trúc và luồng dữ liệu

```text
MainWindow
  └─ HighlightFootballPage (Qt main thread: form/state only)
       └─ FootballWorker (QThread)
            └─ FootballHighlightPipeline
                 ├─ YouTubeDownloader (yt-dlp Python API)
                 ├─ FFmpegRunner (ffprobe, extract frame, normalize, render)
                 ├─ FootballSpeechService (CapCut hoặc Google adapter)
                 └─ output MP4 đã ffprobe + rename atomic
```

Luồng `start()`:

1. UI validate URL + JSON + `FootballRenderConfig`, yêu cầu voice và thư mục output.
2. UI tạo `CancellationToken`, `FootballHighlightPipeline`, chuyển `FootballWorker` sang `QThread`.
3. Worker gọi `pipeline.run()`. Chỉ `Signal` cập nhật progress/status về main thread.
4. Pipeline tải video, rồi với từng `FootballSegment`: tạo/tái dùng audio, trích frame, tạo ASS, render MP4 segment.
5. Pipeline ghép các segment, ffprobe file `.partial`, rồi `os.replace` sang output cuối.
6. `finished` luôn quit/delete worker; UI trở lại trạng thái chỉ hiện nút **Tạo video**.

Tuyệt đối không gọi HTTP, yt-dlp hoặc FFmpeg trực tiếp từ widget.

## 4. UI, trạng thái và persistence

`HighlightFootballPage` có các trường sau:

| Nhãn UI | State / key lưu trong `highlight_football_settings` | Ghi chú |
|---|---|---|
| Video YouTube | `youtube_url` | Chỉ chấp nhận HTTPS YouTube/youtu.be/youtube-nocookie. |
| Giọng đọc | `tts_provider`, `voice`, `voice_name`, `voice_language`, `voice_gender`, `voice_family`, `voice_categories`, `voice_volume`, `voice_rate` | Chỉ metadata voice, không có credential; tốc độ đọc 0,50×–2,00×, mặc định 1,00×. |
| Đốt phụ đề | `burn_subtitles` | Mặc định `true`. |
| Zoom ảnh (Ken Burns) | `ken_burns_start_zoom`, `ken_burns_end_zoom` | Mặc định `1.00` → `1.15`. |
| Thư mục xuất | `output_folder` | Không xóa output cũ. |
| Chữ đầu video | `top_text`, `top_font_size`, `top_margin`, `top_font_color`, `top_background_color`, `top_background_opacity` | Thanh nền toàn chiều rộng ở đầu video, cùng semantics với layer `intro_text` của VietSub. |
| Kiểu phụ đề | `subtitle_position`, `subtitle_font_size`, `subtitle_margin`, `subtitle_font_color`, `subtitle_background_color`, `subtitle_background_opacity` | `top`/`bottom`; nền full-width được render dưới ASS. |
| Kịch bản JSON | `json` | Toàn bộ text JSON. |

`MainWindow._sync_minigame_project()` cũng đồng bộ `highlight.to_safe_dict()` vào `ProjectModel.highlight_football_settings` trước Save/autosave. `LastUsedSettingsStore` giữ cùng payload không nhạy cảm để project mới khôi phục lựa chọn gần nhất. Project schema chung hiện là v2. Khi thêm trường không nhạy cảm, thêm vào cả `to_safe_dict()` và `set_settings()`; cần giữ default để project cũ mở được.

Typography của hai lớp được cố định đồng bộ: `drawtext` của chữ đầu video và ASS của phụ đề cùng dùng font hệ thống đã chọn (Arial trên Windows, DejaVu Sans khi fallback). ASS đặt `Spacing=-0.5` để giảm tracking. Mỗi cue dùng `\an5\pos(960, y + 0.75 × font_size)` nên tâm chữ nằm đúng tâm dải nền có chiều cao `1.5 × font_size`.

`start_button` và `stop_button` là **exclusive**: chưa chạy chỉ hiện Start; đang chạy chỉ hiện Stop. `stop()` gọi `pipeline.cancel_run()`, vô hiệu hóa Stop và chờ worker tự kết thúc.

## 5. Contract dữ liệu đầu vào

Parser là `FootballHighlightInput.from_json(youtube_url, text)`. Mảng không rỗng; mỗi item bắt buộc có năm trường string/object như sau:

```json
[
  {
    "match_minute": "12:34",
    "situation_type": "Bàn thắng",
    "frames": {
      "setup": "12:28",
      "finish": ["12:32", "12:34"]
    },
    "content": "Manchester United thực hiện một tình huống phối hợp đẹp mắt và ghi bàn.",
    "tts_text": "Man-chét-tơ Yu-nai-tịt thực hiện một tình huống phối hợp đẹp mắt và ghi bàn."
  }
]
```

Quy tắc quan trọng:

- Timestamp nhận `MM:SS` hoặc `HH:MM:SS`; phải hợp lệ theo regex `^(?:\d{1,2}:)?[0-5]\d:[0-5]\d$`.
- `frames` giữ đúng thứ tự key và thứ tự item trong mảng. `FootballSegment.timestamps()` flatten theo thứ tự đó.
- Các giá trị `""`, `không xuất hiện`, `not found`, `none`, `n/a` được bỏ qua không phân biệt hoa/thường.
- `content` bắt buộc không rỗng và là nội dung duy nhất được hiển thị trong phụ đề; giữ nguyên cách viết tên nước ngoài.
- `tts_text` bắt buộc không rỗng, chỉ dùng để tạo voice/cache và làm trọng số phân bổ timeline; có thể Việt hóa tên nước ngoài để provider TTS đọc tự nhiên hơn.
- `match_minute` và `situation_type` hiện là metadata hợp đồng, chưa được overlay hoặc dùng cho visual/SFX. Đừng giả định chúng đã render.

`FootballRenderConfig` là dataclass immutable. Ràng buộc hiện tại: `1.00 <= start < end <= 1.30`; sai sẽ báo lỗi trước khi job vào worker. Nếu muốn cho phép ảnh tĩnh hoặc zoom out, cần thay ràng buộc này, UI range và test cùng lúc.

## 6. Luồng pipeline thực thi

### 6.1. Tải nguồn YouTube

`YouTubeDownloader.download()` dùng **yt-dlp Python API**, không parse CLI stdout. Lần lượt thử:

1. `android` — `best[height<=1080]/best`;
2. `web_safari` — HLS `m3u8` tối đa 1080p;
3. `web` — `bestvideo+bestaudio`/fallback tối đa 1080p.

Mục tiêu là giảm 403 YouTube. Source tải vào `<workspace>/source/source-<attempt>.<ext>`, được ffprobe bằng `VideoArtifact` trước khi dùng. Lỗi cuối trả `PipelineError(code="youtube_download_failed", provider="youtube")` với debug detail chỉ là tên exception, không có URL query/credential.

### 6.2. Voice và cache audio

`HighlightFootballPage.start()` chọn adapter:

- CapCut: `CapCutEnglishSpeechService(voice_id, volume, rate, categories=("vietnamese",))`;
- Google: `GoogleCloudSpeechService(Path.cwd()/"services-account.json", voice_id, language_code, volume, speaking_rate)`.

Voice được tạo ở worker. Cache local hiện dùng path:

```text
<workspace>/audio/segment-<NNN>-<stable_hash({text, voice_cache_key})[:20]>.wav
```

`voice_cache_key` chứa provider, voice, language, volume và tốc độ đọc. Khi cache miss, adapter xuất `.source.mp3` (CapCut) hoặc `.source.wav` (Google), sau đó `_normalize_audio()` đổi atomic sang WAV PCM 48 kHz/stereo. Retry chỉ `PipelineError.retryable`, tối đa 3 lần, backoff cancellation-aware 0.5 s, 1 s.

Khi mở rộng TTS, adapter mới phải khớp protocol:

```python
def synthesize(self, text: str, output: Path, *, cancel: CancellationToken | None = None) -> Path: ...
```

Không lưu service-account path, cookie, token hoặc secret vào project, workspace manifest, tài liệu hay log.

### 6.3. Frame extraction

Với từng timestamp, pipeline gọi FFmpeg qua argument list:

```text
-ss <timestamp> -i <source> -frames:v 1 -q:v 2 <frame>.jpg
```

Mỗi frame nằm tại `<workspace>/frames/segment-<NNN>/frame-<NNN>.jpg`. Một lỗi `PipelineError` của frame chỉ bỏ frame đó. Nếu không có frame nào, pipeline tạo `fallback.jpg` màu `#0f172a`, nên segment vẫn render được.

### 6.4. Phụ đề kiểu SRT

Nếu `burn_subtitles=true`, `_write_ass()` tạo `<workspace>/subtitles/segment-<NNN>.ass`.

- Tách tại hết câu (`. ! ? …`), ưu tiên tách tiếp tại `, ; :`, cuối cùng tách theo từ.
- Tối đa 42 ký tự mỗi cue; không ghi `\N`, nên mỗi thời điểm đúng **một dòng**.
- Tổng duration lấy từ audio thật đã synthesize bằng `tts_text`. Khi số cue tương ứng, phân bổ duration theo độ dài các cue `tts_text`, tối thiểu 500 ms/cue; cue cuối chiếm phần dư. Text ghi vào ASS luôn lấy từ `content`.
- ASS dùng PlayRes 1920×1080, Arial 44 px, chữ trắng, outline/shadow đậm, căn đáy giữa.

Sửa style hoặc policy tách cue ở `_subtitle_cues()` / `_write_ass()`, đồng thời cập nhật `test_football_ass_uses_timed_single_line_dialogues`.

### 6.5. Render segment và Ken Burns

`_render_segment()` chia duration voice đều cho số ảnh (`>= 0.7 s/ảnh`), loop từng ảnh input 30 fps. `FADE_MS = 450`; `xfade=fade` giữa ảnh và offsets tính từ duration từng ảnh.

Ken Burns ở `_ken_burns_filter()` có các tính chất cần giữ:

- zoom được nội suy easing cosine theo **output frame number** `on`, không tăng theo burst input; vận tốc bằng 0 ở đầu/cuối mỗi move;
- `d=1`, input loop `-framerate 30`, output đúng 30 fps;
- canvas làm việc đúng 1920×1080; crop tâm nên intermediate 4K/Lanczos không làm chuyển động mượt hơn nhưng tăng mạnh thời gian render;
- zoom dùng đầy đủ khoảng `ken_burns_start_zoom` → `ken_burns_end_zoom` đã chọn trong `FootballRenderConfig` (mặc định `1.00` → `1.15`), không tự giới hạn thêm ở renderer;
- crop cố định tại tâm ảnh (`x/y = (input - crop) / 2`), không pan luân phiên. Đây là điều kiện cần để xfade giữa các ảnh không bị cảm giác lắc ngang/dọc.

**Không quay lại `d=<số frame segment>`**: biến thể cũ này phụ thuộc nhịp input và gây cảm giác giật/reset. Khi đổi filter, phải giữ test kiểm tra chuỗi `d=1`, `fps=30`, easing cosine và chạy render FFmpeg fixture.

Video segment mã hóa `libx264`, `High`, level `4.1`, `yuv420p`, CRF 20, AAC 192 kbps. Filter cuối ép `scale=in_range=full:out_range=tv,format=yuv420p` để không tạo `yuvj444p` / profile 4:4:4 mà Windows Media Player hay từ chối.

### 6.6. Ghép và xuất

`_concat()` chuẩn hóa mỗi segment 30 fps/yuv420p và audio 48 kHz, rồi áp dụng:

- `xfade=fade` cho video;
- `acrossfade` triangular cho audio;
- duration cursor trừ fade để timestamp nối chính xác.

Output final được tạo ở `<output_folder>/<safe title>.mp4`. `_available_output()` sanitize ký tự Windows và tăng suffix `_2`, `_3`, … nếu trùng. Mọi normalize/segment/concat ghi `.partial`, ffprobe, rồi `os.replace`; `finally` xóa partial còn lại.

## 7. Workspace, artifact và cache

Từ UI production, workspace là:

```text
%LOCALAPPDATA%/QuyetBoITStudio/workspaces/<project_uuid>/football/
  source/       video yt-dlp
  audio/        raw source + WAV normalized cache
  frames/       ảnh timestamp theo segment
  subtitles/    ASS theo segment
  segments/     MP4 từng segment
```

Output do người dùng chọn là vị trí cuối cùng. Không delete source/artifact/output cũ như một side effect của render mới.

**Hạn chế hiện tại:** chưa có `ArtifactCache` manifest/checksum/config hash riêng cho Football. Audio reuse dựa trên path cache key; frame/segment luôn render lại trong mỗi run. Nếu thêm cache segment hoặc resume, bắt buộc hash tối thiểu gồm source checksum, JSON segment, `burn_subtitles`, voice cache key và toàn bộ `FootballRenderConfig`; thay đổi filter phải invalidate downstream.

## 8. Cancellation, lỗi và bảo mật

- `CancellationToken` được check trước download, trước mỗi segment, trong retry wait và được truyền vào `FFmpegRunner` / speech service.
- Stop có thể phải chờ boundary provider/yt-dlp hiện tại; mở rộng downloader phải giữ cancel propagation.
- `FootballWorker` bắt mọi exception, đưa `user_message` an toàn lên `QMessageBox`; nếu là `PipelineError`, dùng `user_message`, không hiển thị debug detail/credential.
- Session CapCut thuộc Windows Credential Manager qua code CapCut dùng chung. UI Football chỉ nhận voice metadata từ `MinigameVoiceDialog`.
- Không log cookie, authorization, API key, service-account content, signed CDN URL hoặc đường dẫn secret. Dữ liệu JSON người dùng cũng không được dùng làm shell command; FFmpeg luôn nhận `list[str]`.

## 9. Kế hoạch mở rộng an toàn

| Muốn thêm | Điểm chạm tối thiểu | Điều kiện bắt buộc |
|---|---|---|
| Thêm config visual (fade, focus, crop, FPS) | `FootballRenderConfig` → UI → `to_safe_dict`/`set_settings` → pipeline | Validate range; thêm vào future render cache hash; test persistence. |
| Thêm animation khác | method riêng cạnh `_ken_burns_filter` | Không đặt filter command trong widget; output 30 fps/yuv420p; fixture FFmpeg + live sample. |
| Clip động thay ảnh tĩnh | service media mới + pipeline timeline | Không tái dùng `_extract_frames` để cắt clip; cần duration/audio sync/cancel riêng. |
| Voice provider mới | adapter `FootballSpeechService` / factory ở `start()` | Không persist credential; cache key phải có provider/voice/config. |
| Style subtitle | `_write_ass`, `_subtitle_cues` | Giữ one cue/one line nếu không có yêu cầu đổi; test ASS timing. |
| Progress từng bước | `pipeline._emit` + `FootballWorker.progress` | Không cập nhật widget từ worker; fraction luôn 0..1. |
| Resume/cache | `core/cache.py`, manifest atomic | Validate path + size/checksum + input/config hash; config visual đổi phải invalidate render. |
| YouTube login/cookie | service `youtube/`, UI riêng nếu cần | Không lưu cookie trong project/log; kiểm tra URL cuối sau redirect. |

Checklist trước khi merge bất kỳ extension nào:

1. Giữ Qt UI chỉ quản lý state; provider/media chạy trong worker/service.
2. Mọi output media ghi `.partial`, ffprobe qua rồi mới atomic rename.
3. Không dùng shell string ghép từ input người dùng.
4. Kiểm tra Start/Stop exclusive và không để `QThread`/FFmpeg mồ côi.
5. Thêm regression test cho contract mới và render FFmpeg nếu có đổi filter/media.
6. Cập nhật file này và `plan-highlight-bong-da.md` với trạng thái thật.

## 10. Kiểm thử và trạng thái đã xác nhận

Lệnh tiêu chuẩn:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\python.exe -m pytest tests/test_football.py tests/test_football_pipeline.py -q
.\.venv\Scripts\python.exe -m compileall -q src
git diff --check
```

Coverage hiện có:

- Parser: YouTube HTTPS, thứ tự timestamp, validation config Ken Burns.
- Pipeline fixture: MP4 1920×1080, audio/video, profile High/yuv420p, Ken Burns 30 fps easing cosine/crop tâm, xfade, subtitle một dòng, cancellation sớm, retry TTS, không còn partial.
- UI: menu vị trí đầu sidebar, validate JSON, Start/Stop visibility, persistence voice không nhạy cảm và zoom config.

Live test đã chạy opt-in trên URL YouTube được cung cấp, CapCut TTS và 11 segment. Bản output mới nhất lúc ghi tài liệu được ffprobe: 1920×1080, 30 fps, H.264 High/yuv420p, AAC, duration khoảng 221.8 s, không còn `.partial`. Đây là bằng chứng cho luồng hiện hữu, không thay thế test của tính năng mới.
