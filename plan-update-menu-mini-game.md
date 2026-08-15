# Kế hoạch cập nhật giao diện và CTA cho menu English Mini game

## Trạng thái triển khai — 2026-08-15

- Hoàn thành và đã kiểm chứng: **30/30 task (100%)**.
- Đã triển khai một phần: **0/30 task (0%)**.
- Chờ người dùng nghiệm thu visual: **0/30 task (0%)**.
- Chưa triển khai: **0/30 task (0%)**.
- Phạm vi: năm theme dọc 9:16 của menu `Tiếng Anh → Mini game`.
- Nguồn flow: `flow-mini-game.md`.
- Renderer, asset production và output video đã được cập nhật. Bằng chứng: 193 test mặc định pass;
  bộ opt-in render lại sau khi nối digit atlas N=5/N=10 trên đủ năm theme pass 10/10 trong
  611,92 giây; Ruff pass. Frame Hook,
  Question, Review Start và CTA đã xem ở 1080×1920 và bản mobile tại
  `.../minigame-update-v3/`; ma trận annotated phủ Hook N=1/5/10/50 và Question ngắn/dài.

## 1. Baseline đã phát hiện trước triển khai (đã được khắc phục)

Baseline trước thay đổi là `Hook → Question/Answer/Voice cho từng từ → Score → Review Start → Review`
từng từ, chưa có CTA sau Review cuối. Sau triển khai, flow chuẩn là
`Hook → Question/Answer/Voice × N → Score → Review Start → Review × N → CTA`. 
`MinigameTimelineBuilder` vẫn là nguồn timing; pipeline ghép frame theo scene, còn
`MinigameSubtitleBuilder` đặt text động bằng slot của theme.

Các nguyên nhân đã được xử lý:

1. `start.png` của cả năm theme chứa sẵn artwork `CAN YOU GET 5 OUT OF 5?`; nay pipeline dùng
   `start-base.png` và digit atlas RGBA 0–9 để dựng số theo dữ liệu.
2. `review-start-screen.png`/`start-review-screen.png` chứa sẵn cả `Let’s review the 5 words...` và
   badge `5/5 completed`; nay pipeline dùng clean base và text layer động cho cả hai chuỗi.
3. Nghĩa tiếng Việt trước đây dùng chung style `Hero` 88/36; nay dùng `question_meaning` 104/44.
4. Question trước đây chưa có section `ENGLISH WORD`; nay có placeholder grapheme-aware trước reveal.
5. Timeline trước đây kết thúc ngay sau Review cuối; nay có CTA 4,5 giây, asset/slot/style/timing và
   transition riêng.

Hai chuỗi có số trên Review Start phải cùng lấy từ `len(project.vocabulary)`. Nếu chỉ đổi badge mà
giữ câu `Let’s review the 5 words...`, màn hình vẫn sai với bộ 10 câu.

## 2. Đặc tả thiết kế đích

### 2.1. Hook và ảnh cover động theo số câu

- Nội dung hiển thị: `CAN YOU GET {N} OUT OF {N}?`, trong đó `N = len(project.vocabulary)` và miền
  hợp lệ hiện tại là 1–50.
- Không che số `5` cũ bằng một rectangle gần đúng. Tạo clean base cho từng theme, giữ nguyên toàn bộ
  artwork ngoài vùng challenge, rồi dựng hai giá trị `N` bằng layer động.
- Vì số hiện tại là chữ raster 3D/texture riêng của từng theme, không thay bằng Montserrat/Bebas Neue
  phẳng. Mỗi theme cần bộ glyph số 0–9 hoặc source style tương đương đã được duyệt, gồm fill,
  outline, shadow/extrusion và tracking đúng với artwork gốc.
- Mỗi theme có hai safe box đo riêng cho `{N}` trước và sau `OUT OF`; dùng metric thật của glyph để
  căn theo tâm thị giác. Với số hai chữ số, co đồng đều trong giới hạn tối thiểu; không kéo méo riêng
  chiều ngang và không chạm chữ `GET`, `OUT`, `OF` hoặc dấu `?`.
- Hook frame sau composite là nguồn duy nhất cho cả đoạn mở đầu và `_cover.png`; cover không render
  theo nhánh riêng.

### 2.2. Tăng cỡ chữ nghĩa tiếng Việt ở Question

- Tạo style semantic riêng `question_meaning`, không tăng toàn bộ style `Hero`.
- Baseline để render A/B: `max_size=104`, `min_size=44` (hiện tại 88/36), bold Montserrat như cũ.
  Text ngắn phải lớn rõ rệt; text dài vẫn auto-fit trong slot và tối đa hai dòng.
- Đo lại safe box nghĩa Việt trên cả năm `background-video.png`; chỉ tách bounds theo theme nếu
  artwork thực sự khác. Không để chữ chạm header, progress bar hoặc section English.

### 2.3. Placeholder English trước Answer

- Trong toàn phase Question, hiển thị label `ENGLISH WORD` và placeholder tại đúng slot sẽ dùng cho
  đáp án ở phase Answer.
- Hàm tạo placeholder xử lý theo Unicode grapheme, không dùng byte length. Mỗi chữ cái/chữ số hiển
  thị thành một `_`; giữ nguyên khoảng trắng, dấu nối và apostrophe để người xem nhận ra cấu trúc từ.
  Ví dụ: `Doctor → ______`, `ice cream → ___ _____`, `mother-in-law → ______-__-___`.
- Số underscore phải bằng số grapheme cần đoán; khoảng trắng/dấu phân cách không được tính thành
  underscore. Placeholder dùng cùng family, palette, outline và căn giữa của `english_word`, nhưng
  có tracking riêng để các gạch không dính nhau và vẫn auto-fit với đáp án dài.
- Khi chuyển sang Answer, placeholder biến mất đúng boundary `answer_reveal`; không có frame lộ đáp
  án sớm hoặc frame cả placeholder và đáp án chồng nhau.

### 2.4. Review Start động theo tổng số câu

- Dựng động đồng thời hai chuỗi: `Let’s review the {N} words you just learned` và
  `{N}/{N} completed`.
- Tạo clean base riêng cho từng theme, giữ icon check, pill, divider và các card; phần text động được
  đặt bằng hai slot đo riêng trên canvas 1080×1920.
- Badge phải căn theo cụm đầy đủ `{N}/{N} completed`, không căn riêng chữ số vào vị trí của số `5`
  cũ. Font, màu, outline/glow và weight lấy từ style Review của từng theme.
- Test ít nhất `N=1, 5, 10, 50`; cụm hai chữ số không đè icon check hoặc tràn pill.

### 2.5. CTA kết thúc video

Thêm scene `cta` sau Review cuối, baseline **4.500 ms**. Nội dung không phụ thuộc tên tài khoản để
không hardcode dữ liệu chưa có trong project:

- Eyebrow: `GREAT WORK!`
- Headline động: `YOU LEARNED {N} NEW WORDS`
- Primary CTA: `FOLLOW FOR MORE ENGLISH MINI GAMES`
- Secondary CTA: `SAVE • REVIEW • SHARE`

Chuyển từ Review cuối bằng fade 220 ms; headline pop nhẹ 220–260 ms, primary CTA vào sau 300 ms và
giữ ổn định ít nhất 3 giây. Chưa thêm voice hoặc sound effect mới trong phase này; chỉ tái sử dụng
audio tail nếu không chồng voice và không vi phạm license.

| Theme | Hướng thiết kế CTA | Màu/hiệu ứng chính | Bố cục |
| --- | --- | --- | --- |
| Theme 1 — Beach | Frosted-glass card trên biển, giữ palm/wave/star accent | Xanh biển, cyan, vàng | Headline giữa card; nút CTA xanh đậm; ba action ở footer |
| Theme 2 — Clinic | Clean medical card, plus/grid rất nhẹ | Trắng, xanh dương, cyan | Card thoáng; check icon phía trên; CTA dạng pill xanh |
| Theme 3 — Scrapbook | Giấy xé, tape/stamp và texture thủ công | Navy, đỏ gạch, vàng giấy | Headline dạng cutout; CTA như nhãn dán; action như con dấu |
| Theme 4 — Cyberpunk | HUD panel với neon frame, không dùng glow làm mờ chữ | Cyan, magenta, electric blue | Headline lớn; nút neon; action theo hàng terminal/HUD |
| Theme 5 — Garden | Glass/cream card, lá và ánh nắng nhẹ | Xanh lá, cream, vàng | Headline giữa; pill xanh lá; action ở thanh footer |

Mỗi CTA base phải giữ safe margin tối thiểu 80 px trên canvas 1080×1920. Contrast text đạt mức dễ
đọc trên frame tĩnh; không đặt nội dung quan trọng sát vùng UI overlay của TikTok/Reels.

## 3. Kế hoạch triển khai

### Phase 1 — Khóa asset và layout từng theme

- [x] 1. Đo và lưu bounds 1080×1920 cho hai số Hook, câu Review, badge Review và bốn vùng CTA của
  từng theme; kèm ảnh annotated làm bằng chứng review.
- [x] 2. Kiểm tra source font/layer gốc của artwork; nếu không có, tạo và duyệt digit atlas 0–9 riêng
  cho từng theme, có metric advance/baseline và style giống số `5` hiện tại.
- [x] 3. Tạo `start-base.png` sạch cho năm theme, loại bỏ riêng hai số cố định mà không phá texture,
  glow, shadow hoặc chữ tĩnh xung quanh.
- [x] 4. Tạo `review-start-base.png` sạch cho năm theme, loại bỏ cả câu chứa `5 words` và badge
  `5/5 completed`, giữ nguyên icon/pill/divider/card.
- [x] 5. Thiết kế `cta-base.png` cho năm theme theo bảng ở mục 2.5 và duyệt visual ở kích thước
  1080×1920 trước khi nối timeline.
- [x] 6. Bổ sung asset mới vào package-data, giữ asset cũ để migration/fallback có chủ đích và không
  sửa trực tiếp file nguồn hiện tại.
- [x] 7. Mở rộng `MinigameThemeManifest` với các base asset, digit style/metric, slot và style semantic
  mới; validation phải báo đúng theme/asset bị thiếu hoặc sai kích thước.
- [x] 8. Tăng `layout_version` và checksum/fingerprint để mọi frame cache cũ bị invalidate sau khi
  đổi asset/layout.

### Phase 2 — Text layer động và placeholder

- [x] 9. Refactor `MinigameTextLayerService` thành các builder có trách nhiệm rõ: Hook, Review Start
  và CTA; mọi output RGBA vẫn ghi `.partial`, validate rồi `os.replace`.
- [x] 10. Dùng `len(project.vocabulary)` làm nguồn duy nhất cho Hook, Review Start và CTA; không còn
  literal `5` trong text/visual logic production.
- [x] 11. Render `{N}` ở hai slot Hook bằng glyph/style của đúng theme; hỗ trợ 1–50 và căn tâm theo
  metric, không scale méo.
- [x] 12. Render động cả câu `Let’s review...` và badge `{N}/{N} completed`; auto-fit theo slot riêng
  của từng theme.
- [x] 13. Tạo helper thuần `answer_placeholder(english)` theo policy grapheme/space/separator ở mục
  2.3; đặt helper ở model/media text layer để unit test không cần chạy FFmpeg.
- [x] 14. Thêm `question_meaning` style/placement với baseline 104/44, giữ font bundled, palette và
  outline hiện tại; không ảnh hưởng Answer/Review.
- [x] 15. Thêm `question_english_label` và `question_english_placeholder` placement; dùng slot English
  của Answer nhưng style/tracking riêng để phân biệt trạng thái chưa reveal.

### Phase 3 — Timeline, composite, cover và CTA

- [x] 16. Trong `_build_theme()`, vẽ nghĩa Việt bằng style mới và vẽ `ENGLISH WORD` + placeholder chỉ
  trong phase Question; Answer tiếp tục vẽ từ thật.
- [x] 17. Composite Hook từ `start-base + challenge layer + topic layer`; dùng chính frame này cho
  scene Hook và `_cover.png`.
- [x] 18. Composite Review Start từ `review-start-base + dynamic review layer`; không dùng nguyên
  ảnh có số cố định.
- [x] 19. Thêm `cta_ms=4_500` vào `MinigameTimingProfile`, validation/version và sinh `MinigameScene`
  loại `cta` ngay sau Review cuối.
- [x] 20. Composite CTA từ `cta-base + dynamic text layer`, thêm vào `_visual_scenes()` và bảo đảm
  tổng duration visual bằng `timeline.duration_ms`.
- [x] 21. Cập nhật cache key timeline/render: đổi N, theme, asset, font hoặc CTA layout phải invalidate
  frame/render; cache TTS vẫn hit khi chỉ đổi visual.
- [x] 22. Giữ cancel/progress hiện tại: CTA không tạo process riêng; FFmpeg vẫn drain output, kết thúc
  100% và không để `.partial` hoặc process mồ côi.

### Phase 4 — Regression, render thật và tài liệu

- [x] 23. Unit test Hook cho cả năm theme với N=1/5/10/50: đúng hai giá trị, nằm trong bounds, style
  asset đúng theme và không còn pixel/text số `5` baked ở clean base.
- [x] 24. Unit test cover byte-for-byte bằng Hook frame động và xác nhận cover N=10 không thể tái dùng
  cache của N=5.
- [x] 25. Unit test Review Start N=1/5/10/50: cả câu và badge cùng đúng N, text nằm trong slot, không
  đè icon/pill.
- [x] 26. Unit test placeholder với ASCII, phrase có space, hyphen, apostrophe và Unicode grapheme;
  kiểm tra underscore count và boundary Question→Answer.
- [x] 27. Frame regression năm theme cho nghĩa Việt ngắn/dài, placeholder ngắn/dài và CTA; xác nhận
  font size Question mới lớn hơn baseline nhưng không overflow.
- [x] 28. Integration render thật ít nhất hai bộ N=5 và N=10 trên cả năm theme; ffprobe xác nhận video/
  audio hợp lệ, duration có thêm 4,5 giây CTA, progress 100% và không còn file/process mồ côi.
- [x] 29. Xem frame Hook/cover, Question, Review Start và CTA ở 100% cùng bản thu nhỏ mobile; ghi nhận
  A/B hoặc approval cho font số, vị trí từng theme và độ đọc CTA.
- [x] 30. Cập nhật `flow-mini-game.md`, `README.md`, kế hoạch chính và số liệu checkbox thực tế;
  giữ riêng nhánh đang chờ nghiệm thu visual, không đánh dấu hoàn thành thay người dùng.

### Bằng chứng nghiệm thu

- Mục 1/2/11/23: bounds được lưu tách theo theme trong typed manifest. Năm atlas RGBA 0–9 có typed
  grid/alpha/spacing/padding metrics, validator đủ 10 cell và checksum cache. Renderer crop alpha,
  ghép baseline rồi scale đồng đều; ma trận annotated N=1/5/10/50 xác nhận hai slot không overflow.
- Mục 5/29: năm CTA base đã tạo, text đã căn lại theo button/footer riêng từng theme và đã xem ở
  100% lẫn bản mobile; người dùng đã duyệt visual ngày 2026-08-15.
- Mục 27: regression tự động phủ font-fit ngắn/dài trên năm theme; contact sheet dùng libass/FFmpeg
  thật xác nhận nghĩa Việt, label và placeholder ngắn/dài nằm trong safe box. CTA mobile cũng nằm
  trong bốn bounds đã khai báo.

## 4. File dự kiến thay đổi khi triển khai

- `src/quyetboit_studio/core/minigame.py`: manifest field mới, placeholder helper nếu đặt ở core,
  `cta_ms`, CTA scene và timeline version.
- `src/quyetboit_studio/services/media/minigame_theme.py`: bounds/style/placement riêng từng theme,
  asset discovery và validation.
- `src/quyetboit_studio/services/media/minigame_text.py`: challenge count, Review Start và CTA RGBA
  layer; cache key gồm N/font/layout.
- `src/quyetboit_studio/services/media/minigame.py`: Question meaning lớn hơn, English placeholder,
  CTA/render transition.
- `src/quyetboit_studio/pipeline/minigame.py`: build composite động, cover và nối CTA vào visual scene.
- `src/quyetboit_studio/assets/theme/theme-{1..5}/`: clean base, digit/style asset và CTA base.
- `tests/test_minigame.py`: unit, frame regression và integration FFmpeg; có thể tách test visual nếu
  file tiếp tục lớn.
- `pyproject.toml`, `flow-mini-game.md`, `README.md`, `plan-implement-chi-tiet-viet-sub.md`: package,
  đặc tả flow và trạng thái nghiệm thu.

## 5. Tiêu chí nghiệm thu cuối

1. Với mọi N từ 1–50, Hook và cover hiển thị đúng `N` ở cả hai vị trí, đúng style theme, không còn
   số `5` cũ hoặc vết che/mask.
2. Nghĩa Việt ở Question lớn hơn rõ rệt so với baseline; từ dài vẫn không tràn panel trên cả năm theme.
3. Trước reveal, người xem thấy đúng số underscore theo từ; sau reveal chỉ còn đáp án thật.
4. Review Start dùng đúng N ở cả câu mô tả lẫn badge; N=10/50 không đè icon hoặc ra ngoài pill.
5. CTA xuất hiện sau Review cuối đủ 4,5 giây, đúng artwork từng theme, có headline động và hai CTA dễ đọc.
6. Hook trong video và cover là cùng một frame composite; đổi số câu làm invalid cache đúng.
7. Timeline audio/visual khớp, output ffprobe hợp lệ, progress đạt 100%, không clipping do thay đổi
   timeline và không có `.partial`/FFmpeg process mồ côi.
8. Test mặc định không gọi Google/CapCut thật; credential và output người dùng không bị ghi log,
   stage, sửa hoặc ghi đè.
