"""主窗口界面"""

from __future__ import annotations

from datetime import datetime

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
from app.email_sender import send_email
from app.scheduler import build_daily_plan


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AutoMail - 自动发送邮件")
        self.setMinimumSize(1100, 640)
        self.resize(1200, 720)

        self._config = load_config()

        self._build_ui()
        self._load_to_form()
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
        self.schedule_count.valueChanged.connect(self._refresh_plan_preview)
        count_unit = QLabel("次/天")
        count_unit.setStyleSheet("color: #666;")
        count_row.addWidget(self.schedule_count)
        count_row.addWidget(count_unit)
        count_row.addStretch()
        schedule_form.addRow("每天次数", count_row)

        interval_row = QHBoxLayout()
        self.schedule_interval = QSpinBox()
        self.schedule_interval.setRange(1, 1440)
        self.schedule_interval.setValue(30)
        self.schedule_interval.valueChanged.connect(self._refresh_plan_preview)
        interval_unit = QLabel("分钟")
        interval_unit.setStyleSheet("color: #666;")
        interval_row.addWidget(self.schedule_interval)
        interval_row.addWidget(interval_unit)
        interval_row.addStretch()
        schedule_form.addRow("发送间隔", interval_row)

        self.schedule_deadline = QLineEdit()
        self.schedule_deadline.setPlaceholderText("23:59")
        self.schedule_deadline.setText("23:59")
        self.schedule_deadline.textChanged.connect(self._refresh_plan_preview)
        schedule_form.addRow("最晚时刻", self.schedule_deadline)

        self.schedule_start = QLineEdit()
        self.schedule_start.setPlaceholderText("留空立即发送")
        self.schedule_start.textChanged.connect(self._refresh_plan_preview)
        schedule_form.addRow("开始时刻", self.schedule_start)

        schedule_hint = QLabel("末封不能超过最晚时刻。")
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

        self.save_btn = QPushButton("保存配置")
        self.save_btn.clicked.connect(self._on_save)
        settings_col.addWidget(self.save_btn)
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
        self.send_btn = QPushButton("发送邮件")
        self.send_btn.setStyleSheet(
            "QPushButton { background: #007aff; color: white; padding: 8px 20px; "
            "border-radius: 6px; font-weight: bold; }"
            "QPushButton:hover { background: #0066d6; }"
            "QPushButton:disabled { background: #a0a0a0; }"
        )
        self.send_btn.clicked.connect(self._on_send)
        send_row.addWidget(self.send_btn)
        mail_box_layout.addLayout(send_row)

        body_label = QLabel("正文")
        mail_box_layout.addWidget(body_label)

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
        QMessageBox.information(self, "保存成功", "配置已保存到 ~/.automail/config.json")

    def _append_log(self, text: str) -> None:
        self.log_view.append(text)

    def _on_send(self) -> None:
        cfg = self._collect_config()
        if not cfg.smtp.host:
            QMessageBox.warning(self, "提示", "请填写 SMTP 服务器")
            return
        if not cfg.mail.recipients:
            QMessageBox.warning(self, "提示", "请填写收件人")
            return

        self.send_btn.setEnabled(False)
        try:
            send_email(cfg.smtp, cfg.mail)
            save_config(cfg)
            msg = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 邮件发送成功"
            self._append_log(msg)
            QMessageBox.information(self, "成功", "邮件已发送")
        except Exception as exc:
            msg = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 发送失败：{exc}"
            self._append_log(msg)
            QMessageBox.critical(self, "发送失败", str(exc))
        finally:
            self.send_btn.setEnabled(True)

    def closeEvent(self, event) -> None:
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
