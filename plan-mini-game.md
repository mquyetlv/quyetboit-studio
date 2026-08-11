# Kế hoạch cập nhật menu English Mini game theo bộ theme ảnh

## 1. Mục tiêu và phạm vi

Thay renderer Minigame dạng card dựng bằng `drawbox` hiện tại bằng renderer theo asset theme có sẵn,
hiệu chỉnh đầy đủ cho `theme-1` đến `theme-5`. Video phải dùng đúng bố cục của ảnh mẫu, không tự thay đổi
nội dung người dùng đã nhập, giữ TTS/timeline/cancel/cache/output atomic hiện có và không block Qt main
thread.

Luồng hình ảnh đề xuất:

1. `start-screen.png`: cảnh hook, thêm tiêu đề thử thách và tên topic.
2. `background-video.png`: nền chính của video theo theme đã chọn.
3. `background-text.png`: nguồn RGB để service tự tạo và lưu cache RGBA alpha mask, sau đó overlay lên
   `background-video.png` trong cảnh câu hỏi, đáp án, phát âm và review.
4. `example-start-screen.png` và `example.png`: chỉ dùng làm visual reference/pixel-regression, không
   đưa nguyên chữ mẫu vào output.

Phạm vi đầu tiên là video dọc 9:16. Luồng 16:9 hiện tại chỉ được giữ nếu có quy tắc thiết kế được duyệt;
không crop ngang tự động từ asset dọc vì sẽ cắt khung và phá tọa độ chữ.

## 2. Kết quả phân tích asset

### 2.1. Thuộc tính file

| Asset | Kích thước gốc | Tỷ lệ gần đúng | Alpha | Vai trò |
| --- | ---: | ---: | --- | --- |
| `theme-1` đến `theme-5` — `start-screen.png` | 941×1672 | 9:16 | Không, RGB | Hook/start theo theme |
| `theme-1` đến `theme-5` — `background-video.png` | 941×1672 | 9:16 | Không, RGB | Nền chính theo theme |
| `theme-1` đến `theme-5` — `background-text.png` | 971×1619 | 3:5 | Không, RGB | Nguồn tạo RGBA overlay |
| `example-start-screen.png` | 941×1672 | 9:16 | Không, RGB | Mẫu vị trí title |
| `example.png` | 941×1672 | 9:16 | Không, RGB | Mẫu typography/cảnh answer |

Lưu ý bắt buộc khi implement:

- Chuẩn hóa tên file thành `background-video.png` cho cả năm theme. Khi implement, đổi
  `theme-1/backgound-videos.png` thành `theme-1/background-video.png`; theme registry chỉ giữ alias tên
  cũ trong một chu kỳ migration để project/cache cũ vẫn đọc được.
- Các PNG không có metadata font, layer hoặc text box. Vì vậy không thể suy ra **tên font tuyệt đối**
  chỉ từ raster. Tọa độ/bounding box dưới đây đo được từ pixel; font được ghi là visual match cần
  xác nhận bằng file thiết kế hoặc font gốc.
- Cả năm `background-text.png` đều không có alpha và có vùng matte đen ngoài khung. Renderer không
  overlay RGB trực tiếp mà phải tự tạo RGBA alpha mask theo profile của từng theme.
- Geometry của năm `background-text.png` giống nhau; màu sắc chia hai nhóm: theme 1/4 nền tối và
  theme 2/3/5 nền sáng. Không dùng một ngưỡng chroma/luminance duy nhất cho cả năm theme.

### 2.2. Hệ tọa độ và phép quy đổi

- Tọa độ trong bảng dùng `(x1, y1)–(x2, y2)`, gốc `(0,0)` ở góc trái trên, đơn vị pixel và bao gồm
  pixel cuối.
- `start-screen`/`example`: quy đổi lên 1080×1920 bằng `sx = 1080/941 = 1,147715`,
  `sy = 1920/1672 = 1,148325`.
- `background-text`: quy đổi độc lập lên 1080×1920 bằng `sx = 1080/971 = 1,112255`,
  `sy = 1920/1619 = 1,185917`. Đây là phép stretch có chủ đích để chuẩn hóa canvas; phải render một
  frame kiểm chứng trước khi khóa. Không scale một hệ số rồi crop vì sẽ làm mất viền ngoài.
- Renderer lưu layout ở tọa độ chuẩn 1080×1920 hoặc normalized coordinate; không rải magic number
  theo từng FFmpeg command.

### 2.3. Quy tắc tự tạo alpha mask cho theme 1–5

Không sửa đè PNG nguồn. `MinigameThemeService` tạo một artifact RGBA trong workspace/cache từ
`background-text.png` theo các bước xác định và lặp lại được:

1. Đọc ảnh RGB, kiểm tra đúng `971×1619`, chuyển sang không gian màu có luminance/chroma ổn định.
2. Tìm vùng matte ngoài khung bằng **flood-fill từ bốn cạnh**, chỉ nhận các pixel gần đen và liên thông
   với biên ảnh. Không xóa pixel tối nằm bên trong panel của theme 1/4.
3. Gán alpha `0` cho matte ngoài khung, feather biên 2–3 px để tránh răng cưa/halo.
4. Với vùng nằm trong silhouette, áp dụng `panel_opacity` theo manifest để nhìn thấy
   `background-video.png` phía dưới nhưng vẫn giữ texture gốc.
5. Tạo `detail_mask` từ độ lệch màu/luminance cục bộ; viền cyan/gold/green, glow, separator, lá và hoa
   văn được nâng alpha gần `255`, không bị mờ cùng panel.
6. Premultiply/unpremultiply đúng quy tắc của renderer, xuất PNG RGBA vào `.partial`, validate rồi
   `os.replace` atomically.

Profile mask tối thiểu cho từng theme:

| Theme | Nhóm | Matte ngoài khung | `panel_opacity` khởi điểm | Bảo toàn chi tiết |
| --- | --- | --- | ---: | --- |
| Theme 1 | Tối, xanh biển | Near-black liên thông biên | 0,82 | Viền/glow cyan |
| Theme 2 | Sáng, xanh băng | Near-black liên thông biên | 0,88 | Viền xanh nhạt/texture trắng |
| Theme 3 | Sáng, kem-vàng | Near-black liên thông biên | 0,90 | Viền navy/gold/texture giấy |
| Theme 4 | Tối, xanh-tím | Near-black liên thông biên | 0,82 | Viền/glow cyan-tím |
| Theme 5 | Sáng, xanh lá | Near-black liên thông biên | 0,88 | Viền xanh/lá/texture sáng |

Các giá trị opacity là baseline để render kiểm chứng, sau đó khóa trong manifest. Mask hợp lệ phải có
alpha min `0`, max `255`, vùng matte biên hoàn toàn trong suốt, viền không có halo đen và coverage nằm
trong tolerance đã ghi theo từng theme. Cache key gồm checksum ảnh RGB + mask profile + mask algorithm
version; không tái sử dụng mask khi asset hoặc thuật toán đổi.

### 2.4. Start screen: vị trí đo từ ảnh tham chiếu

Các bounding box chữ thật đo trên `example-start-screen.png`:

| Thành phần | Native 941×1672 | Quy đổi 1080×1920 | Ghi chú |
| --- | ---: | ---: | --- |
| Header `ENGLISH QUICK CHALLENGE` và hai vạch/trang trí | `(160,172)–(780,192)` | `(184,197)–(895,220)` | Đã nằm sẵn trong asset |
| Title dòng 1 `YOUR TITLE` | `(173,458)–(764,556)` | `(199,526)–(877,638)` | Glyph cao 99 px native |
| Title dòng 2 `HERE` | `(320,595)–(627,684)` | `(367,683)–(720,785)` | Glyph cao 90 px native |
| Label `TOPIC` | `(422,915)–(506,936)` | `(484,1051)–(581,1075)` | Đã nằm sẵn trong asset |
| Footer `Listen • Guess • Repeat` + icon | `(172,1185)–(781,1228)` | `(197,1361)–(896,1410)` | Đã nằm sẵn trong asset |

Thông số placement cần khóa cho nội dung động:

- Title safe box native: `(145,400)–(796,760)`, tâm `(471,580)`; tương ứng 1080×1920 là
  `(166,459)–(913,873)`, tâm `(540,666)`.
- Title mặc định trình bày hai dòng để giữ độ lớn: `CAN YOU GET` / `{N} / {N}?`; căn giữa cả hai trục,
  line spacing `0,92em`, không vượt safe box. Nếu bắt buộc đúng một dòng `Can you get 5/5?`, dùng
  auto-fit nhỏ hơn thay vì kéo giãn ngang font.
- Topic box native đo theo khung: xấp xỉ `(171,944)–(756,1099)`; vùng chữ an toàn được **định nghĩa**
  là `(205,972)–(736,1070)`, tâm `(471,1021)`. Trên 1080×1920 vùng an toàn là
  `(235,1116)–(845,1229)`, tâm `(540,1172)`.
- Topic một dòng, căn giữa; ellipsis không được dùng trong video. Nếu vượt chiều rộng, giảm font đến
  ngưỡng tối thiểu rồi wrap tối đa hai dòng và validate không chạm viền.

### 2.5. Ảnh khung chữ: tọa độ khung và vùng đặt chữ an toàn

Các biên dưới đây đo trên canvas native 971×1619. Cột 1080×1920 là vùng sau phép stretch đã nêu trên.

| Vùng | Biên native | Biên 1080×1920 | Nội dung dự kiến |
| --- | ---: | ---: | --- |
| Viền ngoài | `(21,20)–(950,1598)` | `(23,24)–(1057,1895)` | Không đặt chữ chạm vùng này |
| Pill đầu trang | khoảng `(378,87)–(593,155)` | `(420,103)–(660,184)` | `QUESTION/ANSWER i / N` |
| Khung lớn A | `(85,248)–(886,693)` | `(95,294)–(985,822)` | Label + nghĩa tiếng Việt |
| Separator 1 | `(175,759)–(795,760)` | `(195,900)–(884,901)` | Countdown/progress nằm quanh divider |
| Khung B | `(85,820)–(886,1041)` | `(95,973)–(985,1235)` | English word + IPA |
| Khung C | `(85,1062)–(886,1140)` | `(95,1260)–(985,1352)` | Part of speech/meaning pill/phase phụ |
| Separator 2 | quanh `y=1193` | quanh `y=1415` | Phân cách voice stage |
| Khung D | `(85,1242)–(886,1504)` | `(95,1473)–(985,1784)` | `LISTEN/SLOW/YOUR TURN` hoặc example |
| Footer safe area | `(250,1515)–(721,1575)` | `(278,1797)–(802,1868)` | Topic/category |

Mỗi vùng nội dung phải inset tối thiểu 32 px native ở trái/phải và 24 px native ở trên/dưới. Text
layout phải đo glyph thật trước khi ghi ASS; mọi dòng vượt safe box phải auto-fit/wrap có giới hạn,
không phụ thuộc ước lượng số ký tự.

### 2.6. Typography đo từ `example.png`

| Thành phần mẫu | Bounding box glyph native | Cỡ glyph đo được | Font/size triển khai ban đầu |
| --- | ---: | ---: | --- |
| `ANSWER 1 / 5` | `(348,138)–(548,170)` | 33 px | Montserrat SemiBold 40 px, tracking 3 px |
| `VIETNAMESE MEANING` | `(306,297)–(629,311)` | 15 px | Montserrat SemiBold 22 px, tracking 4 px |
| `Bác sĩ` | `(357,450)–(596,518)` | 69 px | Montserrat ExtraBold 88 px |
| `REVEALING ANSWER IN` | `(343,730)–(593,744)` | 15 px | Montserrat SemiBold 22 px, tracking 3 px |
| `ENGLISH WORD` | `(331,877)–(594,892)` | 16 px | Montserrat SemiBold 22 px, tracking 4 px |
| `Doctor` | `(271,932)–(677,1030)` | 99 px | Montserrat Medium/SemiBold 126 px |
| `/ˈdɒk.tər/` | `(368,1073)–(569,1109)` | 37 px | Montserrat Regular 46 px |
| Part of speech `noun` | khoảng `(408,1139)–(532,1184)` gồm pill | khoảng 30 px glyph | Montserrat Italic 34 px |
| Voice heading `1 • LISTEN` | text quanh `(385,1369)–(541,1389)` | 21 px | Montserrat Bold 30 px |
| Voice helper | `(303,1409)–(629,1431)` | 23 px | Montserrat Regular 28 px |

Start title có dáng condensed display; best visual match là **Bebas Neue Regular/Bold, 120 px native**.
Các chữ còn lại best match là **Montserrat**. Hai font này chỉ là baseline hiệu chỉnh; muốn tuyên bố
pixel-perfect phải có font/source thiết kế. Font được chọn cuối cùng phải đóng gói kèm app (kèm license
phù hợp) và ASS phải tham chiếu font ổn định, không phụ thuộc máy người dùng có cài font.

Màu baseline lấy từ ảnh mẫu:

- Main text: trắng `#F6F8FF`.
- Secondary/cyan text: khoảng `#70D8FF`.
- Accent cyan: khoảng `#43E6F4`.
- Shadow/outline: xanh đen `#07152B`, outline 2–4 px tùy cỡ chữ.
- Title start có gradient trắng → cyan trong ảnh mẫu. ASS thuần không hỗ trợ gradient glyph tốt;
  implement bằng RGBA text layer/Pillow-compatible renderer hoặc chấp nhận màu trắng-cyan đồng nhất
  sau khi được duyệt, không giả lập bằng nhiều lớp ASS lệch nhau.

## 3. Thiết kế kỹ thuật cần triển khai

### 3.1. Model và theme registry

- [ ] Thêm `theme_id` vào `MinigameProject`, nhận `theme-1`…`theme-5`, mặc định `theme-1`, có migration
  an toàn từ schema hiện tại.
- [ ] Tạo model dùng chung `MinigameThemeManifest`, `CanvasSpec`, `TextSlot`, `TextStyle` trong `core/`;
  widget chỉ chọn theme và hiển thị state.
- [ ] Tạo service `services/media/minigame_theme.py` để discovery/validate đủ năm theme, resolve alias
  tên file cũ, kiểm tra kích thước/mode/checksum và trả manifest typed.
- [ ] Thêm `AlphaMaskProfile` vào manifest: matte threshold, flood-fill connectivity, feather radius,
  panel opacity, detail threshold và mask algorithm version cho từng theme.
- [ ] Đưa toàn bộ coordinate, style, font min/max, wrap policy và scene mapping vào manifest/config;
  không nhúng logic theme vào widget hoặc `pipeline/minigame.py`.
- [ ] Thêm preflight báo rõ asset/font thiếu hoặc sai kích thước; lỗi phải có hướng xử lý.

### 3.2. UI menu Minigame

- [ ] Thêm card **Chọn giao diện (Theme)** với năm option `Theme 1`…`Theme 5`; mỗi option có thumbnail
  lấy từ `start-screen.png`, tên, bảng màu và trạng thái tỷ lệ hỗ trợ.
- [ ] Chọn theme phải cập nhật ngay preview, `theme_id` trong project và asset manifest; không làm mất
  Topic, vocabulary, voice hoặc output setting đang nhập.
- [ ] Thay preview text mô phỏng hiện tại bằng preview frame thật của start screen; render debounced ở
  worker để nhập Topic/số từ không block UI.
- [ ] Preview có safe-box debug bật trong development/test, hiển thị đúng title và topic sau auto-fit.
- [ ] Nếu bộ theme chỉ hỗ trợ 9:16, disable 16:9/Both kèm giải thích; không cho chạy rồi mới báo lỗi.
- [ ] Không còn bắt buộc chọn `background_video` khi dùng image theme; giữ field cũ để migration hoặc
  chế độ legacy, không xóa dữ liệu project cũ.

### 3.3. Scene/timeline

- [ ] Hook dùng `start-screen.png` trong `HOOK_MS`, title động `CAN YOU GET {N}/{N}?` và Topic.
- [ ] Question composite `background-video.png` + RGBA mask sinh từ `background-text.png`: pill
  `QUESTION i/N`, khung A hiện nghĩa Việt, separator hiện countdown 3→2→1; chưa lộ English/IPA.
- [ ] Answer dùng cùng composite: pill `ANSWER i/N`, khung A giữ nghĩa Việt, khung B hiện English + IPA,
  khung C hiện phase/loại từ nếu có dữ liệu; không tự đoán part of speech khi model chưa có field.
- [ ] Voice scene đồng bộ `LISTEN → SLOW → YOUR TURN` trong khung D với audio event hiện tại.
- [ ] Review dùng khung A/B/D cho English, IPA, nghĩa Việt và example; wrap example theo pixel width.
- [ ] Score/CTA dùng `background-video.png` và style đúng theme; score range vẫn
  tính động theo `score_ranges(total)`.
- [ ] Giữ cue âm thanh, trim silence, loudness, cache TTS và timeline động hiện có; thay layout không
  được gọi lại TTS nếu text/voice không đổi.

### 3.4. Render media

- [ ] Mở rộng `MinigameVideoRenderer` nhận danh sách scene background/still image thay vì một video nền
  duy nhất; loop ảnh đúng duration bằng FFmpeg input/image sequence an toàn.
- [ ] Thêm `MinigameAlphaMaskService` độc lập UI, dùng Pillow + NumPy để flood-fill matte, feather,
  detail mask và sinh RGBA cho theme 1–5; không đặt xử lý pixel trong widget hoặc pipeline.
- [ ] Composite theo thứ tự `background-video → RGBA background-text → text/animation`; dùng đúng
  alpha mode, không dùng blend mode để che lỗi mask.
- [ ] Tạo từng visual scene vào `.partial`, validate resolution/duration rồi mới ghép; output cuối vẫn
  ffprobe và rename atomic.
- [ ] Dùng ASS cho text phẳng/animation đơn giản; dùng pre-render RGBA layer cho gradient title hoặc
  typography cần pixel-match, sau đó overlay bằng argument list/filter script đã escape.
- [ ] Đo text bằng cùng font engine với renderer, hỗ trợ auto-fit, max 2 dòng, line spacing và tracking;
  có fallback rõ ràng cho Vietnamese/IPA glyph nhưng không đổi font âm thầm.
- [ ] Cache visual frame theo theme checksum + canvas + text + style + layout version; thay crop/format
  không làm mất cache TTS.
- [ ] Giữ progress FFmpeg thật, drain stdout/stderr, cancellation watcher và GPU→CPU fallback hiện có.
- [ ] Xóa file `.partial`/temporary font layer trong `finally` khi cancel/fail nhưng không xóa output cũ.

### 3.5. Font và packaging

- [ ] Xác nhận font gốc từ Canva/Figma/PSD hoặc nhận file `.ttf/.otf`; nếu không có, duyệt baseline
  Bebas Neue + Montserrat bằng ảnh render so sánh cạnh reference.
- [ ] Kiểm tra license, thêm font redistributable vào assets/package manifest và test font được tìm thấy
  khi chạy từ bản build, không chỉ từ source tree.
- [ ] Thêm font version/checksum vào visual cache key để đổi font invalidate đúng frame và downstream.
- [ ] Thêm `Pillow` vào dependency chính thức để xử lý RGBA/mask độc lập Qt; khóa version tương thích
  Python của repo và cập nhật README/packaging.

## 4. File dự kiến thay đổi

- `src/quyetboit_studio/core/minigame.py`: schema/theme/layout model và validation.
- `src/quyetboit_studio/services/media/minigame_theme.py`: discovery, manifest, font/text measurement.
- `src/quyetboit_studio/services/media/minigame_mask.py`: flood-fill, feather, detail mask và RGBA cache.
- `src/quyetboit_studio/services/media/minigame.py`: scene ASS/RGBA builder và renderer ảnh theme.
- `src/quyetboit_studio/pipeline/minigame.py`: điều phối asset/scene/cache; không chứa FFmpeg/theme detail.
- `src/quyetboit_studio/minigame.py`: bộ chọn theme, preview thật và state tỷ lệ.
- `src/quyetboit_studio/assets/theme/theme-1/manifest.json` đến `theme-5/manifest.json`: canvas, slot,
  style, mask profile và scene mapping.
- `src/quyetboit_studio/assets/theme/theme-1/background-video.png`: tên chuẩn sau khi đổi từ
  `backgound-videos.png`; không giữ hai bản asset trùng lặp.
- `src/quyetboit_studio/assets/fonts/`: font đã duyệt và license, nếu được phép phân phối.
- `requirements.txt`/packaging và `README.md`: dependency Pillow, quy ước asset/theme.
- `tests/test_minigame.py`: model/migration/layout/renderer regression.
- Có thể thêm `tests/fixtures/minigame/` chứa frame reference nhỏ hoặc hash/mask đã duyệt; không dùng
  snapshot chứa credential/user data.

## 5. Kiểm thử và nghiệm thu

### Unit/regression

- [ ] Validate discovery đủ ba asset cho từng theme 1–5 và migration alias filename cũ.
- [ ] Test flood-fill chỉ xóa matte liên thông với biên, không xóa mảng tối bên trong theme 1/4.
- [ ] Test theme 2/3/5 không mất texture sáng, viền mảnh hoặc họa tiết sau detail-mask/feather.
- [ ] Test alpha min/max, coverage, feather và checksum/cache key cho từng mask profile.
- [ ] Test phép quy đổi native→1080×1920 cho mọi slot, sai số tối đa 1 px.
- [ ] Test title `CAN YOU GET 5/5?`, topic ngắn/dài, tiếng Việt có dấu và IPA không tràn safe box.
- [ ] Test question không lộ đáp án trước reveal; phase voice đúng scene/audio timestamp.
- [ ] Test project cũ không có `theme_id` vẫn mở được và không mất `background_video`.
- [ ] Test UI hiển thị đủ năm theme, chọn theme cập nhật preview/project nhưng giữ nguyên dữ liệu nhập.
- [ ] Test cache visual invalidate khi theme/font/layout đổi nhưng TTS cache vẫn hit.
- [ ] Test không dùng font hệ thống ngoài fallback được khai báo.

### Integration render thật

- [ ] Render frame start 1080×1920 với đúng title/topic, so bbox với spec sai số tối đa 2 px sau scale.
- [ ] Render một composite 1080×1920 cho từng theme 1–5; nền video nhìn thấy qua panel theo opacity,
  matte ngoài khung trong suốt, không có viền đen/halo.
- [ ] Render question, answer, voice và review bằng từ ngắn/dài; kiểm tra bằng image mask/pixel diff với
  tolerance cho encoder/antialiasing, không chỉ so file tồn tại.
- [ ] Render video ít nhất 5 từ qua toàn timeline; progress đạt 100%, ffprobe có video/audio 1080×1920,
  30 fps, duration khớp audio và phát được.
- [ ] Cancel giữa render không để FFmpeg/QThread/file `.partial` mồ côi.
- [ ] Xác nhận GPU lỗi fallback CPU đúng một lần.

Chạy bộ kiểm tra repo sau thay đổi:

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src
git diff --check
```

## 6. Definition of Done

- [ ] Preview UI và frame thật dùng cùng manifest/layout, không có hai bộ tọa độ khác nhau.
- [ ] UI chọn được đủ Theme 1–5; theme đã chọn được lưu/mở lại đúng và output dùng đúng ba asset.
- [ ] Alpha mask của cả năm theme được sinh/cached từ RGB source, không sửa đè asset gốc và không có
  matte đen hoặc halo sau composite.
- [ ] Title/topic trên start screen đúng safe box và font đã duyệt ở 1080×1920.
- [ ] Question/answer/IPA/example/voice phase nằm đúng khung, không tràn với bộ fixture biên.
- [ ] Video 5 từ chạy thật qua hook→question→answer→voice→review→score→CTA, audio/text đồng bộ.
- [ ] Output được ghi `.partial`, ffprobe hợp lệ rồi rename atomic; Stop không để process/file mồ côi.
- [ ] Không làm giảm test hiện có và không phá project Minigame cũ.

## 7. Thông tin cần xác nhận trước khi code

1. `Can you get 5/5?` phải là một dòng đúng hoa/thường, hay được phép trình bày hai dòng
   `CAN YOU GET` / `5 / 5?` giống bố cục `YOUR TITLE / HERE`?
2. Bộ Theme 1–5 chỉ xuất 9:16, hay vẫn phải hỗ trợ 16:9/Both? Nếu cần 16:9, cần asset/layout ngang riêng.
3. Có file thiết kế nguồn hoặc font `.ttf/.otf` không? Nếu không, baseline sẽ là Bebas Neue cho title
   và Montserrat cho nội dung, sau đó duyệt bằng render comparison.
