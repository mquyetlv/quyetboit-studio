# Kế hoạch triển khai Quyết Bồ IT Studio

Quy ước: `[ ]` chưa làm, `[-]` đang làm, `[x]` hoàn thành, `[!]` bị chặn.

## Giai đoạn 1 — Nền tảng

- [x] Khởi tạo Git repository và virtual environment.
- [x] Tạo README, dependency, cấu hình package và entrypoint.
- [x] Tách model, persistence, workflow simulation và UI.
- [x] Hoàn thiện test và kiểm tra chất lượng mã (23 test, Ruff sạch).

## Giai đoạn 2 — Application shell

- [x] Cửa sổ 1440×900, minimum 1100×700 và light theme.
- [x] Sidebar toggle với VietSub và Audio Creator.
- [x] Stacked pages, menu File và cảnh báo thay đổi chưa lưu.
- [x] Widget dùng chung, empty state và placeholder Audio Creator.

## Giai đoạn 3 — Workflow VietSub

- [x] Header project, stepper 8 bước, panel chi tiết và log.
- [x] Run All/Run Step/Pause/Resume/Stop/Retry/Reset.
- [x] URL validation và nhận diện Douyin/Xiaohongshu/Bilibili.
- [x] UI tách audio, audio chunks và cấu hình nâng cao.
- [x] UI CapCut/CapCut CN, API dialog và preview SRT.
- [x] UI dịch, context tùy chỉnh và preview đối chiếu.
- [x] UI TTS, giọng đọc và danh sách thoại.
- [x] UI trộn voice/âm gốc và output trung gian.
- [x] Studio riêng: assets, preview, properties, timeline, drag-drop, Undo/Redo.
- [x] UI xuất video và mô phỏng render.

## Giai đoạn 4 — Dữ liệu và an toàn

- [x] ProjectModel và các enum provider/status.
- [x] WorkflowController và MockPipelineService.
- [x] JSON versioned, validation và lọc credential.
- [x] New/Open/Save/Save As, autosave recovery và project assets.

## Giai đoạn 5 — Kiểm thử và nghiệm thu

- [x] Test domain detection và validation.
- [x] Test workflow state machine và Run All.
- [x] Test save/load, schema lỗi và không rò credential.
- [x] Test shell/sidebar/placeholder và form.
- [x] Test Studio Apply/Cancel và persistence.
- [x] Smoke test chạy ứng dụng từ source.

## Kết quả nghiệm thu

- [x] Ứng dụng khởi động từ source/package editable.
- [x] 23 test tự động vượt qua trong môi trường Qt offscreen.
- [x] Ruff kiểm tra toàn bộ `src` và `tests` không còn lỗi.
- [x] Không có logic tải video, gọi API hoặc render media thật.
