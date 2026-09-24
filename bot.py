from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeChatMember, BotCommandScopeDefault

from config import Config, load_config
from handlers import admin, commands, messages
from services.managers import ManagersService
from services.statistics import StatisticsService
from services.storage import JsonStorage
from utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


BASE_COMMANDS = [
    BotCommand(command="info", description="Статистика заявок за текущий месяц"),
    BotCommand(command="help", description="Список доступных команд"),
]

ADMIN_COMMANDS = BASE_COMMANDS + [
    BotCommand(command="managers", description="Список менеджеров"),
    BotCommand(command="add_manager", description="Добавить менеджера"),
    BotCommand(command="remove_manager", description="Деактивировать менеджера"),
    BotCommand(command="rename_manager", description="Изменить имя менеджера"),
    BotCommand(command="add_stat", description="Добавить корректировку статистики"),
    BotCommand(command="remove_stat", description="Убрать корректировку статистики"),
]


async def setup_commands(bot: Bot, config: Config) -> None:
    """Регистрирует список команд, чтобы Telegram показывал подсказки
    с описанием при вводе '/' или упоминании бота."""
    await bot.set_my_commands(BASE_COMMANDS, scope=BotCommandScopeDefault())

    for admin_id in config.admin_ids:
        try:
            await bot.set_my_commands(
                ADMIN_COMMANDS,
                scope=BotCommandScopeChatMember(chat_id=config.work_chat_id, user_id=admin_id),
            )
        except Exception:  # noqa: BLE001
            # Например, если админ ещё не писал в рабочий чат — Telegram
            # не даёт установить команды для него в этом scope, пока он
            # не станет "виден" боту в чате.
            logger.warning("Не удалось задать меню команд для админа %s", admin_id)


async def main() -> None:
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
    storage.backup_now(reason="startup")

    managers_service = ManagersService(storage)
    stats_service = StatisticsService(timezone=config.timezone)

    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # Порядок важен: сначала команды (в т.ч. админские), затем универсальный
    # обработчик автоматического распознавания назначений.
    dp.include_router(commands.router)
    dp.include_router(admin.router)
    dp.include_router(messages.router)

    logger.info("Бот запускается...")

    async def _daily_backup_loop():
        while True:
            await asyncio.sleep(6 * 60 * 60)  # проверяем раз в 6 часов
            try:
                storage.backup_if_needed_today()
            except Exception:  # noqa: BLE001
                logger.exception("Ошибка при создании ежедневного бэкапа")

    backup_task = asyncio.create_task(_daily_backup_loop())

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await setup_commands(bot, config)
        await dp.start_polling(
            bot,
            storage=storage,
            managers_service=managers_service,
            stats_service=stats_service,
            config=config,
        )
    finally:
        backup_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")