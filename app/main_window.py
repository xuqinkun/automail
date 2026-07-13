"""主窗口界面"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTime, QTimer, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig, load_config, save_config
from app.scheduler import MailScheduler, build_daily_plan, calc_interval_minutes


class ClickableLineEdit(QLineEdit):
    """只读输入框，点击后弹出选择器。"""

    clicked = Signal()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class TimePickDialog(QDialog):
    """HH:MM 时间选择对话框。"""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        initial: str = "",
        *,
        allow_empty: bool = False,
        empty_label: str = "立即",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self._allow_empty = allow_empty
        self._empty_label = empty_label
        self._result = initial.strip()

        layout = QVBoxLayout(self)
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setTime(self._parse_time(initial) or QTime.currentTime())
        layout.addWidget(self.time_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        if allow_empty:
            clear_btn = buttons.addButton(
                empty_label, QDialogButtonBox.ButtonRole.ResetRole
            )
            clear_btn.clicked.connect(self._on_clear)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _parse_time(value: str) -> QTime | None:
        text = value.strip()
        if not text:
            return None
        parsed = QTime.fromString(text, "HH:mm")
        return parsed if parsed.isValid() else None

    def _on_clear(self) -> None:
        self._result = ""
        self.accept()

    def _on_accept(self) -> None:
        self._result = self.time_edit.time().toString("HH:mm")
        self.accept()

    def selected_time(self) -> str:
        return self._result


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AutoMail - 自动发送邮件")
        self.setMinimumSize(1100, 640)
        self.resize(1200, 720)

        self._config = load_config()
        self._scheduler = MailScheduler(self)

        self._build_ui()
        self._load_to_form()
        self._auto_calc_interval()
        self._connect_signals()
        self._refresh_plan_preview()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(12)
        root.setContentsMargins(16, 16, 16, 16)

        main_row = QHBoxLayout()
        main_row.setSpacing(12)

        # 左侧：SMTP + 代理
        settings_col = QVBoxLayout()
        settings_col.setSpacing(12)

        smtp_box = QGroupBox("SMTP 设置")
        smtp_form = QFormLayout(smtp_box)
        smtp_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.smtp_host = QLineEdit()
        self.smtp_host.setPlaceholderText("例如 smtp.qq.com")
        smtp_form.addRow("服务器", self.smtp_host)

        port_row = QHBoxLayout()
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(465)
        self.smtp_ssl = QCheckBox("SSL")
        self.smtp_ssl.setChecked(True)
        port_row.addWidget(self.smtp_port)
        port_row.addWidget(self.smtp_ssl)
        port_row.addStretch()
        smtp_form.addRow("端口", port_row)

        self.smtp_user = QLineEdit()
        self.smtp_user.setPlaceholderText("登录用户名 / 邮箱")
        smtp_form.addRow("用户名", self.smtp_user)

        self.smtp_pass = QLineEdit()
        self.smtp_pass.setEchoMode(QLineEdit.EchoMode.Password)
        smtp_form.addRow("密码", self.smtp_pass)

        self.smtp_sender = QLineEdit()
        self.smtp_sender.setPlaceholderText("留空则使用用户名")
        smtp_form.addRow("发件人", self.smtp_sender)

        smtp_hint = QLabel("提示：需使用授权码而非登录密码。")
        smtp_hint.setWordWrap(True)
        smtp_hint.setStyleSheet("color: #666; font-size: 12px;")
        smtp_form.addRow(smtp_hint)
        settings_col.addWidget(smtp_box)

        proxy_box = QGroupBox("本地代理")
        proxy_form = QFormLayout(proxy_box)
        proxy_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.proxy_enabled = QCheckBox("启用代理")
        self.proxy_enabled.toggled.connect(self._on_proxy_toggled)
        proxy_form.addRow("", self.proxy_enabled)

        self.proxy_type = QComboBox()
        self.proxy_type.addItem("SOCKS5", "socks5")
        self.proxy_type.addItem("HTTP", "http")
        self.proxy_type.addItem("SOCKS4", "socks4")
        proxy_form.addRow("类型", self.proxy_type)

        self.proxy_host = QLineEdit()
        self.proxy_host.setPlaceholderText("127.0.0.1")
        self.proxy_host.setText("127.0.0.1")
        proxy_form.addRow("地址", self.proxy_host)

        self.proxy_port = QSpinBox()
        self.proxy_port.setRange(1, 65535)
        self.proxy_port.setValue(7890)
        proxy_form.addRow("端口", self.proxy_port)

        self.proxy_user = QLineEdit()
        self.proxy_user.setPlaceholderText("可选")
        proxy_form.addRow("用户名", self.proxy_user)

        self.proxy_pass = QLineEdit()
        self.proxy_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.proxy_pass.setPlaceholderText("可选")
        proxy_form.addRow("密码", self.proxy_pass)

        proxy_hint = QLabel(
            "建议 SOCKS5（Clash 7890）。Gmail 经代理请用 587 且不要勾选 SSL；"
            "勾选 465+SSL 失败时会自动回退 587。"
        )
        proxy_hint.setWordWrap(True)
        proxy_hint.setStyleSheet("color: #666; font-size: 12px;")
        proxy_form.addRow(proxy_hint)
        settings_col.addWidget(proxy_box)

        save_row = QHBoxLayout()
        self.save_btn = QPushButton("保存配置")
        self.save_btn.clicked.connect(self._on_save)
        self.save_status_label = QLabel("")
        self.save_status_label.setStyleSheet("color: #2e7d32; font-size: 12px;")
        save_row.addWidget(self.save_btn)
        save_row.addWidget(self.save_status_label)
        save_row.addStretch()
        settings_col.addLayout(save_row)
        settings_col.addStretch()

        settings_widget = QWidget()
        settings_widget.setLayout(settings_col)
        settings_widget.setMinimumWidth(280)
        settings_widget.setMaximumWidth(320)

        # 中间：邮件内容
        mail_col = QVBoxLayout()
        mail_col.setSpacing(8)

        mail_box = QGroupBox("邮件内容")
        mail_box_layout = QVBoxLayout(mail_box)
        mail_box_layout.setSpacing(8)

        mail_form = QFormLayout()
        mail_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.mail_to = QLineEdit()
        self.mail_to.setPlaceholderText("多个收件人用逗号分隔")
        mail_form.addRow("收件人", self.mail_to)

        self.mail_cc = QLineEdit()
        self.mail_cc.setPlaceholderText("可选")
        mail_form.addRow("抄送", self.mail_cc)

        self.mail_subject = QLineEdit()
        mail_form.addRow("主题", self.mail_subject)

        self.mail_html = QCheckBox("HTML 格式正文")
        mail_form.addRow("", self.mail_html)

        mail_box_layout.addLayout(mail_form)

        # 发送计划参数 + 开始按钮同一行
        send_row = QHBoxLayout()
        send_row.setSpacing(6)
        send_row.setContentsMargins(0, 0, 0, 0)

        lbl_count = QLabel("次数")
        lbl_count.setStyleSheet("color: #555;")
        self.schedule_count = QSpinBox()
        self.schedule_count.setRange(1, 999)
        self.schedule_count.setValue(3)
        self.schedule_count.setFixedWidth(64)
        self.schedule_count.setToolTip("每天发送次数")
        self.schedule_count.valueChanged.connect(self._on_schedule_changed)
        send_row.addWidget(lbl_count)
        send_row.addWidget(self.schedule_count)

        lbl_interval = QLabel("间隔")
        lbl_interval.setStyleSheet("color: #555;")
        self.schedule_interval = QSpinBox()
        self.schedule_interval.setRange(1, 1440)
        self.schedule_interval.setValue(30)
        self.schedule_interval.setFixedWidth(64)
        self.schedule_interval.setToolTip("发送间隔（分钟），可手动修改")
        self.schedule_interval.valueChanged.connect(self._on_interval_changed)
        lbl_interval_unit = QLabel("分")
        lbl_interval_unit.setStyleSheet("color: #888;")
        send_row.addWidget(lbl_interval)
        send_row.addWidget(self.schedule_interval)
        send_row.addWidget(lbl_interval_unit)

        lbl_deadline = QLabel("最晚")
        lbl_deadline.setStyleSheet("color: #555;")
        self.schedule_deadline = ClickableLineEdit()
        self.schedule_deadline.setReadOnly(True)
        self.schedule_deadline.setPlaceholderText("23:59")
        self.schedule_deadline.setText("23:59")
        self.schedule_deadline.setFixedWidth(56)
        self.schedule_deadline.setCursor(Qt.CursorShape.PointingHandCursor)
        self.schedule_deadline.setToolTip("点击选择最晚发送时刻")
        self.schedule_deadline.clicked.connect(self._on_pick_deadline)
        send_row.addWidget(lbl_deadline)
        send_row.addWidget(self.schedule_deadline)

        lbl_start = QLabel("开始")
        lbl_start.setStyleSheet("color: #555;")
        self.schedule_start = ClickableLineEdit()
        self.schedule_start.setReadOnly(True)
        self.schedule_start.setPlaceholderText("立即")
        self.schedule_start.setFixedWidth(56)
        self.schedule_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.schedule_start.setToolTip("点击选择开始时刻，可设为立即")
        self.schedule_start.clicked.connect(self._on_pick_start)
        send_row.addWidget(lbl_start)
        send_row.addWidget(self.schedule_start)

        send_row.addStretch(1)

        self.send_btn = QPushButton("开始自动发送")
        self.send_btn.setStyleSheet(self._start_btn_style())
        self.send_btn.clicked.connect(self._on_toggle_schedule)
        send_row.addWidget(self.send_btn)
        mail_box_layout.addLayout(send_row)

        self.plan_label = QLabel()
        self.plan_label.setWordWrap(True)
        self.plan_label.setStyleSheet(
            "QLabel { background: #f0f4f8; border-radius: 4px; padding: 6px 8px; font-size: 12px; }"
        )
        mail_box_layout.addWidget(self.plan_label)

        self.mail_body = QTextEdit()
        self.mail_body.setPlaceholderText("邮件正文...")
        mail_box_layout.addWidget(self.mail_body, stretch=1)

        mail_col.addWidget(mail_box)

        mail_widget = QWidget()
        mail_widget.setLayout(mail_col)

        # 右侧：发送日志
        log_box = QGroupBox("发送日志")
        log_layout = QVBoxLayout(log_box)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        log_layout.addWidget(self.log_view)
        log_box.setMinimumWidth(240)

        main_row.addWidget(settings_widget)
        main_row.addWidget(mail_widget, stretch=3)
        main_row.addWidget(log_box, stretch=1)
        root.addLayout(main_row, stretch=1)

    def _start_btn_style(self) -> str:
        return (
            "QPushButton { background: #007aff; color: white; padding: 8px 20px; "
            "border-radius: 6px; font-weight: bold; }"
            "QPushButton:hover { background: #0066d6; }"
        )

    def _stop_btn_style(self) -> str:
        return (
            "QPushButton { background: #ff3b30; color: white; padding: 8px 20px; "
            "border-radius: 6px; font-weight: bold; }"
            "QPushButton:hover { background: #d70015; }"
        )

    def _connect_signals(self) -> None:
        self._scheduler.log_message.connect(self._append_log)
        self._scheduler.running_changed.connect(self._on_running_changed)

    def _load_to_form(self) -> None:
        c = self._config
        self.smtp_host.setText(c.smtp.host)
        self.smtp_port.setValue(c.smtp.port)
        self.smtp_ssl.setChecked(c.smtp.use_ssl)
        self.smtp_user.setText(c.smtp.username)
        self.smtp_pass.setText(c.smtp.password)
        self.smtp_sender.setText(c.smtp.sender)

        self.proxy_enabled.setChecked(c.proxy.enabled)
        idx = self.proxy_type.findData(c.proxy.proxy_type)
        self.proxy_type.setCurrentIndex(idx if idx >= 0 else 0)
        self.proxy_host.setText(c.proxy.host or "127.0.0.1")
        self.proxy_port.setValue(c.proxy.port or 7890)
        self.proxy_user.setText(c.proxy.username)
        self.proxy_pass.setText(c.proxy.password)
        self._on_proxy_toggled(c.proxy.enabled)

        self.mail_to.setText(c.mail.recipients)
        self.mail_cc.setText(c.mail.cc)
        self.mail_subject.setText(c.mail.subject)
        self.mail_body.setPlainText(c.mail.body)
        self.mail_html.setChecked(c.mail.is_html)

        self.schedule_count.setValue(c.schedule.daily_count)
        self.schedule_interval.blockSignals(True)
        self.schedule_interval.setValue(c.schedule.interval_minutes)
        self.schedule_interval.blockSignals(False)
        self.schedule_deadline.setText(c.schedule.deadline_time or "23:59")
        self._set_start_display(c.schedule.start_time)

    def _set_start_display(self, start_time: str) -> None:
        text = start_time.strip()
        self.schedule_start.setText(text if text else "")
        self.schedule_start.setPlaceholderText("立即")

    def _start_time_value(self) -> str:
        return self.schedule_start.text().strip()

    def _collect_config(self) -> AppConfig:
        self._config.smtp.host = self.smtp_host.text().strip()
        self._config.smtp.port = self.smtp_port.value()
        self._config.smtp.use_ssl = self.smtp_ssl.isChecked()
        self._config.smtp.username = self.smtp_user.text().strip()
        self._config.smtp.password = self.smtp_pass.text()
        self._config.smtp.sender = self.smtp_sender.text().strip()

        self._config.proxy.enabled = self.proxy_enabled.isChecked()
        self._config.proxy.proxy_type = str(self.proxy_type.currentData() or "socks5")
        self._config.proxy.host = self.proxy_host.text().strip() or "127.0.0.1"
        self._config.proxy.port = self.proxy_port.value()
        self._config.proxy.username = self.proxy_user.text().strip()
        self._config.proxy.password = self.proxy_pass.text()

        self._config.mail.recipients = self.mail_to.text().strip()
        self._config.mail.cc = self.mail_cc.text().strip()
        self._config.mail.subject = self.mail_subject.text().strip()
        self._config.mail.body = self.mail_body.toPlainText()
        self._config.mail.is_html = self.mail_html.isChecked()

        self._config.schedule.daily_count = self.schedule_count.value()
        self._config.schedule.interval_minutes = self.schedule_interval.value()
        self._config.schedule.deadline_time = self.schedule_deadline.text().strip()
        self._config.schedule.start_time = self._start_time_value()

        return self._config

    def _current_schedule(self):
        from app.config import ScheduleConfig

        return ScheduleConfig(
            daily_count=self.schedule_count.value(),
            interval_minutes=self.schedule_interval.value(),
            deadline_time=self.schedule_deadline.text().strip(),
            start_time=self._start_time_value(),
        )

    def _auto_calc_interval(self) -> None:
        interval = calc_interval_minutes(self._current_schedule())
        if interval is not None:
            self.schedule_interval.blockSignals(True)
            self.schedule_interval.setValue(interval)
            self.schedule_interval.blockSignals(False)

    def _on_schedule_changed(self) -> None:
        # 次数 / 起止时刻变化时自动估算间隔，仍可再手动改
        self._auto_calc_interval()
        self._refresh_plan_preview()

    def _on_interval_changed(self) -> None:
        self._refresh_plan_preview()

    def _on_pick_deadline(self) -> None:
        dlg = TimePickDialog(
            self,
            "选择最晚发送时刻",
            self.schedule_deadline.text(),
            allow_empty=False,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.schedule_deadline.setText(dlg.selected_time() or "23:59")
            self._on_schedule_changed()

    def _on_pick_start(self) -> None:
        dlg = TimePickDialog(
            self,
            "选择开始发送时刻",
            self._start_time_value(),
            allow_empty=True,
            empty_label="立即",
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._set_start_display(dlg.selected_time())
            self._on_schedule_changed()

    def _refresh_plan_preview(self) -> None:
        cfg = self._collect_config()
        plan = build_daily_plan(cfg.schedule)
        if plan.valid:
            self.plan_label.setText(f"✅ {plan.message}")
            self.plan_label.setStyleSheet(
                "QLabel { background: #e8f5e9; border-radius: 4px; padding: 6px 8px; "
                "font-size: 12px; color: #2e7d32; }"
            )
        else:
            self.plan_label.setText(f"⚠️ {plan.message}")
            self.plan_label.setStyleSheet(
                "QLabel { background: #fff3e0; border-radius: 4px; padding: 6px 8px; "
                "font-size: 12px; color: #e65100; }"
            )

    def _on_proxy_toggled(self, enabled: bool) -> None:
        for widget in (
            self.proxy_type,
            self.proxy_host,
            self.proxy_port,
            self.proxy_user,
            self.proxy_pass,
        ):
            widget.setEnabled(enabled)

    def _on_save(self) -> None:
        cfg = self._collect_config()
        save_config(cfg)
        self.save_status_label.setText("✅ 配置已保存")
        QTimer.singleShot(3000, self.save_status_label.clear)

    def _append_log(self, text: str) -> None:
        self.log_view.append(text)

    def _on_running_changed(self, running: bool) -> None:
        if running:
            self.send_btn.setText("停止自动发送")
            self.send_btn.setStyleSheet(self._stop_btn_style())
            self.save_btn.setEnabled(False)
        else:
            self.send_btn.setText("开始自动发送")
            self.send_btn.setStyleSheet(self._start_btn_style())
            self.save_btn.setEnabled(True)

    def _on_toggle_schedule(self) -> None:
        if self._scheduler.is_running:
            self._scheduler.stop()
            return

        cfg = self._collect_config()
        if not cfg.smtp.host:
            QMessageBox.warning(self, "提示", "请填写 SMTP 服务器")
            return
        if cfg.proxy.enabled and not cfg.proxy.host:
            QMessageBox.warning(self, "提示", "已启用代理，请填写代理地址")
            return
        if not cfg.mail.recipients:
            QMessageBox.warning(self, "提示", "请填写收件人")
            return

        save_config(cfg)

        plan = build_daily_plan(cfg.schedule)
        if not plan.valid:
            QMessageBox.warning(self, "计划无效", plan.message)
            return

        ok, msg = self._scheduler.start(cfg)
        if not ok:
            QMessageBox.warning(self, "无法启动", msg)

    def closeEvent(self, event) -> None:
        if self._scheduler.is_running:
            reply = QMessageBox.question(
                self,
                "确认退出",
                "自动发送正在运行，退出将停止调度。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._scheduler.stop()
        save_config(self._collect_config())
        event.accept()


def run_app() -> None:
    import sys

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("AutoMail")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
