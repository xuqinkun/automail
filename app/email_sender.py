"""SMTP 邮件发送"""

from __future__ import annotations

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Iterable

from app.config import MailConfig, SmtpConfig


def _parse_addresses(raw: str) -> list[str]:
    return [addr.strip() for addr in raw.replace(";", ",").split(",") if addr.strip()]


def send_email(smtp_cfg: SmtpConfig, mail_cfg: MailConfig) -> None:
    recipients = _parse_addresses(mail_cfg.recipients)
    if not recipients:
        raise ValueError("请填写至少一个收件人")

    cc_list = _parse_addresses(mail_cfg.cc)
    sender = smtp_cfg.sender.strip() or smtp_cfg.username.strip()
    if not sender:
        raise ValueError("请填写发件人邮箱")

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = mail_cfg.subject

    subtype = "html" if mail_cfg.is_html else "plain"
    msg.attach(MIMEText(mail_cfg.body, subtype, "utf-8"))

    all_targets: Iterable[str] = [*recipients, *cc_list]

    if smtp_cfg.use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_cfg.host, smtp_cfg.port, context=context) as server:
            if smtp_cfg.username:
                server.login(smtp_cfg.username, smtp_cfg.password)
            server.sendmail(sender, list(all_targets), msg.as_string())
    else:
        with smtplib.SMTP(smtp_cfg.host, smtp_cfg.port) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            if smtp_cfg.username:
                server.login(smtp_cfg.username, smtp_cfg.password)
            server.sendmail(sender, list(all_targets), msg.as_string())
