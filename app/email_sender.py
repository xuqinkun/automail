"""SMTP 邮件发送"""

from __future__ import annotations

import smtplib
import socket
import ssl
import sys
import time
import traceback
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Iterable, Optional

from app.config import MailConfig, ProxyConfig, SmtpConfig

# 连接 / 读写超时（秒）。未设置时 smtplib 会无限阻塞，导致界面一直停在「正在发送」。
SMTP_TIMEOUT_SECONDS = 30
SMTP_CONNECT_RETRY_DELAYS = (1.0,)
# 只重试“SMTP 欢迎语返回前断线”。此时尚未登录或提交邮件，不会造成重复发送。
SMTP_CONNECT_ATTEMPTS = len(SMTP_CONNECT_RETRY_DELAYS) + 1
# 冻结为 .app 后应用包不可写；日志与配置统一放到用户目录。
LOG_DIR = Path.home() / ".automail" / "logs"
# 隐式 SSL(465) 经代理失败时，回退到 STARTTLS 常用端口
STARTTLS_FALLBACK_PORT = 587


class _SMTPGreetingDisconnectedError(ConnectionError):
    """SMTP 连接建立后、服务器欢迎语返回前被关闭。"""


def _write_stderr(text: str, *, end: str = "\n") -> None:
    """窗口模式可能没有 stderr；此时静默跳过控制台输出。"""
    stream = sys.stderr
    if stream is None:
        return
    try:
        print(text, file=stream, end=end)
    except (AttributeError, OSError, ValueError):
        pass


def _log_exception(message: str, exc: BaseException) -> None:
    """把异常完整堆栈打到控制台，并追加写入 logs/。"""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    text = f"[{stamp}] {message}\n{tb}"
    _write_stderr(text, end="" if text.endswith("\n") else "\n")
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LOG_DIR / f"error-{datetime.now().strftime('%Y-%m-%d')}.log"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")
            f.write("\n")
    except OSError as log_exc:
        _write_stderr(f"写入日志文件失败：{log_exc}")


def _log_info(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = f"[{stamp}] {message}"
    _write_stderr(text)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LOG_DIR / f"error-{datetime.now().strftime('%Y-%m-%d')}.log"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass


def _parse_addresses(raw: str) -> list[str]:
    return [addr.strip() for addr in raw.replace(";", ",").split(",") if addr.strip()]


def _timeout_error(smtp_cfg: SmtpConfig, proxy_cfg: Optional[ProxyConfig]) -> TimeoutError:
    hint = f"{smtp_cfg.host}:{smtp_cfg.port}"
    if proxy_cfg and proxy_cfg.enabled:
        hint += (
            f"（经代理 {proxy_cfg.host}:{proxy_cfg.port}，"
            f"{proxy_cfg.proxy_type.upper()}）"
        )
    return TimeoutError(
        f"连接 SMTP 服务器超时（{SMTP_TIMEOUT_SECONDS}s）：{hint}。"
        f"请检查服务器地址、端口、SSL、代理及网络/防火墙。"
    )


def _ssl_error(
    smtp_cfg: SmtpConfig,
    proxy_cfg: Optional[ProxyConfig],
    exc: BaseException,
) -> ConnectionError:
    via = ""
    tips = (
        "请确认：1) 经代理发 Gmail/Outlook 建议端口 587 且取消勾选 SSL（走 STARTTLS）；"
        "2) 代理用 SOCKS5（Clash mixed 口常见 7890）；"
        "3) 代理规则放行 smtp 域名，并换一个能访问 Google/微软邮件端口的节点。"
    )
    if proxy_cfg and proxy_cfg.enabled:
        via = (
            f"（经代理 {proxy_cfg.host}:{proxy_cfg.port}，"
            f"{proxy_cfg.proxy_type.upper()}）"
        )
    return ConnectionError(
        f"SMTP SSL 握手失败 {smtp_cfg.host}:{smtp_cfg.port}{via}：{exc}。{tips}"
    )


def _proxy_type_code(proxy_type: str) -> int:
    try:
        import socks
    except ImportError as exc:
        _log_exception("缺少 PySocks 依赖", exc)
        raise RuntimeError(
            "使用代理需要安装 PySocks：pip install PySocks"
        ) from exc

    mapping = {
        "socks5": socks.SOCKS5,
        "socks4": socks.SOCKS4,
        "http": socks.HTTP,
    }
    key = proxy_type.strip().lower()
    if key not in mapping:
        raise ValueError(f"不支持的代理类型：{proxy_type}（可选 socks5 / socks4 / http）")
    return mapping[key]


def _create_proxied_connection(
    proxy_cfg: ProxyConfig,
    host: str,
    port: int,
    timeout: float | None,
):
    import socks

    if not proxy_cfg.host.strip():
        raise ValueError("已启用代理，但未填写代理地址")

    # proxy_rdns=True：域名由代理侧解析，避免本地解析/分流导致连错目标后 SSL 被断开
    return socks.create_connection(
        (host, port),
        timeout=timeout,
        proxy_type=_proxy_type_code(proxy_cfg.proxy_type),
        proxy_addr=proxy_cfg.host.strip(),
        proxy_port=int(proxy_cfg.port),
        proxy_username=proxy_cfg.username.strip() or None,
        proxy_password=proxy_cfg.password or None,
        proxy_rdns=True,
    )


class _ProxySMTP(smtplib.SMTP):
    def __init__(self, *args, proxy: ProxyConfig, **kwargs) -> None:
        self._proxy = proxy
        super().__init__(*args, **kwargs)

    def _get_socket(self, host, port, timeout):
        return _create_proxied_connection(self._proxy, host, port, timeout)


class _ProxySMTP_SSL(smtplib.SMTP_SSL):
    def __init__(self, *args, proxy: ProxyConfig, **kwargs) -> None:
        self._proxy = proxy
        super().__init__(*args, **kwargs)

    def _get_socket(self, host, port, timeout):
        sock = _create_proxied_connection(self._proxy, host, port, timeout)
        if timeout is not None:
            sock.settimeout(timeout)
        # 使用本次 connect 的 host，避免 server_hostname 不一致
        return self.context.wrap_socket(sock, server_hostname=host)


def _open_smtp(
    host: str,
    port: int,
    use_ssl: bool,
    proxy_cfg: Optional[ProxyConfig],
):
    use_proxy = bool(proxy_cfg and proxy_cfg.enabled)
    if use_ssl:
        context = ssl.create_default_context()
        if use_proxy:
            assert proxy_cfg is not None
            return _ProxySMTP_SSL(
                host,
                port,
                context=context,
                timeout=SMTP_TIMEOUT_SECONDS,
                proxy=proxy_cfg,
            )
        return smtplib.SMTP_SSL(
            host,
            port,
            context=context,
            timeout=SMTP_TIMEOUT_SECONDS,
        )

    if use_proxy:
        assert proxy_cfg is not None
        return _ProxySMTP(
            host,
            port,
            timeout=SMTP_TIMEOUT_SECONDS,
            proxy=proxy_cfg,
        )
    return smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS)


def _early_disconnect_error(
    host: str,
    port: int,
    use_ssl: bool,
    proxy_cfg: Optional[ProxyConfig],
) -> _SMTPGreetingDisconnectedError:
    mode = "隐式 SSL/TLS" if use_ssl else "STARTTLS"
    via = ""
    advice = "请检查网络、防火墙以及 SMTP 服务端口是否可达。"
    if proxy_cfg and proxy_cfg.enabled:
        via = (
            f"（经代理 {proxy_cfg.host}:{proxy_cfg.port}，"
            f"{proxy_cfg.proxy_type.upper()}）"
        )
        advice = (
            "代理隧道已建立，但 SMTP 会话被提前关闭；请在代理软件中切换允许 "
            "SMTP 端口的节点/规则，或暂时关闭代理进行对比测试。"
        )
    return _SMTPGreetingDisconnectedError(
        f"SMTP 服务器在返回欢迎语前断开连接：{host}:{port}{via}，"
        f"连接模式 {mode}，已尝试 {SMTP_CONNECT_ATTEMPTS} 次。"
        f"此时尚未进入账号认证，通常与密码无关。{advice}"
    )


def _open_smtp_with_retry(
    host: str,
    port: int,
    use_ssl: bool,
    proxy_cfg: Optional[ProxyConfig],
):
    """安全重试欢迎语前断线；登录或发送阶段绝不自动重试。"""
    for attempt in range(1, SMTP_CONNECT_ATTEMPTS + 1):
        try:
            return _open_smtp(host, port, use_ssl, proxy_cfg)
        except smtplib.SMTPServerDisconnected as exc:
            if attempt >= SMTP_CONNECT_ATTEMPTS:
                raise _early_disconnect_error(
                    host, port, use_ssl, proxy_cfg
                ) from exc
            delay = SMTP_CONNECT_RETRY_DELAYS[attempt - 1]
            _log_info(
                f"SMTP 欢迎语前连接被关闭，{delay:g}s 后重试 "
                f"({attempt + 1}/{SMTP_CONNECT_ATTEMPTS})"
            )
            time.sleep(delay)

    raise AssertionError("unreachable")


def _deliver(
    host: str,
    port: int,
    use_ssl: bool,
    smtp_cfg: SmtpConfig,
    proxy_cfg: Optional[ProxyConfig],
    sender: str,
    all_targets: list[str],
    raw_message: str,
) -> None:
    with _open_smtp_with_retry(host, port, use_ssl, proxy_cfg) as server:
        if not use_ssl:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if smtp_cfg.username:
            server.login(smtp_cfg.username, smtp_cfg.password)
        server.sendmail(sender, all_targets, raw_message)


def send_email(
    smtp_cfg: SmtpConfig,
    mail_cfg: MailConfig,
    proxy_cfg: Optional[ProxyConfig] = None,
) -> None:
    from app.gmail_api import is_gmail_account, send_via_gmail_api

    # Gmail 账号走 OAuth API；其它邮箱继续 SMTP
    if is_gmail_account(smtp_cfg):
        try:
            send_via_gmail_api(smtp_cfg, mail_cfg, proxy_cfg)
            return
        except Exception as exc:
            _log_exception("Gmail API 发送失败", exc)
            raise

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

    all_targets: list[str] = [*recipients, *cc_list]
    raw_message = msg.as_string()

    try:
        _deliver(
            smtp_cfg.host,
            smtp_cfg.port,
            smtp_cfg.use_ssl,
            smtp_cfg,
            proxy_cfg,
            sender,
            all_targets,
            raw_message,
        )
    except (TimeoutError, socket.timeout) as exc:
        _log_exception("SMTP 连接超时", exc)
        raise _timeout_error(smtp_cfg, proxy_cfg) from exc
    except _SMTPGreetingDisconnectedError as exc:
        _log_exception("SMTP 建连错误", exc)
        raise
    except smtplib.SMTPException as exc:
        _log_exception("SMTP 协议/认证错误", exc)
        raise
    except ssl.SSLError as exc:
        # 465 隐式 SSL 经代理常被节点拦截；自动改用 587 + STARTTLS 再试一次
        can_fallback = (
            smtp_cfg.use_ssl
            and smtp_cfg.port == 465
            and smtp_cfg.port != STARTTLS_FALLBACK_PORT
        )
        if can_fallback:
            _log_exception("SMTP SSL 握手错误（将回退 587 STARTTLS）", exc)
            _log_info(
                f"{smtp_cfg.host}:465 SSL 失败，自动改用 "
                f"{smtp_cfg.host}:{STARTTLS_FALLBACK_PORT} STARTTLS 重试"
            )
            try:
                _deliver(
                    smtp_cfg.host,
                    STARTTLS_FALLBACK_PORT,
                    False,
                    smtp_cfg,
                    proxy_cfg,
                    sender,
                    all_targets,
                    raw_message,
                )
                _log_info("587 STARTTLS 回退发送成功")
                return
            except Exception as fallback_exc:
                _log_exception("587 STARTTLS 回退仍失败", fallback_exc)
                raise _ssl_error(smtp_cfg, proxy_cfg, exc) from fallback_exc
        _log_exception("SMTP SSL 握手错误", exc)
        raise _ssl_error(smtp_cfg, proxy_cfg, exc) from exc
    except OSError as exc:
        _log_exception("SMTP 网络连接错误", exc)
        via = ""
        if proxy_cfg and proxy_cfg.enabled:
            via = f"（经代理 {proxy_cfg.host}:{proxy_cfg.port}）"
        raise ConnectionError(
            f"无法连接 SMTP 服务器 {smtp_cfg.host}:{smtp_cfg.port}{via}：{exc}"
        ) from exc
    except Exception as exc:
        _log_exception("SMTP 发送未知错误", exc)
        raise
