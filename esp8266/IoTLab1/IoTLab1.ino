#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <DHT.h>
#include <time.h>
#include "secrets.h"

#define DHTPIN D2
#define DHTTYPE DHT11

// Relay active-low: LOW = ON, HIGH = OFF
#define RELAY_PIN D1

// LED cảnh báo độ ẩm: D5 -> điện trở 220Ω -> LED -> GND
#define HUMIDITY_LED_PIN D5

// Ngưỡng demo. Thay đổi theo yêu cầu của giảng viên/thí nghiệm.
const float FIRE_TEMP_THRESHOLD_C = 30.0;
const float HUMIDITY_HIGH_THRESHOLD = 60.0;

const unsigned long SAMPLE_INTERVAL_MS = 60000; // 60 giây

DHT dht(DHTPIN, DHTTYPE);

uint64_t epochBaseMs = 0;
bool clockReady = false;

String uint64ToString(uint64_t value) {
  char buf[32];
  snprintf(buf, sizeof(buf), "%llu", (unsigned long long)value);
  return String(buf);
}

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.print("Connecting WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.print("WiFi connected. ESP IP: ");
  Serial.println(WiFi.localIP());
}

bool syncClock() {
  // UTC
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");

  Serial.print("Syncing NTP");
  unsigned long started = millis();

  while (time(nullptr) < 1700000000 && millis() - started < 20000) {
    delay(500);
    Serial.print(".");
  }

  time_t nowSec = time(nullptr);
  if (nowSec < 1700000000) {
    Serial.println("\nNTP sync failed.");
    return false;
  }

  epochBaseMs = ((uint64_t)nowSec * 1000ULL) - (uint64_t)millis();
  clockReady = true;

  Serial.println("\nNTP synced.");
  return true;
}

uint64_t epochMs() {
  if (!clockReady) return 0;
  return epochBaseMs + (uint64_t)millis();
}

String buildEventId() {
  String id = String(ESP.getChipId(), HEX);
  id += "-";
  id += String(millis());
  return id;
}

bool postAck(const String& eventId, uint64_t t3) {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }

  WiFiClient client;
  HTTPClient http;

  String url = String(BACKEND_URL) + "/ack";
  http.addHeader("Content-Type", "application/json");


  String payload = "{";
  payload += "\"event_id\":\"" + eventId + "\",";
  payload += "\"t3_send_end_ms\":\"" + uint64ToString(t3) + "\"";
  payload += "}";

  int code = http.POST(payload);

  Serial.print("ACK HTTP code: ");
  Serial.println(code);

  http.end();
  return code >= 200 && code < 300;
}

void setup() {
  Serial.begin(115200);

  delay(200);
  Serial.println();
  Serial.println("ESP8266 STARTED");
  Serial.println();
Serial.println("=== IOT LAB 1 - NEW FIRMWARE ===");

Serial.print("Firmware build: ");
Serial.print(__DATE__);
Serial.print(" ");
Serial.println(__TIME__);

Serial.print("Humidity threshold: ");
Serial.println(HUMIDITY_HIGH_THRESHOLD);

Serial.print("Backend URL: ");
Serial.println(BACKEND_URL);

  // Relay active-low: ép OFF trước khi cấu hình OUTPUT để tránh còi bật lúc boot.
  digitalWrite(RELAY_PIN, HIGH);
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, HIGH);

  pinMode(HUMIDITY_LED_PIN, OUTPUT);
  digitalWrite(HUMIDITY_LED_PIN, LOW);

  dht.begin();

  connectWiFi();
  syncClock();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }

  if (!clockReady && !syncClock()) {
    delay(5000);
    return;
  }

  // t0: thời điểm lấy mẫu cảm biến
  uint64_t t0 = epochMs();

  float temperature = dht.readTemperature();
  float humidity = dht.readHumidity();

  if (isnan(temperature) || isnan(humidity)) {
    Serial.println("Failed to read DHT11.");
    delay(3000);
    return;
  }

  bool fireAlert = temperature >= FIRE_TEMP_THRESHOLD_C;
  bool humidityAlert = humidity >= HUMIDITY_HIGH_THRESHOLD;

  // Quyết định local
  digitalWrite(RELAY_PIN, fireAlert ? LOW : HIGH);
  digitalWrite(HUMIDITY_LED_PIN, humidityAlert ? HIGH : LOW);

  // t1: thời điểm ra quyết định local
  uint64_t t1 = epochMs();

  String eventId = buildEventId();

  Serial.println();
  Serial.println("====================================");
  Serial.print("event_id: ");
  Serial.println(eventId);
  Serial.print("Temperature: ");
  Serial.print(temperature);
  Serial.println(" C");
  Serial.print("Humidity: ");
  Serial.print(humidity);
  Serial.println(" %");
  Serial.print("Fire alert: ");
  Serial.println(fireAlert ? "YES" : "NO");
  Serial.print("Humidity alert: ");
  Serial.println(humidityAlert ? "YES" : "NO");

  WiFiClient client;
  HTTPClient http;

  String url = String(BACKEND_URL) + "/ingest";

  if (!http.begin(client, url)) {
    Serial.println("http.begin failed");
    delay(SAMPLE_INTERVAL_MS);
    return;
  }

  http.addHeader("Content-Type", "application/json");

  // t2: bắt đầu gửi gói lên backend
  uint64_t t2 = epochMs();

  String payload = "{";
  payload += "\"event_id\":\"" + eventId + "\",";
  payload += "\"temperature\":" + String(temperature, 2) + ",";
  payload += "\"humidity\":" + String(humidity, 2) + ",";
  payload += "\"fire_alert\":" + String(fireAlert ? "true" : "false") + ",";
  payload += "\"humidity_alert\":" + String(humidityAlert ? "true" : "false") + ",";
  payload += "\"t0_sample_ms\":\"" + uint64ToString(t0) + "\",";
  payload += "\"t1_decision_ms\":\"" + uint64ToString(t1) + "\",";
  payload += "\"t2_send_start_ms\":\"" + uint64ToString(t2) + "\"";
  payload += "}";

  int httpCode = http.POST(payload);
  String response = "";

  if (httpCode > 0) {
    response = http.getString();
  }

  // t3: request/response kết thúc ở device
  uint64_t t3 = epochMs();

  Serial.print("POST /ingest HTTP code: ");
Serial.println(httpCode);

if (httpCode < 0) {
  Serial.print("HTTP ERROR: ");
  Serial.println(http.errorToString(httpCode));
}
  Serial.println(httpCode);

  if (response.length() > 0) {
    Serial.println("Backend response:");
    Serial.println(response);
  }

  http.end();

  // Gửi t3 ở request riêng để backend lưu đủ t0..t7.
  postAck(eventId, t3);

  Serial.print("Local device decision latency (t1-t0): ");
  Serial.print((unsigned long)(t1 - t0));
  Serial.println(" ms");

  delay(SAMPLE_INTERVAL_MS);
}
