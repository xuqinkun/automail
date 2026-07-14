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
    QFileDialog,
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
from app.gmail_api import (
    authorize_gmail,
    credentials_imported,
    import_credentials,
    is_gmail_account,
    resolve_sender,
    token_authorized,
    validate_gmail_ready,
)
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
        self._form_locked = False
        self._loading_form = False
        self._autosave_ready = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(800)
        self._autosave_timer.timeout.connect(self._autosave_config)

        self._build_ui()
        self._load_to_form()
        self._auto_calc_interval()
        self._connect_signals()
        self._connect_autosave()
        self._refresh_plan_preview()
        self._refresh_gmail_status()
        # 延迟开启自动保存，避免启动加载过程中误把空表单写回磁盘
        QTimer.singleShot(1500, self._enable_autosave)

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
        self.smtp_host.setPlaceholderText("例如 smtp.qq.com / smtp.gmail.com")
        self.smtp_host.textChanged.connect(self._refresh_gmail_status)
        smtp_form.addRow("服务器", self.smtp_host)

        port_row = QHBoxLayout()
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(465)
        self.smtp_ssl = QCheckBox("SSL")
        self.smtp_ssl.setChecked(False)
        port_row.addWidget(self.smtp_port)
        port_row.addWidget(self.smtp_ssl)
        port_row.addStretch()
        smtp_form.addRow("端口", port_row)

        self.smtp_user = QLineEdit()
        self.smtp_user.setPlaceholderText("登录用户名 / 邮箱")
        self.smtp_user.textChanged.connect(self._refresh_gmail_status)
        smtp_form.addRow("用户名", self.smtp_user)

        self.smtp_pass = QLineEdit()
        self.smtp_pass.setEchoMode(QLineEdit.EchoMode.Password)
        smtp_form.addRow("密码", self.smtp_pass)

        self.smtp_sender = QLineEdit()
        self.smtp_sender.setPlaceholderText("留空则使用用户名")
        self.smtp_sender.textChanged.connect(self._refresh_gmail_status)
        smtp_form.addRow("发件人", self.smtp_sender)

        smtp_hint = QLabel("提示：非 Gmail 需使用授权码；Gmail 请导入凭证并用 API 发送。")
        smtp_hint.setWordWrap(True)
        smtp_hint.setStyleSheet("color: #666; font-size: 12px;")
        smtp_form.addRow(smtp_hint)
        settings_col.addWidget(smtp_box)

        gmail_box = QGroupBox("Gmail API")
        gmail_form = QFormLayout(gmail_box)

        self.gmail_status_label = QLabel()
        self.gmail_status_label.setWordWrap(True)
        self.gmail_status_label.setStyleSheet("font-size: 12px;")
        gmail_form.addRow("状态", self.gmail_status_label)

        gmail_btn_row = QHBoxLayout()
        self.gmail_import_btn = QPushButton("导入凭证")
        self.gmail_import_btn.setToolTip("选择 Google Cloud 下载的 credentials.json")
        self.gmail_import_btn.clicked.connect(self._on_import_gmail_credentials)
        self.gmail_auth_btn = QPushButton("授权 Gmail")
        self.gmail_auth_btn.setToolTip("打开浏览器完成 OAuth 授权")
        self.gmail_auth_btn.clicked.connect(self._on_authorize_gmail)
        gmail_btn_row.addWidget(self.gmail_import_btn)
        gmail_btn_row.addWidget(self.gmail_auth_btn)
        gmail_form.addRow(gmail_btn_row)

        gmail_hint = QLabel(
            "仅当用户名/发件人为 @gmail.com，或 SMTP 为 smtp.gmail.com 时走 Gmail API。"
        )
        gmail_hint.setWordWrap(True)
        gmail_hint.setStyleSheet("color: #666; font-size: 12px;")
        gmail_form.addRow(gmail_hint)
        settings_col.addWidget(gmail_box)

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
            "建议 SOCKS5（Clash 7890）。Gmail API 与 SMTP 均可走代理；"
            "SMTP 勾选 465+SSL 失败时会自动回退 587。"
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

    def _connect_autosave(self) -> None:
        """表单变动后自动写入 ~/.automail/config.json，下次启动可恢复。"""
        text_widgets = (
            self.smtp_host,
            self.smtp_user,
            self.smtp_pass,
            self.smtp_sender,
            self.proxy_host,
            self.proxy_user,
            self.proxy_pass,
            self.mail_to,
            self.mail_cc,
            self.mail_subject,
            self.schedule_deadline,
            self.schedule_start,
        )
        for widget in text_widgets:
            widget.textChanged.connect(self._schedule_autosave)

        self.mail_body.textChanged.connect(self._schedule_autosave)

        for widget in (
            self.smtp_port,
            self.proxy_port,
            self.schedule_count,
            self.schedule_interval,
        ):
            widget.valueChanged.connect(self._schedule_autosave)

        for widget in (self.smtp_ssl, self.proxy_enabled, self.mail_html):
            widget.toggled.connect(self._schedule_autosave)

        self.proxy_type.currentIndexChanged.connect(self._schedule_autosave)

    def _schedule_autosave(self, *_args) -> None:
        if not self._autosave_ready or self._loading_form or self._form_locked:
            return
        self._autosave_timer.start()

    def _enable_autosave(self) -> None:
        self._autosave_ready = True

    def _autosave_config(self) -> None:
        if not self._autosave_ready or self._loading_form or self._form_locked:
            return
        try:
            save_config(self._collect_config())
            self.save_status_label.setStyleSheet("color: #2e7d32; font-size: 12px;")
            self.save_status_label.setText("已自动保存")
            QTimer.singleShot(2000, self.save_status_label.clear)
        except OSError:
            self.save_status_label.setStyleSheet("color: #e65100; font-size: 12px;")
            self.save_status_label.setText("自动保存失败")
            QTimer.singleShot(3000, self._reset_save_status)

    def _reset_save_status(self) -> None:
        self.save_status_label.clear()
        self.save_status_label.setStyleSheet("color: #2e7d32; font-size: 12px;")

    def _load_to_form(self) -> None:
        self._loading_form = True
        widgets = (
            self.smtp_host,
            self.smtp_port,
            self.smtp_ssl,
            self.smtp_user,
            self.smtp_pass,
            self.smtp_sender,
            self.proxy_enabled,
            self.proxy_type,
            self.proxy_host,
            self.proxy_port,
            self.proxy_user,
            self.proxy_pass,
            self.mail_to,
            self.mail_cc,
            self.mail_subject,
            self.mail_html,
            self.mail_body,
            self.schedule_count,
            self.schedule_interval,
            self.schedule_deadline,
            self.schedule_start,
        )
        for widget in widgets:
            widget.blockSignals(True)
        try:
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

            self.mail_to.setText(c.mail.recipients)
            self.mail_cc.setText(c.mail.cc)
            self.mail_subject.setText(c.mail.subject)
            self.mail_body.setPlainText(c.mail.body)
            self.mail_html.setChecked(c.mail.is_html)

            self.schedule_count.setValue(c.schedule.daily_count)
            self.schedule_interval.setValue(c.schedule.interval_minutes)
            self.schedule_deadline.setText(c.schedule.deadline_time or "23:59")
            self._set_start_display(c.schedule.start_time)
        finally:
            for widget in widgets:
                widget.blockSignals(False)
            self._on_proxy_toggled(self.proxy_enabled.isChecked())
            self._loading_form = False

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
        if getattr(self, "_form_locked", False):
            return
        dlg = TimePickDialog(
            self,
            "选择最晚发送时刻",
            self.schedule_deadline.text(),
            allow_empty=False,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.schedule_deadline.setText(dlg.selected_time() or "23:59")
            self._on_schedule_changed()
            self._schedule_autosave()

    def _on_pick_start(self) -> None:
        if getattr(self, "_form_locked", False):
            return
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
            self._schedule_autosave()

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

    def _refresh_gmail_status(self) -> None:
        if self._loading_form or getattr(self, "_form_locked", False):
            return
        cfg = self._collect_config()
        if not is_gmail_account(cfg.smtp):
            self.gmail_status_label.setText("当前非 Gmail，将使用 SMTP")
            self.gmail_status_label.setStyleSheet("color: #666; font-size: 12px;")
            self.gmail_auth_btn.setEnabled(False)
            return

        self.gmail_auth_btn.setEnabled(True)
        if not resolve_sender(cfg.smtp):
            self.gmail_status_label.setText("⚠️ 请填写用户名或发件人（Gmail 地址）")
            self.gmail_status_label.setStyleSheet("color: #e65100; font-size: 12px;")
        elif not credentials_imported():
            self.gmail_status_label.setText("⚠️ 未导入 credentials.json")
            self.gmail_status_label.setStyleSheet("color: #e65100; font-size: 12px;")
        elif token_authorized():
            self.gmail_status_label.setText("✅ 已授权，将使用 Gmail API 发送")
            self.gmail_status_label.setStyleSheet("color: #2e7d32; font-size: 12px;")
        else:
            self.gmail_status_label.setText("⚠️ 已导入凭证，请点击「授权 Gmail」")
            self.gmail_status_label.setStyleSheet("color: #e65100; font-size: 12px;")

    def _on_import_gmail_credentials(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 credentials.json",
            "",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            import_credentials(path)
            self.save_status_label.setText("✅ 凭证已导入")
            QTimer.singleShot(3000, self.save_status_label.clear)
            self._refresh_gmail_status()
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", str(exc))

    def _on_authorize_gmail(self) -> None:
        cfg = self._collect_config()
        if not credentials_imported():
            QMessageBox.warning(self, "提示", "请先导入 credentials.json")
            return
        try:
            self.gmail_status_label.setText("正在打开浏览器授权…")
            QApplication.processEvents()
            authorize_gmail(cfg.proxy)
            self._refresh_gmail_status()
            self.save_status_label.setText("✅ Gmail 授权成功")
            QTimer.singleShot(3000, self.save_status_label.clear)
        except Exception as exc:
            self._refresh_gmail_status()
            QMessageBox.critical(self, "授权失败", str(exc))

    def _on_proxy_toggled(self, enabled: bool) -> None:
        if getattr(self, "_form_locked", False):
            return
        for widget in (
            self.proxy_type,
            self.proxy_host,
            self.proxy_port,
            self.proxy_user,
            self.proxy_pass,
        ):
            widget.setEnabled(enabled)

    def _set_form_locked(self, locked: bool) -> None:
        """自动发送运行期间锁定配置与邮件内容，仅保留停止按钮与日志。"""
        self._form_locked = locked
        editable = not locked

        for widget in (
            self.smtp_host,
            self.smtp_port,
            self.smtp_ssl,
            self.smtp_user,
            self.smtp_pass,
            self.smtp_sender,
            self.gmail_import_btn,
            self.gmail_auth_btn,
            self.proxy_enabled,
            self.save_btn,
            self.mail_to,
            self.mail_cc,
            self.mail_subject,
            self.mail_html,
            self.mail_body,
            self.schedule_count,
            self.schedule_interval,
            self.schedule_deadline,
            self.schedule_start,
        ):
            widget.setEnabled(editable)

        if editable:
            self._on_proxy_toggled(self.proxy_enabled.isChecked())
            self._refresh_gmail_status()
        else:
            for widget in (
                self.proxy_type,
                self.proxy_host,
                self.proxy_port,
                self.proxy_user,
                self.proxy_pass,
            ):
                widget.setEnabled(False)

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
        else:
            self.send_btn.setText("开始自动发送")
            self.send_btn.setStyleSheet(self._start_btn_style())
        self._set_form_locked(running)

    def _on_toggle_schedule(self) -> None:
        if self._scheduler.is_running:
            self._scheduler.stop()
            return

        cfg = self._collect_config()
        if is_gmail_account(cfg.smtp):
            gmail_error = validate_gmail_ready(cfg.smtp)
            if gmail_error:
                QMessageBox.warning(self, "提示", gmail_error)
                return
        elif not cfg.smtp.host:
            QMessageBox.warning(self, "提示", "请填写 SMTP 服务器")
            return
        elif not resolve_sender(cfg.smtp):
            QMessageBox.warning(self, "提示", "请填写发件人邮箱或用户名")
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
        self._autosave_timer.stop()
        try:
            save_config(self._collect_config())
        except OSError:
            pass
        event.accept()


def run_app() -> None:
    import sys

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("AutoMail")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
