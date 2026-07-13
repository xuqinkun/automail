"""主窗口界面"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig, load_config, save_config
from app.scheduler import MailScheduler, build_daily_plan, calc_interval_minutes


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

        # 左侧：SMTP + 发送计划
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

        schedule_box = QGroupBox("发送计划")
        schedule_form = QFormLayout(schedule_box)

        count_row = QHBoxLayout()
        self.schedule_count = QSpinBox()
        self.schedule_count.setRange(1, 999)
        self.schedule_count.setValue(3)
        self.schedule_count.valueChanged.connect(self._on_schedule_changed)
        
        count_row.addWidget(self.schedule_count)        
        count_row.addStretch()
        schedule_form.addRow("每天次数", count_row)

        interval_row = QHBoxLayout()
        self.schedule_interval = QSpinBox()
        self.schedule_interval.setRange(1, 1440)
        self.schedule_interval.setValue(30)
        self.schedule_interval.setEnabled(False)
        interval_unit = QLabel("分钟")
        interval_unit.setStyleSheet("color: #666;")
        interval_row.addWidget(self.schedule_interval)
        interval_row.addWidget(interval_unit)
        interval_row.addStretch()
        schedule_form.addRow("发送间隔", interval_row)

        self.schedule_deadline = QLineEdit()
        self.schedule_deadline.setPlaceholderText("23:59")
        self.schedule_deadline.setText("23:59")
        self.schedule_deadline.editingFinished.connect(self._on_schedule_changed)
        schedule_form.addRow("最晚时刻", self.schedule_deadline)

        self.schedule_start = QLineEdit()
        self.schedule_start.setPlaceholderText("留空按当前时间")
        self.schedule_start.editingFinished.connect(self._on_schedule_changed)
        schedule_form.addRow("开始时刻", self.schedule_start)

        schedule_hint = QLabel("根据起止时间和发送次数自动计算间隔；开始留空按当前时间。")
        schedule_hint.setWordWrap(True)
        schedule_hint.setStyleSheet("color: #666; font-size: 12px;")
        schedule_form.addRow(schedule_hint)
        settings_col.addWidget(schedule_box)

        self.plan_label = QLabel()
        self.plan_label.setWordWrap(True)
        self.plan_label.setStyleSheet(
            "QLabel { background: #f0f4f8; border-radius: 6px; padding: 10px; }"
        )
        settings_col.addWidget(self.plan_label)

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

        send_row = QHBoxLayout()
        send_row.addStretch()
        self.send_btn = QPushButton("开始自动发送")
        self.send_btn.setStyleSheet(self._start_btn_style())
        self.send_btn.clicked.connect(self._on_toggle_schedule)
        send_row.addWidget(self.send_btn)
        mail_box_layout.addLayout(send_row)

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

        self.mail_to.setText(c.mail.recipients)
        self.mail_cc.setText(c.mail.cc)
        self.mail_subject.setText(c.mail.subject)
        self.mail_body.setPlainText(c.mail.body)
        self.mail_html.setChecked(c.mail.is_html)

        self.schedule_count.setValue(c.schedule.daily_count)
        self.schedule_interval.setValue(c.schedule.interval_minutes)
        self.schedule_deadline.setText(c.schedule.deadline_time)
        self.schedule_start.setText(c.schedule.start_time)

    def _collect_config(self) -> AppConfig:
        self._config.smtp.host = self.smtp_host.text().strip()
        self._config.smtp.port = self.smtp_port.value()
        self._config.smtp.use_ssl = self.smtp_ssl.isChecked()
        self._config.smtp.username = self.smtp_user.text().strip()
        self._config.smtp.password = self.smtp_pass.text()
        self._config.smtp.sender = self.smtp_sender.text().strip()

        self._config.mail.recipients = self.mail_to.text().strip()
        self._config.mail.cc = self.mail_cc.text().strip()
        self._config.mail.subject = self.mail_subject.text().strip()
        self._config.mail.body = self.mail_body.toPlainText()
        self._config.mail.is_html = self.mail_html.isChecked()

        self._config.schedule.daily_count = self.schedule_count.value()
        self._config.schedule.interval_minutes = self.schedule_interval.value()
        self._config.schedule.deadline_time = self.schedule_deadline.text().strip()
        self._config.schedule.start_time = self.schedule_start.text().strip()

        return self._config

    def _current_schedule(self):
        from app.config import ScheduleConfig

        return ScheduleConfig(
            daily_count=self.schedule_count.value(),
            interval_minutes=self.schedule_interval.value(),
            deadline_time=self.schedule_deadline.text().strip(),
            start_time=self.schedule_start.text().strip(),
        )

    def _auto_calc_interval(self) -> None:
        interval = calc_interval_minutes(self._current_schedule())
        if interval is not None:
            self.schedule_interval.blockSignals(True)
            self.schedule_interval.setValue(interval)
            self.schedule_interval.blockSignals(False)

    def _on_schedule_changed(self) -> None:
        self._auto_calc_interval()
        self._refresh_plan_preview()

    def _refresh_plan_preview(self) -> None:
        cfg = self._collect_config()
        plan = build_daily_plan(cfg.schedule)
        if plan.valid:
            self.plan_label.setText(f"✅ {plan.message}")
            self.plan_label.setStyleSheet(
                "QLabel { background: #e8f5e9; border-radius: 6px; padding: 10px; color: #2e7d32; }"
            )
        else:
            self.plan_label.setText(f"⚠️ {plan.message}")
            self.plan_label.setStyleSheet(
                "QLabel { background: #fff3e0; border-radius: 6px; padding: 10px; color: #e65100; }"
            )

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
