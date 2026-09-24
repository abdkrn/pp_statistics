"""Расчёт статистики по реальным назначениям и корректировкам."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ManagerStat:
    telegram_id: int
    name: str
    username: str
    total: int
    auto: int = 0
    correction: int = 0


@dataclass(frozen=True)
class MonthKey:
    year: int
    month: int

    def label(self) -> str:
        months = [
            "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
            "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
        ]
        return f"{months[self.month - 1]} {self.year}"

    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @classmethod
    def from_key(cls, key: str) -> "MonthKey":
        year_s, month_s = key.split("-")
        return cls(int(year_s), int(month_s))


class StatisticsService:
    def __init__(self, timezone: str = "Europe/Moscow") -> None:
        self.tz = ZoneInfo(timezone)

    def _local_datetime(self, value: str | None) -> datetime:
        if not value:
            raise ValueError("Дата статистической записи отсутствует")
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.tz)
        return dt.astimezone(self.tz)

    def _record_datetime(self, record: dict, field: str) -> datetime:
        return self._local_datetime(record.get(field))

    def current_month(self) -> MonthKey:
        now = datetime.now(self.tz)
        return MonthKey(now.year, now.month)

    def filter_by_month(
        self,
        assignments: list[dict],
        corrections: list[dict],
        month: MonthKey,
    ) -> tuple[list[dict], list[dict]]:
        assignment_result = []
        correction_result = []
        for record in assignments:
            dt = self._record_datetime(record, "assigned_at")
            if dt.year == month.year and dt.month == month.month:
                assignment_result.append(record)
        for record in corrections:
            dt = self._record_datetime(record, "created_at")
            if dt.year == month.year and dt.month == month.month:
                correction_result.append(record)
        return assignment_result, correction_result

    def available_months(self, assignments: list[dict], corrections: list[dict]) -> list[MonthKey]:
        seen: dict[str, MonthKey] = {}
        for records, field in ((assignments, "assigned_at"), (corrections, "created_at")):
            for record in records:
                dt = self._record_datetime(record, field)
                mk = MonthKey(dt.year, dt.month)
                seen[mk.key()] = mk
        return sorted(seen.values(), key=lambda m: (m.year, m.month), reverse=True)

    def totals_by_month(
        self, assignments: list[dict], corrections: list[dict]
    ) -> list[tuple[MonthKey, int]]:
        counts: dict[str, int] = {}
        keys: dict[str, MonthKey] = {}

        for records, field in ((assignments, "assigned_at"), (corrections, "created_at")):
            for record in records:
                dt = self._record_datetime(record, field)
                mk = MonthKey(dt.year, dt.month)
                counts[mk.key()] = counts.get(mk.key(), 0)
                if field == "assigned_at":
                    counts[mk.key()] += 1
                else:
                    counts[mk.key()] += int(record.get("delta", 0))
                keys[mk.key()] = mk

        return sorted(
            ((keys[k], v) for k, v in counts.items()),
            key=lambda item: (item[0].year, item[0].month),
            reverse=True,
        )

    def build_stats(
        self,
        managers: list[dict],
        assignments: list[dict],
        corrections: list[dict],
    ) -> list[ManagerStat]:
        by_manager = {
            m["telegram_id"]: ManagerStat(
                telegram_id=m["telegram_id"],
                name=m["name"],
                username=m.get("username", ""),
                total=0,
            )
            for m in managers
        }

        mutable = {
            manager_id: {
                "telegram_id": stat.telegram_id,
                "name": stat.name,
                "username": stat.username,
                "total": stat.total,
                "auto": stat.auto,
                "correction": stat.correction,
            }
            for manager_id, stat in by_manager.items()
        }

        for assignment in assignments:
            mid = assignment.get("manager_telegram_id")
            if mid in mutable:
                mutable[mid]["total"] += 1
                mutable[mid]["auto"] += 1

        for correction in corrections:
            mid = correction.get("manager_telegram_id")
            if mid in mutable:
                delta = int(correction.get("delta", 0))
                mutable[mid]["total"] += delta
                mutable[mid]["correction"] += delta

        return [ManagerStat(**value) for value in sorted(mutable.values(), key=lambda x: x["total"], reverse=True)]
