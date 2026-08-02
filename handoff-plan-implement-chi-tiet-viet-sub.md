# Handoff triển khai tiếp VietSub Pipeline

Cập nhật: 2026-08-02

## 1. Mục đích

Tài liệu này dùng để tiếp tục công việc ở session sau. Nguồn checklist chính vẫn là
`plan-implement-chi-tiet-viet-sub.md`; không thay tài liệu này cho checklist chính.

Trước khi sửa code, session mới phải đọc đầy đủ:

1. `AGENTS.md`.
2. `plan-implement-chi-tiet-viet-sub.md`.
3. `tai-lieu-api-capcut.md` nếu đụng CapCut login, session, upload, ASR, voice hoặc TTS.
4. `README.md` nếu thay dependency, cách chạy, workspace hoặc credential behavior.

Không ghi credential, cookie value, Gemini key, STS hoặc signed CDN query vào log, test,
project, manifest hay tài liệu handoff.

## 2. Trạng thái hiện tại

- Checklist hiện tại: 124/180 task hoàn thành, 39 task một phần, 14 task chưa làm và 3 task
  chờ credential/live test.
- Baseline tự động: 42 test pass, Ruff pass, `compileall` pass và `git diff --check` pass.
- Chưa live-test lại CapCut voice catalog, preview và TTS sau thay đổi mới nhất.
- Chưa nghiệm thu end-to-end bước 8/output cuối.
- `E:\test.qbstudio.json` chỉ được dùng để kiểm tra read-only; không sửa hoặc xóa project/artifact
  của người dùng.

Lệnh kiểm tra bắt buộc:

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src
git diff --check
```

Luôn kiểm tra `git status --short` trước khi sửa và giữ nguyên mọi thay đổi không thuộc task.

## 3. Những thay đổi vừa hoàn thành

### Catalog voice CapCut

- Popup cấu hình CapCut có bộ lọc Tất cả, nam, nữ, ngôn ngữ và phong cách; có tìm kiếm,
  chọn voice, Refresh và nghe thử.
- Catalog lấy đồng thời nhiều category, retry lỗi retryable và loại trùng theo `speaker`.
- Cache catalog lưu vô thời hạn theo app version/region tại local app data.
- Cache lưu `fetched_at`, popup hiển thị thời điểm lấy gần nhất.
- Chỉ gọi lại API khi chưa có cache/cache không tương thích hoặc người dùng bấm Refresh.
- Cache ghi atomically và không chứa credential.

File chính:

- `src/quyetboit_studio/services/capcut/client.py`
- `src/quyetboit_studio/services/capcut/config.py`
- `src/quyetboit_studio/vietsub.py`

### TTS concurrency

- TTS từng cue chạy bounded concurrency cấu hình 1–50.
- Mặc định project mới là 25.
- Project cũ giữ giá trị đã lưu; không tự ghi đè cấu hình của người dùng.
- Catalog concurrency tối đa 4 là limiter riêng, không phải limiter sinh TTS.
- Retry TTS vẫn tối đa ba lần cho lỗi retryable.

File chính:

- `src/quyetboit_studio/models.py`
- `src/quyetboit_studio/pipeline/vietsub.py`
- `src/quyetboit_studio/services/capcut/client.py`

### Restore project từ manifest

- `Artifact.from_dict()` khôi phục đúng subtype `VideoArtifact`, `AudioArtifact` và `AudioChunk`.
- `ArtifactCache.find()` tìm artifact từ lần chạy trước và xác minh workspace, path, size,
  checksum.
- Pipeline có thể tìm dependency từ manifest thay vì chỉ dựa vào `results` trong RAM.
- Đã xác nhận read-only với `E:\test.qbstudio.json`: artifact bước 1, 4, 5 và 6 hợp lệ,
  gồm `translated.vi.srt`, `voice_timeline.wav` và `mixed.mp4`.

File chính:

- `src/quyetboit_studio/core/models.py`
- `src/quyetboit_studio/core/cache.py`
- `src/quyetboit_studio/pipeline/vietsub.py`

## 4. Các vấn đề phát hiện khi audit kế hoạch

### P0 — Checklist đang đánh dấu hoàn thành sai

1. Restore CapCut chưa luôn validate session.
   - `CapCutEditClient.ensure_session()` chỉ gọi `validate_session()` khi UID/workspace trống.
   - Session khôi phục có sẵn UID/workspace được dùng thẳng.
   - Trái với yêu cầu phải gọi account-info và workspace API trước upload.

2. Thay config chưa invalidate bước và downstream.
   - Signal `page.changed` hiện chỉ đánh dấu project dirty.
   - Trạng thái cũ vẫn `completed`, nên Run All có thể bỏ qua bước dù voice/model/config đã đổi.
   - Dòng checklist “Thay đổi một bước sẽ invalidate…” đang `[x]` nhưng chưa đúng.

3. Manifest “sau mỗi chunk/batch” chưa đúng với Gemini.
   - Chunk ASR và cue TTS có checkpoint.
   - Gemini mới cache output cuối, chưa cache từng batch.
   - Checklist dòng manifest và dòng cache Gemini đang mâu thuẫn.

4. Workspace project đã lưu chưa luôn nằm cạnh project.
   - `save_project_as()` không đổi workspace nếu pipeline đã có bước hoàn thành.
   - Project có thể tiếp tục dùng `%LOCALAPPDATA%` dù checklist đang `[x]` cho
     `.qbstudio-work/<uuid>` cạnh project.

5. Restore manifest mới đúng cho manifest hợp lệ.
   - Nếu manifest mất/hỏng/checksum sai nhưng project lưu `completed`, UI chưa reconcile trạng thái.
   - Cần hạ bước không hợp lệ và downstream về `ready/pending`, đồng thời báo artifact bị thiếu.

### P1 — Chức năng UI đang hứa nhiều hơn renderer thực hiện

1. Studio lưu `pitch`, logo, overlay, text, `layer_start` và `layer_duration`, nhưng renderer hiện
   chỉ áp dụng subtitle, speed, zoom, flip và saturation.
2. Layer Studio hiện chủ yếu là tên; chưa có model asset/path/property đủ để render logo/overlay.
3. `VideoRenderer.render(..., export=...)` nhận export config nhưng không dùng resolution, FPS,
   quality hoặc preset codec.
4. Bước 8 chủ yếu copy output bước 7; lựa chọn export trên UI chưa được áp dụng thật.
5. Task bỏ encode bước 7 phải ghi rõ: chỉ được reuse bước 6 khi không có visual filter, bao gồm
   không burn subtitle.

### P1 — Async/retry/concurrency

1. Gemini retry gọi `CancellationToken.wait()` đồng bộ trong coroutine, có thể block event loop
   và các batch khác trong thời gian backoff.
2. TTS hiện cho concurrency mặc định 25, tối đa 50 theo yêu cầu người dùng, nhưng chưa có adaptive
   limiter khi CapCut trả `429`. Nên giảm concurrency động và tránh retry storm.
3. Multipart upload retry ngay lập tức và chưa phân biệt đầy đủ timeout/429/5xx với lỗi 4xx không
   retry; chưa có backoff cancellation-aware giữa các lần retry.
4. Dòng checklist giới hạn provider còn ghi CapCut TTS chủ yếu tuần tự; thông tin này đã lỗi thời.

### P2 — Task trùng hoặc quá thiên về implementation

- Pitch/rubberband fallback bị lặp ở hai nơi trong checklist.
- Nút Test Gemini bị lặp trong phần credential và phần bước 4.
- Cache batch Gemini lặp với Definition of Done về resume.
- `CapCutVoiceService` độc lập không bắt buộc nếu voice catalog được tách sạch trong
  `CapCutTtsService`.
- CapCut HTTP không bắt buộc phải async nếu toàn bộ request nằm trong worker thread và có bounded
  concurrency/cancellation đúng.
- “Tối đa hai encode song song” đã thỏa điều kiện an toàn khi chạy tuần tự; chỉ còn là tối ưu hiệu
  năng nếu muốn chạy thật sự song song.
- Hai interface `AudioProcessor.extract(...)` và `CaptionService.transcribe(...)` nên đánh giá theo
  capability/kiến trúc, không bắt buộc khớp đúng tên/signature trong kế hoạch.

### Thông tin đã xác minh bên ngoài

`gemini-3.6-flash` là exact stable model ID và hỗ trợ structured output theo tài liệu Google cập
nhật 2026-07-21:

https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash

Không đổi sang alias `latest`.

## 5. Thứ tự triển khai đề xuất cho session sau

### Giai đoạn A — Sửa tính đúng đắn của state/checkpoint

1. Sửa restore CapCut:
   - Phân biệt session mới vừa login và session restore từ Credential Manager.
   - Trước upload/ASR phải validate account-info và workspace ít nhất một lần trong process hiện tại.
   - Sau validate lưu lại UID/workspace vào Credential Manager.
   - Không retry invalid session/captcha.

2. Thêm config invalidation mapping:
   - Source/auth ảnh hưởng từ bước 1.
   - Audio config ảnh hưởng từ bước 2.
   - ASR provider/language ảnh hưởng từ bước 3.
   - Gemini model/context/batch/concurrency ảnh hưởng từ bước 4.
   - TTS provider/voice/speed/pitch/volume ảnh hưởng từ bước 5.
   - Mix config ảnh hưởng từ bước 6.
   - Studio config ảnh hưởng từ bước 7.
   - Export config ảnh hưởng bước 8.
   - Khi config đổi: cập nhật status bước đó/downstream và loại record cache tương ứng; không xóa
     artifact unrelated hoặc output người dùng.
   - Không cho đổi config ảnh hưởng pipeline khi worker đang chạy.

3. Reconcile project với manifest khi mở:
   - Validate artifact bắt buộc cho từng bước đã `completed`.
   - Gặp bước đầu tiên thiếu/hỏng thì giữ các bước trước hợp lệ, hạ bước đó thành `ready`, downstream
     thành `pending`.
   - Hiển thị thông báo cụ thể, không reset toàn pipeline.
   - Bổ sung regression test manifest mất, JSON hỏng, checksum sai và workspace bị di chuyển.

4. Chốt hành vi Save As/workspace:
   - Không di chuyển recursive artifact âm thầm.
   - Hoặc giữ workspace hiện tại và sửa wording checklist, hoặc cung cấp lựa chọn migrate có copy,
     validate rồi mới chuyển.

Definition of Done giai đoạn A:

- Đổi một config đã hoàn thành khiến đúng bước/downstream cần chạy lại.
- Mở project cũ với artifact hợp lệ chạy tiếp được.
- Mở project có artifact hỏng không báo sai rằng bước trước hoàn thành.
- Session restore luôn được validate trước upload mà không làm mất artifact đã xong.

### Giai đoạn B — Hoàn thiện Studio và export

1. Thiết kế model layer thật cho logo/overlay/text; không chỉ lưu tên hiển thị.
2. Validate path asset và escape filter/path/text an toàn.
3. Implement pitch:
   - Ưu tiên `rubberband`.
   - Fallback `asetrate + aresample + atempo` giữ duration.
4. Implement logo/overlay theo position/start/duration.
5. Implement intro text và scrolling text bằng `drawtext` hoặc ASS với escaping an toàn.
6. Map export preset sang resolution, FPS, encoder và quality/CRF tương ứng.
7. Giữ source resolution/FPS khi người dùng chọn chế độ giữ nguyên; UI hiện chưa có lựa chọn rõ này.
8. Chỉ reuse output bước 6 khi không có bất kỳ visual transform/subtitle burn nào.
9. Thêm integration fixture FFmpeg cho từng filter và combination chính.

Definition of Done giai đoạn B:

- Mỗi control Studio hoặc tạo thay đổi đo/quan sát được trong output, hoặc bị ẩn/disable nếu chưa hỗ trợ.
- ffprobe xác nhận resolution/FPS/audio/video stream đúng export config.
- Output ghi `.partial`, validate rồi rename atomically.

### Giai đoạn C — Gemini resume và async correctness

1. Thay blocking wait trong coroutine bằng async cancellation-aware delay.
2. Cache từng batch theo model + context/prompt + source cue hash + batch config.
3. Resume chỉ chạy batch thiếu/sai; không dịch lại cue hợp lệ.
4. Bổ sung UI model refresh, nguồn API key, Test key/model và bảng cue lỗi/retry phần thiếu.
5. Test `Retry-After`, 408, 429, 5xx, invalid model/key, thiếu ID và schema lỗi.

### Giai đoạn D — CapCut robustness và throughput

1. Thêm retry policy chung cho request CapCut retryable; không retry captcha/session invalid/4xx
   không phù hợp.
2. Thêm adaptive limiter cho TTS concurrency 1–50, khởi đầu bằng giá trị project, giảm khi 429 và
   phục hồi từ từ sau request thành công.
3. Nếu parallel ASR chunk, giới hạn tối đa 2 và bảo đảm STS/upload context hoàn toàn độc lập.
4. Thêm backoff+jitter+cancellation cho retry TOS part, vẫn giữ nguyên upload ID/part/offset/CRC.
5. Thêm fixture cho mọi response schema voice/TTS/caption đã ghi trong tài liệu CapCut.

### Giai đoạn E — Preflight, logging và nghiệm thu

1. Màn hình preflight cho FFmpeg/ffprobe, encoder/filter, browser profile, CapCut session và Gemini.
2. Structured log có project/run/step/item ID, elapsed, retry và cache status nhưng phải redact.
3. Mock đầy đủ CapCut login → workspace → upload → asset → caption.
4. Mock yt-dlp progress/error/cancel và kiểm tra cleanup `.part`.
5. Chuyển FFmpeg integration thủ công thành pytest fixture ngắn.
6. Chạy live matrix opt-in và end-to-end bước 8 bằng credential test riêng.

## 6. Cách cập nhật checklist sau khi triển khai

- Trước hết sửa lại các dòng đang `[x]` nhưng bằng chứng code chưa đáp ứng, đặc biệt session restore,
  config invalidation, workspace và manifest batch.
- Gộp hoặc ghi chú các task trùng trước khi tính lại tổng số.
- Không thay tổng mẫu số 180 nếu chỉ gộp wording mà chưa quyết định chính thức cách đếm.
- Sau mỗi thay đổi checkbox, đếm lại chính xác `[x]`, `[-]`, `[ ]`, `[!]` và cập nhật phần tổng quan.
- Chỉ dùng `[x]` khi có unit/integration/live evidence phù hợp rủi ro.
- Definition of Done Run All vẫn chưa hoàn thành cho đến khi video thật đi qua bước 8, output phát
  được, subtitle/voice đồng bộ và quét credential sạch.

## 7. Lưu ý an toàn khi tiếp tục

- Không live-test mặc định; chỉ opt-in với credential test hoặc yêu cầu rõ của người dùng.
- Không in cookie/session/token/key hoặc signed URL khi debug.
- Không chỉnh sửa `E:\test.qbstudio.json` hoặc artifact ngoài workspace task.
- Không xóa cache/artifact recursive nếu chưa resolve và xác nhận target nằm đúng workspace.
- Không dùng `git reset --hard`, `git checkout --` hoặc ghi đè thay đổi hiện có.
- Khi sửa FFmpeg/filter/progress, bắt buộc chạy integration render thật phù hợp rủi ro và ffprobe
  output.

