# Handoff — Menu English Mini game

Cập nhật: 2026-08-12

## 1. Mục đích

Tài liệu này giúp session/agent sau nắm nhanh chức năng **Tiếng Anh → Mini game** trước khi bắt đầu
task ở section khác. Nguồn checklist chính vẫn là `plan-mini-game.md`; handoff này không thay thế
checklist hoặc quy tắc repository.

Trước khi sửa code, phải đọc:

1. `AGENTS.md`.
2. `plan-mini-game.md` nếu task liên quan Minigame.
3. `plan-implement-chi-tiet-viet-sub.md` nếu thay pipeline/workspace/persistence dùng chung.
4. `tai-lieu-api-capcut.md` nếu đụng CapCut session, voice hoặc TTS.
5. `README.md` nếu đổi dependency, cách chạy, credential hoặc định dạng input.

Không ghi service-account, password, cookie, token hoặc API key vào JSON nội dung, project, cache,
test, log hay tài liệu handoff.

## 2. Trạng thái hiện tại

- Checklist Minigame: **90/90 task hoàn thành**.
- Baseline tự động: **142 test pass**; 68 test nằm trong `tests/test_minigame.py`.
- Ruff, `compileall` và `git diff --check` pass.
- Đã render integration thật bằng FFmpeg: video 1080×1920, có audio/video, progress 100%, output
  ffprobe hợp lệ và không còn file `.partial`/scene tạm.
- Đã render QA trực quan đủ Theme 1–5 cho hook/body/review và riêng Theme 2 với toàn bộ field JSON.
- Wheel đã được kiểm tra chứa đủ 10/10 asset review.
- Test mặc định không gọi Google/CapCut thật. Live TTS vẫn cần credential hợp lệ của người dùng.
- Review, palette và JSON input đã nằm trong trạng thái code hiện tại của repository. Tại thời điểm tạo
  handoff, chỉ `handoff-menu-minigame.md` là file untracked; vẫn phải kiểm tra lại status ở session mới
  và không reset/ghi đè thay đổi của người dùng.

Lệnh kiểm tra bắt buộc:

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src
git diff --check
```

Luôn chạy `git status --short --untracked-files=all` trước khi sửa.

## 3. Chức năng người dùng hiện có

Menu Minigame tạo video đoán từ, phát âm, score có CTA tích hợp và review theo luồng:

```text
JSON content
→ validate + project model
→ resolve Theme 1–5
→ tạo/cached hook, body, review frames
→ Google hoặc CapCut TTS
→ normalize + slow voice + cue sounds
→ timeline động
→ ASS subtitles theo typed manifest/palette
→ FFmpeg render 9:16
→ ffprobe validate
→ atomic output rename
```

UI production dùng `MinigamePipeline` trong `QThread`; preview cũng chạy worker riêng và debounce.
Nút Stop dùng `CancellationToken`, không chạy network/TTS/FFmpeg trên Qt main thread.

Các lựa chọn UI:

- Theme 1–5.
- Output 9:16. Hai lựa chọn 16:9/Both vẫn hiện nhưng bị disable và có tooltip giải thích.
- JSON editor cho Topic và toàn bộ vocabulary.
- Google Cloud TTS hoặc CapCut TTS, voice/language/family/gender/category, volume và slow rate.
- Output folder và output name.

Video nền legacy vẫn được giữ trong project schema để tương thích file cũ nhưng renderer theme không
yêu cầu hoặc sử dụng field này.

## 4. JSON content contract

Schema máy đọc nằm tại `minigame-content.schema.json`.

Định dạng chuẩn:

```json
{
  "schemaVersion": 1,
  "topic": "Hospital",
  "words": [
    {
      "vietnamese": "Bác sĩ",
      "english": "Doctor",
      "ipa": "/ˈdɒk.tər/",
      "wordType": "Noun",
      "example": "The doctor checks the patient.",
      "exampleVietnamese": "Bác sĩ khám cho bệnh nhân.",
      "level": "Dễ"
    }
  ]
}
```

Quy tắc:

- Root bắt buộc có đúng `schemaVersion`, `topic`, `words`; field lạ bị từ chối.
- `schemaVersion` hiện phải bằng `1`.
- `words` có 1–50 phần tử.
- Mỗi word phải khai báo đủ bảy key trong ví dụ. Giá trị phải là string; `level` chỉ nhận `Dễ`,
  `Trung bình` hoặc `Khó`.
- Tên chuẩn là `wordType`. Parser chấp nhận alias `workType`, nhưng `to_json()` và nút
  **Định dạng JSON** luôn chuẩn hóa thành `wordType`.
- `vietnamese`, `english`, `ipa`, `example` phải có nội dung khi render.
- `wordType` và `exampleVietnamese` được phép rỗng để project cũ không bị phá, nhưng nên cung cấp để
  output hoàn chỉnh.
- Parser bỏ BOM UTF-8 và báo lỗi syntax theo dòng/cột; lỗi schema báo theo `words[index]`.
- File mở từ UI phải là UTF-8/UTF-8 BOM và tối đa 2 MiB.

UI JSON:

- Dùng `QPlainTextEdit`, fixed-width font, không wrap dòng.
- Có **Mở file JSON…**, **Định dạng JSON**, **Sao chép JSON mẫu**.
- Preview và estimate dùng content hợp lệ gần nhất.
- Nếu người dùng đang gõ JSON dở, status báo lỗi; autosave/project sync giữ bản hợp lệ gần nhất và
  Render từ chối JSON hiện tại với lỗi cụ thể.
- Project persistence vẫn lưu model canonical (`topic` + `vocabulary`), không lưu nguyên JSON lỗi và
  không lưu `service_account_path`.

Model liên quan:

- `MinigameContent`: parse/normalize/serialize JSON content.
- `VocabularyItem`: `vietnamese`, `english`, `ipa`, `example`, `word_type`, `example_vietnamese`.
- `MinigameProject`: schema project v2, cấu hình theme/voice/output và danh sách vocabulary.

## 5. Scene và dữ liệu hiển thị

Timeline chuẩn:

1. `hook`: `start.png`, artwork challenge cố định và Topic động tự co/wrap/căn giữa.
2. Mỗi word:
   - `question`: Vietnamese + countdown 3→2→1.
   - `answer`: Vietnamese, English, IPA và Word Type nếu có.
   - ba voice phase: `LISTEN → SLOW → YOUR TURN`.
3. `score`: dùng `score.png`; ba range tính theo số từ level Dễ/Trung bình/Khó.
4. `review_start`: xuất hiện đúng một lần trong 2,8 giây bằng review-start asset.
5. Mỗi `review`: Vietnamese, English, IPA, Word Type, Example và Example Vietnamese nếu có.

TTS chỉ đọc English word và English example. `exampleVietnamese` là text hiển thị, không đưa vào TTS.

Audio gồm normal word, slow word, example và các cue tick/reveal/open/finish. Speech cache key dùng
provider + voice + language + volume + text + processing version; đổi theme/layout không làm mất cache
TTS.

## 6. Theme, asset và palette

Assets nằm trong `src/quyetboit_studio/assets/theme/`.

Mỗi theme dùng:

- `start.png` — hook và ảnh bìa; Topic động nằm trong panel riêng của từng theme.
- `background-video.png` — nền body; registry còn fallback alias legacy.
- `background-text.png` — nguồn RGB tạo RGBA mask.
- `review-start-screen.png` — màn mở đầu review.
- Theme 2 dùng alias hiện có `start-review-screen.png`.
- `rewview.png` — nền review; giữ nguyên typo filename hiện có, registry có fallback `review.png`.

`review-example.png`, `example.png` và `example-start-screen.png` chỉ là visual reference.

Tất cả asset theme hiện là dọc 941×1672; `background-text.png` là 971×1619 và được scale/composite lên
canvas canonical 1080×1920. Không sửa đè PNG nguồn. Mask/frame ghi `.partial`, validate rồi
`os.replace`.

Hệ màu:

- `ThemeColorPalette` định nghĩa semantic color riêng cho body, hook và review của từng theme.
- Theme 1/4: foreground sáng trên body tối.
- Theme 2/3/5: foreground đậm trên body sáng.
- Review Theme 4 dùng chữ sáng; review Theme 1/2/3/5 dùng chữ đậm.
- ASS lấy primary/outline/shadow/outline width từ typed `TextStyle`; không hard-code chữ trắng.
- Hook Pillow dùng gradient/outline theo theme; Topic dùng Montserrat SemiBold.
- Regression đo contrast trên actual safe-box, yêu cầu fill hoặc outline đạt tối thiểu 4,5:1.

Font bundle:

- `BebasNeue-Regular.ttf` cho title.
- `Montserrat-Variable.ttf` và `Montserrat-Italic-Variable.ttf` cho content/IPA.
- License OFL được đóng gói; renderer truyền `fontsdir` cho libass, không phụ thuộc font hệ thống.

`layout_version` hiện là **8**. Score dùng bounds/màu riêng cho từng theme; answer/review dùng các vùng độc lập
cho English Word, IPA, Word Type, voice và bilingual example. Mọi thay đổi slot/style/palette phải tăng version hoặc thay manifest
fingerprint để cache visual cũ bị invalidate đúng cách.

## 7. Kiến trúc và file quan trọng

### Model/core

- `src/quyetboit_studio/core/minigame.py`
  - JSON content/schema model.
  - Project/vocabulary model và migration.
  - Theme manifest, slots, styles, palette.
  - Timeline/scenes/score ranges.

### UI

- `src/quyetboit_studio/minigame.py`
  - JSON editor, theme picker, preview, voice/output config.
  - `MinigameWorker` và `MinigamePreviewWorker`.
  - Project collect/restore và invalid-JSON behavior.
- `src/quyetboit_studio/minigame_voice.py`
  - Voice catalog/filter/preview/config dialog.
- `src/quyetboit_studio/main_window.py`
  - Project save/autosave và liên kết Minigame settings.

Widget chỉ quản lý UI/state. Không đưa HTTP, provider signature, FFmpeg command hoặc pixel processing
vào widget.

### Pipeline và TTS

- `src/quyetboit_studio/pipeline/minigame.py`
  - Điều phối theme/frame/TTS/audio/timeline/subtitle/render/cache/cancel.
- `src/quyetboit_studio/services/minigame_tts.py`
  - Google/CapCut speech service và voice catalog.

### Media/theme

- `src/quyetboit_studio/services/media/minigame_theme.py` — discovery, typed layout, palette, preflight.
- `src/quyetboit_studio/services/media/minigame_mask.py` — flood-fill matte, feather/detail alpha mask.
- `src/quyetboit_studio/services/media/minigame_frame.py` — composited frame cache.
- `src/quyetboit_studio/services/media/minigame_text.py` — Pillow hook typography/cache.
- `src/quyetboit_studio/services/media/minigame.py` — audio service, ASS builder, renderer và scene concat.
- `src/quyetboit_studio/services/media/ffmpeg.py` — FFmpeg runner/progress/cancel/probe.

### Tài liệu và test

- `minigame-content.schema.json` — JSON Schema draft 2020-12.
- `plan-mini-game.md` — checklist/trạng thái chính.
- `README.md` — hướng dẫn người dùng và JSON mẫu.
- `tests/test_minigame.py` — unit, Qt UI, cache, palette, FFmpeg integration và full timeline.

## 8. Cache, workspace và output safety

- Workspace mặc định:
  `%LOCALAPPDATA%/QuyetBoITStudio/workspaces/<project_uuid>/minigame`.
- TTS normalized, slow audio, cue WAV, mask, hook text và visual frame đều có cache riêng.
- Visual frame key chứa theme checksum, layout version, slots, styles và placements.
- Font checksum nằm trong hook text cache key.
- Output không ghi đè file cũ; `_available_output()` tăng suffix `_2`, `_3`, ...
- Output và scene trung gian dùng `.partial`, ffprobe validate rồi rename atomic.
- Renderer drain progress/stderr, có cancellation watcher và GPU→CPU fallback đúng một lần.
- Stop/cancel không xóa output cũ hợp lệ và phải dọn scene/partial tạm.

## 9. Credential và persistence

- Google TTS yêu cầu service-account JSON trong phiên chạy.
- `service_account_path` bị xóa trong `MinigameProject.to_safe_dict()`; project/autosave không lưu path
  hoặc nội dung credential.
- CapCut TTS dùng session do phần VietSub quản lý; password không lưu.
- JSON content chỉ chứa nội dung học từ vựng, không chứa provider config/credential.
- Project schema hiện là v2; `VocabularyItem` field mới có default rỗng nên project cũ vẫn mở được.
- Không đổi schema project hoặc migration chỉ để đổi JSON content schema; đây là hai contract khác nhau.

## 10. Giới hạn/điểm cần biết trước task mới

1. **Chỉ hỗ trợ 9:16.** Không bật 16:9/Both cho đến khi có asset/layout ngang riêng.
2. `review-start-screen.png` là raster có nội dung thiết kế cố định. Một số theme có chữ “5 words/5/5”
   baked trong ảnh; JSON cho phép 1–50 từ nên task dynamic review-start phải có asset/template mới hoặc
   cơ chế che/vẽ lại được duyệt, không tự sửa PNG nguồn.
3. `wordType` và `exampleVietnamese` bắt buộc có key trong JSON v1 nhưng value được phép rỗng để tương
   thích project cũ. Nếu product muốn bắt buộc nội dung, cần chốt migration/UX trước khi đổi validate.
4. JSON editor chưa có syntax highlighting, line-number gutter hoặc JSON Schema autocomplete; hiện có
   fixed font, validation, formatter và file loader.
5. Live Google/CapCut TTS phụ thuộc credential/network/provider. Unit/integration mặc định dùng fixture;
   không tuyên bố provider live pass nếu chưa chạy opt-in.
6. Estimate duration trên UI là gần đúng; timeline thật phụ thuộc duration TTS sau normalize.
7. Không thêm logic HTTP/FFmpeg/pixel vào `MinigamePage`; giữ boundary service/pipeline hiện tại.

## 11. Cách bắt đầu nhanh theo loại task

### Thay JSON/input/data field

1. Sửa `MinigameContent`/`VocabularyItem` trong `core/minigame.py`.
2. Cập nhật `minigame-content.schema.json` và JSON mẫu trong README/UI.
3. Quyết định field có đưa vào TTS, answer, review hay persistence không; không parse rồi bỏ.
4. Giữ migration project cũ và credential-free serialization.
5. Thêm parser path-error, UI round-trip và subtitle/render regression.

### Thay UI/preview

1. Sửa `minigame.py`.
2. Tác vụ dài vẫn phải chạy worker; editor/preview cần debounce.
3. Kiểm tra dirty/autosave/set_project/shutdown.
4. Chạy Qt tests, bao gồm preview thread cleanup.

### Thay theme/layout/palette

1. Đo asset thật; không suy đoán màu/tọa độ.
2. Sửa typed slot/style/palette trong `minigame_theme.py`.
3. Tăng `layout_version` nếu output visual thay đổi.
4. Render frame thật cho cả nhóm nền sáng/tối và review.
5. Kiểm tra contrast, overflow, Vietnamese/IPA và long example.

### Thay TTS/audio/timeline

1. Sửa service trong `services/minigame_tts.py` hoặc `services/media/minigame.py`.
2. Giữ cache key bao phủ mọi config ảnh hưởng audio.
3. Giữ cancellation boundary và không gọi provider trong test mặc định.
4. Test duration/progress/normalization và full timeline 5 từ.

### Thay FFmpeg/render/output

1. Dùng argument list/filter helper; không dựng shell command từ input người dùng.
2. Drain stdout/stderr và giữ cancellation watcher.
3. Ghi partial, ffprobe, atomic rename và giữ output cũ khi fail.
4. Chạy integration render thật, kiểm tra progress 100%, stream video/audio và không còn process/file
   mồ côi.

## 12. Definition of Done cho task Minigame tiếp theo

- Yêu cầu hiển thị/dữ liệu được nối end-to-end từ input → model → pipeline → output.
- Không block Qt main thread.
- Không phá project v2 cũ hoặc lưu credential.
- Cache đúng: config/layout đổi thì invalidate phần liên quan; TTS không mất khi chỉ đổi theme.
- Output `.partial`/ffprobe/atomic vẫn đúng; Stop không để process/file mồ côi.
- Có unit/regression phù hợp; thay FFmpeg/layout phải có render QA/integration thật.
- `ruff`, toàn bộ `pytest`, `compileall` và `git diff --check` pass.
- Cập nhật `plan-mini-game.md`, README/schema nếu contract người dùng thay đổi.
