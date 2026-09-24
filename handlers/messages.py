from __future__ import annotations

from datetime import datetime

from aiogram import Router
from aiogram.types import Message

from config import Config
from services.assignment_parser import parse_assignment
from services.managers import ManagersService
from services.storage import JsonStorage
from utils.logger import get_logger

logger = get_logger(__name__)
router = Router(name="messages")


def _extract_mentioned_username_and_id(message: Message) -> tuple[str | None, int | None]:
    """
    Предпочитаем Telegram entities (mention/text_mention) строковому поиску @username,
    как того требует п.18 ТЗ. text_mention содержит объект пользователя с реальным ID.
    """
    if not message.entities:
        return None, None
    for entity in message.entities:
        if entity.type == "text_mention" and entity.user:
            username = entity.user.username
            return username, entity.user.id
        if entity.type == "mention":
            text = message.text or ""
            raw = text[entity.offset : entity.offset + entity.length]
            return raw.lstrip("@"), None
    return None, None


async def _resolve_manager(
    managers_service: ManagersService, parsed_username: str, entity_user_id: int | None
) -> dict | None:
    if entity_user_id is not None:
        # Реальный Telegram ID известен — приоритетный путь. Если менеджер был заведён
        # ранее только по username, актуализируем его ID.
        await managers_service.resolve_real_telegram_id(parsed_username, entity_user_id)
        manager = await managers_service.find_by_telegram_id(entity_user_id)
        if manager:
            return manager
    return await managers_service.find_by_username(parsed_username)


def _build_assignment_id(chat_id: int, message_id: int) -> str:
    return f"{chat_id}_{message_id}"


async def _make_record(manager: dict, message: Message) -> dict:
    dt = message.date.astimezone() if message.date.tzinfo else message.date
    return {
        "id": _build_assignment_id(message.chat.id, message.message_id),
        "manager_telegram_id": manager["telegram_id"],
        "manager_username": manager["username"],
        "chat_id": message.chat.id,
        "assignment_message_id": message.message_id,
        "assigned_by": message.from_user.id,
        "assigned_at": dt.isoformat(),
        "source": "auto",
    }


@router.message()
async def handle_chat_message(
    message: Message,
    storage: JsonStorage,
    managers_service: ManagersService,
    config: Config,
):
    # Шаг 2: работаем только в рабочем чате.
    if message.chat.id != config.work_chat_id:
        return

    # Шаг 3: только сообщения руководителя могут создавать назначения.
    if not message.from_user or not config.is_admin(message.from_user.id):
        return

    text = message.text or message.caption
    if not text:
        return

    parsed_username, entity_user_id = _extract_mentioned_username_and_id(message)

    # Шаг 4-5: используем распознанный parse_assignment по сырому тексту как основной
    # источник, entities — как уточнение username/ID.
    result = parse_assignment(text)
    if not result.is_assignment:
        return

    username = parsed_username or result.username
    if not username:
        return

    # Шаг 6-7: находим менеджера.
    manager = await _resolve_manager(managers_service, username, entity_user_id)
    if manager is None or not manager.get("active", True):
        logger.info("Unknown manager: @%s", username)
        return

    # Шаг 8-9: защита от повторного подсчёта — уникальный id = chat_id + message_id.
    record = await _make_record(manager, message)
    added = await storage.add_assignment(record)

    if added:
        logger.info("Assignment detected: @%s", username)
        logger.info("Assignment saved")
    else:
        logger.info("Assignment already processed for message_id=%s", message.message_id)


@router.edited_message()
async def handle_edited_message(
    message: Message,
    storage: JsonStorage,
    managers_service: ManagersService,
    config: Config,
):
    """
    п.41 ТЗ: если руководитель отредактировал назначение (сменил менеджера), отменяем
    старое назначение и создаём новое. Ограничение: Telegram присылает edited_message
    только пока сообщение штатно редактируется ботом-видимым способом (в обычных группах
    это поддерживается Bot API), но если сообщение было отправлено ДО добавления бота в
    чат, событие редактирования по нему бот не получит — это описано в README.
    """
    if message.chat.id != config.work_chat_id:
        return
    if not message.from_user or not config.is_admin(message.from_user.id):
        return

    text = message.text or message.caption
    if not text:
        return

    assignment_id = _build_assignment_id(message.chat.id, message.message_id)
    existing = await storage.find_assignment_by_message(message.chat.id, message.message_id)

    parsed_username, entity_user_id = _extract_mentioned_username_and_id(message)
    result = parse_assignment(text)

    new_username = None
    if result.is_assignment:
        new_username = parsed_username or result.username

    if existing and (not result.is_assignment or new_username is None):
        # Сообщение перестало быть назначением — отменяем старую запись.
        await storage.remove_assignment(assignment_id)
        logger.info("Assignment cancelled (edited to non-assignment): message_id=%s", message.message_id)
        return

    if not result.is_assignment or new_username is None:
        return

    manager = await _resolve_manager(managers_service, new_username, entity_user_id)
    if manager is None or not manager.get("active", True):
        logger.info("Unknown manager (edited message): @%s", new_username)
        if existing:
            await storage.remove_assignment(assignment_id)
        return

    if existing and existing["manager_telegram_id"] == manager["telegram_id"]:
        return  # Ничего не изменилось.

    if existing:
        await storage.remove_assignment(assignment_id)
        logger.info(
            "Assignment reassigned via edit: message_id=%s old_manager=%s",
            message.message_id,
            existing["manager_telegram_id"],
        )

    record = await _make_record(manager, message)
    await storage.add_assignment(record)
    logger.info("Assignment detected (edited): @%s", new_username)
