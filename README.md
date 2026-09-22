# IoT LAB 1 — ESP8266 + DHT11 + ThingSpeak + MySQL + Gmail SMTP

## Kiến trúc

DHT11
  ↓
ESP8266
  ├── Relay / còi cảnh báo nhiệt độ
  ├── LED cảnh báo độ ẩm
  └── HTTP → Python FastAPI
                 ├── MySQL
                 ├── ThingSpeak
                 ├── Gmail SMTP
                 └── Dashboard latency

Không dùng IFTTT.

## 1. Phần cứng

- DHT11 DATA -> D2
- Relay IN -> D1
- LED cảnh báo độ ẩm -> D5 qua điện trở 220 Ω
- GND chung

Relay trong code là active-low:
- LOW = bật relay/còi
- HIGH = tắt relay/còi

## 2. Arduino IDE

Cài:
- ESP8266 board package
- DHT sensor library

Sao chép:
- `esp8266/secrets.example.h` thành `esp8266/secrets.h`
- chỉnh Wi-Fi và BACKEND_URL

BACKEND_URL phải là IP LAN của máy tính, không phải localhost.

Ví dụ khi dùng Windows Mobile Hotspot, kiểm tra bằng:

```powershell
ipconfig
```

Sau đó lấy IPv4 phù hợp, ví dụ:

```text
http://192.168.137.1:8000
```

## 3. Backend Python

```bash
cd backend
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Chỉnh `.env`.

Chạy:

```powershell
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

Kiểm tra:

```text
http://localhost:8000/health
```

Dashboard:

```text
http://localhost:8000/
```

## 4. Gmail SMTP

Không đặt mật khẩu Gmail thật vào `.env`.

Bật 2-Step Verification cho Google Account, tạo App Password và điền vào:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_EMAIL=youraccount@gmail.com
SMTP_APP_PASSWORD=YOUR_16_CHARACTER_APP_PASSWORD
ALERT_TO_EMAIL=receiver@gmail.com
```

## 5. ThingSpeak fields

- field1: temperature
- field2: humidity
- field3: fire_alert (0/1)
- field4: humidity_alert (0/1)
- status: event_id

## 6. Latency

Hệ thống log:

- t0: sensor sample
- t1: local decision
- t2: device bắt đầu gửi HTTP
- t3: device nhận xong HTTP response
- t4: Python backend nhận request
- t5: backend xử lý DB/ThingSpeak xong
- t6: bắt đầu gửi email
- t7: Gmail SMTP chấp nhận email

Công thức:

- L_device = t1 - t0
- L_uplink = t4 - t2
- L_cloud = t5 - t4
- L_notify = t7 - t6
- L_e2e = t7 - t0

Lưu ý: t7 là thời điểm SMTP server chấp nhận message, không phải thời điểm người dùng nhìn thấy email trong inbox.

## 7. Ngưỡng demo

Trong file `.ino`:

```cpp
const float FIRE_TEMP_THRESHOLD_C = 30.0;
const float HUMIDITY_HIGH_THRESHOLD = 80.0;
```

Đây là ngưỡng demo và có thể đổi theo yêu cầu bài lab.

## 8. Bảo mật

Không commit:
- `secrets.h`
- `.env`
- API key
- Gmail App Password

Nếu API key đã từng được đưa vào source/public repository, nên rotate key đó.


USE iot_lab1;

SELECT
    event_id,
    temperature,
    humidity,
    fire_alert,
    humidity_alert,
    thingspeak_entry_id,
    email_sent,
    created_at
FROM sensor_events
ORDER BY created_at DESC
LIMIT 10;
