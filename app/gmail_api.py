"""Gmail API OAuth 授权与发信"""

from __future__ import annotations

import base64
import contextlib
import os
import shutil
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from app.config import CONFIG_DIR, MailConfig, ProxyConfig, SmtpConfig

GMAIL_CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
GMAIL_TOKEN_FILE = CONFIG_DIR / "token.json"
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def is_gmail_account(smtp_cfg: SmtpConfig) -> bool:
    """根据发件人/用户名/SMTP 主机判断是否应为 Gmail API 发送。"""
    host = smtp_cfg.host.strip().lower()
    if "gmail.com" in host:
        return True
    for address in (smtp_cfg.sender, smtp_cfg.username):
        addr = address.strip().lower()
        if addr.endswith("@gmail.com") or addr.endswith("@googlemail.com"):
            return True
    return False


def credentials_imported() -> bool:
    return GMAIL_CREDENTIALS_FILE.is_file()


def token_authorized() -> bool:
    return GMAIL_TOKEN_FILE.is_file()


def import_credentials(source_path: str | Path) -> Path:
    """将用户选择的 credentials.json 复制到 ~/.automail/。"""
    src = Path(source_path)
    if not src.is_file():
        raise FileNotFoundError(f"找不到凭证文件：{src}")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, GMAIL_CREDENTIALS_FILE)

    # 换了新的 OAuth 客户端后，旧 token 大概率失效，清除以便重新授权
    if GMAIL_TOKEN_FILE.exists():
        GMAIL_TOKEN_FILE.unlink()

    return GMAIL_CREDENTIALS_FILE


def clear_gmail_auth() -> None:
    if GMAIL_TOKEN_FILE.exists():
        GMAIL_TOKEN_FILE.unlink()


@contextlib.contextmanager
def _proxy_environ(proxy_cfg: Optional[ProxyConfig]):
    """临时设置 HTTP(S)_PROXY，供 google-auth / requests 使用。"""
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy")
    previous = {k: os.environ.get(k) for k in keys}

    try:
        if proxy_cfg and proxy_cfg.enabled:
            host = proxy_cfg.host.strip() or "127.0.0.1"
            port = int(proxy_cfg.port)
            user = proxy_cfg.username.strip()
            password = proxy_cfg.password or ""
            ptype = proxy_cfg.proxy_type.strip().lower() or "http"

            if ptype.startswith("socks"):
                scheme = "socks5"
            else:
                scheme = "http"

            if user:
                auth = f"{user}:{password}@"
            else:
                auth = ""
            proxy_url = f"{scheme}://{auth}{host}:{port}"
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                os.environ[key] = proxy_url
            os.environ["NO_PROXY"] = "localhost,127.0.0.1"
            os.environ["no_proxy"] = "localhost,127.0.0.1"
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def get_credentials(
    *,
    proxy_cfg: Optional[ProxyConfig] = None,
    interactive: bool = True,
):
    """获取可用的 Gmail OAuth 凭证；必要时弹出浏览器完成授权。"""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError(
            "缺少 Gmail API 依赖，请安装：pip install google-api-python-client "
            "google-auth-httplib2 google-auth-oauthlib"
        ) from exc

    if not credentials_imported():
        raise FileNotFoundError(
            "尚未导入 credentials.json。请在界面中点击「导入 Gmail 凭证」选择 Google "
            "Cloud 下载的 OAuth 客户端文件。"
        )

    with _proxy_environ(proxy_cfg):
        creds = None
        if GMAIL_TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(GMAIL_TOKEN_FILE), GMAIL_SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not interactive:
                    raise RuntimeError(
                        "Gmail 尚未授权或授权已失效，请先在界面完成「授权 Gmail」。"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(GMAIL_CREDENTIALS_FILE),
                    GMAIL_SCOPES,
                )
                creds = flow.run_local_server(port=0)

            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with GMAIL_TOKEN_FILE.open("w", encoding="utf-8") as token:
                token.write(creds.to_json())

        return creds


def authorize_gmail(proxy_cfg: Optional[ProxyConfig] = None) -> None:
    """交互式完成 OAuth 授权并保存 token.json。"""
    get_credentials(proxy_cfg=proxy_cfg, interactive=True)


def _build_raw_message(smtp_cfg: SmtpConfig, mail_cfg: MailConfig) -> str:
    recipients = [
        addr.strip()
        for addr in mail_cfg.recipients.replace(";", ",").split(",")
        if addr.strip()
    ]
    if not recipients:
        raise ValueError("请填写至少一个收件人")

    cc_list = [
        addr.strip()
        for addr in mail_cfg.cc.replace(";", ",").split(",")
        if addr.strip()
    ]
    sender = smtp_cfg.sender.strip() or smtp_cfg.username.strip()
    if not sender:
        raise ValueError("请填写发件人邮箱（Gmail 地址）")

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = mail_cfg.subject

    subtype = "html" if mail_cfg.is_html else "plain"
    msg.attach(MIMEText(mail_cfg.body, subtype, "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return raw


def send_via_gmail_api(
    smtp_cfg: SmtpConfig,
    mail_cfg: MailConfig,
    proxy_cfg: Optional[ProxyConfig] = None,
) -> None:
    """通过 Gmail API users.messages.send 发送邮件。"""
    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise RuntimeError(
            "缺少 Gmail API 依赖，请安装：pip install google-api-python-client "
            "google-auth-httplib2 google-auth-oauthlib"
        ) from exc

    raw = _build_raw_message(smtp_cfg, mail_cfg)

    with _proxy_environ(proxy_cfg):
        # 后台线程发送时不要弹浏览器；需事先授权
        creds = get_credentials(proxy_cfg=proxy_cfg, interactive=False)
        try:
            service = build("gmail", "v1", credentials=creds, cache_discovery=False)
            service.users().messages().send(
                userId="me",
                body={"raw": raw},
            ).execute()
        except HttpError as exc:
            raise RuntimeError(f"Gmail API 发送失败：{exc}") from exc
