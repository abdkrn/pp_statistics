from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


CB_MONTH = "stats:month"
CB_TOTAL = "stats:total"
CB_BY_MONTH = "stats:by_month"
CB_BY_MONTH_PICK = "stats:month_pick"
CB_BACK = "stats:back"


def statistics_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 По месяцам",
                    callback_data=CB_BY_MONTH,
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 За всё время",
                    callback_data=CB_TOTAL,
                )
            ],
        ]
    )


def months_keyboard(months: list) -> InlineKeyboardMarkup:
    buttons = []

    for month in months:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=month.label(),
                    callback_data=f"{CB_BY_MONTH_PICK}:{month.key()}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=CB_BACK,
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def by_month_list_keyboard(
    months: list[str],
) -> InlineKeyboardMarkup:
    return months_keyboard(months)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data=CB_BACK,
                )
            ]
        ]
    )