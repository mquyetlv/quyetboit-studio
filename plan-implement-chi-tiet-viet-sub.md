# Kế hoạch implement logic chi tiết VietSub

## 1. Mục tiêu và kiến trúc

Chuyển prototype hiện tại thành pipeline xử lý thật:

```text
Douyin URL
→ tải video
→ tách/chia audio
→ CapCut ASR tạo SRT tiếng Trung
→ Gemini dịch SRT sang tiếng Việt
→ CapCut TTS tạo voice theo từng cue
→ ghép voice
→ áp dụng cấu hình Studio
→ xuất video
```

Ưu tiên: UI không bị block, có cancel/retry/resume, tái sử dụng artifact theo hash, không rò credential và có thể thay provider về sau.

Quy ước checklist: `[ ]` chưa làm, `[-]` đang làm, `[x]` hoàn thành, `[!]` bị chặn.

### Tiến độ cập nhật ngày 2026-08-22

- Hoàn thành: **152/205 task (74,15%)**.
- Đang làm/đã có một phần: **40 task (19,51%)**.
- Chưa triển khai: **8 task (3,90%)**.
- Chờ credential/live test hoặc nghiệm thu: **5 task (2,44%)**.
- Unit/lint hiện tại: **231 test pass**, Ruff pass.
- Luồng thật đã chạy qua bước 1–7 trên video Douyin 7,2 phút; bước 7 đã sửa deadlock FFmpeg và được render kiểm chứng 0→100%. Nút chạy tự động hiện điều phối đủ bước 1–8; bước 8/output cuối vẫn cần live-test để nghiệm thu chính thức.

### Cấu trúc module tái sử dụng

- [x] Tạo lớp `core/` chứa model độc lập UI: `Artifact`, `SrtCue`, `StepResult`, `PipelineError`, `CancellationToken`, progress event và retry policy.
- [x] Tạo `services/douyin/` cho phân tích URL, metadata, cookie browser và tải video bằng yt-dlp.
- [x] Tạo `services/media/` cho ffprobe, FFmpeg runner, audio extraction, silence splitting, time-stretch, mixing, filter graph và encoder detection.
- [-] Tách CapCut thành `CapCutAuth`, `CapCutSessionStore`, `CapCutEditClient`, `VodUploader`, `CapCutCaptionService`, `CapCutVoiceService` và `CapCutTtsService`. Đã tách các lớp chính; chưa có `CapCutVoiceService` độc lập.
- [x] Tạo `services/gemini/` với `GeminiClient`, model discovery, translation batching, validation và retry.
- [x] Tạo `services/subtitles/` cho parse/serialize SRT, offset timestamp, merge overlap, phát hiện cue chưa dịch và phân chia batch.
- [x] Tạo `pipeline/vietsub.py` chỉ điều phối các service; không chứa HTTP, FFmpeg command hoặc logic provider đặc thù.
- [x] Thay `MockPipelineService` bằng `VietSubPipeline`; giữ mock service cho test và chế độ demo.
- [-] Chạy pipeline trong worker thread riêng; networking dùng async I/O và bounded concurrency, FFmpeg/yt-dlp chạy subprocess hoặc blocking worker. Worker/FFmpeg/yt-dlp và Gemini async đã có; CapCut HTTP vẫn đồng bộ trong worker.
- [-] Mọi service nhận `CancellationToken`, progress callback và logger đã redact. Các service chính đã nhận cancel/progress; structured logger dùng chung chưa hoàn chỉnh.

### Interface chính

- [x] `DouyinDownloader.probe(url, auth) -> VideoMetadata`.
- [x] `DouyinDownloader.download(request, progress, cancel) -> VideoArtifact`.
- [-] `AudioProcessor.extract(video, config) -> AudioArtifact`. Đã có interface tương đương, config hiện được chuẩn hóa trong service.
- [x] `AudioProcessor.split_on_silence(audio, config) -> list[AudioChunk]`.
- [-] `CaptionService.transcribe(chunk, language) -> list[SrtCue]`. Hiện tách thành upload asset và `transcribe_resource`.
- [x] `GeminiTranslationService.translate(cues, config) -> TranslationResult`.
- [x] `CapCutTtsService.list_voices(refresh=False) -> list[Voice]`.
- [x] `CapCutTtsService.synthesize(text, voice, config) -> AudioArtifact`.
- [x] `TimelineAudioAssembler.assemble(cues, clips) -> AudioArtifact`.
- [x] `VideoRenderer.render(inputs, studio_config, export_config) -> VideoArtifact`.
- [x] `ArtifactCache.get/put/invalidate(step, input_hash, config_hash)` để resume từng chunk/batch.

## 2. Nền tảng, cấu hình và bảo mật

### Dependency và preflight

- [x] Thêm `yt-dlp`, `google-genai>=2`, `pydantic`, `httpx`, `keyring`, `numpy` và dependency test HTTP.
- [-] Kiểm tra FFmpeg/ffprobe khi khởi động; hiển thị đường dẫn, version, encoder và filter khả dụng. Preflight service đã có, UI hiển thị chưa đầy đủ.
- [x] Dò `h264_nvenc`, `h264_qsv`, `h264_amf`; ưu tiên GPU encoder, tự chạy lại bằng `libx264` nếu encoder phần cứng lỗi.
- [-] Dò filter `rubberband`; fallback sang `asetrate + aresample + atempo` khi không có. Đã dò filter; fallback pitch chưa hoàn thiện.
- [ ] Thêm màn hình preflight cho CapCut session, Gemini key/model, FFmpeg và browser profile.

### Credential

- [x] CapCut UI nhận email/password dạng password field.
- [x] Không lưu password; sau login chỉ lưu cookie/session và workspace ID trong Windows Credential Manager.
- [x] Khi restore phải gọi account-info và workspace API thật; không coi UID/workspace cache hoặc voice list là bằng chứng session hợp lệ. `CapCutEditClient` bắt buộc validate đúng một lần trong process trước upload/ASR, chống gọi trùng và lưu lại UID/workspace đã xác thực; có regression test cho session restore đã chứa metadata.
- [x] Khi session hết hạn, dừng ở bước hiện tại và yêu cầu đăng nhập lại; artifact đã hoàn thành vẫn được giữ.
- [x] Nhận diện mã captcha `1105`, `1101`, `2046–2048`; không retry mù quáng, hiển thị hướng dẫn xác minh trong browser.
- [x] Gemini lấy key từ `GEMINI_API_KEY`; ô API key trên UI cho phép override. Theo yêu cầu mới, override được lưu an toàn trong Windows Credential Manager thay vì chỉ RAM.
- [-] Ô key luôn masked, có nút Test; không ghi key vào project, cache, autosave hoặc log. Masking và lưu an toàn đã có; chưa có nút Test riêng.
- [x] Redact email, password, cookie, authorization, API key, STS secret, upload auth và CDN query nhạy cảm khỏi structured log.

### Project và checkpoint

- [x] Nâng schema project từ v1 lên v2, có migration tự động giữ cấu hình UI hiện tại.
- [x] Bổ sung `project_uuid`, source language, browser/profile, Gemini model, batch size, concurrency, CapCut voice, artifact manifest và config hash.
- [x] Lưu atomically cấu hình nghiệp vụ gần nhất của cả tám bước trong LocalAppData và nạp cho project mới; không lưu URL/project data, workspace hoặc bất kỳ credential/token nào.
- [x] Project chưa lưu dùng workspace tại `%LOCALAPPDATA%/QuyetBoITStudio/workspaces/<uuid>`.
- [x] Project đã lưu mặc định dùng `.qbstudio-work/<uuid>` cạnh project; cho phép đổi workspace.
- [x] Ghi manifest atomically sau mỗi chunk/batch; mỗi artifact lưu size, checksum, thời gian tạo, input hash và config hash.
- [x] Khi chạy lại, chỉ tái sử dụng artifact nếu file tồn tại, checksum đúng và toàn bộ cấu hình ảnh hưởng trùng hash.
- [x] Thay đổi một bước sẽ invalidate bước đó và các bước sau, không xóa artifact không liên quan. Signal cấu hình được map theo số bước, hạ status bước đó/downstream và loại record manifest tương ứng nhưng giữ file artifact.
- [x] Thêm nút “Dọn cache”, “Mở workspace” và “Chạy lại bỏ qua cache”.

## 3. Logic chi tiết 8 bước

### Bước 1 — Tải video Douyin

- [x] Chỉ chấp nhận HTTPS và hostname `douyin.com` hoặc subdomain; resolve link rút gọn nhưng kiểm tra lại redirect cuối để tránh tải URL ngoài phạm vi.
- [x] Dùng yt-dlp Python API thay vì parse stdout; dùng logger và `progress_hooks` để cập nhật tốc độ, bytes, ETA và phần trăm. Cách nhúng này được yt-dlp hỗ trợ chính thức trong [README](https://github.com/yt-dlp/yt-dlp/blob/master/README.md).
- [x] Cho chọn Chrome hoặc Edge và profile; không copy/export toàn bộ cookie sang project. Đã hỗ trợ thêm JSON cookie export chuyển tạm sang Netscape.
- [x] Profile tải mặc định: `bestvideo*+bestaudio/best`, merge/remux MP4, không playlist, không re-encode tại bước tải.
- [x] Chia UI bước 1 thành ba tab `Tải từng tập`, `Tải Theo Series` và `Tải nhiều Videos`; lưu/khôi phục mode cùng danh sách URL mà không phá project v1/v2.
- [!] Với link một tập hoặc `/collection|mix/<id>`, resolve `mix_info`, phân trang danh sách Series, dedupe nhưng giữ thứ tự và cho chọn tập. Unit test parser/phân trang đã qua; cần cookie/live test Douyin để nghiệm thu contract API private hiện tại.
- [x] Chế độ nhiều link tải đúng thứ tự rồi dùng FFmpeg chuẩn hóa resolution/FPS/audio và nối thành một video nguồn `.partial`; integration test hai clip khác tỷ lệ đã thoát process, đạt output ffprobe hợp lệ và không để file tạm.
- [-] Trước khi tải, gọi `extract_info(download=False)`, sanitize metadata và hiển thị tiêu đề, duration, format, kích thước dự kiến. Probe/sanitize đã có; UI metadata trước download chưa đầy đủ.
- [x] Tên file dùng video ID/hash, không dùng trực tiếp tiêu đề chưa sanitize.
- [x] Sau tải chạy ffprobe, xác nhận có video stream, duration > 0 và file đọc được.
- [x] Nếu cookie browser bị khóa hoặc hết hạn, báo lỗi có hướng dẫn đóng browser/thử profile khác; không log cookie.
- [-] Hỗ trợ stop: hủy yt-dlp worker, xóa `.part` không hợp lệ nhưng giữ file hoàn tất. Cancel đã có; cleanup `.part` khi cancellation cần hoàn thiện thêm.
- [x] Cache theo URL cuối + video ID + format ID; không tải lại khi artifact hợp lệ.

### Bước 2 — Tách và chia audio

- [x] Tách audio bằng FFmpeg thành AAC/M4A mono, 16 kHz, 96 kbps; đây là bản tối ưu upload/ASR, không thay đổi audio gốc của video.
- [x] Video không quá 5 phút dùng một audio chunk.
- [x] Video dài dùng `silencedetect`, đặt mục tiêu 240 giây/chunk, tìm khoảng lặng gần nhất trong cửa sổ ±30 giây.
- [x] Giới hạn chunk 180–300 giây; nếu không có silence phù hợp thì cắt tại 240 giây.
- [x] Thêm overlap 250 ms ở hai phía và lưu `source_start_ms`, `source_end_ms`, checksum cho từng chunk.
- [-] Chỉ chạy tối đa hai tác vụ encode/chunk song song để không tranh CPU/disk với upload. Hiện xử lý tuần tự nên an toàn nhưng chưa có bounded parallelism 2.
- [x] Kiểm tra từng chunk bằng ffprobe; chunk lỗi được tạo lại độc lập.
- [x] Progress bước 2 dựa trên tổng duration đã xử lý, không dựa trên số process.

### Bước 3 — CapCut ASR tạo SRT

- [-] Cố định provider là CapCut API; ngôn ngữ mặc định `zh-CN`, UI cho đổi `zh-CN`, `vi-VN`, `en-US`. Provider/default đã có; UI đổi ngôn ngữ chưa có.
- [x] Login gồm seed cookie, resolve region và passport email login theo `tai-lieu-api-capcut.md`.
- [x] Validate session bằng account info để lấy `uid`, sau đó lấy workspace owner.
- [x] Thống nhất tập cookie hợp lệ: `sessionid`, `sid_tt`, `sessionid_ss`, `sid_guard`, `uid_tt`; không chấp nhận session chỉ có `odin_tt`.
- [x] Với mỗi chunk: tính MD5 streaming, prepare upload, tạo STS signer, ApplyUploadInner, upload TOS multipart 3 MiB, finish, CommitUploadInner và create cloud asset.
- [x] Retry từng TOS part tối đa ba lần, giữ nguyên upload ID, part number, offset, bytes và CRC.
- [ ] Upload tối đa hai audio chunk đồng thời; account/workspace/session dùng chung nhưng STS/upload context tách riêng.
- [x] Submit caption với `words_per_line=55`, poll ban đầu mỗi 1,5 giây và tăng dần tối đa 5 giây kèm jitter.
- [x] Timeout mỗi caption job 30 phút; xử lý riêng running, completed, completed-empty, lỗi nghiệp vụ và schema không nhận diện.
- [-] Không retry login sai/captcha; retry network timeout, `429` và `5xx` bằng exponential backoff có jitter. Phân loại lỗi đã có; retry chung cho mọi CapCut request chưa hoàn chỉnh.
- [x] Chuyển utterance thành `SrtCue`, cộng offset chunk vào timestamp.
- [x] Ở vùng overlap, loại cue trùng bằng timestamp giao nhau + normalized text similarity; không xóa hai câu khác nhau chỉ vì gần thời gian.
- [x] Sắp xếp cue, sửa timestamp âm/đảo, đánh lại ID liên tục và xuất `source.srt` UTF-8 BOM.
- [x] Cache kết quả theo audio checksum + ngôn ngữ + cấu hình CapCut; retry chỉ upload/transcribe chunk thiếu.

### Bước 4 — Gemini dịch SRT

- [x] Dùng SDK chính thức `google-genai`; Google xác nhận đây là SDK GA được khuyến nghị thay cho `google-generativeai` cũ trong [tài liệu thư viện](https://ai.google.dev/gemini-api/docs/libraries).
- [-] Mặc định `gemini-3.6-flash`; đây là model stable/GA hiện hành. Dropdown cho Refresh danh sách model và chỉ hiển thị model hỗ trợ text generation/structured output. Default/model discovery đã có; UI Refresh chưa có.
- [x] Không dùng alias `latest` làm mặc định vì alias có thể đổi model; lưu exact model ID trong project.
- [x] Không gửi `temperature`, `top_p`, `top_k` cho Gemini 3.6 Flash vì tài liệu hiện tại đã đánh dấu chúng deprecated.
- [-] UI có API key masked, trạng thái nguồn `GEMINI_API_KEY`/override, nút Test, dropdown model, batch size 10–200 mặc định 50 và concurrency 1–8. Thiếu trạng thái nguồn và nút Test.
- [x] Chia cue thành batch mặc định 50 block; mỗi item gồm `id`, timestamp, duration, source text và ngân sách độ dài ước tính theo duration.
- [x] Mỗi batch được kèm các cue nguồn lân cận làm context nhưng schema chỉ yêu cầu trả đúng ID thuộc batch.
- [x] Prompt gồm ngôn ngữ đích tiếng Việt, context Gen Z/Hài hước/Bắt trend/Tùy chỉnh, giữ nghĩa/tên riêng, không thêm giải thích và ưu tiên câu đủ ngắn để đọc trong timestamp.
- [x] Dùng structured output với Pydantic schema `[{id: int, translated_text: str}]`; structured output giúp đảm bảo JSON đúng schema nhưng vẫn phải validation ngữ nghĩa theo [hướng dẫn chính thức](https://ai.google.dev/gemini-api/docs/structured-output).
- [x] Các batch chạy async với limiter thích nghi, concurrency khởi đầu 3.
- [x] Khi gặp `429`, tôn trọng `Retry-After`, giảm concurrency xuống tối thiểu 1; sau chuỗi request thành công mới tăng dần lại tối đa giá trị UI.
- [x] Retry tối đa bốn lần cho timeout, `408`, `429`, `5xx`; dùng exponential backoff + full jitter. Không retry tự động `400`, `401`, invalid key hoặc model không tồn tại.
- [x] Rate limit được quản lý theo project/model và thay đổi theo usage tier, vì vậy không hardcode RPM/TPM; tham chiếu [Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits).
- [-] Sau lượt dịch, quét toàn bộ cue và đánh dấu chưa dịch khi: thiếu ID, text rỗng, text trùng source sau normalize, tỷ lệ ký tự Han còn cao, response trùng lặp bất thường hoặc schema sai. Các kiểm tra chính đã có; heuristic bất thường chưa đầy đủ.
- [x] Gom riêng cue lỗi thành batch repair tối đa 50 cue và retry theo cùng policy; không dịch lại các cue đã hợp lệ.
- [-] Sau retry cuối, bước chỉ hoàn thành khi không còn cue lỗi; nếu còn, UI hiển thị tổng số cue thành công/thất bại và cho “Thử lại phần lỗi”. Retry riêng đã có và giữ checkpoint; UI sửa tay/bảng chi tiết từng cue chưa có.
- [x] Giữ nguyên ID/timestamp nguồn, chỉ thay `translated_text`; xuất `translated.vi.srt`.
- [-] Cache từng batch theo model + prompt/context + source cue hash + batch config, cho phép resume sau khi đóng app. Hiện đã checkpoint atomically SRT hợp lệ sau từng batch theo source/config hash và khi mở/chạy lại chỉ gửi cue thiếu; chưa tách thành artifact độc lập cho từng batch/prompt hash.

### Bước 5 — CapCut TTS

- [x] Cố định provider là CapCut TTS endpoint `storyboard/v1/tts/multi_platform`; không dùng endpoint audition cho nội dung thật.
- [x] Lấy voice category `vietnamese`, chuẩn hóa `speaker`, `platform`, `effect_id`, `resource_id`, giới tính và tên hiển thị.
- [x] Cache voice catalog vô thời hạn theo app version/region, lưu thời điểm lấy và có nút Refresh chủ động; cache ghi atomically và không chứa credential.
- [x] UI chọn voice theo Tất cả/giới tính/ngôn ngữ/phong cách, tìm kiếm, nghe thử, rate và volume; voice ID được lưu trong project nhưng cookie không được lưu ở đó.
- [x] Tạo TTS riêng cho từng cue để giữ ánh xạ timestamp; bounded concurrency cấu hình 1–50, mặc định 25.
- [x] Mỗi request trả audio URL; tải bytes, xác định container bằng ffprobe và remux/decode đúng chuẩn, không nối MP3 bằng phép cộng bytes.
- [x] Retry tối đa ba lần cho timeout/`429`/`5xx`; session invalid chuyển về trạng thái cần đăng nhập, không tạo lại các cue đã cache.
- [x] Đo duration audio sinh ra bằng ffprobe.
- [x] Nếu audio dài hơn cue, tạo chuỗi `atempo` để speed-up đúng duration; factor ngoài khoảng tương thích được chia thành nhiều filter.
- [x] Nếu audio ngắn hơn cue, giữ tốc độ voice và pad silence đến đúng duration.
- [x] Nếu phải speed-up quá 1,5×, vẫn khớp timestamp tuyệt đối theo lựa chọn đã chốt nhưng ghi quality warning trên cue.
- [x] Chuẩn hóa mỗi clip thành PCM float/48 kHz trước khi assemble.
- [x] `TimelineAudioAssembler` đặt clip đúng `start_ms`, mix cue overlap bằng NumPy, giới hạn peak và xuất `voice_timeline.wav` đúng duration video.
- [x] Cache mỗi cue theo translated text + voice ID + TTS config; thay một câu chỉ tạo lại voice câu đó.

### Bước 6 — Ghép voice

- [x] Dùng video gốc và `voice_timeline.wav`.
- [x] Nếu tắt âm gốc: map video stream gốc và voice mới.
- [x] Nếu giữ âm gốc: điều chỉnh gain từng track, dùng `amix`, chống clipping bằng limiter và giữ duration theo video.
- [x] Nếu chưa có hiệu ứng hình ảnh ở bước 7, bước này copy video stream, chỉ encode audio AAC để giảm thời gian.
- [x] Xuất file trung gian atomically rồi ffprobe xác nhận video/audio stream và chênh duration trong tolerance.
- [x] Không ghi đè artifact cũ cho đến khi file mới đã validate thành công.

### Bước 7 — Render cấu hình Studio

- [x] Chuyển cấu hình Studio thành `FilterGraphBuilder`; tuyệt đối không ghép chuỗi shell từ dữ liệu người dùng.
- [x] Tốc độ dùng `setpts` cho video và chuỗi `atempo` cho audio.
- [x] Pitch ưu tiên `rubberband`; fallback `asetrate + aresample + atempo` để giữ duration.
- [x] Zoom dùng scale/crop có clamp; lật dùng `hflip`/`vflip`; màu dùng `eq`/`hue`.
- [x] Logo kiểm tra file tồn tại, chuẩn hóa đường dẫn, scale theo chiều cao và giữ đúng tỷ lệ ảnh; hỗ trợ bốn góc, opacity cùng margin X/Y theo góc và hiển thị xuyên suốt video.
- [x] Text cố định dùng thanh nền toàn chiều rộng tại Top/Bottom, cao đúng 1,5 lần cỡ chữ và căn giữa hai trục. Chữ chạy dùng `drawtext` xuyên suốt video, nảy chéo trong khung với vận tốc X/Y khác nhau, hỗ trợ opacity/speed/bold và bóng nhẹ; layer tắt hoặc text rỗng không được render.
- [-] Subtitle được burn bằng `subtitles`/ASS xuyên suốt video, hỗ trợ bật/tắt, Top/Bottom, margin theo cạnh, màu nền/chữ, opacity nền; thanh nền cao đúng 1,5 lần cỡ chữ và text căn giữa hai trục. Đã tự chọn Arial/DejaVu và integration render tiếng Việt, chưa đóng gói font redistributable cùng app.
- [x] Áp subtitle trước biến đổi tốc độ để timestamp thay đổi đồng bộ với video/voice.
- [x] Xuất filter graph ra file script tạm thay vì truyền command line quá dài.
- [x] Parse `ffmpeg -progress pipe:1` để cập nhật progress/time/ETA và hỗ trợ cancel. Đã sửa deadlock stderr và cancel watcher.
- [x] Encoder ưu tiên NVENC → QSV → AMF → libx264 theo kết quả preflight; nếu GPU encode lỗi, tự chạy lại một lần bằng CPU.
- [x] Nếu project không có chỉnh sửa hình/âm thanh và layer subtitle bị tắt, bỏ qua encode video và tái sử dụng output bước 6.

### Bước 8 — Xuất và kiểm tra video

- [ ] Map preset UI sang codec/CRF hoặc GPU quality tương đương; giữ resolution/FPS nguồn nếu người dùng không override.
- [x] Render vào file `.partial`, không ghi trực tiếp output cuối.
- [x] Khi hoàn thành, ffprobe kiểm tra video stream, audio stream, duration, resolution, FPS và file size > 0.
- [x] Chỉ rename atomically sang tên output sau khi validation qua.
- [x] Nếu output đã tồn tại, hỏi overwrite hoặc tự tăng suffix; không xóa file cũ trước khi file mới hợp lệ.
- [ ] Ghi summary gồm thời gian từng bước, cache hit/miss, retry, model Gemini, voice CapCut, encoder và warning chất lượng.
- [-] Cho mở thư mục, mở file, copy log và dọn artifact trung gian theo lựa chọn người dùng. Mở thư mục/copy log/dọn cache đã có; mở file và lựa chọn cleanup chi tiết chưa có.

## 4. Điều phối, lỗi và tối ưu

- [x] Nút **Chạy tất cả các bước** điều phối liên tục bước 1–8; cấu hình Studio hiện tại được dùng ở bước 7 và output được xuất ở bước 8. Vẫn hỗ trợ chạy lại riêng từng bước để căn chỉnh.
- [-] Thay timer mô phỏng bằng state machine thật: `preparing`, `running`, `waiting_remote`, `retrying`, `paused`, `completed`, `failed`, `cancelled`. Worker/state thật đã thay timer trong production; chưa phát event đầy đủ cho mọi state trung gian.
- [x] Progress tổng dùng trọng số động theo duration/bytes thực tế; không chia đều tám bước.
- [x] Pause chỉ áp dụng tại boundary an toàn; upload/FFmpeg/Gemini đang chạy được cancel, sau đó resume từ checkpoint.
- [x] Mọi lỗi có `error_code`, provider, retryable, user_message và debug detail đã redact. Gemini ghi thêm HTTP status, reason, provider message, quota metric/ID, `Retry-After`, attempt và thay đổi concurrency vào log an toàn.
- [x] Không retry lỗi xác thực, captcha, invalid input, unsupported model hoặc schema project.
- [x] Retry network dùng exponential backoff có jitter và cancellation-aware wait.
- [-] Giới hạn đồng thời riêng cho download, FFmpeg, CapCut ASR, Gemini và CapCut TTS để tránh một provider làm nghẽn toàn pipeline. Gemini có limiter; các provider còn lại chủ yếu tuần tự.
- [ ] Có thể pipeline overlap an toàn: ASR chunk hoàn tất được merge/checkpoint sớm; Gemini bắt đầu khi toàn bộ source SRT ổn định; TTS có thể bắt đầu theo batch dịch đã validate.
- [-] Không cho thay provider/model/voice/config ảnh hưởng artifact khi bước liên quan đang chạy. UI chính và Layer Studio đã có edit guard theo active step; chưa khóa toàn bộ dialog cấu hình khác.
- [ ] Structured log có project ID, run ID, step, chunk/batch/cue ID, elapsed, retry count và cache status.
- [-] Thu thập benchmark cho video 1, 10 và 30 phút; báo thời gian từng bước thay vì cam kết tổng thời gian vì CapCut/Gemini phụ thuộc mạng/quota. Đã đo video 7,2 phút và thời gian render; chưa đủ bộ benchmark.
- [x] Ghi rõ CapCut là private reverse-engineered API, có thể đổi endpoint/schema/chữ ký và cần contract test thường xuyên.

## 5. Kiểm thử và nghiệm thu

### Unit test

- [-] URL/redirect Douyin, filename sanitation và metadata normalization. URL/domain/modal URL/cookie JSON đã test; redirect và metadata edge-case chưa đủ.
- [x] Silence boundary, overlap, timestamp offset và dedupe cue.
- [-] CapCut sign MD5 bằng clock cố định; AWS SigV4 bằng test vector cố định. Có test deterministic, chưa dùng đầy đủ official test vector.
- [-] CRC/offset/part numbering cho TOS multipart. CRC đã test; offset/part numbering chưa có test độc lập đầy đủ.
- [-] Parse mọi response schema voice/TTS đã ghi trong tài liệu. Đã test catalog đa category, merge/dedupe/cache/retry/concurrency; chưa có fixture cho mọi biến thể response thực tế.
- [-] Caption completed, completed-empty, running, lỗi và timeout. Parser trạng thái đã có; test matrix chưa đầy đủ.
- [x] SRT parse/serialize UTF-8, timestamp validation và merge chunk.
- [-] Gemini batch size, structured schema, missing ID, Han detection và retry-only-missing. Các luồng chính đã test; error matrix chưa đầy đủ.
- [-] Adaptive limiter giảm khi `429`, tôn trọng `Retry-After` và phục hồi concurrency. Giảm/phục hồi cùng parse/redact/log `Retry-After` đã test; chờ thực tế với `Retry-After` khác 0 chưa có test chuyên biệt.
- [-] TTS cache key, duration matching, atempo chain và silence padding. Atempo có test; các nhánh còn lại chủ yếu được kiểm chứng qua pipeline.
- [x] Filter graph cho tùy chọn Studio, subtitle/text bar, logo giữ tỷ lệ, chữ nảy hai trục và escaping path/text; tốc độ chữ chạy được tách khỏi timeline video/audio, subtitle được composite sau zoom/crop và quy đổi PlayResY của libass để khớp kích thước pixel/font/margin với Text cố định; có unit test, test persistence/UI và integration render FFmpeg.
- [x] Artifact/config hash, cache hit, invalidation và schema v1→v2 migration.
- [x] Redaction bảo đảm không log/lưu Gemini key, password, cookie, STS hoặc authorization.

### Integration test không gọi dịch vụ thật

- [ ] Mock toàn bộ chuỗi CapCut login → workspace → upload → asset → caption.
- [-] Mock Gemini structured output, `429`, timeout, missing cue và model invalid. Structured output/adaptive limiter đã mock; chưa đủ toàn bộ error matrix.
- [ ] Mock yt-dlp progress/error/cancel.
- [-] Chạy FFmpeg trên fixture ngắn để xác nhận extract, split, voice assembly, mix, subtitle và render. Studio render đã có pytest thật với subtitle tiếng Việt, pitch, logo, text cố định/chữ chạy và style nền/layout; extract/split/assembly/mix chưa được gom đầy đủ vào pytest.
- [x] Mô phỏng app bị đóng giữa ASR/Gemini/TTS rồi mở lại từ manifest. Pipeline khôi phục typed artifact đã kiểm checksum; đã regression-test và xác nhận trực tiếp với `E:\test.qbstudio.json`.
- [x] Xác nhận UI responsive, progress/log cập nhật đúng thread và Stop không để worker mồ côi.

### Live test có opt-in

- [x] Chỉ chạy khi có biến môi trường/test credential riêng; không chạy trong test mặc định.
- [-] Douyin video ngắn, video dài, link rút gọn và cookie hết hạn. Đã chạy video 7,2 phút với JSON cookie; chưa đủ matrix.
- [-] CapCut session mới/hết hạn, caption một chunk/nhiều chunk và completed-empty. Session thật/multi-chunk đã chạy; expired/empty chưa xác nhận live.
- [!] Gemini model discovery, batch 50 cue, concurrency 3, `429` và repair cue thiếu. Cần quota/credential test riêng để hoàn tất live matrix.
- [-] CapCut voice list, TTS câu ngắn/dài và speed-up >1,5×. Pipeline thật đã tạo voice; chưa xác nhận đầy đủ ba trường hợp.
- [!] Pipeline end-to-end cho video 1–3 phút; kiểm tra tiếng Việt, sync voice/subtitle, audio không clipping và output phát được. Đã chạy video dài hơn nhưng chưa nghiệm thu chính thức output cuối.
- [-] GPU render thành công và GPU lỗi tự fallback CPU. Đã xác nhận GPU lỗi fallback `libx264`; chưa có render GPU thành công trên máy hiện tại.
- [x] Quét project, cache và log để xác nhận không có credential.

### Definition of Done

- [-] Quy trình tổng xử lý video Douyin thật đến file video hoàn chỉnh. Điều phối tự động bước 1–8 đã có; bước 7 đã chạy thật, nhưng bước 8/output cuối vẫn cần live-test và nghiệm thu chính thức.
- [x] Mọi logic provider/media được gọi qua module tái sử dụng, không nằm trong widget Qt.
- [x] Batch Gemini mặc định 50 cue, cấu hình được, async adaptive concurrency mặc định 3.
- [x] Cue chưa dịch được quét và retry riêng; không dịch lại cue hợp lệ.
- [x] Voice từng cue khớp timestamp tuyệt đối.
- [x] Retry/resume dùng lại chunk/batch/cue đã hoàn thành sau lỗi hoặc restart. Chunk ASR, batch/cue Gemini và raw/WAV TTS được checkpoint atomically; UI lưu số lượng/ID thất bại và nút thử lại chỉ gửi lại phần thiếu.
- [x] FFmpeg tự chọn GPU và fallback CPU.
- [x] UI không block, Stop hoạt động và lỗi có hướng dẫn xử lý.
- [x] Không credential nào xuất hiện trong project, cache, autosave hoặc log.
- [!] Unit/integration test qua; live end-to-end test được xác nhận bằng tài khoản test. Unit hiện qua; live E2E chưa nghiệm thu chính thức.

## 6. Giả định đã khóa

- Chỉ implement nguồn Douyin trong giai đoạn này; Xiaohongshu và Bilibili vẫn giữ UI nhưng báo chưa hỗ trợ logic thật.
- Douyin dùng yt-dlp với cookie đọc từ Chrome/Edge profile, tải best quality và remux MP4.
- CapCut dùng email/password, lưu session cookie bằng Windows Credential Manager, không lưu password.
- Captcha CapCut không được tự động vượt qua; pipeline dừng và yêu cầu người dùng xác minh.
- ASR mặc định `zh-CN`, cho phép đổi ngôn ngữ.
- Audio ASR chia adaptive 3–5 phút và tối đa hai chunk CapCut đồng thời.
- Gemini key lấy từ `GEMINI_API_KEY`, UI chỉ override trong RAM.
- Gemini mặc định exact model ID `gemini-3.6-flash`.
- Dịch một lượt theo batch 50 cue; chỉ chạy repair cho cue thiếu/không hợp lệ.
- Gemini concurrency thích nghi mặc định 3, UI cho chọn 1–8.
- TTS dùng CapCut và ưu tiên khớp timestamp tuyệt đối.
- Bước render tự chọn GPU và fallback CPU.
- CapCut là private web API không có cam kết ổn định; các constant/version phải tập trung trong cấu hình để cập nhật nhanh.

## 7. English Mini game

- [x] Thay placeholder `Tiếng Anh → Mini game` bằng trang Qt production có cấu hình, bảng từ,
  progress, Stop và mở thư mục output.
- [x] Bắt buộc người dùng cung cấp Topic cùng nghĩa tiếng Việt, từ tiếng Anh, IPA và câu ví dụ cho
  từng từ; không tự dịch, đoán hoặc sửa nội dung.
- [x] Hỗ trợ 16:9, 9:16 và xuất cả hai; crop kiểu cover theo focus X/Y riêng từng tỷ lệ, loop video
  ngắn và luôn bỏ audio nền. Với flow theme dọc, video chỉ thay lớp `background-video.png` trong
  Question/Answer và nền ngoài panel ở từng Review card; renderer giữ nguyên alpha mask
  `background-text.png`, nguyên RGB panel `review.png`, ASS style/text và toàn bộ artwork
  Hook/Score/Review Start/CTA của theme.
- [x] Timeline động theo số từ gồm hook, countdown, ding, đáp án và ba lượt phát âm tự nhiên → chậm
  → tự nhiên.
- [x] Phần ôn tập đọc từ và câu ví dụ; phần chấm điểm tính range theo tổng số từ và có CTA comment.
- [x] Tách interface TTS dùng chung và adapter CapCut English/Google Cloud Text-to-Speech; slow voice
  được time-stretch bằng FFmpeg để không phụ thuộc provider có tôn trọng `rate` hay không.
- [x] Google service-account chỉ được đọc từ file trong phiên chạy; không persist đường dẫn/nội dung,
  private key hoặc token vào project, workspace hay log.
- [x] TTS và FFmpeg chạy ngoài Qt main thread, dùng `CancellationToken`; Stop hủy subprocess tại
  boundary an toàn.
- [x] Cache voice theo provider + voice + text + volume; output ghi `.partial`, ffprobe validate rồi
  rename atomically và tự tăng suffix nếu file cũ tồn tại.
- [x] Thêm tab JSON `Nhiều phần`: kiểm tra `totalParts`/`totalWords`/`partNumber`, render tuần tự
  từng mảng qua đúng pipeline cũ, workspace riêng từng phần và tên output `<Topic> Part <n>`.
- [x] Thêm dependency chính thức `google-cloud-texttospeech` và import-lazy với lỗi hướng dẫn rõ ràng.
- [x] Thêm unit test validation, credential-safe serialization, score, timeline, ASS và integration
  render FFmpeg thật xác nhận cả 1920×1080/1080×1920, video/audio stream và progress 100%.
- [x] Nâng cấp layout bằng typography Unicode, phân cấp English/IPA/nghĩa Việt/example và nhãn voice
  đồng bộ `LISTEN → SLOW → YOUR TURN`; trim khoảng lặng TTS, thêm cue hook/countdown/reveal/end,
  chuẩn hóa loudness và render kiểm chứng video dọc thật 1080×1920 có đủ video/audio stream.
- [x] Hook/cover, Review Start và CTA dùng tổng số từ động từ project; Question có nghĩa Việt lớn hơn
  cùng placeholder grapheme-aware, CTA 4,5 giây nằm sau Review cuối. Asset gốc được giữ lại, clean
  base/CTA base nằm riêng theo theme; Hook dùng atlas số RGBA 0–9 đúng từng theme với metric typed,
  scale đồng đều và cache visual invalidate theo asset/font/layout/N.
- [x] Thay field voice nhập tay bằng nút **Cấu hình Voice…** mở dialog tải catalog ngoài Qt main
  thread, hỗ trợ lọc ngôn ngữ, loại voice, danh mục, giới tính, tìm kiếm và nghe thử.
- [x] Google TTS dùng API chính thức `list_voices()` và giữ voice ID, language codes, SSML gender,
  sample rate cùng nhóm công nghệ; live-test service account trả 2.066 voice/260 voice English.
- [x] CapCut tải/merge toàn bộ `VOICE_CATEGORIES` theo `tai-lieu-api-capcut.md`; live-test session
  hiện tại trả 326 voice và giữ đủ category trên từng speaker.
- [x] Thêm `services-account.json` cùng các pattern service-account vào `.gitignore`; kiểm tra xác
  nhận credential không còn xuất hiện trong `git status`.
- [-] Preview hiện mô phỏng đúng nội dung, tỷ lệ và thời lượng dự kiến; chưa hiển thị frame thật của
  video nền/crop trong editor.
- [!] Catalog Google và CapCut đã live-test thành công; vẫn cần nghiệm thu synthesize toàn bộ video
  bằng hai provider với quota/credential test trước khi đánh dấu TTS end-to-end hoàn tất.
