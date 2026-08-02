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

### Cấu trúc module tái sử dụng

- [ ] Tạo lớp `core/` chứa model độc lập UI: `Artifact`, `SrtCue`, `StepResult`, `PipelineError`, `CancellationToken`, progress event và retry policy.
- [ ] Tạo `services/douyin/` cho phân tích URL, metadata, cookie browser và tải video bằng yt-dlp.
- [ ] Tạo `services/media/` cho ffprobe, FFmpeg runner, audio extraction, silence splitting, time-stretch, mixing, filter graph và encoder detection.
- [ ] Tách CapCut thành `CapCutAuth`, `CapCutSessionStore`, `CapCutEditClient`, `VodUploader`, `CapCutCaptionService`, `CapCutVoiceService` và `CapCutTtsService`.
- [ ] Tạo `services/gemini/` với `GeminiClient`, model discovery, translation batching, validation và retry.
- [ ] Tạo `services/subtitles/` cho parse/serialize SRT, offset timestamp, merge overlap, phát hiện cue chưa dịch và phân chia batch.
- [ ] Tạo `pipeline/vietsub.py` chỉ điều phối các service; không chứa HTTP, FFmpeg command hoặc logic provider đặc thù.
- [ ] Thay `MockPipelineService` bằng `VietSubPipeline`; giữ mock service cho test và chế độ demo.
- [ ] Chạy pipeline trong worker thread riêng; networking dùng async I/O và bounded concurrency, FFmpeg/yt-dlp chạy subprocess hoặc blocking worker.
- [ ] Mọi service nhận `CancellationToken`, progress callback và logger đã redact.

### Interface chính

- [ ] `DouyinDownloader.probe(url, auth) -> VideoMetadata`.
- [ ] `DouyinDownloader.download(request, progress, cancel) -> VideoArtifact`.
- [ ] `AudioProcessor.extract(video, config) -> AudioArtifact`.
- [ ] `AudioProcessor.split_on_silence(audio, config) -> list[AudioChunk]`.
- [ ] `CaptionService.transcribe(chunk, language) -> list[SrtCue]`.
- [ ] `GeminiTranslationService.translate(cues, config) -> TranslationResult`.
- [ ] `CapCutTtsService.list_voices(refresh=False) -> list[Voice]`.
- [ ] `CapCutTtsService.synthesize(text, voice, config) -> AudioArtifact`.
- [ ] `TimelineAudioAssembler.assemble(cues, clips) -> AudioArtifact`.
- [ ] `VideoRenderer.render(inputs, studio_config, export_config) -> VideoArtifact`.
- [ ] `ArtifactCache.get/put/invalidate(step, input_hash, config_hash)` để resume từng chunk/batch.

## 2. Nền tảng, cấu hình và bảo mật

### Dependency và preflight

- [ ] Thêm `yt-dlp`, `google-genai>=2`, `pydantic`, `httpx`, `keyring`, `numpy` và dependency test HTTP.
- [ ] Kiểm tra FFmpeg/ffprobe khi khởi động; hiển thị đường dẫn, version, encoder và filter khả dụng.
- [ ] Dò `h264_nvenc`, `h264_qsv`, `h264_amf`; ưu tiên GPU encoder, tự chạy lại bằng `libx264` nếu encoder phần cứng lỗi.
- [ ] Dò filter `rubberband`; fallback sang `asetrate + aresample + atempo` khi không có.
- [ ] Thêm màn hình preflight cho CapCut session, Gemini key/model, FFmpeg và browser profile.

### Credential

- [ ] CapCut UI nhận email/password dạng password field.
- [ ] Không lưu password; sau login chỉ lưu cookie/session và workspace ID trong Windows Credential Manager.
- [ ] Khi restore phải gọi account-info hoặc workspace API thật; không coi cache/voice list là bằng chứng session hợp lệ.
- [ ] Khi session hết hạn, dừng ở bước hiện tại và yêu cầu đăng nhập lại; artifact đã hoàn thành vẫn được giữ.
- [ ] Nhận diện mã captcha `1105`, `1101`, `2046–2048`; không retry mù quáng, hiển thị hướng dẫn xác minh trong browser.
- [ ] Gemini lấy key từ `GEMINI_API_KEY`; ô API key trên UI cho phép override trong RAM cho phiên hiện tại.
- [ ] Ô key luôn masked, có nút Test; không ghi key vào project, cache, autosave hoặc log.
- [ ] Redact email, password, cookie, authorization, API key, STS secret, upload auth và CDN query nhạy cảm khỏi structured log.

### Project và checkpoint

- [ ] Nâng schema project từ v1 lên v2, có migration tự động giữ cấu hình UI hiện tại.
- [ ] Bổ sung `project_uuid`, source language, browser/profile, Gemini model, batch size, concurrency, CapCut voice, artifact manifest và config hash.
- [ ] Project chưa lưu dùng workspace tại `%LOCALAPPDATA%/QuyetBoITStudio/workspaces/<uuid>`.
- [ ] Project đã lưu mặc định dùng `.qbstudio-work/<uuid>` cạnh project; cho phép đổi workspace.
- [ ] Ghi manifest atomically sau mỗi chunk/batch; mỗi artifact lưu size, checksum, thời gian tạo, input hash và config hash.
- [ ] Khi chạy lại, chỉ tái sử dụng artifact nếu file tồn tại, checksum đúng và toàn bộ cấu hình ảnh hưởng trùng hash.
- [ ] Thay đổi một bước sẽ invalidate bước đó và các bước sau, không xóa artifact không liên quan.
- [ ] Thêm nút “Dọn cache”, “Mở workspace” và “Chạy lại bỏ qua cache”.

## 3. Logic chi tiết 8 bước

### Bước 1 — Tải video Douyin

- [ ] Chỉ chấp nhận HTTPS và hostname `douyin.com` hoặc subdomain; resolve link rút gọn nhưng kiểm tra lại redirect cuối để tránh tải URL ngoài phạm vi.
- [ ] Dùng yt-dlp Python API thay vì parse stdout; dùng logger và `progress_hooks` để cập nhật tốc độ, bytes, ETA và phần trăm. Cách nhúng này được yt-dlp hỗ trợ chính thức trong [README](https://github.com/yt-dlp/yt-dlp/blob/master/README.md).
- [ ] Cho chọn Chrome hoặc Edge và profile; không copy/export toàn bộ cookie sang project.
- [ ] Profile tải mặc định: `bestvideo*+bestaudio/best`, merge/remux MP4, không playlist, không re-encode tại bước tải.
- [ ] Trước khi tải, gọi `extract_info(download=False)`, sanitize metadata và hiển thị tiêu đề, duration, format, kích thước dự kiến.
- [ ] Tên file dùng video ID/hash, không dùng trực tiếp tiêu đề chưa sanitize.
- [ ] Sau tải chạy ffprobe, xác nhận có video stream, duration > 0 và file đọc được.
- [ ] Nếu cookie browser bị khóa hoặc hết hạn, báo lỗi có hướng dẫn đóng browser/thử profile khác; không log cookie.
- [ ] Hỗ trợ stop: hủy yt-dlp worker, xóa `.part` không hợp lệ nhưng giữ file hoàn tất.
- [ ] Cache theo URL cuối + video ID + format ID; không tải lại khi artifact hợp lệ.

### Bước 2 — Tách và chia audio

- [ ] Tách audio bằng FFmpeg thành AAC/M4A mono, 16 kHz, 96 kbps; đây là bản tối ưu upload/ASR, không thay đổi audio gốc của video.
- [ ] Video không quá 5 phút dùng một audio chunk.
- [ ] Video dài dùng `silencedetect`, đặt mục tiêu 240 giây/chunk, tìm khoảng lặng gần nhất trong cửa sổ ±30 giây.
- [ ] Giới hạn chunk 180–300 giây; nếu không có silence phù hợp thì cắt tại 240 giây.
- [ ] Thêm overlap 250 ms ở hai phía và lưu `source_start_ms`, `source_end_ms`, checksum cho từng chunk.
- [ ] Chỉ chạy tối đa hai tác vụ encode/chunk song song để không tranh CPU/disk với upload.
- [ ] Kiểm tra từng chunk bằng ffprobe; chunk lỗi được tạo lại độc lập.
- [ ] Progress bước 2 dựa trên tổng duration đã xử lý, không dựa trên số process.

### Bước 3 — CapCut ASR tạo SRT

- [ ] Cố định provider là CapCut API; ngôn ngữ mặc định `zh-CN`, UI cho đổi `zh-CN`, `vi-VN`, `en-US`.
- [ ] Login gồm seed cookie, resolve region và passport email login theo `tai-lieu-api-capcut.md`.
- [ ] Validate session bằng account info để lấy `uid`, sau đó lấy workspace owner.
- [ ] Thống nhất tập cookie hợp lệ: `sessionid`, `sid_tt`, `sessionid_ss`, `sid_guard`, `uid_tt`; không chấp nhận session chỉ có `odin_tt`.
- [ ] Với mỗi chunk: tính MD5 streaming, prepare upload, tạo STS signer, ApplyUploadInner, upload TOS multipart 3 MiB, finish, CommitUploadInner và create cloud asset.
- [ ] Retry từng TOS part tối đa ba lần, giữ nguyên upload ID, part number, offset, bytes và CRC.
- [ ] Upload tối đa hai audio chunk đồng thời; account/workspace/session dùng chung nhưng STS/upload context tách riêng.
- [ ] Submit caption với `words_per_line=55`, poll ban đầu mỗi 1,5 giây và tăng dần tối đa 5 giây kèm jitter.
- [ ] Timeout mỗi caption job 30 phút; xử lý riêng running, completed, completed-empty, lỗi nghiệp vụ và schema không nhận diện.
- [ ] Không retry login sai/captcha; retry network timeout, `429` và `5xx` bằng exponential backoff có jitter.
- [ ] Chuyển utterance thành `SrtCue`, cộng offset chunk vào timestamp.
- [ ] Ở vùng overlap, loại cue trùng bằng timestamp giao nhau + normalized text similarity; không xóa hai câu khác nhau chỉ vì gần thời gian.
- [ ] Sắp xếp cue, sửa timestamp âm/đảo, đánh lại ID liên tục và xuất `source.srt` UTF-8 BOM.
- [ ] Cache kết quả theo audio checksum + ngôn ngữ + cấu hình CapCut; retry chỉ upload/transcribe chunk thiếu.

### Bước 4 — Gemini dịch SRT

- [ ] Dùng SDK chính thức `google-genai`; Google xác nhận đây là SDK GA được khuyến nghị thay cho `google-generativeai` cũ trong [tài liệu thư viện](https://ai.google.dev/gemini-api/docs/libraries).
- [ ] Mặc định `gemini-3.6-flash`; đây là model stable/GA hiện hành. Dropdown cho Refresh danh sách model và chỉ hiển thị model hỗ trợ text generation/structured output. Tham chiếu [danh sách model](https://ai.google.dev/gemini-api/docs/models).
- [ ] Không dùng alias `latest` làm mặc định vì alias có thể đổi model; lưu exact model ID trong project.
- [ ] Không gửi `temperature`, `top_p`, `top_k` cho Gemini 3.6 Flash vì tài liệu hiện tại đã đánh dấu chúng deprecated.
- [ ] UI có API key masked, trạng thái nguồn `GEMINI_API_KEY`/override, nút Test, dropdown model, batch size 10–200 mặc định 50 và concurrency 1–8.
- [ ] Chia cue thành batch mặc định 50 block; mỗi item gồm `id`, timestamp, duration, source text và ngân sách độ dài ước tính theo duration.
- [ ] Mỗi batch được kèm các cue nguồn lân cận làm context nhưng schema chỉ yêu cầu trả đúng ID thuộc batch.
- [ ] Prompt gồm ngôn ngữ đích tiếng Việt, context Gen Z/Hài hước/Bắt trend/Tùy chỉnh, giữ nghĩa/tên riêng, không thêm giải thích và ưu tiên câu đủ ngắn để đọc trong timestamp.
- [ ] Dùng structured output với Pydantic schema `[{id: int, translated_text: str}]`; structured output giúp đảm bảo JSON đúng schema nhưng vẫn phải validation ngữ nghĩa theo [hướng dẫn chính thức](https://ai.google.dev/gemini-api/docs/structured-output).
- [ ] Các batch chạy async với limiter thích nghi, concurrency khởi đầu 3.
- [ ] Khi gặp `429`, tôn trọng `Retry-After`, giảm concurrency xuống tối thiểu 1; sau chuỗi request thành công mới tăng dần lại tối đa giá trị UI.
- [ ] Retry tối đa bốn lần cho timeout, `408`, `429`, `5xx`; dùng exponential backoff + full jitter. Không retry tự động `400`, `401`, invalid key hoặc model không tồn tại.
- [ ] Rate limit được quản lý theo project/model và thay đổi theo usage tier, vì vậy không hardcode RPM/TPM; tham chiếu [Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits).
- [ ] Sau lượt dịch, quét toàn bộ cue và đánh dấu chưa dịch khi: thiếu ID, text rỗng, text trùng source sau normalize, tỷ lệ ký tự Han còn cao, response trùng lặp bất thường hoặc schema sai.
- [ ] Gom riêng cue lỗi thành batch repair tối đa 50 cue và retry theo cùng policy; không dịch lại các cue đã hợp lệ.
- [ ] Sau retry cuối, bước chỉ hoàn thành khi không còn cue lỗi; nếu còn, UI hiển thị bảng cue lỗi và cho “Retry phần thiếu” hoặc sửa tay.
- [ ] Giữ nguyên ID/timestamp nguồn, chỉ thay `translated_text`; xuất `translated.vi.srt`.
- [ ] Cache từng batch theo model + prompt/context + source cue hash + batch config, cho phép resume sau khi đóng app.

### Bước 5 — CapCut TTS

- [ ] Cố định provider là CapCut TTS endpoint `storyboard/v1/tts/multi_platform`; không dùng endpoint audition cho nội dung thật.
- [ ] Lấy voice category `vietnamese`, chuẩn hóa `speaker`, `platform`, `effect_id`, `resource_id`, giới tính và tên hiển thị.
- [ ] Cache voice catalog 24 giờ theo app version/region, có nút Refresh.
- [ ] UI chọn voice, rate và volume; voice ID được lưu trong project nhưng cookie không được lưu ở đó.
- [ ] Tạo TTS riêng cho từng cue để giữ ánh xạ timestamp; concurrency mặc định 2 và cấu hình tối đa 4 vì đây là private API.
- [ ] Mỗi request trả audio URL; tải bytes, xác định container bằng ffprobe và remux/decode đúng chuẩn, không nối MP3 bằng phép cộng bytes.
- [ ] Retry tối đa ba lần cho timeout/`429`/`5xx`; session invalid chuyển về trạng thái cần đăng nhập, không tạo lại các cue đã cache.
- [ ] Đo duration audio sinh ra bằng ffprobe.
- [ ] Nếu audio dài hơn cue, tạo chuỗi `atempo` để speed-up đúng duration; factor ngoài khoảng tương thích được chia thành nhiều filter.
- [ ] Nếu audio ngắn hơn cue, giữ tốc độ voice và pad silence đến đúng duration.
- [ ] Nếu phải speed-up quá 1,5×, vẫn khớp timestamp tuyệt đối theo lựa chọn đã chốt nhưng ghi quality warning trên cue.
- [ ] Chuẩn hóa mỗi clip thành PCM float/48 kHz trước khi assemble.
- [ ] `TimelineAudioAssembler` đặt clip đúng `start_ms`, mix cue overlap bằng NumPy, giới hạn peak và xuất `voice_timeline.wav` đúng duration video.
- [ ] Cache mỗi cue theo translated text + voice ID + TTS config; thay một câu chỉ tạo lại voice câu đó.

### Bước 6 — Ghép voice

- [ ] Dùng video gốc và `voice_timeline.wav`.
- [ ] Nếu tắt âm gốc: map video stream gốc và voice mới.
- [ ] Nếu giữ âm gốc: điều chỉnh gain từng track, dùng `amix`, chống clipping bằng limiter và giữ duration theo video.
- [ ] Nếu chưa có hiệu ứng hình ảnh ở bước 7, bước này copy video stream, chỉ encode audio AAC để giảm thời gian.
- [ ] Xuất file trung gian atomically rồi ffprobe xác nhận video/audio stream và chênh duration trong tolerance.
- [ ] Không ghi đè artifact cũ cho đến khi file mới đã validate thành công.

### Bước 7 — Render cấu hình Studio

- [ ] Chuyển cấu hình Studio thành `FilterGraphBuilder`; tuyệt đối không ghép chuỗi shell từ dữ liệu người dùng.
- [ ] Tốc độ dùng `setpts` cho video và chuỗi `atempo` cho audio.
- [ ] Pitch ưu tiên `rubberband`; fallback `asetrate + aresample + atempo` để giữ duration.
- [ ] Zoom dùng scale/crop có clamp; lật dùng `hflip`/`vflip`; màu dùng `eq`/`hue`.
- [ ] Logo/overlay kiểm tra file tồn tại, chuẩn hóa đường dẫn, scale theo preview và overlay theo vị trí/timeline.
- [ ] Text đầu video và chữ chạy dùng `drawtext`; escape font/path/text an toàn.
- [ ] Subtitle được burn bằng `subtitles`/ASS; kiểm tra font tiếng Việt và dùng font fallback đóng gói cùng app.
- [ ] Áp subtitle trước biến đổi tốc độ để timestamp thay đổi đồng bộ với video/voice.
- [ ] Xuất filter graph ra file script tạm thay vì truyền command line quá dài.
- [ ] Parse `ffmpeg -progress pipe:1` để cập nhật progress/time/ETA và hỗ trợ cancel.
- [ ] Encoder ưu tiên NVENC → QSV → AMF → libx264 theo kết quả preflight; nếu GPU encode lỗi, tự chạy lại một lần bằng CPU.
- [ ] Nếu project không có chỉnh sửa hình ảnh, bỏ qua encode video và tái sử dụng output bước 6.

### Bước 8 — Xuất và kiểm tra video

- [ ] Map preset UI sang codec/CRF hoặc GPU quality tương đương; giữ resolution/FPS nguồn nếu người dùng không override.
- [ ] Render vào file `.partial`, không ghi trực tiếp output cuối.
- [ ] Khi hoàn thành, ffprobe kiểm tra video stream, audio stream, duration, resolution, FPS và file size > 0.
- [ ] Chỉ rename atomically sang tên output sau khi validation qua.
- [ ] Nếu output đã tồn tại, hỏi overwrite hoặc tự tăng suffix; không xóa file cũ trước khi file mới hợp lệ.
- [ ] Ghi summary gồm thời gian từng bước, cache hit/miss, retry, model Gemini, voice CapCut, encoder và warning chất lượng.
- [ ] Cho mở thư mục, mở file, copy log và dọn artifact trung gian theo lựa chọn người dùng.

## 4. Điều phối, lỗi và tối ưu

- [ ] Thay timer mô phỏng bằng state machine thật: `preparing`, `running`, `waiting_remote`, `retrying`, `paused`, `completed`, `failed`, `cancelled`.
- [ ] Progress tổng dùng trọng số động theo duration/bytes thực tế; không chia đều tám bước.
- [ ] Pause chỉ áp dụng tại boundary an toàn; upload/FFmpeg/Gemini đang chạy được cancel, sau đó resume từ checkpoint.
- [ ] Mọi lỗi có `error_code`, provider, retryable, user_message và debug detail đã redact.
- [ ] Không retry lỗi xác thực, captcha, invalid input, unsupported model hoặc schema project.
- [ ] Retry network dùng exponential backoff có jitter và cancellation-aware wait.
- [ ] Giới hạn đồng thời riêng cho download, FFmpeg, CapCut ASR, Gemini và CapCut TTS để tránh một provider làm nghẽn toàn pipeline.
- [ ] Có thể pipeline overlap an toàn: ASR chunk hoàn tất được merge/checkpoint sớm; Gemini bắt đầu khi toàn bộ source SRT ổn định; TTS có thể bắt đầu theo batch dịch đã validate.
- [ ] Không cho thay provider/model/voice/config ảnh hưởng artifact khi bước liên quan đang chạy.
- [ ] Structured log có project ID, run ID, step, chunk/batch/cue ID, elapsed, retry count và cache status.
- [ ] Thu thập benchmark cho video 1, 10 và 30 phút; báo thời gian từng bước thay vì cam kết tổng thời gian vì CapCut/Gemini phụ thuộc mạng/quota.
- [ ] Ghi rõ CapCut là private reverse-engineered API, có thể đổi endpoint/schema/chữ ký và cần contract test thường xuyên.

## 5. Kiểm thử và nghiệm thu

### Unit test

- [ ] URL/redirect Douyin, filename sanitation và metadata normalization.
- [ ] Silence boundary, overlap, timestamp offset và dedupe cue.
- [ ] CapCut sign MD5 bằng clock cố định; AWS SigV4 bằng test vector cố định.
- [ ] CRC/offset/part numbering cho TOS multipart.
- [ ] Parse mọi response schema voice/TTS đã ghi trong tài liệu.
- [ ] Caption completed, completed-empty, running, lỗi và timeout.
- [ ] SRT parse/serialize UTF-8, timestamp validation và merge chunk.
- [ ] Gemini batch size, structured schema, missing ID, Han detection và retry-only-missing.
- [ ] Adaptive limiter giảm khi `429`, tôn trọng `Retry-After` và phục hồi concurrency.
- [ ] TTS cache key, duration matching, atempo chain và silence padding.
- [ ] Filter graph cho từng tùy chọn Studio và escaping path/text.
- [ ] Artifact/config hash, cache hit, invalidation và schema v1→v2 migration.
- [ ] Redaction bảo đảm không log/lưu Gemini key, password, cookie, STS hoặc authorization.

### Integration test không gọi dịch vụ thật

- [ ] Mock toàn bộ chuỗi CapCut login → workspace → upload → asset → caption.
- [ ] Mock Gemini structured output, `429`, timeout, missing cue và model invalid.
- [ ] Mock yt-dlp progress/error/cancel.
- [ ] Chạy FFmpeg trên fixture ngắn để xác nhận extract, split, voice assembly, mix, subtitle và render.
- [ ] Mô phỏng app bị đóng giữa ASR/Gemini/TTS rồi mở lại từ manifest.
- [ ] Xác nhận UI responsive, progress/log cập nhật đúng thread và Stop không để worker mồ côi.

### Live test có opt-in

- [ ] Chỉ chạy khi có biến môi trường/test credential riêng; không chạy trong test mặc định.
- [ ] Douyin video ngắn, video dài, link rút gọn và cookie hết hạn.
- [ ] CapCut session mới/hết hạn, caption một chunk/nhiều chunk và completed-empty.
- [ ] Gemini model discovery, batch 50 cue, concurrency 3, `429` và repair cue thiếu.
- [ ] CapCut voice list, TTS câu ngắn/dài và speed-up >1,5×.
- [ ] Pipeline end-to-end cho video 1–3 phút; kiểm tra tiếng Việt, sync voice/subtitle, audio không clipping và output phát được.
- [ ] GPU render thành công và GPU lỗi tự fallback CPU.
- [ ] Quét project, cache và log để xác nhận không có credential.

### Definition of Done

- [ ] Run All xử lý video Douyin thật đến file video hoàn chỉnh.
- [ ] Mọi logic provider/media được gọi qua module tái sử dụng, không nằm trong widget Qt.
- [ ] Batch Gemini mặc định 50 cue, cấu hình được, async adaptive concurrency mặc định 3.
- [ ] Cue chưa dịch được quét và retry riêng; không dịch lại cue hợp lệ.
- [ ] Voice từng cue khớp timestamp tuyệt đối.
- [ ] Retry/resume dùng lại chunk/batch/cue đã hoàn thành sau lỗi hoặc restart.
- [ ] FFmpeg tự chọn GPU và fallback CPU.
- [ ] UI không block, Stop hoạt động và lỗi có hướng dẫn xử lý.
- [ ] Không credential nào xuất hiện trong project, cache, autosave hoặc log.
- [ ] Unit/integration test qua; live end-to-end test được xác nhận bằng tài khoản test.

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

