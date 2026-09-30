"""Ventanas de publicación en hora costarricense, recalculadas en cada ciclo."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("America/Costa_Rica")


@dataclass(frozen=True)
class DateWindow:
    start: datetime | None
    end: datetime | None
    end_exclusive: bool = False

    def contains(self, value: datetime) -> bool:
        return (self.start is None or value >= self.start) and (
            self.end is None or (value < self.end if self.end_exclusive else value <= self.end)
        )

    @classmethod
    def parse(cls, start="yesterday", end="now", *, now=None):
        now = now or datetime.now(ZONE)
        today = now.astimezone(ZONE).date()

        def boundary(raw, upper):
            value = raw.strip().lower()
            if value in {"all", "none"}:
                return None, False
            if value == "now":
                return now, False
            if value in {"yesterday", "today"}:
                day = today - timedelta(days=value == "yesterday")
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                day = date.fromisoformat(value)
            else:
                try:
                    instant = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError as error:
                    raise ValueError("Usa YYYY-MM-DD, ISO 8601 con zona, yesterday, today, now o all") from error
                if instant.tzinfo is None or instant.utcoffset() is None:
                    raise ValueError("Las fechas con hora deben incluir zona horaria")
                return instant, False
            if upper:
                day += timedelta(days=1)
            return datetime.combine(day, time.min, ZONE), upper

        lower, _ = boundary(start, False)
        upper, exclusive = boundary(end, True)
        if lower is not None and upper is not None and (
            lower > upper or (exclusive and lower == upper)
        ):
            raise ValueError("--from debe ser anterior o igual a --until")
        return cls(lower, upper, exclusive)
