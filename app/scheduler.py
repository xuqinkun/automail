"""邮件发送调度器"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal

from app.config import AppConfig, MailConfig, ProxyConfig, ScheduleConfig, SmtpConfig
from app.email_sender import send_email


def parse_hhmm(value: str) -> Optional[time]:
    text = value.strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError("时间格式应为 HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("时间值无效")
    return time(hour, minute)


def combine_datetime(day: date, t: time) -> datetime:
    return datetime.combine(day, t)


@dataclass
class SchedulePlan:
    first_send: datetime
    last_send: datetime
    valid: bool
    message: str


def build_daily_plan(
    schedule: ScheduleConfig,
    now: Optional[datetime] = None,
) -> SchedulePlan:
    """根据配置计算当天发送计划，并校验最后一封是否超过截止时刻。"""
    now = now or datetime.now()
    today = now.date()

    try:
        deadline = parse_hhmm(schedule.deadline_time)
        if deadline is None:
            raise ValueError("请设置最晚发送时间")
        start_t = parse_hhmm(schedule.start_time)
    except ValueError as exc:
        return SchedulePlan(now, now, False, str(exc))

    if schedule.daily_count < 1:
        return SchedulePlan(now, now, False, "每天发送次数至少为 1")
    if schedule.interval_minutes < 1:
        return SchedulePlan(now, now, False, "发送间隔至少为 1 分钟")

    if start_t is None:
        first_send = now
    else:
        first_send = combine_datetime(today, start_t)
        if first_send < now:
            first_send = now

    last_send = first_send + timedelta(
        minutes=schedule.interval_minutes * (schedule.daily_count - 1)
    )
    deadline_dt = combine_datetime(today, deadline)

    if last_send.date() > today:
        return SchedulePlan(
            first_send,
            last_send,
            False,
            (
                f"计划最后一封发送时间为次日 {last_send.strftime('%H:%M')}，"
                f"超过了当天最晚发送时刻 {deadline.strftime('%H:%M')}。"
                f"请减少次数、缩短间隔或推迟开始时间。"
            ),
        )

    if last_send > deadline_dt:
        return SchedulePlan(
            first_send,
            last_send,
            False,
            (
                f"计划最后一封发送时间为 {last_send.strftime('%H:%M')}，"
                f"超过了最晚发送时刻 {deadline.strftime('%H:%M')}。"
                f"请减少次数、缩短间隔或推迟开始时间。"
            ),
        )

    first_label = (
        first_send.strftime("%H:%M")
        if start_t is not None
        else f"立即发送（{first_send.strftime('%H:%M')}）"
    )
    return SchedulePlan(
        first_send,
        last_send,
        True,
        (
            f"今日计划：首封 {first_label}，"
            f"末封 {last_send.strftime('%H:%M')}，共 {schedule.daily_count} 封"
        ),
    )


def calc_interval_minutes(
    schedule: ScheduleConfig,
    now: Optional[datetime] = None,
) -> Optional[int]:
    """根据起止时刻和每天次数，自动计算发送间隔（分钟）。"""
    if schedule.daily_count <= 1:
        return 1

    now = now or datetime.now()
    today = now.date()

    try:
        deadline = parse_hhmm(schedule.deadline_time)
        if deadline is None:
            return None
        start_t = parse_hhmm(schedule.start_time)
    except ValueError:
        return None

    if start_t is None:
        first_send = now
    else:
        first_send = combine_datetime(today, start_t)
        if first_send < now:
            first_send = now

    deadline_dt = combine_datetime(today, deadline)
    if deadline_dt <= first_send:
        return None

    total_minutes = int((deadline_dt - first_send).total_seconds() // 60)
    return max(1, total_minutes // (schedule.daily_count - 1))


class _SendWorker(QObject):
    finished = Signal(bool, str)

    def __init__(
        self,
        smtp_cfg: SmtpConfig,
        mail_cfg: MailConfig,
        proxy_cfg: Optional[ProxyConfig] = None,
        generation: int = 0,
    ) -> None:
        super().__init__()
        self._smtp_cfg = smtp_cfg
        self._mail_cfg = mail_cfg
        self._proxy_cfg = proxy_cfg
        self.generation = generation

    def run(self) -> None:
        try:
            send_email(self._smtp_cfg, self._mail_cfg, self._proxy_cfg)
            self.finished.emit(True, "")
        except Exception as exc:
            self.finished.emit(False, str(exc))


class MailScheduler(QObject):
    log_message = Signal(str)
    status_changed = Signal(str)
    running_changed = Signal(bool)
    send_finished = Signal(bool, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._config: Optional[AppConfig] = None
        self._running = False
        self._sent_today = 0
        self._current_day = date.today()
        self._next_send_at: Optional[datetime] = None
        self._sending = False
        self._send_started_at: Optional[datetime] = None
        self._send_generation = 0
        self._thread: Optional[QThread] = None
        self._worker: Optional[_SendWorker] = None
        self._pending_result: Optional[tuple[bool, str]] = None

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def sent_today(self) -> int:
        return self._sent_today

    def start(self, config: AppConfig) -> tuple[bool, str]:
        plan = build_daily_plan(config.schedule)
        if not plan.valid:
            return False, plan.message

        self._config = config
        self._running = True
        self._sent_today = 0
        self._current_day = date.today()
        self._next_send_at = plan.first_send

        self._timer.start()
        self.running_changed.emit(True)
        self.status_changed.emit("运行中")
        self.log_message.emit(plan.message)
        self._on_tick()
        return True, plan.message

    def stop(self) -> None:
        self._running = False
        self._timer.stop()
        self._next_send_at = None
        self._shutdown_send_thread()
        self.running_changed.emit(False)
        self.status_changed.emit("已停止")
        self.log_message.emit("调度已停止")

    def _shutdown_send_thread(self, timeout_ms: int = 3000) -> None:
        """在主线程中停止发送线程，避免退出时 QThread 仍在运行。"""
        self._send_generation += 1
        self._pending_result = None
        thread = self._thread
        worker = self._worker
        self._thread = None
        self._worker = None
        self._sending = False

        if thread is None:
            return

        if thread.isRunning():
            # SMTP 阻塞在 socket 时 quit 无效；短等后强制结束
            try:
                thread.finished.disconnect(self._on_thread_finished)
            except (RuntimeError, TypeError):
                pass
            thread.quit()
            if not thread.wait(timeout_ms):
                thread.terminate()
                thread.wait(1000)

        if worker is not None:
            try:
                worker.finished.disconnect(self._on_worker_finished)
            except (RuntimeError, TypeError):
                pass
            worker.deleteLater()
        thread.deleteLater()

    def _schedule_next_send(self, now: datetime) -> None:
        assert self._config is not None
        daily_count = self._config.schedule.daily_count

        if self._sent_today < daily_count:
            self._next_send_at = now + timedelta(
                minutes=self._config.schedule.interval_minutes
            )
            remaining = daily_count - self._sent_today
            projected_last = self._next_send_at + timedelta(
                minutes=self._config.schedule.interval_minutes * (remaining - 1)
            )
            deadline = parse_hhmm(self._config.schedule.deadline_time)
            if deadline:
                deadline_dt = combine_datetime(now.date(), deadline)
                if projected_last > deadline_dt:
                    self.log_message.emit("后续发送将超过最晚时刻，今日任务提前结束")
                    self._sent_today = daily_count
        else:
            self.status_changed.emit(f"今日已完成 {self._sent_today}/{daily_count} 封")

    def _reset_for_new_day(self, now: datetime) -> None:
        assert self._config is not None
        self._current_day = now.date()
        self._sent_today = 0
        plan = build_daily_plan(self._config.schedule, now)
        if plan.valid:
            self._next_send_at = plan.first_send
            self.log_message.emit(f"新的一天：{plan.message}")
        else:
            self.log_message.emit(f"今日计划无效：{plan.message}，等待明日重试")
            tomorrow = now.date() + timedelta(days=1)
            start_t = parse_hhmm(self._config.schedule.start_time)
            if start_t:
                self._next_send_at = combine_datetime(tomorrow, start_t)
            else:
                self._next_send_at = combine_datetime(tomorrow, time(0, 0))

    def _on_tick(self) -> None:
        if not self._running or self._config is None:
            return

        now = datetime.now()
        if now.date() != self._current_day:
            self._reset_for_new_day(now)
            return

        daily_count = self._config.schedule.daily_count
        if self._sent_today >= daily_count:
            tomorrow = now.date() + timedelta(days=1)
            start_t = parse_hhmm(self._config.schedule.start_time)
            if start_t:
                self._next_send_at = combine_datetime(tomorrow, start_t)
            else:
                self._next_send_at = combine_datetime(tomorrow, time(0, 0))
            self.status_changed.emit(
                f"今日已完成 {self._sent_today}/{daily_count} 封，等待明日"
            )
            return

        if self._next_send_at is None:
            return

        if now < self._next_send_at:
            remaining = int((self._next_send_at - now).total_seconds())
            self.status_changed.emit(
                f"等待发送 ({self._sent_today}/{daily_count})，"
                f"下次 {self._next_send_at.strftime('%H:%M:%S')}，"
                f"剩余 {remaining} 秒"
            )
            return

        # 截止时刻保护：若当前已超过 deadline，则跳过今日剩余发送
        deadline = parse_hhmm(self._config.schedule.deadline_time)
        if deadline and now.time() > deadline:
            self.log_message.emit(
                f"当前时间 {now.strftime('%H:%M')} 已超过最晚发送时刻 "
                f"{deadline.strftime('%H:%M')}，今日剩余发送已跳过"
            )
            self._sent_today = daily_count
            return

        if self._sending:
            # 兜底：若 SMTP 异常导致长期无回调，避免界面永久停在「正在发送」
            if (
                self._send_started_at is not None
                and (now - self._send_started_at).total_seconds() > 90
            ):
                self.log_message.emit(
                    f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] "
                    "发送超时未返回结果，请检查 SMTP 设置后重试"
                )
                self._shutdown_send_thread(timeout_ms=1000)
                if self._running:
                    self._schedule_next_send(now)
            return

        self._do_send(now)

    def _do_send(self, now: datetime) -> None:
        assert self._config is not None
        daily_count = self._config.schedule.daily_count

        # 确保不会叠两个发送线程
        if self._thread is not None and self._thread.isRunning():
            return

        self._sending = True
        self._send_started_at = now
        self._send_generation += 1
        generation = self._send_generation
        self._pending_result = None
        self.log_message.emit(
            f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] "
            f"正在发送第 {self._sent_today + 1}/{daily_count} 封..."
        )

        worker = _SendWorker(
            self._config.smtp,
            self._config.mail,
            self._config.proxy,
            generation=generation,
        )
        # 不设置 parent，避免窗口销毁时 QThread 仍在运行被连带析构
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # 必须 QueuedConnection：lambda/无 context 时默认可能在工作线程直连执行
        worker.finished.connect(
            self._on_worker_finished, Qt.ConnectionType.QueuedConnection
        )
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._worker = worker
        self._thread = thread
        thread.start()

    def _on_worker_finished(self, success: bool, error: str) -> None:
        """在主线程接收发送结果；不在此 wait 线程。"""
        worker = self._worker
        if worker is None or worker.generation != self._send_generation:
            return
        self._pending_result = (success, error)

    def _on_thread_finished(self) -> None:
        """线程真正退出后再写日志 / 调度下一封。"""
        if self.sender() is not self._thread:
            return

        result = self._pending_result
        generation_ok = (
            self._worker is not None
            and self._worker.generation == self._send_generation
        )
        self._pending_result = None
        self._thread = None
        self._worker = None
        self._sending = False

        if not generation_ok or result is None or self._config is None:
            return

        success, error = result
        now = self._send_started_at or datetime.now()
        daily_count = self._config.schedule.daily_count

        if success:
            self._sent_today += 1
            msg = (
                f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] "
                f"第 {self._sent_today}/{daily_count} 封发送成功"
            )
            self.log_message.emit(msg)
            self.send_finished.emit(True, msg)
        else:
            msg = f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] 发送失败：{error}"
            self.log_message.emit(msg)
            self.send_finished.emit(False, msg)

        if not self._running:
            return

        self._schedule_next_send(now)
