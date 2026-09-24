from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import Config
from keyboards.statistics import (
    CB_BACK,
    CB_BY_MONTH,
    CB_BY_MONTH_PICK,
    CB_MONTH,
    CB_TOTAL,
    by_month_list_keyboard,
    months_keyboard,
)
from services.statistics import MonthKey, StatisticsService
from services.storage import JsonStorage
from utils.logger import get_logger

router = Router(name="commands")
logger = get_logger(__name__)


def _format_stats_block(title: str, stats) -> str:
    lines = [f"📊 {title}", ""]
    total = 0
    for s in stats:
        lines.append(f"{s.name} — {s.total}")
        total += s.total
    lines += ["", "━━━━━━━━━━━━", f"Всего — {total}"]
    return "\n".join(lines)


async def _build_month_view(storage: JsonStorage, stats_service: StatisticsService, month: MonthKey):
    managers = await storage.get_managers()
    assignments = await storage.get_assignments()
    corrections = await storage.get_corrections()
    month_assignments, month_corrections = stats_service.filter_by_month(
        assignments, corrections, month
    )
    stats = stats_service.build_stats(managers, month_assignments, month_corrections)
    text = _format_stats_block(f"Статистика за {month.label()}", stats)
    months = stats_service.available_months(assignments, corrections)
    if month not in months:
        months = [month] + months
    return text, months_keyboard(months)


@router.message(Command("info"))
async def cmd_info(message: Message, storage: JsonStorage, stats_service: StatisticsService, config: Config):
    if message.chat.id != config.work_chat_id:
        logger.info(
            "/info проигнорирован: chat.id=%s (title=%r) != WORK_CHAT_ID=%s",
            message.chat.id, message.chat.title, config.work_chat_id,
        )
        return
    month = stats_service.current_month()
    text, kb = await _build_month_view(storage, stats_service, month)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith(f"{CB_MONTH}:"))
async def cb_pick_month(callback: CallbackQuery, storage: JsonStorage, stats_service: StatisticsService):
    month = MonthKey.from_key(callback.data.rsplit(":", 1)[1])
    text, kb = await _build_month_view(storage, stats_service, month)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == CB_TOTAL)
async def cb_total(callback: CallbackQuery, storage: JsonStorage, stats_service: StatisticsService):
    managers = await storage.get_managers()
    assignments = await storage.get_assignments()
    corrections = await storage.get_corrections()
    stats = stats_service.build_stats(managers, assignments, corrections)
    text = _format_stats_block("Статистика за всё время", stats)
    months = stats_service.available_months(assignments, corrections)
    await callback.message.edit_text(text, reply_markup=months_keyboard(months))
    await callback.answer()


@router.callback_query(F.data == CB_BY_MONTH)
async def cb_by_month(callback: CallbackQuery, storage: JsonStorage, stats_service: StatisticsService):
    assignments = await storage.get_assignments()
    corrections = await storage.get_corrections()
    totals = stats_service.totals_by_month(assignments, corrections)
    lines = ["📅 По месяцам", ""]
    for mk, total in totals:
        lines.append(f"{mk.label()} — {total}")
    months = [mk for mk, _ in totals]
    await callback.message.edit_text("\n".join(lines), reply_markup=by_month_list_keyboard(months))
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB_BY_MONTH_PICK}:"))
async def cb_by_month_pick(callback: CallbackQuery, storage: JsonStorage, stats_service: StatisticsService):
    month = MonthKey.from_key(callback.data.rsplit(":", 1)[1])
    text, kb = await _build_month_view(storage, stats_service, month)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == CB_BACK)
async def cb_back(callback: CallbackQuery, storage: JsonStorage, stats_service: StatisticsService):
    month = stats_service.current_month()
    text, kb = await _build_month_view(storage, stats_service, month)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.message(Command("managers"))
async def cmd_managers(message: Message, storage: JsonStorage, config: Config):
    if message.chat.id != config.work_chat_id:
        return
    if not config.is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав для выполнения этой команды.")
        return
    managers = await storage.get_managers()
    if not managers:
        await message.answer("👥 Менеджеры\n\nСписок пуст.")
        return
    lines = ["👥 Менеджеры", ""]
    for m in managers:
        icon = "🟢" if m.get("active") else "🔴"
        lines.append(f"{icon} {m['name']} — @{m['username']} (ID: {m['telegram_id']})")
    await message.answer("\n".join(lines))


@router.message(Command("help"))
async def cmd_help(message: Message, config: Config):
    if config.is_admin(message.from_user.id):
        text = (
            "📊 Бот статистики заявок\n\n"
            "/info — статистика\n"
            "/managers — список менеджеров\n"
            "/add_manager — добавить менеджера\n"
            "/remove_manager — деактивировать менеджера\n"
            "/rename_manager — изменить имя\n"
            "/add_stat — добавить корректировку\n"
            "/remove_stat — убрать корректировку\n\n"
            "Для /add_manager лучше ответить на сообщение менеджера, "
            "чтобы Telegram ID определился автоматически."
        )
    else:
        text = "📊 Бот статистики заявок\n\n/info — статистика"
    await message.answer(text)