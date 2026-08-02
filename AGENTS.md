# Quy tắc làm việc cho agent

File này áp dụng cho toàn bộ repository. Agent phải đọc file này trước khi phân tích, sửa code,
chạy dịch vụ thật hoặc cập nhật kế hoạch.

## 1. Ngôn ngữ và cách phối hợp

- Trao đổi với người dùng bằng tiếng Việt, trừ khi người dùng yêu cầu ngôn ngữ khác.
- Dùng thuật ngữ kỹ thuật và tên class/API bằng tiếng Anh khi cần chính xác.
- Khi sửa lỗi, phải tìm nguyên nhân bằng bằng chứng từ process, log đã redact, artifact, manifest
  hoặc test. Không suy đoán rồi sửa mò.
- Nếu chỉ chẩn đoán, không thay đổi dữ liệu ngoài phạm vi. Khi người dùng yêu cầu triển khai/sửa,
  thực hiện trọn vẹn, kiểm thử và báo rõ phần chưa được live-test.
- Không tuyên bố hoàn thành nếu mới tạo skeleton, mock hoặc chưa kiểm chứng nhánh quan trọng.

## 2. Tài liệu phải đọc

- Đọc `plan-implement-chi-tiet-viet-sub.md` trước khi làm thay đổi liên quan pipeline. Đây là nguồn
  trạng thái triển khai chính.
- Đọc `tai-lieu-api-capcut.md` trước khi sửa CapCut login, workspace, upload, ASR, voice hoặc TTS.
- Đọc `README.md` trước khi thay dependency, cách chạy, workspace hoặc credential behavior.
- Khi hoàn thành thêm task trong kế hoạch, cập nhật checkbox bằng bằng chứng:
  - `[x]`: hoàn thành và đã kiểm chứng.
  - `[-]`: mới hoàn thành một phần; ghi ngắn gọn phần còn thiếu.
  - `[ ]`: chưa triển khai.
  - `[!]`: cần credential/live test hoặc bị chặn bên ngoài.
- Sau khi thay checklist, cập nhật số liệu tổng quan ở đầu file kế hoạch. Không làm tròn hoặc ghi
  phần trăm không khớp số checkbox thực tế.

## 3. Kiến trúc bắt buộc

- Widget Qt chỉ quản lý UI/state người dùng. Không đặt HTTP, chữ ký provider, yt-dlp command,
  FFmpeg command hoặc xử lý media trong widget.
- Model dùng chung nằm trong `src/quyetboit_studio/core/`.
- Provider nằm trong `src/quyetboit_studio/services/` theo từng domain: `douyin`, `capcut`,
  `gemini`, `media`, `subtitles`.
- `src/quyetboit_studio/pipeline/vietsub.py` chỉ điều phối service, checkpoint và artifact; không
  nhúng logic HTTP/chữ ký/filter đặc thù nếu có thể đặt trong service.
- UI production dùng `VietSubPipeline` qua worker thread. Giữ `MockPipelineService` cho unit test
  hoặc demo, không để mock trở thành default của UI production.
- Mọi tác vụ dài phải chạy ngoài Qt main thread. Chỉ cập nhật widget qua Qt signal/slot.
- Service dài hạn phải hỗ trợ `CancellationToken`; pause/resume phải dừng ở boundary an toàn và
  tiếp tục từ cache/checkpoint.

## 4. Credential và dữ liệu nhạy cảm

- Tuyệt đối không ghi password, cookie value, Gemini API key, `Authorization`, STS access/secret,
  upload auth, session token hoặc CDN query nhạy cảm vào project, autosave, workspace, manifest,
  test snapshot hay log.
- Password CapCut không bao giờ được lưu. Chỉ lưu cookie phiên, `did`, UID và workspace ID trong
  Windows Credential Manager qua `CapCutSessionStore`.
- Sau khi restore CapCut, phải xác thực bằng account-info và workspace API trước upload. Nếu UID
  hoặc workspace trống, gọi `ensure_session()`; sau validate phải cập nhật lại Credential Manager.
- Gemini API key lấy theo thứ tự: override đã lưu an toàn, `GEMINI_API_KEY`, hoặc input UI. Key
  nhập trên UI được lưu qua `SecureValueStore`, không ghi vào project.
- Chỉ lưu đường dẫn file cookie Douyin trong Credential Manager. Không copy nội dung cookie vào
  project/workspace.
- JSON cookie extension phải được lọc chỉ giữ domain `douyin.com`, chuyển sang Netscape trong file
  tạm quyền hạn chế và xóa trong `finally` sau mỗi lần gọi yt-dlp.
- Không lưu STS/upload token CapCut vì đây là credential ngắn hạn, không tái sử dụng được.
- Khi cần kiểm tra file cookie/session, chỉ in schema, số lượng, boolean hoặc mã lỗi đã redact;
  không in `name=value` thật.
- Dùng `sanitize()`/`redact_text()` cho dữ liệu có thể đi vào persistence hoặc log. Business code
  và tên operation được phép hiển thị nếu không chứa credential.
- Các file export cookie phải nằm trong `.gitignore`. Không stage/commit chúng dưới bất kỳ dạng nào.

## 5. Quy tắc provider

### Douyin

- Chỉ chấp nhận HTTPS trên `douyin.com` hoặc subdomain và kiểm tra URL cuối sau redirect.
- Chuẩn hóa URL `jingxuan?modal_id=<id>` thành `/video/<id>` trước khi gọi yt-dlp.
- Dùng yt-dlp Python API, không parse stdout CLI.
- Tên hiển thị Chromium có thể khác thư mục profile. Dùng `resolve_browser_profile()`; ví dụ
  `YTB - Cát Cát Review` ánh xạ thành `Profile 6` trên máy hiện tại.
- File tải dùng video ID/hash, không dùng title chưa sanitize. Sau tải luôn validate bằng ffprobe.

### CapCut

- CapCut là private reverse-engineered API, có thể đổi endpoint, schema và chữ ký. Tập trung constant
  trong config và giữ parser chịu được schema đã ghi trong tài liệu.
- Không retry login sai, captcha hoặc session invalid. Captcha `1101`, `1105`, `2046–2048` phải yêu
  cầu xác minh thật.
- Trước `prepare_upload_cloud`, luôn bảo đảm UID và workspace đã hợp lệ.
- Không dùng endpoint audition để tạo nội dung thật; TTS chính là
  `/storyboard/v1/tts/multi_platform`.
- Multipart TOS dùng part 3 MiB, CRC32, offset/part number ổn định và tối đa ba lần retry mỗi part.

### Gemini

- Dùng SDK `google-genai`, structured output Pydantic và exact model ID; không dùng alias `latest`.
- Không gửi tham số sampling đã bị loại khỏi cấu hình model mục tiêu.
- Giữ ID/timestamp nguồn, chỉ thay `translated_text`.
- Retry chỉ lỗi timeout/408/429/5xx. Tôn trọng `Retry-After`, giảm adaptive concurrency khi 429
  và không dịch lại cue đã hợp lệ.

## 6. Media, FFmpeg và artifact

- Không xây shell command từ dữ liệu người dùng. Luôn truyền argument list cho subprocess và escape
  filter/path bằng helper chuyên dụng.
- Output trung gian và cuối phải ghi vào `.partial`, validate bằng ffprobe rồi rename atomically.
  Không xóa/ghi đè output cũ trước khi file mới hợp lệ.
- FFmpeg progress dùng `-progress pipe:1`; `out_time_ms` thực tế có thể là microseconds và phải đổi
  đúng đơn vị trước khi tính tỷ lệ.
- Khi đọc FFmpeg progress, phải drain cả stdout và stderr. Có thể merge stderr vào stdout; không để
  pipe stderr đầy gây deadlock. Đây là lỗi đã từng làm bước 7 treo dù video render xong.
- FFmpeg phải có cancellation watcher độc lập, không chỉ kiểm tra cancel khi nhận thêm output.
- Không spam log mỗi progress event; chỉ log milestone hữu ích, hiện dùng mỗi 5%.
- Encoder ưu tiên NVENC → QSV → AMF → libx264 và fallback CPU đúng một lần nếu GPU lỗi.
- Cache chỉ hit khi path tồn tại, size/checksum đúng, input hash và config hash khớp.
- Thay config của một bước phải invalidate bước đó và downstream, không xóa artifact không liên quan.
- Project chưa lưu dùng `%LOCALAPPDATA%/QuyetBoITStudio/workspaces/<uuid>`. Project đã lưu mặc định
  dùng `.qbstudio-work/<uuid>` cạnh project.

## 7. UI và tiến trình

- Không block Qt main thread bằng login, HTTP, yt-dlp, FFmpeg, polling hoặc Gemini.
- Progress từng bước phải dựa trên bytes/duration/items thực tế. Progress tổng dùng `WEIGHTS`, không
  chia đều tám bước và không giữ giá trị 100% khi vẫn còn bước pending/running.
- Nút Stop phải hủy subprocess/network worker và không để QThread/process mồ côi.
- Lỗi UI phải có hướng xử lý và operation/business code an toàn khi có; không chỉ ghi “provider từ
  chối yêu cầu”. Debug detail không được chứa credential.
- Khi session hết hạn, giữ artifact đã hoàn thành và yêu cầu đăng nhập lại thay vì reset toàn pipeline.

## 8. Persistence và an toàn file

- Project schema hiện tại là v2; giữ migration v1→v2 và không phá file project cũ.
- Persistence và manifest phải ghi atomically bằng temp file + `os.replace`.
- Không chỉnh sửa hoặc xóa artifact/user output không thuộc task. Không dùng `git reset --hard`,
  `git checkout --` hoặc thao tác phá hủy thay đổi của người dùng.
- Trước cleanup recursive, resolve đường dẫn tuyệt đối và xác nhận target nằm trong đúng workspace.
- Không commit `.quyetboit-autosave.json`, cookie export, credential hoặc artifact workspace.

## 9. Kiểm thử và Definition of Done

- Dùng Python trong `.venv` của repo:

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src
git diff --check
```

- Baseline ngày 2026-08-02 là 38 test pass và Ruff pass. Không làm giảm số test hoặc bỏ test để
  che lỗi nếu chưa có lý do được người dùng chấp thuận.
- Sửa parser/cache/provider phải thêm unit test cho regression tương ứng.
- Sửa FFmpeg/filter/progress phải chạy ít nhất một integration render thực tế phù hợp rủi ro, kiểm
  tra process thoát, progress đạt 100%, output ffprobe hợp lệ và không còn file/process mồ côi.
- Test mặc định không gọi dịch vụ thật. Live test chỉ chạy opt-in khi có credential test riêng hoặc
  khi người dùng yêu cầu chẩn đoán luồng thật.
- Không đánh dấu Definition of Done cho Run All cho đến khi video thật đi qua bước 8, output cuối
  phát được, subtitle/voice đồng bộ và quét credential sạch.
