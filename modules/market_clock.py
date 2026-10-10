"""A-share clocks use Shanghai time independently of the computer timezone."""

from datetime import datetime, timedelta, timezone


SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


def market_now(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    # Explicit naive arguments are market wall time; production now is aware.
    if value.tzinfo is None:
        value = value.replace(tzinfo=SHANGHAI)
    return value.astimezone(SHANGHAI)


def market_from_timestamp(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, SHANGHAI)


def continuous_market_open(value: datetime | None = None) -> bool:
    current = market_now(value)
    if current.weekday() >= 5:
        return False
    minute = current.hour * 60 + current.minute
    return 570 <= minute < 690 or 780 <= minute < 900
