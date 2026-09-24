"""
Импорт исторической статистики из экспорта истории чата Telegram Desktop.

Использование:
    python import_history.py chat_history.json

Поддерживается JSON-экспорт Telegram Desktop (Экспортировать историю чата → JSON).
Скрипт идемпотентен: повторный запуск на том же (или расширенном) файле экспорта
не создаёт дубликатов — используется тот же способ дедупликации, что и в боте
(id = chat_id + message_id), через тот же JsonStorage/services/storage.py.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from config import load_config
from services.assignment_parser import parse_assignment
from services.statistics import StatisticsService
from services.storage import JsonStorage
from utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


def _extract_text(message: dict) -> str:
    """
    В экспорте Telegram Desktop поле "text" может быть строкой или списком,
    где элементы — либо строки, либо объекты {"type": ..., "text": ...}
    (форматирование, упоминания и т.д.). Собираем итоговый текст сообщения.
    """
    text = message.get("text", "")
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        parts = []
        for chunk in text:
            if isinstance(chunk, str):
                parts.append(chunk)
            elif isinstance(chunk, dict):
                parts.append(chunk.get("text", ""))
        return "".join(parts)
    return ""


def _extract_mention_user_id(message: dict) -> int | None:
    """Если в тексте есть mention_name (упоминание пользователя без username, содержащее
    его user_id), возвращаем его — это самый надёжный способ определить менеджера."""
    text = message.get("text", "")
    if not isinstance(text, list):
        return None
    for chunk in text:
        if isinstance(chunk, dict) and chunk.get("type") == "mention_name":
            user_id = chunk.get("user_id")
            if user_id is not None:
                try:
                    return int(user_id)
                except (TypeError, ValueError):
                    return None
    return None


def _extract_author_id(message: dict) -> int | None:
    from_id = message.get("from_id") or message.get("actor_id")
    if not from_id:
        return None
    # Форматы: "user123456789" (новый экспорт) или просто число (старый экспорт).
    if isinstance(from_id, str) and from_id.startswith("user"):
        digits = from_id[len("user"):]
        return int(digits) if digits.isdigit() else None
    if isinstance(from_id, (int, str)):
        try:
            return int(from_id)
        except ValueError:
            return None
    return None


async def import_history(export_path: Path) -> None:
    setup_logging()
    config = load_config()

    storage = JsonStorage(
        managers_file=config.managers_file,
        assignments_file=config.assignments_file,
        settings_file=config.settings_file,
        corrections_file=config.corrections_file,
        backups_dir=config.backups_dir,
        backup_retention_days=config.backup_retention_days,
    )
    storage.backup_now(reason="pre-import")

    try:
        data = json.loads(export_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Не удалось прочитать файл {export_path}: {exc}")
        sys.exit(1)

    messages = data.get("messages", [])
    managers = await storage.get_managers()
    managers_by_username = {m["username"].lower(): m for m in managers if m.get("username")}
    managers_by_id = {m["telegram_id"]: m for m in managers}

    new_records: list[dict] = []
    unknown_managers: list[dict] = []
    found_assignments = 0
    unknown_count = 0

    chat_id = config.work_chat_id

    for message in messages:
        if message.get("type") != "message":
            continue

        author_id = _extract_author_id(message)
        if author_id is None or author_id not in config.admin_ids:
            continue

        text = _extract_text(message)
        result = parse_assignment(text)
        if not result.is_assignment:
            continue

        found_assignments += 1
        mention_user_id = _extract_mention_user_id(message)

        manager = None
        if mention_user_id is not None:
            manager = managers_by_id.get(mention_user_id)
        if manager is None and result.username:
            manager = managers_by_username.get(result.username.lower())

        message_id = message.get("id")
        if manager is None:
            unknown_count += 1
            unknown_managers.append(
                {
                    "username": result.username,
                    "message_id": message_id,
                    "date": message.get("date"),
                }
            )
            logger.info("Unknown manager during import: @%s (message_id=%s)", result.username, message_id)
            continue

        record = {
            "id": f"{chat_id}_{message_id}",
            "manager_telegram_id": manager["telegram_id"],
            "manager_username": manager["username"],
            "chat_id": chat_id,
            "assignment_message_id": message_id,
            "assigned_by": author_id,
            "assigned_at": message.get("date") or message.get("date_unixtime"),
            "source": "auto",
        }
        new_records.append(record)

    added, skipped = await storage.add_assignments_bulk(new_records)

    if unknown_managers:
        unknown_path = config.data_dir / "unknown_managers.json"
        existing: list[dict] = []
        if unknown_path.exists():
            try:
                existing = json.loads(unknown_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = []

        # Идемпотентность распространяется и на список неизвестных менеджеров:
        # повторный импорт того же экспорта не должен размножать записи.
        seen_unknown = {
            (item.get("message_id"), item.get("username"))
            for item in existing
        }
        for item in unknown_managers:
            key = (item.get("message_id"), item.get("username"))
            if key not in seen_unknown:
                existing.append(item)
                seen_unknown.add(key)

        unknown_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    stats_service = StatisticsService(timezone=config.timezone)
    all_assignments = await storage.get_assignments()
    all_managers = await storage.get_managers()
    corrections = await storage.get_corrections()
    stats = stats_service.build_stats(all_managers, all_assignments, corrections)

    print("Импорт завершён.\n")
    print(f"Обработано сообщений: {len(messages)}")
    print(f"Найдено назначений: {found_assignments}")
    print(f"Добавлено новых назначений: {added}")
    print(f"Пропущено дубликатов: {skipped}")
    print(f"Неизвестных менеджеров: {unknown_count}\n")
    print("Статистика:\n")
    total = 0
    for s in stats:
        print(f"{s.name} — {s.total}")
        total += s.total
    print(f"\nВсего: {total}")

    if unknown_managers:
        print(f"\n⚠️ Не удалось определить {unknown_count} менеджеров.")
        print("Список сохранён в data/unknown_managers.json")


def main() -> None:
    if len(sys.argv) != 2:
        print("Использование: python import_history.py chat_history.json")
        sys.exit(1)

    export_path = Path(sys.argv[1])
    if not export_path.exists():
        print(f"Файл не найден: {export_path}")
        sys.exit(1)

    asyncio.run(import_history(export_path))


if __name__ == "__main__":
    main()
