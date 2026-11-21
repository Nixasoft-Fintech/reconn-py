import calendar
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def calculate_expiry_date(created_at: datetime) -> datetime:
    created_at = created_at.astimezone(IST)

    year = created_at.year
    month = created_at.month + 1

    if month > 12:
        month = 1
        year += 1

    day = min(
        created_at.day,
        calendar.monthrange(year, month)[1],
    )

    return created_at.replace(
        year=year,
        month=month,
        day=day,
    )
