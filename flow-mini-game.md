# Đặc tả luồng video English Mini game

> Trạng thái: **Baseline vNext + dynamic challenge/CTA đã triển khai và nghiệm thu visual**
> — cập nhật ngày 2026-08-22.
>
> File này là nguồn tra cứu cho mọi thay đổi về flow, timing, visual scene và sound effect của
> menu `Tiếng Anh → Mini game`. Khi code và tài liệu khác nhau, phải cập nhật file này cùng thay đổi
> code sau khi render/test đã xác nhận hành vi mới. Baseline hiện dùng timing profile, cue manifest
> đóng gói và progress bar 30 fps; license, render A/B và khóa timing vẫn cần xác nhận ngoài repo.

## 1. Mục tiêu trải nghiệm

Video phải tạo được nhịp `gợi tò mò → cho đủ thời gian đoán → reveal có phần thưởng → nghe rõ →
tự lặp lại → ôn tập → tự đánh giá`. Không nối các TTS clip sát nhau, không để sound effect che voice,
không kéo dài bằng màn hình đứng yên thiếu chủ đích.

Phạm vi hiện tại là video dọc 9:16 với hai flow visual độc lập: `static_theme` và `video_theme`.
Nội dung English, IPA, nghĩa Việt, word type và example vẫn lấy nguyên từ JSON; renderer không tự
dịch hoặc sửa nội dung.

## 2. Bản đồ code và asset

| Nhu cầu thay đổi | Nguồn sự thật/code hiện tại | Điểm cần sửa khi triển khai vNext |
| --- | --- | --- |
| Thứ tự scene và timing | `src/quyetboit_studio/core/minigame.py` — `MinigameTimelineBuilder` | Tách timing thành profile có tên; builder sinh boundary/event xác định |
| Gắn sound effect vào timeline | `src/quyetboit_studio/pipeline/minigame.py` — đoạn tạo `cue_dir` và append `timeline.audio_events` | Resolve asset qua manifest, normalize/cache, đặt cue theo semantic event |
| Normalize, mix, loudness | `src/quyetboit_studio/services/media/minigame.py` — `MinigameAudioService` | Thêm import asset, gain/fade/ducking và chống overlap không chủ đích |
| Countdown/voice/score text | `src/quyetboit_studio/services/media/minigame.py` — `MinigameSubtitleBuilder._build_theme()` | Thay ba số countdown bằng remaining-time progress bar và giữ phase hold |
| Tọa độ/style theme | `src/quyetboit_studio/services/media/minigame_theme.py` | Thêm track/fill slot cho progress bar trong safe box từng theme |
| Ghép visual scene/nền tùy chọn | `src/quyetboit_studio/pipeline/minigame.py` — `_visual_scenes()` và `services/media/minigame.py` — `_render_theme()` | Không có video: dùng frame theme như cũ. Có video: Question/Answer dùng mask `background-text.png`; từng Review card dùng alpha cutout nguyên panel `review.png` trên cùng video |
| Video-theme cũ | `services/media/minigame_video_theme.py` | Giữ để tương thích code/project cũ; production không dùng surface sinh bằng code khi người dùng chọn video nền |
| Preview và thời lượng dự kiến | `src/quyetboit_studio/minigame.py` | Dùng cùng timing profile, không tự tính một bộ số khác |
| Hook/Review Start động; CTA tĩnh | `services/media/minigame_text.py`, `services/media/minigame_theme.py` | Hook dùng digit atlas 0–9 riêng từng theme; Review Start dựng RGBA theo `N`; CTA dùng artwork hoàn chỉnh `cta-new.png` riêng từng theme |
| Regression/integration | `tests/test_minigame.py` | Kiểm tra timestamp, asset mapping, mix, frame animation và render thật |
| Đóng gói asset | `pyproject.toml` — `[tool.setuptools.package-data]` | Bổ sung `assets/audio/**/*`; hiện mới đóng gói theme và font |

### 2.1. Hai flow visual

- **Theme tĩnh:** khi không có `background_video`, toàn bộ Hook, Question/Answer, Score, Review Start,
  Review và CTA dùng artwork PNG của `theme-1`…`theme-5` như baseline cũ.
- **Nền video:** khi có `background_video`, Question/Answer dùng video người dùng chọn làm lớp dưới,
  sau đó composite nguyên alpha mask sinh từ `background-text.png`; mỗi Review card dùng cùng video
  dưới alpha cutout lấy trực tiếp từ panel trung tâm của `review.png`. ASS text vẫn lấy nguyên từ
  `theme_id`. Không recolor, không tạo glass/panel mới, không đổi slot/font/cỡ chữ. Hook, Score,
  Review Start và CTA tiếp tục dùng đúng artwork PNG tĩnh của theme.
- Video nền bị tắt audio, loop khi ngắn hơn phần học và crop focus 9:16 được áp dụng thống nhất.
- Timeline, TTS, sound effect, subtitle content và timing dùng chung giữa hai flow; khác biệt duy nhất
  ở phần học là nguồn nền ảnh hay video trước khi composite cùng một mask/text theme.

### Asset âm thanh chuẩn

| Semantic cue | Asset | Thời lượng ffprobe ngày 2026-08-14 | Vai trò |
| --- | --- | ---: | --- |
| `question_start` | `assets/audio/sound-effect/check-mark.mp3` | 1,765 giây | Đánh dấu bắt đầu một từ/câu hỏi mới |
| `countdown_bed` | `assets/audio/sound-effect/clock-tick.mp3` | 3,709 giây | Nhạc nền duy nhất của countdown |
| `answer_reveal` | `assets/audio/sound-effect/hoan-thanh/Happy Win Game.mp3` | 1,858 giây | Phần thưởng khi English word xuất hiện |
| `score_win` | `assets/audio/sound-effect/hoan-thanh/Cartoon Big Win.mp3` | 4,340 giây | Mở màn kết quả |
| `hook_bed`, `review_start_bed`, `cta_bed` | `assets/audio/sound-effect/intro/Be Happy (Teaser) (Kiểm tra bản quyền).mp3` | 35,135 giây | Hook/Review Start phát 3,2 giây, fade out 450 ms rồi giữ màn 650 ms im lặng; CTA trim đúng scene với fade 180 ms |

Không tham chiếu asset bằng working directory. Đường dẫn phải resolve tương đối từ package
`quyetboit_studio`. Mỗi asset được decode/normalize một lần sang PCM mono 48 kHz trong cache; cache key
gồm checksum nguồn và audio-processing version.

## 3. Timing profile vNext đề xuất

Các mốc dưới đây là baseline để tạo bản render A/B đầu tiên, chưa phải cấu hình đã nghiệm thu.

| Hằng/profile field | Giá trị đề xuất | Lý do |
| --- | ---: | --- |
| `HOOK_MS` | 3.850 ms | 3,2 giây nhạc (fade out 450 ms) + 650 ms giữ Hook im lặng trước Question |
| `HOOK_BED_MS` / `HOOK_TAIL_HOLD_MS` | 3.200 / 650 ms | Tách duration nhạc khỏi khoảng dừng màn hình, không chuyển cảnh đột ngột |
| `QUESTION_INTRO_MS` | 700 ms | Transition + `check-mark`, tạo ranh giới giữa hai từ |
| `COUNTDOWN_MS` | 3.750 ms | Phát trọn `clock-tick` 3,709 giây và có fade biên |
| `COUNTDOWN_BAR_START` | 100% | Fill phủ toàn bộ track tại đầu countdown |
| `COUNTDOWN_BAR_END` | 0% | Fill co tuyến tính về 0; mép phải chạy từ phải sang trái |
| `POST_REVEAL_SFX_GAP_MS` | 450 ms | Chờ sau khi `Happy Win Game` kết thúc rồi mới đọc English |
| `BETWEEN_LISTEN_SLOW_MS` | 700 ms | Cho người xem xử lý lượt đọc tự nhiên |
| `BETWEEN_SLOW_REPEAT_MS` | 900 ms | Cho chuẩn bị trước `YOUR TURN` |
| `AFTER_REPEAT_MS` | 1.200 ms | Khoảng trống để người xem thực sự nhắc lại |
| `SCORE_MS` | 6.000 ms | Phát trọn Big Win và giữ thêm khoảng 1,66 giây để đọc kết quả |
| `REVIEW_START_MS` | 3.850 ms | 3,2 giây nhạc (fade out 450 ms) + 650 ms giữ Review Start im lặng trước card đầu |
| `REVIEW_START_BED_MS` / `REVIEW_START_TAIL_HOLD_MS` | 3.200 / 650 ms | Tạo nhịp chuyển êm giữa Score và Review |
| `REVIEW_LEAD_MS` | 650 ms | Cho mắt định vị card trước voice từ |
| `REVIEW_WORD_TO_EXAMPLE_MS` | 1.800 ms | Cho người xem đọc hết từ/IPA/nghĩa trên card trước khi voice câu ví dụ bắt đầu |
| `REVIEW_AFTER_EXAMPLE_MIN_MS` | 1.600 ms | Giữ chữ sau khi voice example kết thúc |
| `REVIEW_AFTER_EXAMPLE_MAX_MS` | 3.500 ms | Tránh review quá dài với câu rất dài |
| `CTA_MS` | 6.500 ms | CTA mới chứa nhiều hành động cần thời gian đọc thoải mái sau Review cuối |

`REVIEW_AFTER_EXAMPLE_MS` nên tính động theo độ dài phần chữ còn cần đọc, clamp trong khoảng
1.600–3.500 ms. Công thức phải deterministic và có unit test; baseline: `max(1.600 ms,
estimated_reading_ms - example_audio_duration_ms)`, với tốc độ đọc màn hình cấu hình tập trung.

## 4. Luồng scene chuẩn

### 4.1. Hook

1. Cả hai flow composite `start-base.png` với hai số động `N`, Topic và artwork tĩnh. Nội dung tương
   đương `CAN YOU GET N OUT OF N?`, với `N = len(project.vocabulary)` trong miền 1–50.
2. Phát `hook_bed` trong 3,2 giây (gain -18 dB, fade out 450 ms), rồi giữ nguyên Hook thêm 650 ms
   không nhạc trước Question. Nhạc nền chỉ nằm trong scene không có TTS; không dùng asset semantic
   khác sai vai trò.
3. Topic dài và số hai chữ số vẫn auto-fit theo manifest. Cover của cả hai flow là byte-for-byte
   Hook frame tĩnh đã composite. Đổi N, asset, font hoặc layout phải invalid cache frame.
4. Mỗi theme có `digit-atlas.png` RGBA gồm lưới 5×2 cho số 0–9. Manifest lưu typed grid metrics,
   alpha threshold, tracking, padding và algorithm version. Renderer crop theo alpha bbox, ghép glyph
   trên baseline chung rồi scale đồng đều cả hai chiều; không dùng font phẳng hoặc kéo méo chữ số.
5. Preflight từ chối atlas thiếu alpha hoặc thiếu bất kỳ cell nào. Checksum atlas tham gia checksum
   theme; thay atlas phải tạo lại Hook/cover nhưng không làm mất cache TTS.

### 4.2. Mỗi từ — Question và countdown

Static flow dùng `background-video.png` và alpha sinh từ `background-text.png`. Video flow chỉ thay
`background-video.png` bằng video đã chọn; dùng lại đúng alpha mask `background-text.png` và cùng
ASS text, không sinh hoặc chỉnh sửa surface khác.

1. Bắt đầu `question_start`; header đổi sang `QUESTION i/N`, nghĩa Việt dùng style semantic
   `question_meaning` (baseline 44–104 px) rồi transition/fade vào.
2. Phát `check-mark.mp3` với gain đã chuẩn hóa. Đây là cue chuyển câu, không phải nhạc nền.
3. Sau 700 ms, bắt đầu `countdown_bed` và countdown 3,75 giây.
4. Countdown là **remaining-time progress bar**, không phải ba slide số. Tại `t=0`, fill đầy 100% từ
   mép trái đến mép phải. Trong 3,75 giây, chiều rộng fill giảm liên tục về 0%; mép trái được neo cố
   định và mép phải trượt đều từ phải sang trái. Track nền giữ nguyên để người xem nhìn được phần
   thời gian đã mất và phần còn lại.
5. Baseline không hiển thị các số lớn `3 → 2 → 1` hoặc label `REVEALING ANSWER IN`. Thanh progress
   nằm trực tiếp trên divider phía sau vị trí label cũ, theo safe slot `countdown_bar` đo riêng cho
   từng theme. Bar là capsule có track/outline mờ, fill accent phát sáng nhẹ và cập nhật 30 fps.
6. `clock-tick.mp3` phát một lần liên tục, không lặp ba bản và không dùng tone tick tổng hợp khi asset
   hợp lệ.
7. Khi fill chạm 0%, chuyển thẳng sang Answer; không có dead frame và không để bar âm/nhảy ngược.
8. Trong toàn phase Question, slot English hiển thị label `ENGLISH WORD` và hint. Mỗi từ lộ Unicode
   grapheme đầu tiên, các grapheme còn lại là `_`; khoảng trắng, hyphen và apostrophe được giữ. Ví dụ
   `Environment → E__________`, `ice cream → i__ c____`. English/IPA thật chỉ xuất hiện tại boundary
   `answer_reveal`.

### 4.3. Answer và ba phase voice

1. English word xuất hiện bằng reveal 240 ms: bay lên 28 px, opacity 0→100% và scale 0→100%; IPA
   giữ fade ngắn để kết quả có điểm nhấn mà không che nội dung.
2. Phát trọn `Happy Win Game.mp3` tại đầu Answer.
3. Sau khi effect kết thúc, giữ gap 450 ms rồi mới bắt đầu voice English đầu tiên. Effect không được
   chồng lên TTS.
4. Khi bắt đầu mỗi phase `LISTEN`, `SLOW` hoặc `YOUR TURN`, hiện label và helper của phase đó ngay
   từ boundary. Giữ nguyên hai text này cho đến boundary bắt đầu phase kế tiếp; riêng `YOUR TURN`
   được giữ đến khi chuyển sang Question của từ kế tiếp hoặc Score. Không ẩn text chỉ vì TTS của
   phase vừa phát xong.
5. Phase `LISTEN`: phát voice tự nhiên; label tiếp tục hiển thị trong 700 ms sau clip.
6. Phase `SLOW`: phát slow voice; label tiếp tục hiển thị trong 900 ms sau clip.
7. Phase `YOUR TURN`: phát lại mẫu tự nhiên như hành vi hiện tại, sau đó giữ 1.200 ms yên lặng để
   người xem nhắc lại. Nếu sau này đổi thành phase hoàn toàn im lặng, đó là thay đổi flow cần xác nhận.
8. Sang từ tiếp theo bằng `question_start`; không chèn thêm effect khác khiến âm thanh dày đặc.

### 4.4. Score

1. Sau từ cuối, cả hai flow hiện nguyên `score.png` và giữ scene tối thiểu 6 giây.
2. Phát `Cartoon Big Win.mp3` sau 120–180 ms để frame score đã ổn định trước transient đầu.
3. Ba score range và CTA phải đọc được trong toàn scene. Không cắt effect 4,340 giây.
4. 1,2 giây cuối giảm motion và audio tail để người xem có khoảng suy ngẫm trước Review.

### 4.5. Review

1. Cả hai flow composite nguyên `review-start-base.png` và hiển thị hai chuỗi động trong 3,2 giây:
   `Let's review the N words you just learned` và `N/N completed`. Cả hai cùng lấy N từ project;
   badge auto-fit cả cụm trong vùng bên phải icon check của từng theme. Score dùng Montserrat
   ExtraBold với dạng gọn `N/N` (cỡ 40–62px trên canvas 1080×1920), label `COMPLETE` dùng
   Montserrat SemiBold (18–23px); vùng badge và cỡ chữ đã tăng để vẫn dễ đọc trên điện thoại.
2. Phát `review_start_bed` 3,2 giây (gain -18 dB, fade out 450 ms), rồi giữ nguyên màn thêm 650 ms
   không nhạc trước Review card đầu; scene này không có TTS.
3. Với mỗi từ, hiện toàn bộ English/IPA/meaning/example trước voice 650 ms.
4. Phát voice của từ, chờ 1.800 ms, rồi phát voice example để người xem kịp đọc toàn bộ card.
5. Sau example, tiếp tục giữ nguyên text 1,6–3,5 giây theo thời gian đọc động.
6. Static flow giữ nguyên `review.png`. Video flow giữ cùng video nền và composite alpha cutout của
   panel trung tâm từ nguyên `review.png`; pixel, label, border và footer của card không bị vẽ lại.
   Nội dung card mới fade/slide nhẹ 180–240 ms; không fade toàn frame (tránh chớp đen) và không thêm
   sound effect ở mọi card để tránh mệt tai.

### 4.6. CTA kết thúc

1. Sau Review cuối, thêm scene `cta` đúng 6,5 giây; visual/audio timeline cùng kết thúc tại cuối CTA.
2. Cả hai flow dùng trực tiếp `cta-new.png`; không dựng lại CTA bằng surface sinh từ code.
3. Scene fade-in 220 ms và phát `cta_bed` (gain -18 dB, fade 180 ms); nhạc được trim đúng 6,5 giây
   scene. CTA không có TTS nên không cần ducking.
4. Nội dung nằm trong safe margin của canvas dọc và không phụ thuộc tên tài khoản.

## 5. Quy tắc mix audio

- Voice là ưu tiên cao nhất; `answer_reveal` phải kết thúc trước voice.
- `question_start` có thể overlap phần intro của question nhưng không được che transient chính của
  `countdown_bed`; dùng envelope/gain thay vì normalize tất cả cue về cùng độ lớn.
- Baseline nghiệm thu: voice khoảng -16 LUFS integrated; effect thấp hơn voice cảm nhận khoảng
  4–8 dB, true peak toàn timeline không vượt -1,5 dBTP.
- Không chỉ peak-normalize cả timeline sau khi cộng track vì sẽ làm một effect lớn kéo voice xuống.
  Mỗi nhóm `voice/effect` cần gain staging trước limiter/master.
- Mọi cue phải có fade in/out ngắn khi trim hoặc pad để không click. Effect phải phát trọn; Hook và
  Review Start bed có fade out 450 ms trước silent hold 650 ms, CTA bed trim theo duration CTA với
  fade 180 ms ở hai đầu.
- Nếu asset thiếu/hỏng trong bản cài đặt, preflight báo `minigame_audio_asset_missing` với hướng dẫn
  cài lại. Tone tổng hợp hiện tại chỉ giữ làm fallback có chủ đích cho compatibility/test, không âm
  thầm thay asset trong production.

## 6. Quy tắc motion và giữ chân người xem

- Remaining-time progress bar là motion chính của Question; Answer dùng pop/reveal vừa phải; Review
  dùng chuyển động nhẹ hơn.
- Các section title (`VIETNAMESE MEANING`, `ENGLISH WORD`, `WORD TYPE` và tương đương ở theme/flow)
  dùng style Label 22–34 px; giá trị Word Type dùng 28–46 px ở cả Answer và Review để còn đọc được
  trên màn hình điện thoại nhỏ.
- Helper dưới ba phase voice (`LISTEN`, `SLOW`, `YOUR TURN`) dùng style đậm 26–42 px thay vì Body
  18–34 px để đọc được trên màn hình điện thoại.
- Thêm progress indicator `i/N` có chuyển trạng thái, nhưng không đổi layout artwork theme hoặc che
  vocabulary.
- Có thể thêm micro-motion nền (zoom/pan 1–2%) trong scene dài để tránh cảm giác ảnh đứng, với điều
  kiện không làm text rung và render vẫn 1080×1920/30 fps.
- Không thêm background music liên tục ở các phần học. `hook_bed`, `review_start_bed` và `cta_bed`
  chỉ phát tại các scene chuyển tiếp không có voice. Track đang chờ xác nhận license/Content ID;
  không phát hành thương mại nếu chưa có chứng từ quyền sử dụng hoặc Claim Clear.
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
  cho tổng số câu. CTA luôn dùng artwork riêng của theme.
- Khi có video, scene Question/Answer và từng Review card có `background_is_video=True`. Overlay
  Question/Answer phải là mask `background-text.png`; overlay Review phải giữ nguyên RGB của panel
  `review.png` và chỉ thay alpha vùng nền ngoài. Không được dùng surface video-theme sinh bằng code,
  recolor hoặc đổi style ASS.
- Mỗi video output luôn có cover 1080×1920 là bản copy đúng Hook frame của theme.
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
