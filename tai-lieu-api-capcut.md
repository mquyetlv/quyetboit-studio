# Tài liệu đăng nhập và toàn bộ API CapCut đang dùng trong project

> Phạm vi: tài liệu này mô tả **toàn bộ request CapCut/ByteDance được mã nguồn hiện tại của project gọi hoặc đã cài đặt sẵn**, không phải toàn bộ API công khai của CapCut.
>
> Nguồn chuẩn khi đối chiếu: `capcut/login.py`, `capcut/client.py`, `capcut/cloud_upload.py`, `capcut/session.py` và `ui/workers.py`. Các endpoint này là API web nội bộ được reverse-engineer, không phải SDK/API công khai ổn định. CapCut có thể thay đổi host, hằng số, schema, chữ ký hoặc yêu cầu captcha bất kỳ lúc nào.

## 1. Có thể làm gì với phần CapCut của project

Project đang dùng CapCut cho hai luồng chính:

1. **Text-to-Speech (TTS)**
   - Đăng nhập bằng email/password.
   - Lấy danh mục giọng.
   - Tổng hợp audio từ text.
   - Tải audio từ URL CDN trả về.

2. **Automatic Speech Recognition (ASR) / tạo SRT**
   - Lấy `uid` và `workspace_id`.
   - Xin thông tin upload và STS credentials tạm thời.
   - Upload video/audio lên cloud storage theo multipart.
   - Đăng ký cloud asset.
   - Submit nhận diện phụ đề và poll kết quả.
   - Chuyển `utterances` thành block SRT.

Luồng tổng quát:

```text
email/password
   │
   ├─ GET trang login để seed cookie
   ├─ POST resolve region
   └─ POST email login ────────────────> cookie phiên (sessionid/sid_tt/...)
                                               │
                 ┌─────────────────────────────┴──────────────────────────┐
                 │                                                        │
                 │ TTS                                                    │ ASR/SRT
                 │                                                        │
                 ├─ POST lấy workspace                                    ├─ GET account info → uid
                 ├─ POST lấy voice catalog                                ├─ POST lấy workspace
                 ├─ POST multi_platform → audio URL                       ├─ POST prepare upload → STS
                 └─ GET audio CDN → bytes                                 ├─ GET ApplyUploadInner (SigV4)
                                                                          ├─ POST TOS init/transfer/finish
                                                                          ├─ POST CommitUploadInner (SigV4)
                                                                          ├─ POST create cloud asset
                                                                          ├─ POST caption submit
                                                                          └─ POST caption query → utterances
```

## 2. Quick start khi dùng lại ngay từ project này

### 2.1 TTS

```python
from pathlib import Path

from capcut.login import login
from capcut.client import CapCutClient

cookies = login("email@example.com", "password", debug=False)

client = CapCutClient()
client.connect(cookies)

voices = client.fetch_voices()
voice = next(v for v in voices if v["category"] == "vietnamese")

audio = client.generate_tts(
    "Xin chào, đây là nội dung cần đọc.",
    voice,
    rate=1.0,
)
Path("output.mp3").write_bytes(audio)
```

### 2.2 Tạo SRT từ video/audio

```python
from pathlib import Path

from capcut.login import login
from capcut.client import CapCutClient
from srt_translator.parser import blocks_to_srt_text

cookies = login("email@example.com", "password", debug=False)

client = CapCutClient()
client.connect(cookies)

if not client.workspace_id:
    raise RuntimeError("Tài khoản không trả về workspace_id")

blocks = client.generate_caption("input.mp4", "vi-VN")
Path("output.srt").write_text(
    blocks_to_srt_text(blocks),
    encoding="utf-8-sig",
)
```

Phụ thuộc tối thiểu cho lớp HTTP là `requests`. Riêng `generate_caption()` hiện import `SrtBlock` từ `srt_translator.parser`; khi tách sang project khác cần copy module này hoặc đổi `_utterances_to_blocks()` để trả dict/dataclass của project mới.

## 3. Hằng số và host

| Tên | Giá trị hiện tại | Dùng cho |
|---|---|---|
| `APPID` | `348188` | Login và edit API |
| `EDIT_API_URL` | `https://edit-api-sg.capcut.com` | Workspace, voice, TTS, asset, caption |
| `WEB_URL` | `https://www.capcut.com` | Seed cookie, account info |
| `LOGIN_HOST` | `https://login-row.www.capcut.com` | Resolve region/login quốc tế |
| `FALLBACK_LOGIN_HOST` | `https://login.us.capcut.com` | Host login dự phòng |
| `LOGIN_SDK_VERSION` | `2.1.10-tiktok` | Passport API |
| `APPVR_AIGC` | `8.4.0` | TTS/caption và nhóm intelligence |
| `APPVR_WORKSPACE` | `5.8.0` | Workspace/asset |
| `PF` | `7` | Chữ ký edit API |
| `SIGN_VER` | `1` | Chữ ký edit API |
| `TDID` | chuỗi rỗng | Chữ ký edit API |
| `STORE_CC` | `vn` | Header khu vực |
| `STORE_CC_SRC` | `uid` | Header khu vực |
| `PART_SIZE` | `3 * 1024 * 1024` | Chunk upload TOS, 3 MiB |
| `VOD_REGION` | `ap-singapore-1` | AWS SigV4 cho VOD |
| `VOD_SERVICE` | `vod` | AWS SigV4 cho VOD |
| `VOD_VERSION` | `2020-11-19` | Apply/Commit upload |

Các giá trị `sg`, `VN`, `vn` và `ap-singapore-1` đang hardcode theo môi trường/tài khoản đã reverse-engineer. Khi dùng ở khu vực khác, không nên giả định các giá trị này luôn đúng; ưu tiên lấy host upload động từ response `prepare_upload_cloud` và kiểm tra request web mới nếu API trả lỗi vùng.

User-Agent hiện giả lập Chrome 140 trên Windows. Đây cũng là hằng số có thể phải cập nhật nếu passport bắt đầu kiểm tra chặt.

## 4. Danh mục toàn bộ endpoint/request

| # | Method | Endpoint | Auth/ký | Trạng thái trong code |
|---:|---|---|---|---|
| 1 | GET | `https://www.capcut.com/en-us/login` | Không | Đang dùng, seed cookie |
| 2 | POST | `{login-row}/passport/web/region/` | Header passport | Đang dùng |
| 3 | POST | `{resolved-host}/passport/web/email/login/` | CSRF + form obfuscation | Đang dùng |
| 4 | POST | `{resolved-host}/passport/web/user/login/` | Như #3 | Fallback khi #3 trả 404/405 |
| 5 | GET | `https://www.capcut.com/passport/web/account/info/` | Cookie | Đang dùng cho upload |
| 6 | POST | `{EDIT_API}/cc/v1/workspace/get_user_workspaces` | Cookie + `sign`, appvr `5.8.0` | Đang dùng |
| 7 | POST | `{EDIT_API}/artist/v1/effect/get_resources_by_category_id` | Cookie, không `sign` | Đang dùng |
| 8 | POST | `{EDIT_API}/storyboard/v1/tts/multi_platform` | Cookie + `sign`, appvr `8.4.0` | TTS chính |
| 9 | POST | `{EDIT_API}/lv/v2/intelligence/tts/mget_audition` | Cookie + `sign`, appvr `8.4.0` | Đã cài đặt nhưng không dùng trong luồng chính |
| 10 | GET | URL CDN audio động | URL đã cấp; cookie session vẫn đi cùng `requests.Session` | Đang dùng |
| 11 | POST | `{EDIT_API}/lv/v1/asset/prepare_upload_cloud` | Cookie + `sign` + `Lan: en` | Đang dùng |
| 12 | GET | `https://{upload_domain}/?Action=ApplyUploadInner...` | STS + AWS SigV4 | Đang dùng |
| 13 | POST | `https://{UploadHost}/upload/v1/{StoreUri}` | Upload `Auth` + `X-Storage-U` | Ba phase init/transfer/finish |
| 14 | POST | `https://{upload_domain}/?Action=CommitUploadInner...` | STS + AWS SigV4 | Đang dùng |
| 15 | POST | `{EDIT_API}/lv/v1/asset/create_cloud_asset` | Cookie + `sign` + `Lan: en` | Đang dùng |
| 16 | POST | `{EDIT_API}/lv/v1/caption/submit` | Cookie + `sign`, appvr `8.4.0` | Đang dùng |
| 17 | POST | `{EDIT_API}/lv/v1/caption/query` | Cookie + `sign`, appvr `8.4.0` | Đang dùng, polling |

`/lv/v2/intelligence/create` và `/query` chỉ còn được nhắc trong docstring cũ của `client.py`; implementation hiện tại **không gọi** hai endpoint đó nên không được xem là API đang sử dụng.

## 5. Đăng nhập email/password

### 5.1 Bản chất xác thực

CapCut Web dùng passport của ByteDance. Project không lấy OAuth access token. Kết quả đăng nhập là một cookie jar có các cookie HttpOnly như:

```text
sessionid, sessionid_ss, sid_tt, sid_guard, uid_tt, ...
```

Chỉ cần có ít nhất một cookie auth được chấp nhận thì `login()` coi là thành công. Tất cả request edit API sau đó dùng cùng cookie jar.

### 5.2 Helper mã hóa và ID

#### Obfuscate email/password

Email và password không gửi plain text trong form. Mỗi byte UTF-8 được XOR với `5`, rồi encode hex hai ký tự:

```python
def xor5_hex(value: str) -> str:
    return "".join(f"{b ^ 5:02x}" for b in value.encode("utf-8"))
```

Đây chỉ là obfuscation phía client, **không phải mã hóa bảo mật**. HTTPS mới là lớp bảo vệ đường truyền.

#### Hash để resolve region

```python
EMAIL_HASH_SALT = "aDy0TUhtql92P7hScCs97YWMT-jub2q9"

hashed_id = sha256(
    email.strip().lower() + EMAIL_HASH_SALT
).hexdigest().lower()
```

#### `verifyFp`, `did`, CSRF

- `verifyFp`: `verify_<base36(now_ms)[-8:]>_<rand8>_<rand4>_<rand4>_<rand4>_<rand12>`.
- `did`: ghép Unix milliseconds với chữ số ngẫu nhiên, cắt còn 19 ký tự.
- `passport_csrf_token`: `secrets.token_hex(16)`.
- Đặt cùng token vào cả `passport_csrf_token` và `passport_csrf_token_default` trên domain `.capcut.com`.

### 5.3 Bước 1 — seed cookie

```http
GET https://www.capcut.com/en-us/login
Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8
Accept-Language: en-US
User-Agent: <USER_AGENT>
```

Mục đích là nhận các cookie ban đầu như `_tea_web_id`, `ttwid` và cookie chống CSRF. Trong code, lỗi request này bị bỏ qua để vẫn thử login tiếp.

### 5.4 Bước 2 — resolve login region

```http
POST https://login-row.www.capcut.com/passport/web/region/
Content-Type: application/x-www-form-urlencoded
appid: 348188
did: <device_id>
Origin: https://www.capcut.com
Referer: https://www.capcut.com/
store-country-code: vn
store-country-code-src: cdn
x-tt-passport-csrf-token:
```

Query:

```text
aid=348188
account_sdk_source=web
sdk_version=2.1.10-tiktok
language=en-US
verifyFp=<verify_fp>
mix_mode=1
```

Form body:

```text
type=2&hashed_id=<sha256_normalized_email_plus_salt>
```

Code tìm host trong các field có thể có:

```text
data.login_domain
data.domain
data.redirect_url
data.redirect_domain
data.region_domain
```

Nếu không resolve được, code tiếp tục với `login-row.www.capcut.com`, rồi `login.us.capcut.com`.

### 5.5 Bước 3 — email login

Endpoint chính:

```http
POST {resolved_host}/passport/web/email/login/
```

Nếu HTTP status là `404` hoặc `405`, fallback:

```http
POST {resolved_host}/passport/web/user/login/
```

Query giống resolve region nhưng không có `mix_mode`:

```text
aid=348188
account_sdk_source=web
sdk_version=2.1.10-tiktok
language=en-US
verifyFp=<verify_fp>
```

Headers đáng chú ý:

```http
Content-Type: application/x-www-form-urlencoded
appid: 348188
did: <device_id>
Origin: https://www.capcut.com
Referer: https://www.capcut.com/en-us/login
store-country-code: vn
store-country-code-src: uid
x-tt-passport-csrf-token: <csrf_token>
```

Form body:

```text
email=<xor5_hex(email)>
password=<xor5_hex(password)>
mix_mode=1
fixed_mix_mode=1
```

Thành công thực tế được xác định bằng cookie jar có một trong:

```text
sessionid, sid_tt, sessionid_ss, sid_guard, uid_tt
```

Các mã hiện được xem là captcha/verify:

```text
1105, 1101, 2046, 2047, 2048
```

Nếu bị captcha, luồng HTTP thuần không xử lý thử thách. Cần đăng nhập/xác minh trong trình duyệt thật hoặc xây một fallback browser/WebView rồi export cookie.

### 5.6 Không có refresh token

Project không cài refresh-cookie/token. Cookie được lưu và dùng lại đến khi server từ chối. Khi hết hạn, cách phục hồi hiện tại là đăng nhập lại.

Không log email, password, cookie value, `Authorization`, STS access key/secret hoặc session token. `debug=True` hiện log response login rút gọn; trong môi trường production nên dùng `debug=False` và lọc dữ liệu nhạy cảm khỏi log.

## 6. Lưu và khôi phục session

`capcut/session.py` lưu file `capcut_session.json`:

```json
{
  "cookies": {
    "sessionid": "<SECRET>",
    "sid_tt": "<SECRET>"
  },
  "workspace_id": "<workspace-id>"
}
```

Cookie trong file này tương đương quyền truy cập phiên đăng nhập. File đã được ignore bởi Git nhưng vẫn cần:

- Giới hạn quyền đọc của user chạy app.
- Không đính kèm vào log, issue, artifact hoặc backup công khai.
- Không commit file HAR vì HAR có thể chứa cookie, request body và token tạm.
- Xóa file khi user “Ngắt kết nối”.

Khi nạp cookie vào client, code đặt cookie trên domain `.capcut.com`, lấy `did` từ `_tea_web_id` hoặc `s_v_web_id`; nếu thiếu thì sinh `did` tạm. `session_id` lấy theo thứ tự `sessionid`, `sid_tt`, `sessionid_ss`.

Có một bất nhất nhỏ trong code hiện tại: `session.has_auth_cookie()` còn chấp nhận `odin_tt`, nhưng `CapCutClient.apply_cookies()` không dùng `odin_tt` để gán `session_id`. Vì vậy một file chỉ còn `odin_tt` có thể được lớp persistence xem là “có session” trong khi `client.is_ready` vẫn là `False`. Khi port nên thống nhất một tập cookie auth và luôn xác thực với server.

### Lưu ý về validation hiện tại

Có hai hành vi dễ gây hiểu nhầm:

1. Nếu truyền `workspace_id` đã lưu vào `client.connect(cookies, workspace_id)`, client không gọi API để xác thực session.
2. `CapCutClient.validate()` gọi `_fetch_workspace()`, nhưng `_fetch_workspace()` tự bắt mọi exception; vì vậy `validate()` có thể trả `True` dù request workspace thất bại.
3. `SessionRestoreWorker` dùng voice cache nếu có, nên cũng có thể báo đã restore trước khi một API cần auth thực sự được gọi.

Ở project mới, nên validate rõ ràng bằng `GET /passport/web/account/info/` hoặc workspace API, gọi `raise_for_status()`, kiểm tra mã nghiệp vụ/`user_id`, và không nuốt exception.

## 7. Cookie và chữ ký của edit API

### 7.1 Ba lớp auth khác nhau

| Lớp | Dùng ở đâu | Cách tạo |
|---|---|---|
| Cookie phiên | `www.capcut.com`, `edit-api-sg.capcut.com` | Kết quả login |
| Header `sign` | Phần lớn edit API | MD5 từ path/version/time |
| AWS SigV4 bằng STS | `*.bytevcloudapi.com` | Credentials tạm từ `prepare_upload_cloud` |
| Upload `Authorization` | `UploadHost/upload/v1/...` | Token `Auth` từ `ApplyUploadInner` |

Không được dùng lẫn bốn cơ chế này.

### 7.2 Header chung của edit API

```python
{
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "appid": "348188",
    "did": did,
    "store-country-code": "vn",
    "store-country-code-src": "uid",
}
```

### 7.3 Thuật toán `sign`

```python
import hashlib
import time

def make_sign(path: str, appvr: str, now: int | None = None) -> dict[str, str]:
    now = int(time.time()) if now is None else now
    pf = "7"
    tdid = ""
    message = f"9e2c|{path[-7:]}|{pf}|{appvr}|{now}|{tdid}|11ac"
    sign = hashlib.md5(message.encode("utf-8")).hexdigest()
    return {
        "sign": sign,
        "device-time": str(now),
        "tdid": tdid,
        "sign-ver": "1",
        "pf": pf,
        "appvr": appvr,
    }
```

Quy tắc quan trọng:

- `path` chỉ là pathname, ví dụ `/lv/v1/caption/query`.
- Chỉ lấy **7 ký tự cuối** của pathname.
- Query string và body không tham gia chữ ký.
- `device-time` phải đúng Unix time theo giây và phải đúng với time dùng để tính MD5.
- Endpoint voice catalog hiện gọi với `signed=False`.
- Asset upload dùng `appvr=5.8.0`; TTS/caption dùng `appvr=8.4.0`.

## 8. API tài khoản và workspace

### 8.1 Account info → `uid`

```http
GET https://www.capcut.com/passport/web/account/info/
```

Query:

```text
aid=348188
account_sdk_source=web
sdk_version=2.1.10-tiktok
language=en
```

Headers: cookie jar, `Accept: application/json, text/javascript`, `appid`, `did`, store-country headers và `Referer: https://www.capcut.com/`.

Lấy:

```python
uid = str(data.get("user_id_str") or data.get("user_id") or "")
```

`uid` được cache trong `CapCutClient._uid` và dùng làm `X-Storage-U` khi upload bytes lên TOS.

### 8.2 Workspace

```http
POST https://edit-api-sg.capcut.com/cc/v1/workspace/get_user_workspaces
```

Auth: cookie + signed headers, `appvr=5.8.0`.

Body:

```json
{
  "cursor": "0",
  "count": 100,
  "need_convert_workspace": true
}
```

Code chấp nhận danh sách ở một trong:

```text
data.workspace_list
data.workspaces
data.workspace_infos
```

Ưu tiên workspace có `role == "owner"`, lấy `workspace_id` hoặc `id`; nếu không có owner thì lấy phần tử đầu.

## 9. API voice catalog

```http
POST https://edit-api-sg.capcut.com/artist/v1/effect/get_resources_by_category_id?<ARTIST_QS>
```

Endpoint này dùng cookie nhưng hiện **không có header `sign`**.

Query cố định:

```text
aid=348188
version_name=19.3.0
version_code=11.0.0
sdk_version=19.3.0
effect_sdk_version=19.3.0
device_platform=web
region=VN
language=en
device_type=web
channel=online
```

Project gọi endpoint một lần cho mỗi `category_key`:

```text
vietnamese, english, trend, TikTok, narration,
female_voice, male_voice, anime, indonesian, zhongwen, taiyu
```

Body:

```json
{
  "panel": "tone",
  "category_id": null,
  "category_key": "vietnamese",
  "panel_source": "heycan",
  "pack_optional": {
    "need_tag": true,
    "need_thumb": true,
    "thumb_opt": "{\"is_support_webp\":1}",
    "image_pack_param": {
      "icon_limit": {
        "static_format": "webp",
        "dynamic_format": "awebp",
        "width": 100,
        "height": 100
      }
    }
  },
  "offset": 0,
  "count": 500
}
```

Danh sách nằm ở `data.effect_item_list`.

### Parse một voice

```text
common_attr.title                       → name
common_attr.effect_id                   → effect_id
common_attr.third_resource_id_str       → resource_id
common_attr.extra                       → JSON string
extra.tonetype                          → JSON string lồng bên trong
tonetype.voice_type hoặc tts_voice      → speaker
tonetype.platform                       → platform nguồn
```

Map platform nội bộ của project:

```text
11labs → 2
volc/bytedance/sami → 1
không biết → 1
```

Voice chuẩn hóa có schema:

```json
{
  "name": "<display name>",
  "speaker": "<voice identifier>",
  "platform": 1,
  "effect_id": "<effect id>",
  "resource_id": "<resource id>",
  "lang": "vi",
  "category": "vietnamese",
  "gender": "Nữ"
}
```

Project loại trùng theo `speaker`, cache danh sách vào `capcut_voices_cache.json`. Cache không có TTL/version nên project tích hợp mới nên bổ sung thời hạn hoặc nút refresh.

## 10. API TTS

### 10.1 TTS chính — `multi_platform`

```http
POST https://edit-api-sg.capcut.com/storyboard/v1/tts/multi_platform
```

Auth: cookie + signed headers, `appvr=8.4.0`.

Body:

```json
{
  "texts": ["Nội dung cần đọc"],
  "tts_conf": {
    "speaker": "<voice.speaker>",
    "rate": 1.0,
    "volume": 100,
    "name": "<voice.name>",
    "platform": "sami",
    "effect_id": "<voice.effect_id>",
    "resource_id": "<voice.resource_id>",
    "is_clone": false
  },
  "need_url": true
}
```

Project dò URL audio khá mềm để chịu nhiều response schema. Trường đã được xử lý gồm:

```text
data.tts_materials[]
data.auditions[]
data.audition_list[]
data.tts_list[]
data.list[]
data.speakers[]
data.results[]
```

Trong mỗi item, dò trực tiếp `audio_url`, `url`, `play_url`, `tts_url`, hoặc dò tiếp trong `audio_info`, `meta_data`, `audio`, `tts_material`.

Schema chính kỳ vọng:

```text
data.tts_materials[0].meta_data.url
```

Sau đó `GET <audio-url>` và trả bytes.

### 10.2 Text dài

`generate_tts()` cắt text thành chunk tối đa 800 ký tự, ưu tiên cắt tại `.`, `!`, `?`, newline, `,`, hoặc space sau mốc 60% chunk. Mỗi chunk tạo một audio URL rồi tải về. Kết quả hiện được nối bằng `b"".join(parts)`.

Nối byte trực tiếp thường dùng được với MP3 nhưng không phải cách ghép container tổng quát. Nếu cần file chuẩn/seek ổn định, hãy concat/remux các đoạn bằng ffmpeg.

### 10.3 Tham số tốc độ

Client vẫn gửi `tts_conf.rate`, làm tròn hai chữ số. Tuy nhiên phần pipeline `sub_videos/dubbing.py` ghi nhận backend CapCut có thể bỏ qua tham số này và chủ động time-stretch bằng ffmpeg. Khi chuyển sang nơi khác, không nên dựa vào `rate` cho timing chính xác nếu chưa đo lại output.

### 10.4 Preview — `mget_audition`

```http
POST https://edit-api-sg.capcut.com/lv/v2/intelligence/tts/mget_audition
```

Body:

```json
{
  "speakers": [
    {
      "speaker": "<voice.speaker>",
      "platform": 1,
      "text": "Nội dung",
      "lan": "vi"
    }
  ]
}
```

Hàm `_audition_url()` đã tồn tại nhưng không được luồng chính gọi. Quan sát của project: endpoint này dùng cho nghe thử và có thể bỏ qua text gửi lên để trả mẫu mặc định. Không dùng nó để synthesize nội dung thật.

## 11. Upload cloud cho ASR/SRT

Toàn bộ ID/token của các bước sau phải lấy động từ response trước. Không hardcode `upload_domain`, `UploadHost`, `StoreUri`, `Auth`, `SessionKey`, STS key hoặc upload ID.

### 11.1 Chuẩn bị file

Trước khi upload:

```python
size = Path(file_path).stat().st_size
md5_hex = md5(toàn_bộ_bytes_file).hexdigest()
```

Project đặt `file_type="video"` trong prepare/create asset kể cả khi pipeline thử đưa file audio vào ASR. Đây là hành vi hiện tại cần giữ nếu muốn tương thích đúng với project.

### 11.2 Prepare upload

```http
POST https://edit-api-sg.capcut.com/lv/v1/asset/prepare_upload_cloud
Lan: en
```

Auth: cookie + signed headers, `appvr=5.8.0`.

Body:

```json
{
  "workspace_id": "<workspace_id>",
  "space_id": "0",
  "md5": "<file-md5-hex>",
  "size": 123456,
  "file_type": "video",
  "is_web_user": true
}
```

Response `data` bắt buộc có:

```text
security_token.access_key_id
security_token.secret_access_key
security_token.session_token
upload_domain
service_id
```

Ngoài ra project giữ `upload_id` và `space_id` để tạo asset.

STS credentials là bí mật tạm thời. Không lưu lâu dài và không log.

### 11.3 ApplyUploadInner

```http
GET https://{upload_domain}/
```

Query:

```text
Action=ApplyUploadInner
Version=2020-11-19
SpaceName=<service_id>
FileType=video
IsInner=1
FileSize=<size>
s=<11 ký tự a-z0-9 ngẫu nhiên>
device_platform=web
```

Auth: AWS Signature V4 bằng STS credentials vừa nhận.

Response lấy từ:

```text
Result.InnerUploadAddress.UploadNodes[0]
```

Các field cần cho bước sau:

```text
StoreInfos[0].StoreUri
StoreInfos[0].Auth
UploadHost
SessionKey
```

### 11.4 Upload bytes lên TOS

Base URL:

```text
https://{UploadHost}/upload/v1/{StoreUri}
```

Headers chung:

```http
Authorization: <StoreInfos[0].Auth>
X-Storage-U: <account uid>
```

Đây **không phải** AWS SigV4 và không phải cookie auth.

#### Phase init

```http
POST ?uploadmode=part&phase=init
Content-Type: multipart/form-data; boundary=----WebKitFormBoundary<rand16>
```

Body multipart rỗng:

```text
--<boundary>--\r\n
```

Lấy `data.uploadid`.

#### Phase transfer

Chia file thành part 3 MiB, `part_number` bắt đầu từ 1, `part_offset` bắt đầu từ 0.

```http
POST ?uploadid=<tos-uploadid>&part_number=<n>&phase=transfer&part_offset=<offset>
Content-Type: application/octet-stream
Content-Disposition: attachment; filename="undefined"
Content-CRC32: <8 ký tự crc32 hex lowercase>
```

Body là bytes của part. Thành công khi response `code == 2000`.

CRC:

```python
crc = format(zlib.crc32(chunk) & 0xFFFFFFFF, "08x")
```

Lưu từng cặp `<part_number>:<crc>` để finish.

#### Phase finish

```http
POST ?uploadmode=part&phase=finish&uploadid=<tos-uploadid>&size=<size>
Content-Type: text/plain;charset=UTF-8
```

Body:

```text
1:<crc1>,2:<crc2>,3:<crc3>
```

Thành công khi response `code == 2000`.

### 11.5 CommitUploadInner

```http
POST https://{upload_domain}/?Action=CommitUploadInner&Version=2020-11-19&SpaceName=<service_id>
Content-Type: text/plain;charset=UTF-8
```

Body:

```json
{"SessionKey":"<SessionKey từ ApplyUploadInner>"}
```

Auth: AWS SigV4 bằng cùng STS credentials. Thành công khi `Result.Results` là list không rỗng.

### 11.6 AWS SigV4 dùng trong project

Scheme và credential scope:

```text
AWS4-HMAC-SHA256
<YYYYMMDD>/ap-singapore-1/vod/aws4_request
```

Canonical query phải sort key tăng dần và percent-encode với safe set `-_.~`.

Khác biệt quan trọng:

| Request | Signed headers | Có `X-Amz-Content-Sha256` |
|---|---|---|
| Apply GET, payload rỗng | `host;x-amz-date;x-amz-security-token` | Không |
| Commit POST, payload JSON | `host;x-amz-content-sha256;x-amz-date;x-amz-security-token` | Có |

Chuỗi canonical request:

```text
<METHOD>
<PATH>
<SORTED_CANONICAL_QUERY>
<CANONICAL_HEADERS>
<SIGNED_HEADER_NAMES>
<SHA256_PAYLOAD>
```

Chuỗi ký:

```text
AWS4-HMAC-SHA256
<X-Amz-Date>
<date>/<region>/<service>/aws4_request
<sha256(canonical_request)>
```

Chuỗi HMAC key:

```text
kDate    = HMAC("AWS4" + secret_key, YYYYMMDD)
kRegion  = HMAC(kDate, "ap-singapore-1")
kService = HMAC(kRegion, "vod")
kSigning = HMAC(kService, "aws4_request")
signature = HMAC_HEX(kSigning, string_to_sign)
```

Header trả ra:

```http
X-Amz-Date: <utc timestamp>
X-Amz-Security-Token: <session_token>
Authorization: AWS4-HMAC-SHA256 Credential=<AK>/<scope>, SignedHeaders=<...>, Signature=<...>
```

Commit còn có:

```http
X-Amz-Content-Sha256: <sha256 payload>
```

## 12. Đăng ký asset và gọi ASR caption

### 12.1 Create cloud asset

```http
POST https://edit-api-sg.capcut.com/lv/v1/asset/create_cloud_asset
Lan: en
```

Auth: cookie + signed headers, `appvr=5.8.0`.

`meta` trước tiên là object:

```json
{
  "uploadSource": {
    "owner": "<uid>",
    "platform": "browser",
    "systemVersion": "<USER_AGENT>",
    "appVersion": "1.0.0"
  }
}
```

Sau đó serialize `meta` thành **JSON string** và đặt vào body:

```json
{
  "everphoto_id": "<space_id từ prepare>",
  "asset": {
    "uri": "<StoreUri>",
    "size": 123456,
    "workspace_id": "<workspace_id>",
    "filename": "input.mp4",
    "upload_id": "<upload_id từ prepare>",
    "if_image_async_resize": true,
    "permission": 0,
    "space_id": "0",
    "file_type": "video",
    "folder_id": "-1",
    "meta": "<JSON string>",
    "md5": "<file-md5>"
  },
  "is_web_user": true
}
```

Lấy `data.cloud_asset.asset_id`; đây là `resource_id` cho caption.

### 12.2 Query parameter chung của caption

```text
aid=348188
device_platform=web
region=VN
web_id=<did>
babi_param=<JSON string URL-encoded>
```

Giá trị `babi_param` trước khi URL encode:

```json
{
  "scenario": "video_editor",
  "feature_key": "subtitle_recognition",
  "feature_entrance": "editor",
  "feature_entrance_detail": "editor-elements-subtitle-subtitle_recognition"
}
```

### 12.3 Body chung của submit/query

```json
{
  "resources": [
    {
      "workspace_id": "<workspace_id>",
      "resource_id": "<asset_id>",
      "url_from_type": 1,
      "language": "vi-VN",
      "caption_type": 0,
      "file_type": 2,
      "words_per_line": 55
    }
  ]
}
```

Mã ngôn ngữ đang cho phép trong UI của project:

```text
vi-VN, en-US, zh-CN
```

Backend có thể hỗ trợ thêm, nhưng project chưa khai báo/test thêm.

### 12.4 Submit

```http
POST https://edit-api-sg.capcut.com/lv/v1/caption/submit
```

Auth: cookie + signed headers, `appvr=8.4.0`. Thành công khi `str(ret) == "0"`.

### 12.5 Query/poll

```http
POST https://edit-api-sg.capcut.com/lv/v1/caption/query
```

Query và body giống submit. Project poll mỗi 1,5 giây, timeout tổng 1.800 giây.

Response:

```text
data.status == 1 → đang xử lý
data.status == 0 → hoàn tất
data.caption[0].utterances[] → kết quả
```

Mỗi utterance được dùng các field:

```json
{
  "text": "Nội dung phụ đề",
  "start_time": 1234,
  "end_time": 5678
}
```

Thời gian tính bằng milliseconds. Project bỏ utterance có text rỗng và chuyển mỗi utterance còn lại thành một block SRT.

### Cảnh báo polling

Loop hiện tại chỉ kết thúc khi `status == 0` **và** `utterances` không rỗng. Nếu server trả `status == 0` với kết quả rỗng, code tiếp tục poll cho đến timeout. Project mới nên xử lý riêng trạng thái hoàn tất-rỗng và các trạng thái lỗi khác.

## 13. Luồng điều phối hoàn chỉnh

Pseudo-code độc lập:

```python
cookies = login(email, password)
http = requests.Session()
apply_cookies(http, cookies, domain=".capcut.com")

did = cookies.get("_tea_web_id") or cookies.get("s_v_web_id") or create_device_id()
uid = get_account_uid(http, did)
workspace_id = get_workspace(http, did)

# TTS
voices = fetch_voice_categories(http, did)
audio_url = create_tts(http, did, text, selected_voice)
audio_bytes = http.get(audio_url).content

# ASR
size, md5_hex = inspect_file(file_path)
prep = prepare_upload_cloud(http, did, workspace_id, md5_hex, size)

signer = VodSigner(
    prep["security_token"]["access_key_id"],
    prep["security_token"]["secret_access_key"],
    prep["security_token"]["session_token"],
)
node = apply_upload(signer, prep["upload_domain"], prep["service_id"], size)
tos_multipart_upload(node, uid, file_path)
commit_upload(signer, prep["upload_domain"], prep["service_id"], node["SessionKey"])

resource_id = create_cloud_asset(http, did, workspace_id, prep, node, file_path)
caption_submit(http, did, workspace_id, resource_id, language)
utterances = poll_caption(http, did, workspace_id, resource_id, language)
srt = utterances_to_srt(utterances)
```

## 14. Những file cần mang sang project khác

### Phương án nhanh: copy implementation hiện tại

| File | Bắt buộc khi nào | Vai trò |
|---|---|---|
| `capcut/login.py` | Luôn cần nếu login bằng email/password | Passport login |
| `capcut/client.py` | Luôn cần | Edit API, TTS, caption orchestration |
| `capcut/cloud_upload.py` | Cần cho ASR/SRT | STS SigV4 + upload multipart |
| `capcut/session.py` | Tùy chọn | Lưu cookie local |
| `capcut/voice_cache.py` | Tùy chọn | Cache voice catalog |
| `srt_translator/parser.py` | Cần nếu giữ nguyên `generate_caption()` | `SrtBlock`, xuất text SRT |

Không cần copy PySide/UI worker nếu project mới không dùng Qt.

### Nên refactor khi tạo thư viện dùng chung

1. Inject đường dẫn session/cache thay vì hardcode ở repository root.
2. Trả về model/dataclass độc lập thay vì import `srt_translator.parser` trong client.
3. Tách `CapCutAuth`, `CapCutEditClient`, `VodUploader` và `CaptionService`.
4. Thêm `raise_for_status()` và kiểm tra mã nghiệp vụ nhất quán cho mọi endpoint.
5. Không nuốt lỗi workspace/account; tạo `validate_session()` thật sự.
6. Thêm timeout/retry có backoff cho lỗi mạng, `429`, `5xx` và polling.
7. Thêm cache TTL/version cho voice catalog.
8. Cho phép cấu hình region/locale/store country/app version thay vì hardcode.
9. Dùng secure credential store thay vì JSON plain text nếu chạy production.
10. Redact toàn bộ cookie/token/key khỏi structured log.

## 15. Lỗi thường gặp và điểm debug

| Hiện tượng | Khả năng cao | Kiểm tra |
|---|---|---|
| Login trả captcha code | Passport yêu cầu xác minh | Login bằng browser thật, hoàn tất captcha, hoặc dùng browser fallback |
| Có cookie nhưng edit API báo chưa login | Cookie hết hạn/sai domain | Gọi account info, kiểm tra cookie jar, đăng nhập lại |
| Không có `workspace_id` | Workspace API lỗi/schema đổi | Không nuốt exception; in mã HTTP và JSON đã redact |
| Voice list rỗng | Query/version/category đổi hoặc session lỗi | Kiểm tra `ret`, `errmsg`, `effect_item_list` |
| TTS đọc sai text | Dùng nhầm `mget_audition` | Phải dùng `/storyboard/v1/tts/multi_platform` |
| TTS không đổi tốc độ | Backend bỏ qua `rate` | Đo duration; time-stretch bằng ffmpeg |
| `InvalidAuthorization` ở Apply/Commit | Canonical SigV4 sai | Query sort, UTC clock, region/service, signed headers, payload hash |
| TOS transfer lỗi | Sai Auth/UID/CRC/offset | Kiểm tra `X-Storage-U`, `Content-CRC32`, part numbering và offsets |
| Caption poll mãi | Session lỗi, asset chưa sẵn sàng, status schema đổi hoặc kết quả rỗng | Log status đã redact, xử lý completed-empty/error status |
| API đột ngột 4xx sau khi từng chạy | Hằng số web client đã đổi | Capture request CapCut Web mới và so appid/appvr/query/sign |

Không retry mù quáng login sai password/captcha. Với upload multipart, retry part phải giữ nguyên `uploadid`, `part_number`, `offset` và bytes/CRC tương ứng.

## 16. Các hạn chế của implementation hiện tại

- Đây là private web API; không có cam kết ổn định hoặc tương thích.
- Không có refresh token/cookie tự động.
- Không có cơ chế captcha tự động.
- `validate()` chưa đáng tin cậy do exception workspace bị nuốt.
- Nhiều request chưa gọi `raise_for_status()` trước khi parse JSON.
- Một số lỗi API bị catch và chuyển thành kết quả rỗng, đặc biệt workspace/voice/account info.
- TTS ghép nhiều MP3 bằng nối bytes trực tiếp.
- `rate` có thể không được backend áp dụng.
- Voice cache không TTL.
- Upload không có retry/resume part sau lỗi mạng.
- Khi user stop giữa upload, code raise `UploadStopped` nhưng không gọi API abort multipart.
- `generate_caption()` có thể poll đến timeout nếu hoàn tất nhưng không có utterance.
- Region/version/locale đang hardcode theo request đã capture tại Việt Nam/Singapore.

## 17. Checklist kiểm thử khi port

1. Login bằng một tài khoản test, chỉ log tên cookie, không log value.
2. Gọi account info và xác nhận có `user_id_str`.
3. Gọi workspace và xác nhận có `workspace_id`.
4. Fetch riêng category `vietnamese`, xác nhận parse được `speaker`, `effect_id`, `resource_id`.
5. TTS một câu ngắn; xác nhận audio URL và file tải về phát được.
6. TTS text trên 800 ký tự; kiểm tra ghép/remux audio.
7. Upload file 30–60 giây; kiểm tra Apply SigV4 trước.
8. Kiểm tra từng TOS part trả `code=2000` và finish thành công.
9. Create asset và xác nhận có `asset_id`.
10. Submit caption, poll đến completed, kiểm tra timestamp SRT.
11. Test session hết hạn, captcha, mạng gián đoạn, `429`, `5xx`, caption rỗng và stop giữa upload.
12. Kiểm tra log/artifact không chứa cookie, STS secret, upload Auth hoặc HAR.

## 18. Bản đồ mã nguồn

| Thành phần | Vị trí hiện tại |
|---|---|
| Login passport | `capcut/login.py` |
| Cookie persistence | `capcut/session.py` |
| Edit API, voice, TTS, caption | `capcut/client.py` |
| AWS SigV4 và cloud multipart upload | `capcut/cloud_upload.py` |
| Voice cache | `capcut/voice_cache.py` |
| Thread login/connect/restore/TTS/SRT | `ui/workers.py` |
| Dialog email/password | `ui/dialogs/capcut_password_login_dialog.py` |
| SRT model/serializer | `srt_translator/parser.py` |

Tài liệu nghiên cứu lịch sử chi tiết hơn nằm ở `docs/CAPCUT_FLOW_RESEARCH.md`; khi có khác biệt, ưu tiên implementation hiện tại và cập nhật lại tài liệu này sau mỗi lần CapCut thay request.
