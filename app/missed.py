"""
the last N scheduled departures at a stop that are in the past.

pure functions over a GtfsFeed
"""

from datetime import datetime, timezone

from .gtfs.csv_utils import epoch_ms_for, sec_to_clock, shift_ymd, ymd_to_dash_date
from .gtfs.service import END_OF_SERVICE_DAY_SECS, services_on

MAX_DAYS_BACK = 7
GRACE_SECS_TIER = (20, 60, 120)  # message thresholds


def excuse_for(minutes: int) -> str:
    if minutes > GRACE_SECS_TIER[2]:
        return "you were never making this one"
    if minutes > GRACE_SECS_TIER[1]:
        return "bold of you to call that \"almost\""
    if minutes > GRACE_SECS_TIER[0]:
        return "so close, and yet"
    return "you basically watched it leave"


def _epoch_iso(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _missed_entry(feed, ymd: str, dep_sec: int, trip_id: str, at: dict) -> dict | None:
    trip = feed.trips.get(trip_id)
    if trip is None:  # feed inconsistency: stop_time without a trip
        return None
    route = feed.routes.get(trip.route_id)

    # GTFS times >= 24:00:00 belong to the next real day; resolve the wall
    # clock and the calendar day separately, then recombine into a timestamp.
    wall_sec = dep_sec % 86400
    day_carry = dep_sec // 86400
    epoch_ms = epoch_ms_for(ymd, wall_sec, at["tz"]) + day_carry * 86_400_000

    missed_min = round((at["epoch_ms"] - epoch_ms) / 60_000)
    return {
        "route": route.short if route else trip.route_id,
        "route_long_name": route.long if route else "",
        "headsign": trip.headsign or "",
        "scheduled_departure": sec_to_clock(wall_sec),
        "schedule_date": ymd_to_dash_date(shift_ymd(ymd, day_carry)),
        "actually_departed_at": _epoch_iso(epoch_ms),
        "missed_by_minutes": missed_min,
        "excuse": excuse_for(missed_min),
        "_epoch_ms": epoch_ms,  # consumed by api.py for the Google legs call; popped before the response
    }


def missed_trips_at(feed, stop_id: str, count: int, at: dict) -> list[dict] | None:
    """
    the last `count` active departures at stop_id before the `at` clock

    returns None when the stop has no schedule data at all
    """
    departures = feed.departures_by_stop.get(stop_id)
    if not departures:
        return None

    missed: list[dict] = []
    ymd = at["ymd"]
    for _day_back in range(MAX_DAYS_BACK + 1):
        active = services_on(feed, ymd)
        cutoff = at["secs"] if ymd == at["ymd"] else END_OF_SERVICE_DAY_SECS
        for dep_sec, trip_id in reversed(departures):
            if len(missed) >= count:
                return missed
            if dep_sec > cutoff:
                continue
            trip = feed.trips.get(trip_id)
            if trip is None or trip.service_id not in active:
                continue
            entry = _missed_entry(feed, ymd, dep_sec, trip_id, at)
            if entry:
                missed.append(entry)
        ymd = shift_ymd(ymd, -1)
    return missed


def next_departure_at(feed, stop_id: str, at: dict) -> int | None:
    """
    epoch milliseconds of the next scheduled departure at stop_id
    none when the schedule has nothing upcoming in the next MAX_DAYS_BACK days
    """
    departures = feed.departures_by_stop.get(stop_id)
    if not departures:
        return None

    ymd = at["ymd"]
    for _day_forward in range(MAX_DAYS_BACK + 1):
        active = services_on(feed, ymd)
        floor = at["secs"] if ymd == at["ymd"] else -1
        for dep_sec, trip_id in departures:
            if dep_sec <= floor:
                continue
            trip = feed.trips.get(trip_id)
            if trip is None or trip.service_id not in active:
                continue
            wall_sec = dep_sec % 86400
            day_carry = dep_sec // 86400
            epoch_ms = epoch_ms_for(ymd, wall_sec, at["tz"]) + day_carry * 86_400_000
            if epoch_ms > at["epoch_ms"]:
                return epoch_ms
            # same service date but already departed in real time (a 26:xx
            # stop_time counted on today's schedule) - keep walking
        ymd = shift_ymd(ymd, 1)
    return None
