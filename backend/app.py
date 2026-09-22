import logging
import os
import smtplib
import socket
import ssl
import time
from email.message import EmailMessage
from html import escape
from typing import Optional

import mysql.connector
import requests
from dotenv import load_dotenv
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Đọc file .env nằm cùng thư mục với app.py
ENV_PATH = Path(__file__).resolve().parent / ".env"

if not ENV_PATH.exists():
    raise FileNotFoundError(f"Không tìm thấy file .env: {ENV_PATH}")

load_dotenv(
    dotenv_path=ENV_PATH,
    override=True,
    encoding="utf-8-sig"
)

print("ENV FILE:", ENV_PATH)
print("MYSQL USER:", os.getenv("DB_USER"))
print("PASSWORD CONFIGURED:", bool(os.getenv("DB_PASSWORD")))

logger = logging.getLogger(__name__)

app = FastAPI(title="IoT Lab 1 Backend", version="1.0.0")


# ============================================================
# CONFIG
# ============================================================

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME", "iot_lab1")

# Kiểm tra cấu hình trước khi kết nối MySQL
if not DB_USER:
    raise ValueError("DB_USER chưa được cấu hình trong .env")

if not DB_PASSWORD:
    raise ValueError("DB_PASSWORD chưa được cấu hình trong .env")

THINGSPEAK_WRITE_API_KEY = os.getenv("THINGSPEAK_WRITE_API_KEY", "").strip()

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT_VALUE = os.getenv("SMTP_PORT", "").strip()
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "").strip()
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD", "").replace(" ", "").strip()
ALERT_TO_EMAIL = os.getenv("ALERT_TO_EMAIL", "").strip()

try:
    SMTP_PORT = int(SMTP_PORT_VALUE) if SMTP_PORT_VALUE else None
except ValueError:
    SMTP_PORT = None

THINGSPEAK_URL = "https://api.thingspeak.com/update.json"


# ============================================================
# MODELS
# ============================================================

class SensorEvent(BaseModel):
    event_id: str
    temperature: float
    humidity: float
    fire_alert: bool
    humidity_alert: bool

    # Gửi dưới dạng string để ESP8266 không bị mất chính xác số nguyên 64-bit.
    t0_sample_ms: str
    t1_decision_ms: str
    t2_send_start_ms: str


class DeviceAck(BaseModel):
    event_id: str
    t3_send_end_ms: str


# ============================================================
# TIME
# ============================================================

def now_ms() -> int:
    return time.time_ns() // 1_000_000


# ============================================================
# DATABASE
# ============================================================

def get_server_connection():
    return mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        autocommit=True,
    )


def get_db_connection():
    return mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        autocommit=True,
    )


def init_db():
    server = get_server_connection()
    cursor = server.cursor()
    cursor.execute(
        f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    cursor.close()
    server.close()

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sensor_events (
            event_id VARCHAR(80) PRIMARY KEY,

            temperature DECIMAL(6,2) NOT NULL,
            humidity DECIMAL(6,2) NOT NULL,
            fire_alert BOOLEAN NOT NULL,
            humidity_alert BOOLEAN NOT NULL,

            t0_sample_ms BIGINT NOT NULL,
            t1_decision_ms BIGINT NOT NULL,
            t2_send_start_ms BIGINT NOT NULL,
            t3_send_end_ms BIGINT NULL,

            t4_backend_received_ms BIGINT NOT NULL,
            t5_cloud_done_ms BIGINT NULL,
            t6_email_issued_ms BIGINT NULL,
            t7_email_accepted_ms BIGINT NULL,

            l_device_ms BIGINT NULL,
            l_uplink_ms BIGINT NULL,
            l_cloud_ms BIGINT NULL,
            l_notify_ms BIGINT NULL,
            l_e2e_ms BIGINT NULL,

            thingspeak_entry_id BIGINT NULL,
            email_sent BOOLEAN NOT NULL DEFAULT FALSE,
            email_error TEXT NULL,
            thingspeak_error TEXT NULL,

            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.close()
    conn.close()


@app.on_event("startup")
def startup_event():
    init_db()


# ============================================================
# THINGSPEAK
# ============================================================

def send_to_thingspeak(event: SensorEvent) -> tuple[Optional[int], Optional[str]]:
    if not THINGSPEAK_WRITE_API_KEY:
        return None, "THINGSPEAK_WRITE_API_KEY is empty"

    try:
        response = requests.post(
            THINGSPEAK_URL,
            data={
                "api_key": THINGSPEAK_WRITE_API_KEY,
                "field1": event.temperature,
                "field2": event.humidity,
                "field3": 1 if event.fire_alert else 0,
                "field4": 1 if event.humidity_alert else 0,
                "status": event.event_id,
            },
            timeout=10,
        )
        response.raise_for_status()

        data = response.json()
        entry_id = data.get("entry_id")
        return entry_id, None

    except Exception as exc:
        return None, str(exc)


# ============================================================
# EMAIL - GMAIL SMTP, KHÔNG IFTTT
# ============================================================

def sanitize_smtp_error(message) -> str:
    if isinstance(message, bytes):
        message = message.decode("utf-8", errors="replace")

    sanitized = " ".join(str(message).split())

    for sensitive_value in (
        SMTP_APP_PASSWORD,
        SMTP_EMAIL,
        ALERT_TO_EMAIL,
    ):
        if sensitive_value:
            sanitized = sanitized.replace(sensitive_value, "[REDACTED]")

    return sanitized[:500]


def send_alert_email(event: SensorEvent) -> tuple[bool, Optional[str], Optional[int], Optional[int]]:
    if not (event.fire_alert or event.humidity_alert):
        return False, None, None, None

    missing_config = [
        name
        for name, value in (
            ("SMTP_HOST", SMTP_HOST),
            ("SMTP_PORT", SMTP_PORT),
            ("SMTP_EMAIL", SMTP_EMAIL),
            ("SMTP_APP_PASSWORD", SMTP_APP_PASSWORD),
            ("ALERT_TO_EMAIL", ALERT_TO_EMAIL),
        )
        if not value
    ]
    if missing_config:
        return (
            False,
            f"Gmail SMTP configuration is missing or invalid: {', '.join(missing_config)}",
            None,
            None,
        )

    reasons = []

    if event.fire_alert:
        reasons.append(
            f"CẢNH BÁO NHIỆT ĐỘ CAO: {event.temperature:.2f} °C"
        )

    if event.humidity_alert:
        reasons.append(
            f"CẢNH BÁO ĐỘ ẨM CAO: {event.humidity:.2f} %"
        )

    message = EmailMessage()
    message["From"] = SMTP_EMAIL
    message["To"] = ALERT_TO_EMAIL
    message["Subject"] = "[IoT LAB 1] Cảnh báo môi trường"
    message.set_content(
        "\n".join(reasons)
        + f"""

Event ID: {event.event_id}
Nhiệt độ: {event.temperature:.2f} °C
Độ ẩm: {event.humidity:.2f} %

Email được gửi tự động bởi Python Backend qua Gmail SMTP.
"""
    )

    t6 = now_ms()
    smtp = None

    try:
        smtp = smtplib.SMTP(
            SMTP_HOST,
            SMTP_PORT,
            timeout=15,
        )
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(SMTP_EMAIL, SMTP_APP_PASSWORD)

        refused_recipients = smtp.send_message(message)
        if refused_recipients:
            error = "Gmail SMTP rejected the alert recipient"
            logger.error("Gmail SMTP send failed: %s", error)
            return False, error, t6, None

        # t7 là lúc SMTP server chấp nhận email, không phải lúc email đến inbox.
        t7 = now_ms()
        return True, None, t6, t7

    except smtplib.SMTPAuthenticationError as exc:
        smtp_code = getattr(exc, "smtp_code", "unknown")
        error = (
            "Gmail SMTP authentication failed "
            f"(SMTP {smtp_code}): check SMTP_EMAIL and Gmail App Password"
        )
        logger.error("Gmail SMTP send failed: %s", error)
        return False, error, t6, None
    except (socket.timeout, TimeoutError):
        error = "Gmail SMTP connection timed out"
        logger.error("Gmail SMTP send failed: %s", error)
        return False, error, t6, None
    except smtplib.SMTPServerDisconnected:
        error = "Gmail SMTP connection unexpectedly closed"
        logger.error("Gmail SMTP send failed: %s", error)
        return False, error, t6, None
    except smtplib.SMTPRecipientsRefused:
        error = "Gmail SMTP rejected the alert recipient"
        logger.error("Gmail SMTP send failed: %s", error)
        return False, error, t6, None
    except smtplib.SMTPResponseException as exc:
        detail = sanitize_smtp_error(exc.smtp_error)
        error = f"Gmail SMTP error (SMTP {exc.smtp_code})"
        if detail:
            error = f"{error}: {detail}"
        logger.error("Gmail SMTP send failed: %s", error)
        return False, error, t6, None
    except smtplib.SMTPException as exc:
        detail = sanitize_smtp_error(exc)
        error = "Gmail SMTP error"
        if detail:
            error = f"{error}: {detail}"
        logger.error("Gmail SMTP send failed: %s", error)
        return False, error, t6, None
    except OSError as exc:
        detail = sanitize_smtp_error(exc)
        error = "Gmail SMTP connection failed"
        if detail:
            error = f"{error}: {detail}"
        logger.error("Gmail SMTP send failed: %s", error)
        return False, error, t6, None
    except Exception as exc:
        detail = sanitize_smtp_error(exc)
        error = "Unexpected Gmail SMTP error"
        if detail:
            error = f"{error}: {detail}"
        logger.error("Gmail SMTP send failed: %s", error)
        return False, error, t6, None
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except (smtplib.SMTPException, OSError):
                try:
                    smtp.close()
                except OSError:
                    pass


# ============================================================
# API
# ============================================================

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ingest")
def ingest(event: SensorEvent):
    try:
        t0 = int(event.t0_sample_ms)
        t1 = int(event.t1_decision_ms)
        t2 = int(event.t2_send_start_ms)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid timestamp format")

    # t4: backend bắt đầu nhận/xử lý request
    t4 = now_ms()

    l_device = t1 - t0
    l_uplink = t4 - t2

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO sensor_events (
                event_id,
                temperature,
                humidity,
                fire_alert,
                humidity_alert,
                t0_sample_ms,
                t1_decision_ms,
                t2_send_start_ms,
                t4_backend_received_ms,
                l_device_ms,
                l_uplink_ms
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                temperature = VALUES(temperature),
                humidity = VALUES(humidity),
                fire_alert = VALUES(fire_alert),
                humidity_alert = VALUES(humidity_alert)
            """,
            (
                event.event_id,
                event.temperature,
                event.humidity,
                event.fire_alert,
                event.humidity_alert,
                t0,
                t1,
                t2,
                t4,
                l_device,
                l_uplink,
            ),
        )

        # Gửi dữ liệu lên ThingSpeak.
        thingspeak_entry_id, thingspeak_error = send_to_thingspeak(event)

        # t5: backend đã xử lý xong phần cloud/DB chính.
        t5 = now_ms()
        l_cloud = t5 - t4

        email_sent, email_error, t6, t7 = send_alert_email(event)

        l_notify = (t7 - t6) if (t6 is not None and t7 is not None) else None
        l_e2e = (t7 - t0) if t7 is not None else None

        cursor.execute(
            """
            UPDATE sensor_events
            SET
                t5_cloud_done_ms = %s,
                t6_email_issued_ms = %s,
                t7_email_accepted_ms = %s,
                l_cloud_ms = %s,
                l_notify_ms = %s,
                l_e2e_ms = %s,
                thingspeak_entry_id = %s,
                email_sent = %s,
                email_error = %s,
                thingspeak_error = %s
            WHERE event_id = %s
            """,
            (
                t5,
                t6,
                t7,
                l_cloud,
                l_notify,
                l_e2e,
                thingspeak_entry_id,
                email_sent,
                email_error,
                thingspeak_error,
                event.event_id,
            ),
        )

        return {
            "ok": True,
            "event_id": event.event_id,
            "thingspeak_entry_id": thingspeak_entry_id,
            "email_sent": email_sent,
            "email_error": email_error,
            "thingspeak_error": thingspeak_error,
            "timestamps": {
                "t0": t0,
                "t1": t1,
                "t2": t2,
                "t4": t4,
                "t5": t5,
                "t6": t6,
                "t7": t7,
            },
            "latency_ms": {
                "device": l_device,
                "uplink": l_uplink,
                "cloud": l_cloud,
                "notify": l_notify,
                "e2e": l_e2e,
            },
        }

    finally:
        cursor.close()
        conn.close()


@app.post("/ack")
def ack(data: DeviceAck):
    try:
        t3 = int(data.t3_send_end_ms)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid t3 timestamp")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE sensor_events
        SET t3_send_end_ms = %s
        WHERE event_id = %s
        """,
        (t3, data.event_id),
    )

    updated = cursor.rowcount

    cursor.close()
    conn.close()

    if updated == 0:
        raise HTTPException(status_code=404, detail="event_id not found")

    return {
        "ok": True,
        "event_id": data.event_id,
        "t3_send_end_ms": t3,
    }


@app.get("/api/events")
def get_events(limit: int = 50):
    limit = max(1, min(limit, 200))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        """
        SELECT *
        FROM sensor_events
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (limit,),
    )

    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return rows


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return """
<!doctype html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>IoT LAB 1 Dashboard</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 24px;
            background: #f6f7f9;
        }
        h1 { margin-bottom: 4px; }
        .muted { color: #666; margin-bottom: 20px; }
        .card {
            background: white;
            border-radius: 12px;
            padding: 16px;
            overflow-x: auto;
            box-shadow: 0 2px 10px rgba(0,0,0,.06);
        }
        table {
            border-collapse: collapse;
            width: 100%;
            min-width: 1300px;
        }
        th, td {
            border-bottom: 1px solid #eee;
            padding: 9px 10px;
            text-align: left;
            white-space: nowrap;
        }
        th { background: #fafafa; }
        .alert { font-weight: bold; }
        .ok { color: #1b6e32; }
        .bad { color: #b42318; }
    </style>
</head>
<body>
    <h1>IoT LAB 1</h1>
    <div class="muted">
        Nhiệt độ, độ ẩm, cảnh báo và latency t0 → t7
    </div>

    <div class="card">
        <table>
            <thead>
                <tr>
                    <th>Event</th>
                    <th>Temp °C</th>
                    <th>Humidity %</th>
                    <th>Fire</th>
                    <th>Humidity alert</th>
                    <th>L_device</th>
                    <th>L_uplink</th>
                    <th>L_cloud</th>
                    <th>L_notify</th>
                    <th>L_e2e</th>
                    <th>ThingSpeak</th>
                    <th>Email</th>
                    <th>Time</th>
                </tr>
            </thead>
            <tbody id="rows"></tbody>
        </table>
    </div>

<script>
async function loadData() {
    const response = await fetch('/api/events?limit=50');
    const data = await response.json();

    const rows = document.getElementById('rows');

    rows.innerHTML = data.map(x => `
        <tr>
            <td>${x.event_id}</td>
            <td>${x.temperature}</td>
            <td>${x.humidity}</td>
            <td class="${x.fire_alert ? 'bad alert' : 'ok'}">
                ${x.fire_alert ? 'ALERT' : 'OK'}
            </td>
            <td class="${x.humidity_alert ? 'bad alert' : 'ok'}">
                ${x.humidity_alert ? 'ALERT' : 'OK'}
            </td>
            <td>${x.l_device_ms ?? '-'} ms</td>
            <td>${x.l_uplink_ms ?? '-'} ms</td>
            <td>${x.l_cloud_ms ?? '-'} ms</td>
            <td>${x.l_notify_ms ?? '-'} ms</td>
            <td>${x.l_e2e_ms ?? '-'} ms</td>
            <td>${x.thingspeak_entry_id ?? 'FAIL'}</td>
            <td>${x.email_sent ? 'SENT' : '-'}</td>
            <td>${x.created_at}</td>
        </tr>
    `).join('');
}

loadData();
setInterval(loadData, 5000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
