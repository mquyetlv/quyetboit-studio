# Quyết Bồ IT Studio

Ứng dụng desktop PySide6 chạy pipeline VietSub thật:

`Douyin → yt-dlp → FFmpeg → CapCut ASR → Gemini → CapCut TTS → render FFmpeg`

Pipeline chạy trong worker thread, hỗ trợ Stop, resume theo manifest/checksum, cache theo cấu hình
và fallback encoder GPU sang `libx264`. Project dùng schema v2 và tự migrate file v1.

Nút **Chạy tất cả các bước** tự động chạy liên tục toàn bộ pipeline từ tải video đến áp dụng cấu
hình Layer Studio và xuất file đầu ra. Người dùng vẫn có thể mở Layer Studio trước khi chạy để căn
chỉnh cấu hình, hoặc chạy lại riêng từng bước bằng nút **Chạy bước này**.

## Yêu cầu

- Python 3.11 trở lên.
- `ffmpeg` và `ffprobe` có trong `PATH`.
- Chrome hoặc Edge đã đăng nhập Douyin nếu video yêu cầu cookie.
- Tài khoản CapCut và `GEMINI_API_KEY` để chạy ASR, dịch và TTS.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
$env:PYTHONPATH="src"
$env:GEMINI_API_KEY="..."
python -m quyetboit_studio
```

Trong bước SRT, mở **Cấu hình API** để đăng nhập CapCut. Password không được lưu. Cookie phiên
chỉ được lưu trong Windows Credential Manager. UID/workspace được xác thực lại và cập nhật vào
Credential Manager trước khi upload. Gemini API key nhập trên UI cũng được lưu trong Credential
Manager, không ghi vào project, autosave, workspace hay log.

Ở bước tải Douyin, nút **Cookie / profile trình duyệt** nhận cả tên hiển thị như
`YTB - Cát Cát Review`; ứng dụng tự ánh xạ sang thư mục thật như `Profile 6`. Nếu Chrome/Edge
khóa cookie database, có thể chọn file Netscape `cookies.txt` hoặc JSON export của extension.
JSON được lọc chỉ giữ domain Douyin, chuyển sang Netscape trong file tạm và xóa ngay sau mỗi lần
gọi yt-dlp. Chỉ đường dẫn file gốc được giữ trong Credential Manager để lần sau tự nạp lại; nội
dung cookie không được chép vào project, autosave hoặc workspace.

Tại **Bước 1 — Nhập video**, ứng dụng có ba chế độ:

- **Tải từng tập**: xử lý một link Douyin như trước đây.
- **Tải Theo Series**: dán link một tập thuộc Series/合集 hoặc link `/collection|mix/<id>`, tải danh sách rồi chọn các tập cần xử lý. Douyin dùng API web private nên thao tác này có thể yêu cầu cookie còn hạn.
- **Tải nhiều Videos**: dán ít nhất hai link, mỗi dòng một link. Ứng dụng tải theo thứ tự đã nhập, dùng FFmpeg chuẩn hóa hình/âm thanh rồi nối thành một video nguồn trước khi sang bước tách audio.

Danh sách link và chế độ nguồn được lưu trong project. Project cũ chỉ có `source_url` tiếp tục mở ở chế độ **Tải từng tập**.

## Workspace và resume

Project chưa lưu dùng `%LOCALAPPDATA%/QuyetBoITStudio/workspaces/<project_uuid>`. Artifact của
project đã lưu có thể đặt tại `.qbstudio-work/<project_uuid>` cạnh project. `manifest.json` ghi
checksum, input hash và config hash atomically; artifact sai checksum không được tái sử dụng.

Nếu Bước 4 hoặc Bước 5 vẫn còn item lỗi sau retry tự động, ứng dụng giữ các batch/cue đã hoàn
thành, hiển thị số lượng thành công/thất bại và đổi nút hành động thành **Thử lại phần lỗi**. Lần
chạy tiếp theo chỉ gửi lại cue còn thiếu; checkpoint này vẫn dùng được sau khi mở lại project.

CapCut là private reverse-engineered API và có thể đổi endpoint/schema/chữ ký. Live test không
chạy mặc định; chỉ nên chạy với tài khoản test riêng.

## Kiểm thử

```powershell
$env:PYTHONPATH="src"
pytest -q
ruff check src tests
```

Test mặc định không gọi Douyin, Gemini hay CapCut thật. Suite kiểm tra migration, redaction,
SRT/dedupe, cache/checksum, silence boundary, adaptive limiter, chữ ký CapCut và worker UI.

Khi Gemini trả lỗi, nhật ký hiển thị chẩn đoán đã redact gồm HTTP status, reason/message của provider,
quota metric/ID, `Retry-After`, lần retry và mức concurrency trước/sau; API key, prompt và nội dung
subtitle không được ghi vào log.

## Cấu hình dùng gần nhất

Ứng dụng lưu tự động cấu hình nghiệp vụ gần nhất của tám bước tại
`%LOCALAPPDATA%/QuyetBoITStudio/last-used-settings.json`. Khi tạo **Dự án mới**, các lựa chọn gần
nhất về audio, ASR, Gemini, TTS, mix, Studio và export được nạp lại; khi mở project đã lưu, cấu hình
trong project được giữ nguyên. File cấu hình dùng gần nhất không chứa URL/project data, password,
cookie, Gemini API key, CapCut session, token upload hoặc credential ngắn hạn.

Trong Layer Studio, Video và Voice không có thuộc tính layer riêng. Subtitle và Text cố định hiển thị
ở Top/Bottom trên thanh nền toàn chiều rộng, cao bằng 1,5 lần cỡ chữ và căn giữa; Logo giữ tỷ lệ ảnh,
đặt tại một trong bốn góc bằng margin X/Y và kích thước chiều cao. Chữ chạy hiển thị xuyên suốt video,
di chuyển chéo và nảy tại bốn cạnh; hỗ trợ màu, opacity, tốc độ và chữ đậm. Cấu hình layer được lưu
trong project và cấu hình dùng gần nhất; layer đang tắt giữ nguyên các giá trị đã nhập.
