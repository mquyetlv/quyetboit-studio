# Kế hoạch nâng cấp nhịp dựng và âm thanh English Mini game

### Trạng thái ngày 2026-08-14

- Hoàn thành triển khai: **29/36 task (80,56%)**.
- Hoàn thành một phần: **2/36 task (5,56%)**.
- Cần quyền sử dụng, A/B hoặc xác nhận bên ngoài: **4/36 task (11,11%)**.
- Chưa triển khai: **1/36 task (2,78%)**.
- Tài liệu đặc tả luồng: `flow-mini-game.md`; baseline vNext đã được hiện thực và test render 5 từ.
- Gate bên ngoài: cần xác nhận quyền sử dụng/phân phối các sound effect trước khi phát hành.

## 1. Kết luận review yêu cầu

Toàn bộ yêu cầu đều hợp lý và cùng giải quyết đúng nguyên nhân video hiện tại bị máy móc: countdown
đổi số cứng theo đúng một giây, các gap chỉ 280–550 ms, reveal chỉ chờ 320 ms trước voice và Score chỉ
hiện 3 giây. Code còn tạo bốn cue bằng sine tone thay vì dùng asset thật.

| Yêu cầu | Đánh giá | Điều chỉnh kỹ thuật đề xuất |
| --- | --- | --- |
| Countdown dạng remaining-time progress bar, dài hơn, dùng `clock-tick.mp3` | Giữ | Bar đầy 100% rồi co liên tục về 0%, mép phải chạy từ phải sang trái trong 3,75 giây; phát asset 3,709 giây một lần |
| Tăng gap giữa từ/voice/flow | Giữ | Dùng timing profile tập trung; baseline 700/900/1.200 ms tùy phase, không dùng một gap chung |
| `check-mark.mp3` khi bắt đầu từ mới | Giữ | Gắn vào semantic event `question_start`, gain thấp hơn voice và tránh transient countdown |
| `Happy Win Game.mp3`, rồi gap trước voice English | Giữ | Phát trọn 1,858 giây + gap 450 ms; voice đầu bắt đầu khoảng 2,308 giây sau reveal |
| `Cartoon Big Win.mp3` và tăng thời gian Score | Giữ | Score 6 giây, cue dài 4,340 giây bắt đầu sau 120–180 ms |
| Tăng gap giữa các voice example | Giữ | 900 ms trước example và hold động 1,6–3,5 giây sau example |
| Viết tài liệu flow tra cứu được | Giữ | `flow-mini-game.md` mô tả scene, timing, asset, code map, update recipe và DoD |

Không có yêu cầu nào bị đề xuất bỏ. Plan giữ tone tổng hợp cũ làm fallback/test thay vì xóa ngay;
việc bỏ fallback hoặc bỏ voice mẫu ở phase `YOUR TURN` phải được người dùng xác nhận trước.

## 2. Bằng chứng hiện trạng

- `MinigameTimelineBuilder` đang dùng `QUESTION_MS=3.000`, `REVEAL_LEAD_MS=320`, gap voice
  `(320, 480)`, `AFTER_WORD_MS=550`, `REVIEW_GAP_MS=280`, `REVIEW_AFTER_MS=500` và `SCORE_MS=3.000`.
- Countdown trong `MinigameSubtitleBuilder` tạo số `3, 2, 1` bằng ba event ASS cắt cứng, mỗi event
  đúng 1.000 ms; animation hiện chỉ là pop/fade.
- Pipeline đang tạo `tick/reveal/open/finish.wav` từ sine filter và đặt chúng trực tiếp vào
  `timeline.audio_events`.
- `MinigameAudioService.assemble()` cộng sample vào một mono timeline rồi scale peak toàn cục; chưa
  có bus voice/effect hoặc ducking theo semantic role.
- `pyproject.toml` hiện chỉ package `assets/theme/**/*.png` và `assets/fonts/*`; thư mục audio chưa
  được khai báo trong package-data.
- Các MP3 yêu cầu đều tồn tại và ffprobe đọc được, nhưng chưa thấy tài liệu license/provenance đi kèm.

## 3. Thiết kế đích

Chi tiết normative nằm trong `flow-mini-game.md`. Kiến trúc đích gồm:

1. `MinigameTimingProfile`: một nguồn timing duy nhất dùng bởi timeline, subtitle, preview và test.
2. `MinigameAudioCueManifest`: mapping semantic cue → package asset → gain/fade/overlap policy.
3. Timeline typed event: scene visual và audio cue dùng chung boundary, không quét scene rồi append
   magic offset trong pipeline.
4. Audio preprocessor normalize/cache từng asset; mixer phân nhóm voice/effect trước master.
5. Countdown progress bar dùng FFmpeg expression hoặc dynamic RGBA layer: fill được neo mép trái,
   chiều rộng giảm tuyến tính theo thời gian để mép phải chạy từ phải sang trái; không dùng ba số
   trượt. Logic nằm trong media service, không đặt trong widget/pipeline.
6. Timing-aware cache version để đổi nhịp không dùng lại timeline/render cũ, nhưng giữ cache TTS.

## 4. Kế hoạch triển khai

### Phase 0 — Khóa quyết định và baseline

- [!] Xác nhận có quyền sử dụng và phân phối bốn MP3 trong sản phẩm/output; lưu provenance/license phù hợp.
- [-] Baseline timing trong `flow-mini-game.md`, đặc biệt countdown 3,75 giây và Score 6 giây, đã hiện thực; còn chờ duyệt A/B.
- [!] Xác nhận phase `YOUR TURN` tiếp tục phát voice mẫu rồi giữ 1,2 giây để người xem lặp lại.
- [x] Chọn ba vocabulary fixture đại diện: từ ngắn, từ dài và example dài có bản dịch Việt.
- [-] Render/đo video vNext 5 từ: duration 80,055 s, integrated -16,76 LUFS và true peak -1,82 dBTP sau AAC; chưa có baseline output cũ để A/B.
- [!] Ghi tiêu chí A/B: dễ đoán hơn, voice không dính nhau, đọc kịp Score/example và không thấy lê thê.

### Phase 1 — Model timing và semantic event

- [x] Tạo `MinigameTimingProfile` trong `core/minigame.py` với các field có tên và validation dương.
- [x] Mở rộng timeline/event model để phân biệt `question_start`, `countdown_bed`, `answer_reveal`,
  `voice_listen`, `voice_slow`, `voice_repeat`, `score_win` và review cue.
- [x] Refactor `MinigameTimelineBuilder` dùng duration audio effect thực tế thay vì giả định cue ngắn.
- [x] Sinh boundary cho `question_intro` và countdown progress từ cùng timing profile; bar nhận đúng
  start/end thay vì ba scene số một giây.
- [x] Tính review hold động theo text/audio, clamp 1,6–3,5 giây và bảo đảm deterministic.
- [x] Thêm timeline/cache version; đổi timing invalidate mix/render cache qua fingerprint, không đổi TTS key.

### Phase 2 — Asset và audio pipeline

- [x] Tạo cue manifest resolve bốn asset từ package, validate tồn tại/đọc được và báo lỗi có hướng xử lý.
- [x] Thêm `assets/audio/**/*` vào package-data và kiểm tra wheel chứa đủ bốn asset có tên space/Unicode.
- [x] Thêm service decode/normalize MP3 sang PCM mono 48 kHz bằng `.partial` + ffprobe + rename atomic.
- [x] Cache normalized cue theo checksum nguồn, processing version, gain/fade config; không cache theo path tuyệt đối.
- [x] Refactor mixer thành voice/effect gain staging, fade và limiter; không để một effect lớn kéo nhỏ toàn bộ voice.
- [x] Giữ sine cue làm fallback rõ ràng cho test/compatibility; production không âm thầm fallback khi asset đóng gói thiếu.

### Phase 3 — Timeline và motion visual

- [x] Gắn `check-mark` đúng đầu mỗi Question; xử lý envelope để không đánh nhau với `clock-tick`.
- [x] Phát `clock-tick` một lần xuyên countdown, không tạo ba tone tick và không cắt đuôi 3,709 giây.
- [x] Implement remaining-time progress bar: fill 100%→0% liên tục ở 30 fps, neo trái, mép phải đi
  từ phải sang trái và nằm trong safe box cả năm theme; bỏ cách hiểu ba số trượt `3 → 2 → 1`.
- [x] Gắn `Happy Win Game` vào Answer; voice English đầu chỉ bắt đầu sau effect + gap đã cấu hình.
- [x] Tăng gap LISTEN→SLOW, SLOW→YOUR TURN, repeat window và đồng bộ label với toàn bộ hold phase.
- [x] Gắn `Cartoon Big Win` vào Score, tăng Score 6 giây và đồng bộ `_visual_scenes()` với audio duration.

### Phase 4 — Review, preview và trải nghiệm giữ chân

- [x] Tăng lead/gap/hold của Review; frame giữ nguyên đủ lâu sau example voice để đọc cả English và Vietnamese.
- [x] Thêm transition fade 200 ms giữa review card mà không chèn effect âm thanh ở mọi card.
- [x] Đồng bộ preview duration/UI summary với timing profile; không tính duration riêng trong widget.
- [x] Thêm progress motion `i/N` tinh tế và kiểm tra không che slot theme; header chuyển 10 px trong 200 ms trong safe slot hiện có.
- [ ] Thử micro-motion nền 1–2% cho scene dài trên bản A/B; chỉ giữ nếu text ổn định và render cost chấp nhận được.
- [x] Không thêm background music ở phase này; ghi backlog riêng sau khi có track licensed và ducking policy.

### Phase 5 — Test, render A/B và tài liệu

- [x] Thêm unit test exact/relative timestamp cho 1 từ, nhiều từ, clip ngắn/dài và example dài.
- [x] Thêm test asset manifest/package-data, cue checksum cache, missing/corrupt asset và fallback policy.
- [x] Thêm test audio overlap: reveal không chồng voice, event không vượt timeline, peak/format hợp lệ.
- [x] Thêm frame regression countdown tại 0/25/50/75/100%, kiểm tra fill giảm đơn điệu, cùng các frame
  Answer, voice phase, Score và Review.
- [x] Chạy integration FFmpeg dọc 1080×1920 với asset thật; ffprobe streams/duration, progress 100%,
  không `.partial`/scene/process mồ côi và output cũ không bị ghi đè khi lỗi.
- [!] Render A/B ba từ, nghe bằng loa + tai nghe, lấy xác nhận người dùng rồi khóa timing trong
  `flow-mini-game.md`, cập nhật README/kế hoạch chính và số test thực tế.

## 5. File dự kiến thay đổi

- `src/quyetboit_studio/core/minigame.py`: timing profile, typed event, timeline builder và version.
- `src/quyetboit_studio/pipeline/minigame.py`: điều phối semantic cue/cache; bỏ magic offset khỏi pipeline.
- `src/quyetboit_studio/services/media/minigame.py`: normalize cue, mix bus, countdown animation và render.
- `src/quyetboit_studio/services/media/minigame_theme.py`: placement/style motion nếu cần theo theme.
- `src/quyetboit_studio/minigame.py`: preview duration và state hiển thị; không chứa xử lý media.
- `pyproject.toml`: package-data audio.
- `tests/test_minigame.py`: unit/regression/integration; có thể tách `tests/test_minigame_audio.py` nếu quá lớn.
- `README.md`, `flow-mini-game.md`, `plan-implement-chi-tiet-viet-sub.md`: hành vi đã nghiệm thu và checklist.

Không sửa/xóa các MP3 nguồn, theme asset hoặc output người dùng. Không đổi TTS provider/cache key chỉ
vì timing/motion thay đổi.

## 6. Đề xuất tăng độ hấp dẫn ngoài yêu cầu gốc

Ưu tiên đưa vào cùng lần nâng cấp:

- Progress `i/N` có animation ngắn để tạo cảm giác tiến bộ.
- Giữ label phase xuyên cả voice và khoảng nghỉ, giúp người xem biết lúc nào nghe/lặp lại.
- Review hold động theo độ dài câu thay vì mọi example có cùng thời lượng.
- Micro-motion nền rất nhẹ ở scene dài, chỉ giữ sau A/B nếu không gây chóng mặt/rung text.
- A/B timing bằng video ba từ trước khi render bộ 20–50 từ để giảm vòng lặp chỉnh sửa.

Đưa vào backlog, không làm ngay:

- Background music liên tục: chỉ hợp lý khi có nhạc licensed và sidechain/ducking; hiện dễ làm rối voice.
- Highlight karaoke theo từ/âm tiết: tăng engagement nhưng cần timestamp TTS cấp từ, phạm vi lớn hơn.
- Random transition/sound theo từng câu: không khuyến nghị vì giảm tính nhất quán và dễ gây mệt tai.
- Cá nhân hóa màn Score theo số đúng thực tế: video offline không biết câu trả lời của từng người xem;
  chỉ khả thi nếu chuyển thành trải nghiệm interactive.

## 7. Tiêu chí nghiệm thu

1. Mỗi câu hỏi bắt đầu bằng `check-mark`; countdown là remaining-time progress bar đầy→rỗng, mép
   phải chạy từ phải sang trái và dùng đúng `clock-tick`.
2. `Happy Win Game` kết thúc, có gap nghe rõ, rồi voice English mới bắt đầu.
3. Ba voice phase và hai từ liên tiếp không tạo cảm giác dính; có đủ repeat window.
4. Score phát trọn `Cartoon Big Win` và giữ đủ lâu để đọc ba range/CTA.
5. Example và bản dịch còn trên màn hình sau voice theo hold động đã khóa.
6. Không clipping/click; voice rõ hơn effect; output có video/audio hợp lệ và duration khớp timeline.
7. Preview duration khớp output; Stop/cancel không để process hoặc file tạm.
8. Wheel/bản cài đặt chứa đủ audio asset; thiếu/hỏng asset cho lỗi rõ ràng, không tạo output sai âm thầm.
9. TTS cache hit khi chỉ đổi timing/theme motion; timeline/render cache invalidate đúng.
10. Người dùng duyệt bản A/B ba từ trước khi đánh dấu 36/36 task hoàn thành.
