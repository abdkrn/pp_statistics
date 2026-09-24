"""
JsonStorage — единая точка доступа к данным на диске (managers.json, assignments.json,
settings.json). Никакой другой модуль не должен читать/писать эти файлы напрямую.

Особенности:
- безопасная запись через временный файл + os.replace;
- asyncio.Lock на запись, чтобы избежать гонок между обработчиками;
- защита от повреждённых/отсутствующих файлов с попыткой восстановления из backup;
- ежедневный бэкап (и бэкап перед админ-изменениями) с очисткой старых копий.

В будущем при необходимости этот класс можно заменить реализацией поверх SQLite,
не меняя бизнес-логику в handlers/ и services/statistics.py, если сохранить тот же
публичный интерфейс.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


class StorageError(Exception):
    """Ошибка чтения/записи хранилища."""


class JsonStorage:
    def __init__(
        self,
        managers_file: Path,
        assignments_file: Path,
        settings_file: Path,
        corrections_file: Path,
        backups_dir: Path,
        backup_retention_days: int = 30,
    ) -> None:
        self.managers_file = Path(managers_file)
        self.assignments_file = Path(assignments_file)
        self.settings_file = Path(settings_file)
        self.corrections_file = Path(corrections_file)
        self.backups_dir = Path(backups_dir)
        self.backup_retention_days = backup_retention_days

        self._managers_lock = asyncio.Lock()
        self._assignments_lock = asyncio.Lock()
        self._corrections_lock = asyncio.Lock()

        self._last_backup_date: Optional[str] = None

        self._ensure_files()

    # ------------------------------------------------------------------ #
    # Инициализация
    # ------------------------------------------------------------------ #
    def _ensure_files(self) -> None:
        self.managers_file.parent.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)

        if not self.managers_file.exists():
            self._write_json_sync(self.managers_file, [])
        if not self.assignments_file.exists():
            self._write_json_sync(self.assignments_file, [])
        if not self.settings_file.exists():
            self._write_json_sync(self.settings_file, {})
        if not self.corrections_file.exists():
            self._write_json_sync(self.corrections_file, [])

    # ------------------------------------------------------------------ #
    # Низкоуровневое безопасное чтение/запись
    # ------------------------------------------------------------------ #
    def _read_json_sync(self, path: Path, default: Any) -> Any:
        if not path.exists():
            logger.warning("Файл %s отсутствует, используется значение по умолчанию", path)
            return default

        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("Ошибка чтения файла %s: %s", path, exc)
            return self._recover_from_backup(path, default)

        if not raw.strip():
            logger.warning("Файл %s пуст, используется значение по умолчанию", path)
            return default

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("Повреждённый JSON в файле %s: %s", path, exc)
            return self._recover_from_backup(path, default)

    def _recover_from_backup(self, path: Path, default: Any) -> Any:
        """
        Файл повреждён/недоступен. НЕ перезаписываем его. Пытаемся достать
        последнюю резервную копию, иначе возвращаем default в памяти
        (следующая успешная запись создаст корректный файл заново).
        """
        candidates = sorted(self.backups_dir.glob("*"), reverse=True)
        for day_dir in candidates:
            backup_path = day_dir / path.name
            if backup_path.exists():
                try:
                    data = json.loads(backup_path.read_text(encoding="utf-8"))
                    logger.error(
                        "Восстановлены данные из резервной копии %s для %s", backup_path, path
                    )
                    return data
                except (OSError, json.JSONDecodeError):
                    continue
        logger.error(
            "Не удалось восстановить %s из резервных копий. Используется пустое значение.",
            path,
        )
        return default

    def _write_json_sync(self, path: Path, data: Any) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp_path.replace(path)  # атомарная замена на большинстве ОС
        except OSError as exc:
            logger.error("Ошибка записи файла %s: %s", path, exc)
            raise StorageError(f"Не удалось записать {path}: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Резервное копирование
    # ------------------------------------------------------------------ #
    def backup_now(self, reason: str = "manual") -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        day_dir = self.backups_dir / today
        day_dir.mkdir(parents=True, exist_ok=True)
        for src in (self.managers_file, self.assignments_file, self.settings_file, self.corrections_file):
            if src.exists():
                shutil.copy2(src, day_dir / src.name)
        self._last_backup_date = today
        logger.info("Резервная копия создана (%s): %s", reason, day_dir)
        self._cleanup_old_backups()

    def backup_if_needed_today(self) -> None:
        """Бэкап не чаще одного раза в сутки, если backup_now не вызывался явно."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_backup_date != today:
            self.backup_now(reason="daily")

    def _cleanup_old_backups(self) -> None:
        cutoff = datetime.now() - timedelta(days=self.backup_retention_days)
        for day_dir in self.backups_dir.glob("*"):
            if not day_dir.is_dir():
                continue
            try:
                day_date = datetime.strptime(day_dir.name, "%Y-%m-%d")
            except ValueError:
                continue
            if day_date < cutoff:
                shutil.rmtree(day_dir, ignore_errors=True)
                logger.info("Удалена устаревшая резервная копия: %s", day_dir)

    # ------------------------------------------------------------------ #
    # Менеджеры
    # ------------------------------------------------------------------ #
    async def get_managers(self) -> list[dict]:
        return await asyncio.to_thread(self._read_json_sync, self.managers_file, [])

    async def save_managers(self, managers: list[dict]) -> None:
        async with self._managers_lock:
            await asyncio.to_thread(self._write_json_sync, self.managers_file, managers)

    async def get_manager_by_telegram_id(self, telegram_id: int) -> Optional[dict]:
        managers = await self.get_managers()
        for m in managers:
            if m["telegram_id"] == telegram_id:
                return m
        return None

    async def get_manager_by_username(self, username: str) -> Optional[dict]:
        username = username.lstrip("@").lower()
        managers = await self.get_managers()
        for m in managers:
            if (m.get("username") or "").lower() == username:
                return m
        return None

    async def add_manager(self, telegram_id: int, username: str, name: str) -> dict:
        async with self._managers_lock:
            managers = await asyncio.to_thread(self._read_json_sync, self.managers_file, [])
            now = datetime.now().astimezone().isoformat()
            username = username.lstrip("@")

            for m in managers:
                if m["telegram_id"] == telegram_id or (
                    m.get("username", "").lower() == username.lower()
                ):
                    m["username"] = username
                    m["name"] = name
                    m["active"] = True
                    m["updated_at"] = now
                    await asyncio.to_thread(self._write_json_sync, self.managers_file, managers)
                    return m

            record = {
                "telegram_id": telegram_id,
                "username": username,
                "name": name,
                "active": True,
                "created_at": now,
                "updated_at": now,
            }
            managers.append(record)
            await asyncio.to_thread(self._write_json_sync, self.managers_file, managers)
            return record

    async def set_manager_active(self, identifier: str | int, active: bool) -> Optional[dict]:
        async with self._managers_lock:
            managers = await asyncio.to_thread(self._read_json_sync, self.managers_file, [])
            target = None
            for m in managers:
                if isinstance(identifier, int) and m["telegram_id"] == identifier:
                    target = m
                    break
                if isinstance(identifier, str) and (
                    m.get("username", "").lower() == identifier.lstrip("@").lower()
                ):
                    target = m
                    break
            if target is None:
                return None
            target["active"] = active
            target["updated_at"] = datetime.now().astimezone().isoformat()
            await asyncio.to_thread(self._write_json_sync, self.managers_file, managers)
            return target

    async def rename_manager(self, identifier: str, new_name: str) -> Optional[dict]:
        async with self._managers_lock:
            managers = await asyncio.to_thread(self._read_json_sync, self.managers_file, [])
            target = None
            for m in managers:
                if m.get("username", "").lower() == identifier.lstrip("@").lower():
                    target = m
                    break
            if target is None:
                return None
            target["name"] = new_name
            target["updated_at"] = datetime.now().astimezone().isoformat()
            await asyncio.to_thread(self._write_json_sync, self.managers_file, managers)
            return target

    # ------------------------------------------------------------------ #
    # Назначения (assignments)
    # ------------------------------------------------------------------ #
    async def get_assignments(self) -> list[dict]:
        return await asyncio.to_thread(self._read_json_sync, self.assignments_file, [])

    async def assignment_exists(self, assignment_id: str) -> bool:
        assignments = await self.get_assignments()
        return any(a["id"] == assignment_id for a in assignments)

    async def add_assignment(self, record: dict) -> bool:
        """
        Возвращает False, если запись с таким id уже существует (не добавлена повторно).
        """
        async with self._assignments_lock:
            assignments = await asyncio.to_thread(
                self._read_json_sync, self.assignments_file, []
            )
            if any(a["id"] == record["id"] for a in assignments):
                return False
            assignments.append(record)
            await asyncio.to_thread(
                self._write_json_sync, self.assignments_file, assignments
            )
            return True

    async def add_assignments_bulk(self, records: list[dict]) -> tuple[int, int]:
        """Для import_history.py. Возвращает (добавлено, пропущено_дубликатов)."""
        async with self._assignments_lock:
            assignments = await asyncio.to_thread(
                self._read_json_sync, self.assignments_file, []
            )
            existing_ids = {a["id"] for a in assignments}
            added = 0
            skipped = 0
            for record in records:
                if record["id"] in existing_ids:
                    skipped += 1
                    continue
                assignments.append(record)
                existing_ids.add(record["id"])
                added += 1
            await asyncio.to_thread(
                self._write_json_sync, self.assignments_file, assignments
            )
            return added, skipped

    async def remove_assignment(self, assignment_id: str) -> bool:
        async with self._assignments_lock:
            assignments = await asyncio.to_thread(
                self._read_json_sync, self.assignments_file, []
            )
            new_assignments = [a for a in assignments if a["id"] != assignment_id]
            if len(new_assignments) == len(assignments):
                return False
            await asyncio.to_thread(
                self._write_json_sync, self.assignments_file, new_assignments
            )
            return True

    async def find_assignment_by_message(
        self, chat_id: int, message_id: int
    ) -> Optional[dict]:
        assignments = await self.get_assignments()
        for a in assignments:
            if a.get("chat_id") == chat_id and a.get("assignment_message_id") == message_id:
                return a
        return None


    # ------------------------------------------------------------------ #
    # Корректировки статистики
    # ------------------------------------------------------------------ #
    async def get_corrections(self) -> list[dict]:
        return await asyncio.to_thread(self._read_json_sync, self.corrections_file, [])

    async def add_correction(self, record: dict) -> bool:
        """Добавляет корректировку. Реальные assignments.json при этом не меняются."""
        async with self._corrections_lock:
            corrections = await asyncio.to_thread(
                self._read_json_sync, self.corrections_file, []
            )
            if any(c.get("id") == record.get("id") for c in corrections):
                return False
            corrections.append(record)
            await asyncio.to_thread(
                self._write_json_sync, self.corrections_file, corrections
            )
            return True

    # ------------------------------------------------------------------ #
    # Настройки
    # ------------------------------------------------------------------ #
    async def get_settings(self) -> dict:
        return await asyncio.to_thread(self._read_json_sync, self.settings_file, {})

    async def save_settings(self, settings: dict) -> None:
        await asyncio.to_thread(self._write_json_sync, self.settings_file, settings)
