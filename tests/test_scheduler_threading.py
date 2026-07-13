from __future__ import annotations

import threading
import time
import unittest
from datetime import datetime
from unittest import mock

from PySide6.QtCore import QCoreApplication

from app.config import AppConfig, ScheduleConfig
from app.scheduler import MAX_CONSECUTIVE_FAILURES, MailScheduler


class SchedulerThreadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        self.scheduler = MailScheduler()
        self.scheduler._config = AppConfig(
            schedule=ScheduleConfig(
                daily_count=1000,
                interval_minutes=1,
                deadline_time="23:59",
            )
        )
        self.scheduler._running = True

    def tearDown(self) -> None:
        self.scheduler.stop()
        self._wait_until(lambda: not self.scheduler._sending)

    def _wait_until(self, predicate, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            self.app.processEvents()
            if time.monotonic() >= deadline:
                self.fail("等待后台发送线程结束超时")
            time.sleep(0.001)
        self.app.processEvents()

    def test_many_completed_sends_do_not_leave_native_threads(self) -> None:
        with mock.patch("app.scheduler.send_email", return_value=None):
            for _ in range(200):
                self.scheduler._do_send(datetime.now())
                self._wait_until(lambda: not self.scheduler._sending)

        self.assertIsNone(self.scheduler._thread)
        self.assertIsNone(self.scheduler._worker)
        self.assertEqual(self.scheduler.sent_today, 200)

    def test_three_consecutive_failures_pause_scheduler(self) -> None:
        results: list[bool] = []
        self.scheduler.send_finished.connect(
            lambda success, _message: results.append(success)
        )

        with mock.patch(
            "app.scheduler.send_email", side_effect=ConnectionError("blocked")
        ):
            for _ in range(MAX_CONSECUTIVE_FAILURES):
                self.scheduler._do_send(datetime.now())
                self._wait_until(lambda: not self.scheduler._sending)

        self.assertEqual(results, [False] * MAX_CONSECUTIVE_FAILURES)
        self.assertFalse(self.scheduler.is_running)
        self.assertFalse(self.scheduler._timer.isActive())

    def test_stop_discards_result_from_in_flight_send(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        results: list[bool] = []

        def delayed_send(*_args) -> None:
            entered.set()
            release.wait(1.0)

        self.scheduler.send_finished.connect(
            lambda success, _message: results.append(success)
        )

        with mock.patch("app.scheduler.send_email", side_effect=delayed_send):
            self.scheduler._do_send(datetime.now())
            self.assertTrue(entered.wait(1.0))
            self.scheduler.stop()
            release.set()
            self._wait_until(lambda: not self.scheduler._sending)

        self.assertEqual(results, [])
        self.assertFalse(self.scheduler.is_running)


if __name__ == "__main__":
    unittest.main()
