from __future__ import annotations

import smtplib
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import email_sender
from app.config import MailConfig, ProxyConfig, SmtpConfig


class OpenSmtpWithRetryTests(unittest.TestCase):
    def test_retries_disconnect_before_greeting_with_backoff(self) -> None:
        server = object()
        disconnect = smtplib.SMTPServerDisconnected("closed before greeting")

        with (
            mock.patch.object(
                email_sender,
                "_open_smtp",
                side_effect=[disconnect, server],
            ) as open_smtp,
            mock.patch.object(email_sender.time, "sleep") as sleep,
            mock.patch.object(email_sender, "_log_info"),
        ):
            result = email_sender._open_smtp_with_retry(
                "smtp.example.com", 587, False, None
            )

        self.assertIs(result, server)
        self.assertEqual(open_smtp.call_count, 2)
        self.assertEqual(sleep.call_args_list, [mock.call(1.0)])

    def test_exhausted_disconnect_is_reported_as_proxy_connection_error(self) -> None:
        proxy = ProxyConfig(
            enabled=True,
            proxy_type="socks5",
            host="127.0.0.1",
            port=7890,
        )

        with (
            mock.patch.object(
                email_sender,
                "_open_smtp",
                side_effect=smtplib.SMTPServerDisconnected(
                    "closed before greeting"
                ),
            ),
            mock.patch.object(email_sender.time, "sleep"),
            mock.patch.object(email_sender, "_log_info"),
            self.assertRaises(ConnectionError) as raised,
        ):
            email_sender._open_smtp_with_retry(
                "smtp.example.com", 587, False, proxy
            )

        message = str(raised.exception)
        self.assertIn("返回欢迎语前断开连接", message)
        self.assertIn("尚未进入账号认证", message)
        self.assertIn("127.0.0.1:7890", message)
        self.assertIsInstance(
            raised.exception.__cause__, smtplib.SMTPServerDisconnected
        )

    def test_ssl_handshake_failure_is_left_for_existing_fallback(self) -> None:
        ssl_error = ssl.SSLError("handshake failed")

        with (
            mock.patch.object(
                email_sender, "_open_smtp", side_effect=ssl_error
            ) as open_smtp,
            mock.patch.object(email_sender.time, "sleep") as sleep,
            self.assertRaises(ssl.SSLError),
        ):
            email_sender._open_smtp_with_retry(
                "smtp.example.com", 465, True, None
            )

        open_smtp.assert_called_once()
        sleep.assert_not_called()

    def test_other_socket_connection_errors_keep_host_context(self) -> None:
        smtp = SmtpConfig(
            host="smtp.example.com",
            port=587,
            use_ssl=False,
            sender="sender@example.com",
        )
        mail = MailConfig(recipients="recipient@example.com")

        with (
            mock.patch.object(
                email_sender,
                "_deliver",
                side_effect=ConnectionRefusedError("connection refused"),
            ),
            mock.patch.object(email_sender, "_log_exception"),
            self.assertRaises(ConnectionError) as raised,
        ):
            email_sender.send_email(smtp, mail)

        self.assertIn("smtp.example.com:587", str(raised.exception))

    def test_logging_works_without_stderr_in_windowed_app(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir) / "logs"
            with (
                mock.patch.object(email_sender, "LOG_DIR", log_dir),
                mock.patch.object(email_sender.sys, "stderr", None),
            ):
                email_sender._log_info("packaged app log")

            log_files = list(log_dir.glob("error-*.log"))
            self.assertEqual(len(log_files), 1)
            self.assertIn(
                "packaged app log", log_files[0].read_text(encoding="utf-8")
            )


if __name__ == "__main__":
    unittest.main()
