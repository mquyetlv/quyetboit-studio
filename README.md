# Quyết Bồ IT Studio

Ứng dụng desktop PySide6 chạy pipeline VietSub thật:

`Douyin → yt-dlp → FFmpeg → CapCut ASR → Gemini → CapCut TTS → render FFmpeg`

Pipeline chạy trong worker thread, hỗ trợ Stop, resume theo manifest/checksum, cache theo cấu hình
và fallback encoder GPU sang `libx264`. Project dùng schema v2 và tự migrate file v1.

Menu **Tiếng Anh → Mini game** tạo video đoán từ từ dữ liệu người dùng nhập. Tính năng hỗ trợ
YouTube 16:9, TikTok/Shorts 9:16 hoặc xuất đồng thời cả hai; video nền được crop theo điểm focus và
luôn tắt audio gốc. Mỗi từ bắt buộc có nghĩa tiếng Việt, từ tiếng Anh, IPA và câu ví dụ. Timeline
gồm hook, countdown có sound cue, ba lượt phát âm được trim khoảng lặng và đồng bộ nhãn
`LISTEN → SLOW → YOUR TURN`, chấm điểm theo level rồi ôn tập có câu ví dụ. Màn score dùng
`score.png` của theme và đã chứa lời kêu gọi comment. Layout dùng
card tối, typography Unicode rõ ràng và tách riêng English, IPA, nghĩa tiếng Việt cùng example;
audio timeline được chuẩn hóa loudness trước khi render.

Nút **Chạy tất cả các bước** tự động chạy liên tục toàn bộ pipeline từ tải video đến áp dụng cấu
hình Layer Studio và xuất file đầu ra. Người dùng vẫn có thể mở Layer Studio trước khi chạy để căn
chỉnh cấu hình, hoặc chạy lại riêng từng bước bằng nút **Chạy bước này**.

## Yêu cầu

- Python 3.11 trở lên.
- `ffmpeg` và `ffprobe` có trong `PATH`.
- Chrome hoặc Edge đã đăng nhập Douyin nếu video yêu cầu cookie.
- Tài khoản CapCut và `GEMINI_API_KEY` để chạy ASR, dịch và CapCut TTS.
- Google Cloud Text-to-Speech service-account JSON nếu chọn Google TTS cho Mini game.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
$env:PYTHONPATH="src"
$env:GEMINI_API_KEY="..."
python -m quyetboit_studio
```

Service-account Google chỉ được đọc trực tiếp từ file người dùng chọn trong phiên chạy. Ứng dụng
không copy nội dung hoặc đường dẫn credential này vào project/workspace và không ghi private key,
access token hay nội dung JSON vào log.

## English Mini game

1. Chọn `16:9`, `9:16` hoặc **Xuất cả hai**.
2. Chọn video nền và chỉnh điểm crop X/Y riêng cho từng tỷ lệ.
3. Nhập/dán JSON nội dung theo schema bên dưới hoặc mở file `.json`; UI có nút định dạng và sao chép
   JSON mẫu.
4. Bấm **Cấu hình Voice…**, chọn Google Cloud TTS hoặc CapCut TTS rồi lọc theo ngôn ngữ, loại
   voice, danh mục, giới tính hoặc tên. Dialog tải catalog ngoài main thread và hỗ trợ nghe thử.
   Với CapCut, đăng nhập ở phần VietSub trước. Google tự nhận `services-account.json` tại thư mục
   chạy ứng dụng; cũng có thể chọn file khác ngay trong dialog.
5. Chọn thư mục rồi bấm **Tạo video**. TTS được cache theo provider, voice, text và volume; đổi crop
   hoặc xuất thêm tỷ lệ không gọi lại TTS.

Output luôn được render vào file `.partial`, kiểm tra bằng ffprobe rồi mới đổi tên. Nếu tên file đã
tồn tại, ứng dụng tăng suffix thay vì ghi đè output cũ.

Mini game theme hiện xuất 9:16 và dùng asset đóng gói trong ứng dụng. Montserrat và Bebas Neue cùng
license OFL được kèm trong `assets/fonts/`; renderer theme truyền thư mục này cho FFmpeg/libass nên không
phụ thuộc font hệ thống để vẽ chữ tiếng Việt/IPA và title.
Hook dùng `start.png` của theme. Tên Topic được tự co chữ, xuống tối đa ba dòng và căn giữa theo cả hai
chiều trong panel Topic riêng của từng theme. Khi render xong, ứng dụng xuất thêm ảnh bìa 1080×1920
`<tên-video>_cover.png` cạnh video; ảnh này chính là frame mở đầu đã điền Topic. Hỗ trợ 16:9/Both sẽ
được bổ sung sau khi có asset và layout ngang riêng.

JSON nội dung dùng `schemaVersion: 1`, chứa `topic` và từ 1–50 phần tử trong `words`:

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

Tên chuẩn là `wordType`; parser cũng nhận `workType` và chuẩn hóa lại khi bấm **Định dạng JSON**.
`level` bắt buộc nhận một trong ba giá trị `Dễ`, `Trung bình`, `Khó`. Ba nhóm này lần lượt xác định
dải điểm Beginner, Good và Perfect; dải chỉ có một mốc được ghi dạng `3/5 correct`.
Không đặt credential, API key hoặc đường dẫn service-account trong JSON nội dung. Schema máy đọc nằm
tại `minigame-content.schema.json`.

Sau vòng đoán từ, video hiển thị màn score theo theme, tiếp theo là màn mở đầu review rồi dùng nền
review riêng cho từng từ. Review hiển thị Vietnamese, English Word, IPA, Word Type, Example Sentence và bản dịch ví dụ
nếu được cung cấp; câu English và bản dịch dùng hai vùng riêng để giữ cỡ chữ dễ đọc. Tọa độ score được đo
riêng theo từng theme, còn Word Type/voice ở answer dùng các hàng độc lập để không chồng chữ. Registry tương thích cả tên `review-start-screen.png`, alias
`start-review-screen.png` của Theme 2 và tên asset hiện có `rewview.png`.

Màu chữ được định nghĩa riêng cho từng theme và từng surface. Theme 1/4 dùng foreground sáng trên nền
tối; Theme 2/3/5 dùng foreground đậm trên nền sáng. Review có palette riêng vì Theme 4 là nền tối còn
bốn theme kia là panel sáng. Hook, nội dung ASS, outline và shadow đều lấy từ cùng typed palette; cache
visual tự invalidate khi palette/layout version thay đổi.

Catalog Google lấy từ `TextToSpeechClient.list_voices()` và giữ language codes, SSML gender, sample
rate cùng nhóm Chirp 3 HD/Chirp HD/Studio/Neural2/WaveNet/Standard và các loại mới provider trả về.
Catalog CapCut gọi toàn bộ category cấu hình trong `services/capcut/config.py`, merge voice trùng
theo speaker ID rồi giữ tất cả category để lọc. File service-account và các biến thể tên phổ biến
được `.gitignore`; không được stage/commit.

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
