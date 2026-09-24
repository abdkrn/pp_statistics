"""
Загрузка и валидация конфигурации бота из переменных окружения (.env).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _parse_admin_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            raise ValueError(f"Некорректный Telegram ID в ADMIN_IDS: {part!r}")
    return ids


@dataclass(frozen=True)
class Config:
    bot_token: str
    work_chat_id: int
    admin_ids: set[int]
    timezone: str = "Europe/Moscow"

    data_dir: Path = field(default_factory=lambda: BASE_DIR / "data")
    backups_dir: Path = field(default_factory=lambda: BASE_DIR / "backups")

    managers_file: Path = field(init=False)
    assignments_file: Path = field(init=False)
    settings_file: Path = field(init=False)
    corrections_file: Path = field(init=False)

    backup_retention_days: int = 30

    def __post_init__(self):
        object.__setattr__(self, "managers_file", self.data_dir / "managers.json")
        object.__setattr__(self, "assignments_file", self.data_dir / "assignments.json")
        object.__setattr__(self, "settings_file", self.data_dir / "settings.json")
        object.__setattr__(self, "corrections_file", self.data_dir / "corrections.json")

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


def load_config() -> Config:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN не задан в .env")

    work_chat_raw = os.getenv("WORK_CHAT_ID", "").strip()
    if not work_chat_raw:
        raise RuntimeError("WORK_CHAT_ID не задан в .env")
    try:
        work_chat_id = int(work_chat_raw)
    except ValueError:
        raise RuntimeError(f"WORK_CHAT_ID должен быть числом, получено: {work_chat_raw!r}")

    admin_ids_raw = os.getenv("ADMIN_IDS", "").strip()
    if not admin_ids_raw:
        raise RuntimeError("ADMIN_IDS не задан в .env")
    admin_ids = _parse_admin_ids(admin_ids_raw)
    if not admin_ids:
        raise RuntimeError("ADMIN_IDS пуст или некорректен")

    timezone = os.getenv("TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"

    backup_retention_days = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))

    return Config(
        bot_token=bot_token,
        work_chat_id=work_chat_id,
        admin_ids=admin_ids,
        timezone=timezone,
        backup_retention_days=backup_retention_days,
    )