# Quyết Bồ IT Studio

Prototype desktop PySide6 cho quy trình Việt hóa video. Phiên bản này chỉ mô phỏng UI, tiến
trình và dữ liệu; không tải video, gọi API hay xử lý media thật.

## Chạy ứng dụng

Yêu cầu Python 3.11 trở lên (khuyến nghị Python 3.12).

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
$env:PYTHONPATH="src"
python -m quyetboit_studio
```

## Kiểm thử

```powershell
$env:PYTHONPATH="src"
pytest -q
ruff check src tests
```

Project được lưu dưới dạng JSON. Token, cookie và API key chỉ tồn tại trong RAM và không được
ghi vào project, autosave hay log.

