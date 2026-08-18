# WaterAI V6.6 Donut Dashboard Fix

Bản này sửa lỗi donut dashboard bị méo/chồng label. Chart.js donut tắt legend nội bộ, dùng legend HTML riêng để không bị bóp nhỏ trong khung dashboard.

Chạy:
```powershell
python -m pip install -r requirements.txt
python app.py
```

Mở qua IIS:
```text
https://192.168.110.164:4443/?cam=CAM01
```

Đăng nhập admin:
```text
admin / cam@2026
```

# WaterAI V6.4 Dashboard/Trend/Packaging

## Điểm mới
- Trend không trôi gốc: dữ liệu được giữ bằng buffer giới hạn, hiển thị theo nhãn thời gian cố định HH:mm:ss.
- Trend có: Pause/Resume, Reset, Zoom +, Zoom -, Reset Zoom, Export PNG, Export CSV, Y min/Y max, time window, rule/cursor timestamp.
- Dashboard tổng hợp thêm: camera hoạt động, camera lỗi, số camera đã bật, alarm, mức bể trung bình, camera đang chọn.
- Bổ sung biểu đồ so sánh mức các bể để xem nhanh bể nào cao/thấp.
- Giữ cơ chế 20 camera, mỗi tab Unified có thể mở cùng link kèm `?cam=CAMxx`.
- Giữ đăng nhập phân quyền: `admin / cam@2026`.

## Chạy thử
```powershell
cd waterai_v6_4_dashboard_trend_packaging
python -m pip install -r requirements.txt
python app.py
```

Mở trực tiếp:
```text
http://127.0.0.1:5000/?cam=CAM01
```

Qua IIS HTTPS:
```text
https://192.168.110.164:4443/?cam=CAM01
https://192.168.110.164:4443/?cam=CAM02
```

## Tài khoản mặc định
```text
User: admin
Pass: cam@2026
Role: admin
```
Role:
- viewer: chỉ giám sát.
- operator: vận hành cơ bản.
- engineer: chỉnh camera/ROI/ngưỡng.
- admin: toàn quyền, thêm/xóa tài khoản.

## Tối ưu tài nguyên cho 20 camera
Trong `config.json`:
```json
"performance": {
  "mjpeg_fps": 6,
  "default_process_fps": 5,
  "stream_width": 960,
  "jpeg_quality": 70,
  "max_video_clients_per_camera": 8
}
```
Gợi ý:
- Nếu WinCC mở nhiều tab: giảm `stream_width` còn 640~800 và `jpeg_quality` 55~65.
- Camera overview/lưới: chỉ bật camera cần xem, các camera disabled không mở thread.
- Process FPS thực tế cho đo mức nước thường chỉ cần 3~5 FPS.
- Video chỉ nên xem ở tab camera chi tiết; dashboard/trend nên đọc API/OPC UA.

## OPC UA cho WinCC Unified
Endpoint:
```text
opc.tcp://192.168.110.164:4845/freeopcua/server/
```
Mỗi camera có object riêng:
```text
Objects/WaterAI/CAM01/WaterLevel
Objects/WaterAI/CAM01/StatusCode
Objects/WaterAI/CAM01/StatusText
Objects/WaterAI/CAM01/CameraOnline
Objects/WaterAI/CAM01/FPS
Objects/WaterAI/CAM01/Confidence
```

## Đóng gói app kiểu đơn giản bằng PyInstaller
Cài:
```powershell
python -m pip install pyinstaller
```
Build thư mục one-folder:
```powershell
pyinstaller --noconfirm --onedir --name WaterAI `
  --add-data "templates;templates" `
  --add-data "static;static" `
  --add-data "config.json;." `
  --add-data "web.config;." `
  app.py
```
Chạy:
```powershell
cd dist\WaterAI
WaterAI.exe
```

## Đóng gói chạy tự động khi bật máy
Khuyến nghị dùng NSSM để chạy như Windows Service:
1. Tải NSSM.
2. Mở CMD/PowerShell Administrator.
3. Cài service:
```powershell
nssm install WaterAI D:\WaterAI\dist\WaterAI\WaterAI.exe
nssm set WaterAI AppDirectory D:\WaterAI\dist\WaterAI
nssm set WaterAI Start SERVICE_AUTO_START
nssm start WaterAI
```

## IIS Reverse Proxy
IIS site HTTPS `:4443` trỏ reverse proxy về:
```text
http://127.0.0.1:5000
```
Giữ `web.config` trong thư mục IIS hoặc cấu hình URL Rewrite/ARR tương đương.

## V6.5 Patch - Trend/ROI/Donut Dashboard

Bản này sửa các lỗi phát sinh sau V6.4:

- ROI trong popup dùng endpoint snapshot tĩnh `/api/camera/<CAM>/snapshot` thay vì mở thêm luồng MJPEG. Nhờ vậy popup ROI không bị đen do quá giới hạn client stream và kéo ROI ổn định hơn.
- Trend dashboard được cố định chiều cao bằng `.trend-canvas-box`, không còn kéo giãn toàn trang khi cập nhật dữ liệu.
- Dashboard bổ sung 3 biểu đồ donut:
  - Mức bể: LOW / NORMAL / HIGH / OFFLINE.
  - Trạng thái bể: NORMAL / ALARM / OFFLINE.
  - Hiện trạng thiết bị: ONLINE / OFFLINE / DISABLED.
- Giữ nguyên đăng nhập:
  - user: `admin`
  - pass: `cam@2026`

Chạy:

```powershell
python -m pip install -r requirements.txt
python app.py
```

IIS vẫn reverse proxy về `http://127.0.0.1:5000`.
