"""
Бизнес-логика управления менеджерами поверх JsonStorage.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.storage import JsonStorage


@dataclass
class ManagerOpResult:
    ok: bool
    message: str
    manager: dict | None = None


class ManagersService:
    def __init__(self, storage: JsonStorage) -> None:
        self.storage = storage

    async def list_managers(self) -> list[dict]:
        """Вернуть список всех менеджеров."""
        return await self.storage.get_managers()

    async def add_manager(
        self,
        telegram_id: int,
        username: str,
        name: str,
    ) -> ManagerOpResult:
        """
        Добавить менеджера или обновить существующего.

        telegram_id должен быть реальным Telegram ID.
        """
        if not isinstance(telegram_id, int) or telegram_id <= 0:
            return ManagerOpResult(
                ok=False,
                message="invalid_telegram_id",
            )

        username = username.lstrip("@").strip()

        if not username:
            return ManagerOpResult(
                ok=False,
                message="invalid_username",
            )

        if not name.strip():
            return ManagerOpResult(
                ok=False,
                message="invalid_name",
            )

        existing = await self.storage.get_manager_by_telegram_id(telegram_id)

        was_active = existing["active"] if existing else None

        manager = await self.storage.add_manager(
            telegram_id=telegram_id,
            username=username,
            name=name.strip(),
        )

        if existing is None:
            return ManagerOpResult(
                ok=True,
                message="added",
                manager=manager,
            )

        if was_active is False:
            return ManagerOpResult(
                ok=True,
                message="reactivated",
                manager=manager,
            )

        return ManagerOpResult(
            ok=True,
            message="updated",
            manager=manager,
        )

    async def deactivate_manager(
        self,
        username: str,
    ) -> ManagerOpResult:
        """Деактивировать менеджера по username."""
        manager = await self.storage.set_manager_active(
            username,
            active=False,
        )

        if manager is None:
            return ManagerOpResult(
                ok=False,
                message="not_found",
            )

        return ManagerOpResult(
            ok=True,
            message="deactivated",
            manager=manager,
        )

    async def activate_manager(
        self,
        username: str,
    ) -> ManagerOpResult:
        """Активировать менеджера по username."""
        manager = await self.storage.set_manager_active(
            username,
            active=True,
        )

        if manager is None:
            return ManagerOpResult(
                ok=False,
                message="not_found",
            )

        return ManagerOpResult(
            ok=True,
            message="activated",
            manager=manager,
        )

    async def rename_manager(
        self,
        username: str,
        new_name: str,
    ) -> ManagerOpResult:
        """Изменить отображаемое имя менеджера."""
        manager = await self.storage.rename_manager(
            username,
            new_name.strip(),
        )

        if manager is None:
            return ManagerOpResult(
                ok=False,
                message="not_found",
            )

        return ManagerOpResult(
            ok=True,
            message="renamed",
            manager=manager,
        )

    async def find_by_username(
        self,
        username: str,
    ) -> dict | None:
        """Найти менеджера по username."""
        return await self.storage.get_manager_by_username(
            username
        )

    async def find_by_telegram_id(
        self,
        telegram_id: int,
    ) -> dict | None:
        """Найти менеджера по реальному Telegram ID."""
        return await self.storage.get_manager_by_telegram_id(
            telegram_id
        )

    async def resolve_real_telegram_id(
        self,
        username: str,
        real_telegram_id: int,
    ) -> dict | None:
        """
        Связать менеджера по username с реальным Telegram ID.

        Используется, когда Telegram прислал entity с реальным
        user.id для @username.

        Основным идентификатором менеджера является telegram_id.

        Если менеджер уже существует с положительным реальным ID,
        его ID не изменяется.

        Если менеджер найден по username и у него был старый/
        синтетический ID, он заменяется на реальный.
        """

        if not isinstance(real_telegram_id, int):
            return None

        if real_telegram_id <= 0:
            return None

        username = username.lstrip("@").strip().lower()

        if not username:
            return None

        # Сначала проверяем, существует ли уже менеджер
        # с таким реальным Telegram ID.
        existing_by_id = await self.storage.get_manager_by_telegram_id(
            real_telegram_id
        )

        if existing_by_id is not None:
            return existing_by_id

        # Ищем менеджера по username.
        manager = await self.storage.get_manager_by_username(
            username
        )

        if manager is None:
            return None

        current_id = manager.get("telegram_id")

        # Если ID уже реальный — ничего менять не нужно.
        if isinstance(current_id, int) and current_id > 0:
            return manager

        # Получаем полный список менеджеров.
        managers = await self.storage.get_managers()

        target = None

        for item in managers:
            if (
                item.get("telegram_id") == current_id
                and (
                    item.get("username") or ""
                ).lower()
                == username
            ):
                target = item
                break

        if target is None:
            return None

        # Заменяем старый ID на реальный Telegram ID.
        target["telegram_id"] = real_telegram_id

        # Сохраняем изменения.
        await self.storage.save_managers(managers)

        # Возвращаем обновлённую запись.
        return await self.storage.get_manager_by_telegram_id(
            real_telegram_id
        )