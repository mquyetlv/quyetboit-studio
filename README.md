# Quyết Bồ IT Studio

Ứng dụng desktop PySide6 chạy pipeline VietSub thật:

`Douyin → yt-dlp → FFmpeg → CapCut ASR → Gemini → CapCut TTS → render FFmpeg`

Pipeline chạy trong worker thread, hỗ trợ Stop, resume theo manifest/checksum, cache theo cấu hình
và fallback encoder GPU sang `libx264`. Project dùng schema v2 và tự migrate file v1.

Nút **Chạy bước 1–6** chỉ tự động tải video, tạo/dịch subtitle, tạo voice và ghép voice. Ứng dụng
dừng tại bước 7 để người dùng mở Video Studio căn chỉnh; bước 7 và bước 8 được chạy thủ công bằng
nút **Chạy bước này**.

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

## Workspace và resume

Project chưa lưu dùng `%LOCALAPPDATA%/QuyetBoITStudio/workspaces/<project_uuid>`. Artifact của
project đã lưu có thể đặt tại `.qbstudio-work/<project_uuid>` cạnh project. `manifest.json` ghi
checksum, input hash và config hash atomically; artifact sai checksum không được tái sử dụng.

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

## Cấu hình dùng gần nhất

Ứng dụng lưu tự động cấu hình nghiệp vụ gần nhất của tám bước tại
`%LOCALAPPDATA%/QuyetBoITStudio/last-used-settings.json`. Khi tạo **Dự án mới**, các lựa chọn gần
nhất về audio, ASR, Gemini, TTS, mix, Studio và export được nạp lại; khi mở project đã lưu, cấu hình
trong project được giữ nguyên. File cấu hình dùng gần nhất không chứa URL/project data, password,
cookie, Gemini API key, CapCut session, token upload hoặc credential ngắn hạn.

Trong Video Studio, Text cố định phía trên, Logo, Chữ chạy và Subtitle mặc định áp dụng xuyên suốt
video. Có thể tắt **Hiển thị xuyên suốt video** để dùng thời điểm bắt đầu/thời lượng riêng. Mỗi layer
hỗ trợ margin theo pixel, width theo phần trăm video, height theo pixel, màu nền; layer text/subtitle
có thêm màu và cỡ chữ.
