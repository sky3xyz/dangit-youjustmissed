from datetime import datetime
from zoneinfo import ZoneInfo

from .csv_utils import epoch_ms_for, secs_now, shift_ymd, ymd_of

WEEKDAY_NAMES = ("sunday", "monday", "tuesday", "wednesday", "thursday",
                 "friday", "saturday")

# "departures of that day" = 27h in GTFS
END_OF_SERVICE_DAY_SECS = 27 * 3600


def weekday_of_ymd(ymd: str) -> int:
    """0 (Sunday) .. 6 (Saturday) for a compact GTFS date string."""
    base = datetime.strptime(ymd, "%Y%m%d")
    return (base.weekday() + 1) % 7


def services_on(feed, ymd: str) -> frozenset[str]:
    """
    Service ids active on ymd: calendar ranges + weekday flags
    """
    cached = feed.days_cache.get(ymd)
    if cached is not None:
        return cached

    dow = WEEKDAY_NAMES[weekday_of_ymd(ymd)]
    active = {
        row.service_id
        for row in feed.calendar
        if row.start <= ymd <= row.end and dow in row.days
    }
    for service_id, exc_type in feed.exceptions.get(ymd, ()):
        if exc_type == 1:
            active.add(service_id)
        elif exc_type == 2:
            active.discard(service_id)

    result = frozenset(active)
    feed.days_cache[ymd] = result
    return result


def build_at(now: datetime, tz: ZoneInfo, at_param: str | None = None) -> dict:
    """
    clock constructor
    """
    if at_param is None:
        return {
            "ymd": ymd_of(now, tz),
            "secs": secs_now(now, tz),
            "epoch_ms": int(now.timestamp() * 1000),
            "tz": tz,
        }

    parts = at_param.split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise ValueError("at must be HH:MM or HH:MM:SS")
    h, m = int(parts[0]), int(parts[1])
    s = int(parts[2]) if len(parts) == 3 else 0
    if h > 23 or m > 59 or s > 59:
        raise ValueError("at is not a valid wall-clock time")

    secs = h * 3600 + m * 60 + s
    ymd = ymd_of(now, tz)
    return {"ymd": ymd, "secs": secs, "epoch_ms": epoch_ms_for(ymd, secs, tz), "tz": tz}
