"""配置持久化"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Type, TypeVar

CONFIG_DIR = Path.home() / ".automail"
CONFIG_FILE = CONFIG_DIR / "config.json"

T = TypeVar("T")


def _from_mapping(cls: Type[T], data: Any) -> T:
    if not isinstance(data, dict):
        return cls()
    allowed = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class SmtpConfig:
    host: str = ""
    port: int = 465
    use_ssl: bool = False
    username: str = ""
    password: str = ""
    sender: str = ""


@dataclass
class ProxyConfig:
    enabled: bool = False
    # socks5 | http（本地 Clash / V2Ray 等常用）
    proxy_type: str = "socks5"
    host: str = "127.0.0.1"
    port: int = 7890
    username: str = ""
    password: str = ""


@dataclass
class MailConfig:
    recipients: str = ""
    cc: str = ""
    subject: str = ""
    body: str = ""
    is_html: bool = False


@dataclass
class ScheduleConfig:
    daily_count: int = 3
    interval_minutes: int = 30
    # 当天最后一封邮件的最晚发送时刻（HH:MM），不能超过此时间
    deadline_time: str = "23:59"
    # 每天开始发送的最早时刻（HH:MM），留空表示启动后立即发送
    start_time: str = ""


@dataclass
class AppConfig:
    smtp: SmtpConfig = field(default_factory=SmtpConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    mail: MailConfig = field(default_factory=MailConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        return cls(
            smtp=_from_mapping(SmtpConfig, data.get("smtp", {})),
            proxy=_from_mapping(ProxyConfig, data.get("proxy", {})),
            mail=_from_mapping(MailConfig, data.get("mail", {})),
            schedule=_from_mapping(ScheduleConfig, data.get("schedule", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_config() -> AppConfig:
    if not CONFIG_FILE.exists():
        return AppConfig()
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            return AppConfig.from_dict(json.load(f))
    except (json.JSONDecodeError, TypeError, ValueError):
        return AppConfig()


def save_config(config: AppConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)
