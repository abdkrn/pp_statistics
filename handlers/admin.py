from __future__ import annotations

import re
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from config import Config
from services.managers import ManagersService
from services.storage import JsonStorage
from utils.logger import get_logger

logger = get_logger(__name__)
router = Router(name="admin")

_ADD_MANAGER_RE = re.compile(
    r"^@?(?P<username>[A-Za-z][A-Za-z0-9_]{4,31})\s+(?P<name>.+)$"
)
_ADD_MANAGER_WITH_ID_RE = re.compile(
    r"^(?P<telegram_id>\d+)\s+@?(?P<username>[A-Za-z][A-Za-z0-9_]{4,31})\s+(?P<name>.+)$"
)
_STAT_RE = re.compile(r"^@?(?P<username>[A-Za-z][A-Za-z0-9_]{4,31})\s+(?P<count>\d+)$")


def _require_admin(message: Message, config: Config) -> bool:
    return bool(message.from_user and config.is_admin(message.from_user.id))


async def _deny(message: Message) -> None:
    await message.answer("⛔ У вас нет прав для выполнения этой команды.")


@router.message(Command("add_manager"))
async def cmd_add_manager(
    message: Message,
    command: CommandObject,
    config: Config,
    managers_service: ManagersService,
):
    if not _require_admin(message, config):
        return await _deny(message)

    if not command.args:
        await message.answer(
            "Формат:\n"
            "1) Ответьте на сообщение менеджера: /add_manager Имя\n"
            "2) Или укажите ID: /add_manager 123456789 @username Имя"
        )
        return

    args = command.args.strip()
    telegram_id: int | None = None
    username: str
    name: str

    # Предпочтительный способ: админ отвечает на сообщение самого менеджера.
    # Telegram тогда гарантированно даёт реальный user_id.
    if message.reply_to_message and message.reply_to_message.from_user:
        reply_user = message.reply_to_message.from_user
        telegram_id = reply_user.id
        username_from_reply = reply_user.username

        # В reply-варианте разрешаем: /add_manager Имя
        name = args
        if args.startswith("@"):
            parts = args.split(maxsplit=1)
            if len(parts) == 2:
                name = parts[1].strip()

        if not name:
            await message.answer("Имя менеджера не может быть пустым.")
            return

        username = username_from_reply or ""
        if not username:
            await message.answer(
                "⚠️ У пользователя нет username. Используйте вариант с явным username:\n"
                "/add_manager 123456789 @username Имя"
            )
            return
    else:
        match_with_id = _ADD_MANAGER_WITH_ID_RE.match(args)
        if not match_with_id:
            # Старый короткий формат теперь разрешён только при reply, чтобы
            # случайно не создавать менеджера без реального Telegram ID.
            await message.answer(
                "⚠️ Нужен реальный Telegram ID менеджера.\n\n"
                "Ответьте на сообщение менеджера:\n"
                "/add_manager Имя\n\n"
                "или используйте:\n"
                "/add_manager 123456789 @username Имя"
            )
            return

        telegram_id = int(match_with_id.group("telegram_id"))
        username = match_with_id.group("username")
        name = match_with_id.group("name").strip()

    existing_by_id = await managers_service.find_by_telegram_id(telegram_id)
    existing_by_username = await managers_service.find_by_username(username)

    if existing_by_id and existing_by_id.get("active"):
        await message.answer("⚠️ Такой Telegram ID уже зарегистрирован как активный менеджер.")
        return
    if (
        existing_by_username
        and existing_by_username.get("active")
        and existing_by_username.get("telegram_id") != telegram_id
    ):
        await message.answer(
            "⚠️ Этот username уже привязан к другому Telegram ID. "
            "Проверьте данные менеджера."
        )
        return

    result = await managers_service.add_manager(telegram_id, username, name)
    logger.info(
        "Manager add/update by admin %s: id=%s @%s (%s)",
        message.from_user.id,
        telegram_id,
        username,
        result.message,
    )

    if result.message == "reactivated":
        await message.answer(f"✅ Менеджер {name} — @{username}\n\nМенеджер повторно активирован.")
    else:
        await message.answer(f"✅ Менеджер добавлен\n\n{name} — @{username}\nID: {telegram_id}")


@router.message(Command("remove_manager"))
async def cmd_remove_manager(
    message: Message, command: CommandObject, config: Config, managers_service: ManagersService
):
    if not _require_admin(message, config):
        return await _deny(message)

    if not command.args:
        await message.answer("Формат: /remove_manager @username")
        return

    username = command.args.strip().lstrip("@").split()[0]
    result = await managers_service.deactivate_manager(username)
    logger.info("Manager deactivate by admin %s: @%s -> %s", message.from_user.id, username, result.ok)

    if not result.ok:
        await message.answer("⚠️ Менеджер не найден.")
        return

    await message.answer(
        f"✅ Менеджер {result.manager['name']} деактивирован.\n\nИсторическая статистика сохранена."
    )


@router.message(Command("rename_manager"))
async def cmd_rename_manager(
    message: Message, command: CommandObject, config: Config, managers_service: ManagersService
):
    if not _require_admin(message, config):
        return await _deny(message)

    if not command.args:
        await message.answer("Формат: /rename_manager @username Новое Имя")
        return

    match = _ADD_MANAGER_RE.match(command.args.strip())
    if not match:
        await message.answer("Формат: /rename_manager @username Новое Имя")
        return

    username = match.group("username")
    new_name = match.group("name").strip()
    result = await managers_service.rename_manager(username, new_name)
    logger.info("Manager rename by admin %s: @%s -> %s", message.from_user.id, username, new_name)

    if not result.ok:
        await message.answer("⚠️ Менеджер не найден.")
        return

    await message.answer(f"✅ Имя изменено:\n\n{new_name} — @{username}")


@router.message(Command("add_stat"))
async def cmd_add_stat(
    message: Message,
    command: CommandObject,
    config: Config,
    managers_service: ManagersService,
    storage: JsonStorage,
):
    if not _require_admin(message, config):
        return await _deny(message)
    await _manual_adjust(message, command, managers_service, storage, delta_positive=True)


@router.message(Command("remove_stat"))
async def cmd_remove_stat(
    message: Message,
    command: CommandObject,
    config: Config,
    managers_service: ManagersService,
    storage: JsonStorage,
):
    if not _require_admin(message, config):
        return await _deny(message)
    await _manual_adjust(message, command, managers_service, storage, delta_positive=False)


async def _manual_adjust(
    message: Message,
    command: CommandObject,
    managers_service: ManagersService,
    storage: JsonStorage,
    delta_positive: bool,
):
    cmd_name = "add_stat" if delta_positive else "remove_stat"
    if not command.args:
        await message.answer(f"Формат: /{cmd_name} @username количество")
        return

    match = _STAT_RE.match(command.args.strip())
    if not match:
        await message.answer(f"Формат: /{cmd_name} @username количество")
        return

    username = match.group("username")
    count = int(match.group("count"))
    if count <= 0:
        await message.answer("Количество должно быть положительным числом.")
        return

    manager = await managers_service.find_by_username(username)
    if manager is None:
        await message.answer("⚠️ Менеджер не найден.")
        return

    now = datetime.now().astimezone()
    correction = {
        "id": f"correction_{int(now.timestamp() * 1000)}_{message.from_user.id}",
        "manager_telegram_id": manager["telegram_id"],
        "manager_username": manager["username"],
        "delta": count if delta_positive else -count,
        "created_by": message.from_user.id,
        "created_at": now.isoformat(),
        "reason": f"/{cmd_name}",
    }

    if not await storage.add_correction(correction):
        await message.answer("⚠️ Не удалось сохранить корректировку.")
        return

    sign = "+" if delta_positive else "-"
    logger.info(
        "Manual correction %s%s for @%s by admin %s",
        sign,
        count,
        username,
        message.from_user.id,
    )
    await message.answer(
        f"✅ Корректировка сохранена: {manager['name']} {sign}{count}\n"
        "Исходные назначения не изменены."
    )
