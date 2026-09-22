import smtplib
import socket
import ssl
import unittest
from unittest.mock import Mock, patch

import app as app_module


class SendAlertEmailTest(unittest.TestCase):
    def setUp(self):
        self.event = app_module.SensorEvent(
            event_id="test-event-1",
            temperature=35.5,
            humidity=82.0,
            fire_alert=True,
            humidity_alert=True,
            t0_sample_ms="1000",
            t1_decision_ms="1001",
            t2_send_start_ms="1002",
        )

        for name, value in (
            ("SMTP_HOST", "smtp.gmail.com"),
            ("SMTP_PORT", 587),
            ("SMTP_EMAIL", "sender@gmail.com"),
            ("SMTP_APP_PASSWORD", "test-app-password"),
            ("ALERT_TO_EMAIL", "receiver@gmail.com"),
        ):
            config_patch = patch.object(app_module, name, value)
            config_patch.start()
            self.addCleanup(config_patch.stop)

    @staticmethod
    def smtp_mock():
        smtp = Mock()
        smtp.send_message.return_value = {}
        return smtp

    def test_returns_success_when_smtp_accepts_email(self):
        smtp = self.smtp_mock()

        with (
            patch.object(app_module.smtplib, "SMTP", return_value=smtp) as smtp_class,
            patch.object(app_module, "now_ms", side_effect=[2000, 2015]),
        ):
            result = app_module.send_alert_email(self.event)

        self.assertEqual((True, None, 2000, 2015), result)
        smtp_class.assert_called_once_with("smtp.gmail.com", 587, timeout=15)
        self.assertEqual(2, smtp.ehlo.call_count)
        smtp.starttls.assert_called_once()
        tls_context = smtp.starttls.call_args.kwargs["context"]
        self.assertIsInstance(tls_context, ssl.SSLContext)
        smtp.login.assert_called_once_with(
            "sender@gmail.com",
            "test-app-password",
        )
        smtp.send_message.assert_called_once()
        smtp.quit.assert_called_once()

    def test_returns_authentication_error(self):
        smtp = self.smtp_mock()
        smtp.login.side_effect = smtplib.SMTPAuthenticationError(
            535,
            b"Username and Password not accepted",
        )

        with patch.object(app_module.smtplib, "SMTP", return_value=smtp):
            sent, error, t6, t7 = app_module.send_alert_email(self.event)

        self.assertFalse(sent)
        self.assertIn("authentication failed", error)
        self.assertIn("SMTP 535", error)
        self.assertIsNotNone(t6)
        self.assertIsNone(t7)

    def test_handles_timeout(self):
        with patch.object(
            app_module.smtplib,
            "SMTP",
            side_effect=socket.timeout("timed out"),
        ):
            sent, error, t6, t7 = app_module.send_alert_email(self.event)

        self.assertFalse(sent)
        self.assertEqual("Gmail SMTP connection timed out", error)
        self.assertIsNotNone(t6)
        self.assertIsNone(t7)

    def test_handles_connection_unexpectedly_closed(self):
        smtp = self.smtp_mock()
        smtp.send_message.side_effect = smtplib.SMTPServerDisconnected(
            "Connection unexpectedly closed"
        )

        with patch.object(app_module.smtplib, "SMTP", return_value=smtp):
            sent, error, t6, t7 = app_module.send_alert_email(self.event)

        self.assertFalse(sent)
        self.assertEqual("Gmail SMTP connection unexpectedly closed", error)
        self.assertIsNotNone(t6)
        self.assertIsNone(t7)

    def test_returns_false_when_recipient_is_refused(self):
        smtp = self.smtp_mock()
        smtp.send_message.return_value = {
            "receiver@gmail.com": (550, b"recipient rejected")
        }

        with patch.object(app_module.smtplib, "SMTP", return_value=smtp):
            sent, error, t6, t7 = app_module.send_alert_email(self.event)

        self.assertFalse(sent)
        self.assertEqual("Gmail SMTP rejected the alert recipient", error)
        self.assertIsNotNone(t6)
        self.assertIsNone(t7)

    def test_reports_missing_configuration_without_connecting(self):
        with (
            patch.object(app_module, "SMTP_APP_PASSWORD", ""),
            patch.object(app_module.smtplib, "SMTP") as smtp_class,
        ):
            sent, error, t6, t7 = app_module.send_alert_email(self.event)

        self.assertFalse(sent)
        self.assertIn("SMTP_APP_PASSWORD", error)
        self.assertIsNone(t6)
        self.assertIsNone(t7)
        smtp_class.assert_not_called()

    def test_redacts_password_and_email_from_connection_error(self):
        with patch.object(
            app_module.smtplib,
            "SMTP",
            side_effect=OSError(
                "test-app-password sender@gmail.com receiver@gmail.com"
            ),
        ):
            _, error, _, _ = app_module.send_alert_email(self.event)

        self.assertNotIn("test-app-password", error)
        self.assertNotIn("sender@gmail.com", error)
        self.assertNotIn("receiver@gmail.com", error)
        self.assertIn("[REDACTED]", error)


if __name__ == "__main__":
    unittest.main()
