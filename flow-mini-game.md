# Đặc tả luồng video English Mini game

> Trạng thái: **Baseline vNext + dynamic challenge/CTA đã triển khai và nghiệm thu visual**
> — cập nhật ngày 2026-08-15.
>
> File này là nguồn tra cứu cho mọi thay đổi về flow, timing, visual scene và sound effect của
> menu `Tiếng Anh → Mini game`. Khi code và tài liệu khác nhau, phải cập nhật file này cùng thay đổi
> code sau khi render/test đã xác nhận hành vi mới. Baseline hiện dùng timing profile, cue manifest
> đóng gói và progress bar 30 fps; license, render A/B và khóa timing vẫn cần xác nhận ngoài repo.

## 1. Mục tiêu trải nghiệm

Video phải tạo được nhịp `gợi tò mò → cho đủ thời gian đoán → reveal có phần thưởng → nghe rõ →
tự lặp lại → ôn tập → tự đánh giá`. Không nối các TTS clip sát nhau, không để sound effect che voice,
không kéo dài bằng màn hình đứng yên thiếu chủ đích.

Phạm vi hiện tại là theme dọc 9:16. Nội dung English, IPA, nghĩa Việt, word type và example vẫn lấy
nguyên từ JSON; renderer không tự dịch hoặc sửa nội dung.

## 2. Bản đồ code và asset

| Nhu cầu thay đổi | Nguồn sự thật/code hiện tại | Điểm cần sửa khi triển khai vNext |
| --- | --- | --- |
| Thứ tự scene và timing | `src/quyetboit_studio/core/minigame.py` — `MinigameTimelineBuilder` | Tách timing thành profile có tên; builder sinh boundary/event xác định |
| Gắn sound effect vào timeline | `src/quyetboit_studio/pipeline/minigame.py` — đoạn tạo `cue_dir` và append `timeline.audio_events` | Resolve asset qua manifest, normalize/cache, đặt cue theo semantic event |
| Normalize, mix, loudness | `src/quyetboit_studio/services/media/minigame.py` — `MinigameAudioService` | Thêm import asset, gain/fade/ducking và chống overlap không chủ đích |
| Countdown/voice/score text | `src/quyetboit_studio/services/media/minigame.py` — `MinigameSubtitleBuilder._build_theme()` | Thay ba số countdown bằng remaining-time progress bar và giữ phase hold |
| Tọa độ/style theme | `src/quyetboit_studio/services/media/minigame_theme.py` | Thêm track/fill slot cho progress bar trong safe box từng theme |
| Ghép visual scene | `src/quyetboit_studio/pipeline/minigame.py` — `_visual_scenes()` và `services/media/minigame.py` — `_render_theme()` | Giữ tổng duration visual bằng audio; thêm transition/motion được cache |
| Preview và thời lượng dự kiến | `src/quyetboit_studio/minigame.py` | Dùng cùng timing profile, không tự tính một bộ số khác |
| Hook/Review Start động; CTA tĩnh | `services/media/minigame_text.py`, `services/media/minigame_theme.py` | Hook dùng digit atlas 0–9 riêng từng theme; Review Start dựng RGBA theo `N`; CTA dùng artwork hoàn chỉnh `cta-new.png` riêng từng theme |
| Regression/integration | `tests/test_minigame.py` | Kiểm tra timestamp, asset mapping, mix, frame animation và render thật |
| Đóng gói asset | `pyproject.toml` — `[tool.setuptools.package-data]` | Bổ sung `assets/audio/**/*`; hiện mới đóng gói theme và font |

### Asset âm thanh chuẩn

| Semantic cue | Asset | Thời lượng ffprobe ngày 2026-08-14 | Vai trò |
| --- | --- | ---: | --- |
| `question_start` | `assets/audio/sound-effect/check-mark.mp3` | 1,765 giây | Đánh dấu bắt đầu một từ/câu hỏi mới |
| `countdown_bed` | `assets/audio/sound-effect/clock-tick.mp3` | 3,709 giây | Nhạc nền duy nhất của countdown |
| `answer_reveal` | `assets/audio/sound-effect/hoan-thanh/Happy Win Game.mp3` | 1,858 giây | Phần thưởng khi English word xuất hiện |
| `score_win` | `assets/audio/sound-effect/hoan-thanh/Cartoon Big Win.mp3` | 4,340 giây | Mở màn kết quả |

Không tham chiếu asset bằng working directory. Đường dẫn phải resolve tương đối từ package
`quyetboit_studio`. Mỗi asset được decode/normalize một lần sang PCM mono 48 kHz trong cache; cache key
gồm checksum nguồn và audio-processing version.

## 3. Timing profile vNext đề xuất

Các mốc dưới đây là baseline để tạo bản render A/B đầu tiên, chưa phải cấu hình đã nghiệm thu.

| Hằng/profile field | Giá trị đề xuất | Lý do |
| --- | ---: | --- |
| `HOOK_MS` | 3.200 ms | Đủ đọc topic và hiểu luật, tăng nhẹ từ 2.800 ms |
| `QUESTION_INTRO_MS` | 700 ms | Transition + `check-mark`, tạo ranh giới giữa hai từ |
| `COUNTDOWN_MS` | 3.750 ms | Phát trọn `clock-tick` 3,709 giây và có fade biên |
| `COUNTDOWN_BAR_START` | 100% | Fill phủ toàn bộ track tại đầu countdown |
| `COUNTDOWN_BAR_END` | 0% | Fill co tuyến tính về 0; mép phải chạy từ phải sang trái |
| `POST_REVEAL_SFX_GAP_MS` | 450 ms | Chờ sau khi `Happy Win Game` kết thúc rồi mới đọc English |
| `BETWEEN_LISTEN_SLOW_MS` | 700 ms | Cho người xem xử lý lượt đọc tự nhiên |
| `BETWEEN_SLOW_REPEAT_MS` | 900 ms | Cho chuẩn bị trước `YOUR TURN` |
| `AFTER_REPEAT_MS` | 1.200 ms | Khoảng trống để người xem thực sự nhắc lại |
| `SCORE_MS` | 6.000 ms | Phát trọn Big Win và giữ thêm khoảng 1,66 giây để đọc kết quả |
| `REVIEW_START_MS` | 3.200 ms | Báo chuyển flow rõ ràng, không quá dài |
| `REVIEW_LEAD_MS` | 650 ms | Cho mắt định vị card trước voice từ |
| `REVIEW_WORD_TO_EXAMPLE_MS` | 900 ms | Tách voice từ khỏi voice câu ví dụ |
| `REVIEW_AFTER_EXAMPLE_MIN_MS` | 1.600 ms | Giữ chữ sau khi voice example kết thúc |
| `REVIEW_AFTER_EXAMPLE_MAX_MS` | 3.500 ms | Tránh review quá dài với câu rất dài |
| `CTA_MS` | 6.500 ms | CTA mới chứa nhiều hành động cần thời gian đọc thoải mái sau Review cuối |

`REVIEW_AFTER_EXAMPLE_MS` nên tính động theo độ dài phần chữ còn cần đọc, clamp trong khoảng
1.600–3.500 ms. Công thức phải deterministic và có unit test; baseline: `max(1.600 ms,
estimated_reading_ms - example_audio_duration_ms)`, với tốc độ đọc màn hình cấu hình tập trung.

## 4. Luồng scene chuẩn

### 4.1. Hook

1. Composite `start-base.png` với hai số động `N`, Topic và artwork tĩnh trong 3,2 giây. Nội dung
   tương đương `CAN YOU GET N OUT OF N?`, với `N = len(project.vocabulary)` trong miền 1–50.
2. Giữ cue mở đầu hiện có nếu không xung đột; không dùng một trong bốn asset semantic phía trên sai
   vai trò.
3. Topic dài và số hai chữ số vẫn auto-fit theo manifest; cover là byte-for-byte cùng frame Hook,
   không có nhánh render riêng. Đổi N, asset, font hoặc layout phải invalid cache frame.
4. Mỗi theme có `digit-atlas.png` RGBA gồm lưới 5×2 cho số 0–9. Manifest lưu typed grid metrics,
   alpha threshold, tracking, padding và algorithm version. Renderer crop theo alpha bbox, ghép glyph
   trên baseline chung rồi scale đồng đều cả hai chiều; không dùng font phẳng hoặc kéo méo chữ số.
5. Preflight từ chối atlas thiếu alpha hoặc thiếu bất kỳ cell nào. Checksum atlas tham gia checksum
   theme; thay atlas phải tạo lại Hook/cover nhưng không làm mất cache TTS.

### 4.2. Mỗi từ — Question và countdown

1. Bắt đầu `question_start`; header đổi sang `QUESTION i/N`, nghĩa Việt dùng style semantic
   `question_meaning` (baseline 44–104 px) rồi transition/fade vào.
2. Phát `check-mark.mp3` với gain đã chuẩn hóa. Đây là cue chuyển câu, không phải nhạc nền.
3. Sau 700 ms, bắt đầu `countdown_bed` và countdown 3,75 giây.
4. Countdown là **remaining-time progress bar**, không phải ba slide số. Tại `t=0`, fill đầy 100% từ
   mép trái đến mép phải. Trong 3,75 giây, chiều rộng fill giảm liên tục về 0%; mép trái được neo cố
   định và mép phải trượt đều từ phải sang trái. Track nền giữ nguyên để người xem nhìn được phần
   thời gian đã mất và phần còn lại.
5. Baseline không hiển thị các số lớn `3 → 2 → 1`; label `REVEALING ANSWER IN` và progress bar là
   tín hiệu countdown chính. Bar phải cập nhật theo thời gian thực ở 30 fps, không giảm theo ba nấc.
6. `clock-tick.mp3` phát một lần liên tục, không lặp ba bản và không dùng tone tick tổng hợp khi asset
   hợp lệ.
7. Khi fill chạm 0%, chuyển thẳng sang Answer; không có dead frame và không để bar âm/nhảy ngược.
8. Trong toàn phase Question, slot English hiển thị label `ENGLISH WORD` và placeholder. Mỗi Unicode
   grapheme cần đoán thành `_`; khoảng trắng, hyphen và apostrophe được giữ. English/IPA thật chỉ
   xuất hiện tại boundary `answer_reveal`.

### 4.3. Answer và ba phase voice

1. English word + IPA xuất hiện bằng reveal ngắn 180–260 ms.
2. Phát trọn `Happy Win Game.mp3` tại đầu Answer.
3. Sau khi effect kết thúc, giữ gap 450 ms rồi mới bắt đầu voice English đầu tiên. Effect không được
   chồng lên TTS.
4. Phase `LISTEN`: phát voice tự nhiên; giữ label trong toàn clip và 700 ms sau clip.
5. Phase `SLOW`: phát slow voice; giữ label trong toàn clip và 900 ms sau clip.
6. Phase `YOUR TURN`: phát lại mẫu tự nhiên như hành vi hiện tại, sau đó giữ 1.200 ms yên lặng để
   người xem nhắc lại. Nếu sau này đổi thành phase hoàn toàn im lặng, đó là thay đổi flow cần xác nhận.
7. Sang từ tiếp theo bằng `question_start`; không chèn thêm effect khác khiến âm thanh dày đặc.

### 4.4. Score

1. Sau từ cuối, hiện `score.png` trong tối thiểu 6 giây.
2. Phát `Cartoon Big Win.mp3` sau 120–180 ms để frame score đã ổn định trước transient đầu.
3. Ba score range và CTA phải đọc được trong toàn scene. Không cắt effect 4,340 giây.
4. 1,2 giây cuối giảm motion và audio tail để người xem có khoảng suy ngẫm trước Review.

### 4.5. Review

1. Composite `review-start-base.png` với hai chuỗi động trong 3,2 giây:
   `Let's review the N words you just learned` và `N/N completed`. Cả hai cùng lấy N từ project;
   badge auto-fit cả cụm, chừa vùng icon check.
2. Với mỗi từ, hiện toàn bộ English/IPA/meaning/example trước voice 650 ms.
3. Phát voice của từ, chờ 900 ms, rồi phát voice example.
4. Sau example, tiếp tục giữ nguyên text 1,6–3,5 giây theo thời gian đọc động.
5. Chuyển card bằng fade/slide nhẹ 180–240 ms; không thêm sound effect ở mọi card để tránh mệt tai.

### 4.6. CTA kết thúc

1. Sau Review cuối, thêm scene `cta` đúng 6,5 giây; visual/audio timeline cùng kết thúc tại cuối CTA.
2. Dùng trực tiếp `cta-new.png` riêng từng theme. Đây là artwork hoàn chỉnh với headline,
   nút Like/Share/Subscribe và các hành động lưu, bình luận, luyện tập; không chồng lớp chữ CTA
   động cũ lên artwork.
3. Scene fade-in 220 ms, không tạo FFmpeg process hoặc audio cue riêng; CTA giữ im lặng nếu audio
   trước đó đã kết thúc.
4. Nội dung nằm trong safe margin của canvas dọc và không phụ thuộc tên tài khoản.

## 5. Quy tắc mix audio

- Voice là ưu tiên cao nhất; `answer_reveal` phải kết thúc trước voice.
- `question_start` có thể overlap phần intro của question nhưng không được che transient chính của
  `countdown_bed`; dùng envelope/gain thay vì normalize tất cả cue về cùng độ lớn.
- Baseline nghiệm thu: voice khoảng -16 LUFS integrated; effect thấp hơn voice cảm nhận khoảng
  4–8 dB, true peak toàn timeline không vượt -1,5 dBTP.
- Không chỉ peak-normalize cả timeline sau khi cộng track vì sẽ làm một effect lớn kéo voice xuống.
  Mỗi nhóm `voice/effect` cần gain staging trước limiter/master.
- Mọi cue phải có fade in/out ngắn khi trim hoặc pad để không click. Không cắt đuôi MP3 bằng timestamp
  scene ngắn hơn asset.
- Nếu asset thiếu/hỏng trong bản cài đặt, preflight báo `minigame_audio_asset_missing` với hướng dẫn
  cài lại. Tone tổng hợp hiện tại chỉ giữ làm fallback có chủ đích cho compatibility/test, không âm
  thầm thay asset trong production.

## 6. Quy tắc motion và giữ chân người xem

- Remaining-time progress bar là motion chính của Question; Answer dùng pop/reveal vừa phải; Review
  dùng chuyển động nhẹ hơn.
- Thêm progress indicator `i/N` có chuyển trạng thái, nhưng không đổi layout artwork theme hoặc che
  vocabulary.
- Có thể thêm micro-motion nền (zoom/pan 1–2%) trong scene dài để tránh cảm giác ảnh đứng, với điều
  kiện không làm text rung và render vẫn 1080×1920/30 fps.
- Không thêm background music liên tục ở giai đoạn đầu. Chỉ bổ sung sau khi có track được xác nhận
  quyền sử dụng và có ducking dưới voice; quá nhiều lớp âm thanh sẽ làm giảm khả năng học.
- Không random sound/motion giữa các từ. Nhịp lặp lại nhất quán giúp người xem hiểu luật và tham gia.

## 7. Cách cập nhật thường gặp

### Đổi nhạc màn Score

1. Thay mapping semantic `score_win` trong audio cue manifest (được tạo khi triển khai plan), không
   sửa trực tiếp `_visual_scenes()`.
2. Đo duration mới bằng ffprobe.
3. Đặt `SCORE_MS >= cue_duration + score_reading_tail_ms`; không hardcode theo tên file.
4. Tăng audio-processing/cache version hoặc để checksum asset tự invalidate cache.
5. Cập nhật bảng asset ở file này, unit test mapping/timestamp và chạy integration render Score.

### Đổi tốc độ countdown

1. Chỉnh `COUNTDOWN_MS`; progress bar luôn nội suy từ 100% về 0% trên đúng duration này.
2. Quyết định rõ asset tick được trim, loop hay time-stretch; mặc định không time-stretch quá ±5%.
3. Cập nhật progress expression/dynamic overlay và audio event từ cùng một nguồn timing.
4. Test frame tại 0/25/50/75/100%, chiều rộng giảm đơn điệu, mép phải đi từ phải sang trái và không
   cắt đuôi effect.

### Đổi gap voice/example

1. Chỉnh field có tên trong timing profile, không cộng magic number trong pipeline.
2. Cập nhật test timestamp tương đối, preview duration và tài liệu mục 3.
3. Render ít nhất một từ ngắn và một câu example dài để nghe/xem thực tế.

## 8. Invariant và Definition of Done cho mọi lần cập nhật flow

- Timeline scene không overlap sai phase; audio event nằm trong `[0, duration_ms]`.
- Tổng visual scene duration bằng audio timeline duration; sai số output sau ffprobe trong tolerance
  hiện hành.
- English/IPA không lộ trong Question; text đúng item và đúng `i/N`.
- Không sound effect nào bị cắt đuôi hoặc che voice; không clipping/click ở điểm nối.
- Cache cũ bị invalidate khi timing/audio processing/asset checksum thay đổi, nhưng không synthesize
  lại TTS nếu text/provider/voice không đổi.
- Hook và Review Start phải cùng dùng `len(project.vocabulary)`; production không chứa literal `5`
  cho tổng số câu. CTA là artwork tĩnh riêng từng theme và checksum asset phải làm invalid cache.
- Atlas số của đúng theme phải có đủ 10 cell alpha, hai cụm số nằm trọn safe box với N=1/5/10/50;
  clean base không còn giữ nguyên pixel của hai vùng số `5` nguồn.
- Preview và output dùng cùng timing profile.
- Output ghi `.partial`, ffprobe hợp lệ rồi rename atomically; cancel không để FFmpeg/file tạm mồ côi.
- Test mặc định không gọi Google/CapCut thật. Integration FFmpeg phải xác nhận video/audio stream,
  progress 100%, frame đúng phase và không còn process mồ côi.

## 9. Quyết định cần xác nhận trước khi thay đổi yêu cầu

- Không bỏ yêu cầu nào của người dùng trong đặc tả này.
- Giữ phase thứ ba có voice mẫu rồi mới để khoảng im lặng. Chỉ bỏ voice mẫu để biến `YOUR TURN`
  thành khoảng im lặng hoàn toàn khi người dùng xác nhận.
- Giữ tone tổng hợp cũ làm fallback/test trong một chu kỳ; chỉ xóa code fallback sau khi package-data,
  license và asset preflight đã được kiểm chứng và người dùng xác nhận.
- Timing mục 3 là baseline; khóa số cuối sau khi người dùng xem bản render A/B ngắn 3 từ.
