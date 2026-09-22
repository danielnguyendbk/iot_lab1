CREATE DATABASE IF NOT EXISTS iot_lab1
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;

USE iot_lab1;

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
);
