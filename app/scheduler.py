"""邮件发送调度器"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from app.config import AppConfig, ScheduleConfig
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
            # 今天的开始时间已过，从当前时刻起算
            first_send = now

    last_send = first_send + timedelta(minutes=schedule.interval_minutes * (schedule.daily_count - 1))
    deadline_dt = combine_datetime(today, deadline)

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

    return SchedulePlan(
        first_send,
        last_send,
        True,
        (
            f"今日计划：首封 {first_send.strftime('%H:%M')}，"
            f"末封 {last_send.strftime('%H:%M')}，共 {schedule.daily_count} 封"
        ),
    )


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
        self.running_changed.emit(False)
        self.status_changed.emit("已停止")
        self.log_message.emit("调度已停止")

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

        self._do_send(now)

    def _do_send(self, now: datetime) -> None:
        assert self._config is not None
        daily_count = self._config.schedule.daily_count

        try:
            send_email(self._config.smtp, self._config.mail)
            self._sent_today += 1
            msg = (
                f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] "
                f"第 {self._sent_today}/{daily_count} 封发送成功"
            )
            self.log_message.emit(msg)
            self.send_finished.emit(True, msg)
        except Exception as exc:
            msg = f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] 发送失败：{exc}"
            self.log_message.emit(msg)
            self.send_finished.emit(False, msg)

        if self._sent_today < daily_count:
            self._next_send_at = now + timedelta(minutes=self._config.schedule.interval_minutes)
            # 再次校验末封时间
            remaining = daily_count - self._sent_today
            projected_last = self._next_send_at + timedelta(
                minutes=self._config.schedule.interval_minutes * (remaining - 1)
            )
            deadline = parse_hhmm(self._config.schedule.deadline_time)
            if deadline:
                deadline_dt = combine_datetime(now.date(), deadline)
                if projected_last > deadline_dt:
                    self.log_message.emit(
                        "后续发送将超过最晚时刻，今日任务提前结束"
                    )
                    self._sent_today = daily_count
        else:
            self.status_changed.emit(f"今日已完成 {self._sent_today}/{daily_count} 封")
