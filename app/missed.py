"""
the last N scheduled departures at a stop that are in the past.

pure functions over a GtfsFeed
"""

import math
from datetime import datetime, timezone

from .gtfs.csv_utils import epoch_ms_for, sec_to_clock, shift_ymd, ymd_to_dash_date
from .gtfs.service import END_OF_SERVICE_DAY_SECS, services_on

MAX_DAYS_BACK = 7
GRACE_SECS_TIER = (20, 60, 120)  # message thresholds
TRANSFER_WALK_M = 250            # re-boarding stops near the drop-off point


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


JOURNEY_MAX_TOTAL_S = 2 * 3600    # boarding to destination
JOURNEY_MAX_FIRST_LEG_S = 3600    # cap on the boarding leg before alighting
JOURNEY_TRANSFER_BUFFER_S = 180   # min transfer time when changing vehicles
JOURNEY_MAX_TRANSFER_WAIT_S = 30 * 60


def _epoch_ms_of(ymd: str, sec: int, tz) -> int:
    wall_sec = sec % 86400
    day_carry = sec // 86400
    return epoch_ms_for(ymd, wall_sec, tz) + day_carry * 86_400_000


def _leg(feed, trip_id: str, from_stop: str, to_stop: str,
         dep_sec: int, arr_sec: int) -> dict:
    trip = feed.trips.get(trip_id)
    route = feed.routes.get(trip.route_id) if trip else None
    stops = feed.stops
    return {
        "mode": "TRANSIT",
        "trip_id": trip_id,
        "route": route.short if route else (trip.route_id if trip else ""),
        "route_long_name": route.long if route else "",
        "headsign": trip.headsign if trip else "",
        "from": stops[from_stop].stop_name if from_stop in stops else from_stop,
        "to": stops[to_stop].stop_name if to_stop in stops else to_stop,
        "departure": sec_to_clock(dep_sec % 86400),
        "arrival": sec_to_clock(arr_sec % 86400),
        "duration_min": round((arr_sec - dep_sec) / 60),
    }


def _direct_arrival(feed, trip_id: str, from_stop: str, to_stop: str,
                    dep_sec: int) -> int | None:
    """arrival seconds at to_stop via trip_id after dep_sec at from_stop, if
    the trip goes there directly"""
    seq = feed.stop_times_by_trip.get(trip_id)
    if not seq:
        return None
    seen_from = False
    for sec, sid in seq:
        if not seen_from:
            if sid == from_stop and sec == dep_sec:
                seen_from = True
            continue
        if sid == to_stop and sec > dep_sec:
            return sec
        if sec - dep_sec > JOURNEY_MAX_TOTAL_S:
            return None
    return None


def _direct_arrivals(feed, trip_id: str, from_stop: str, to_stops: set[str],
                     dep_sec: int):
    """like _direct_arrival but checks every candidate destination; yields
    (arr_sec, stop_id) sorted by arrival (first = closest alighting)"""
    seq = feed.stop_times_by_trip.get(trip_id)
    if not seq:
        return
    seen_from = False
    hits = []
    for sec, sid in seq:
        if not seen_from:
            if sid == from_stop and sec == dep_sec:
                seen_from = True
            continue
        if sec - dep_sec > JOURNEY_MAX_TOTAL_S:
            break
        if sid in to_stops and sec > dep_sec:
            hits.append((sec, sid))
    hits.sort()
    yield from hits


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _near_boardables(feed, x_stop: str) -> list[str]:
    """stops within TRANSFER_WALK_M of x_stop (bus platform <-> O-Train
    platform of the same station, the other side of the street, ...);
    cached per feed"""
    cache = getattr(feed, "xfer_cache", None)
    if cache is None:
        cache = feed.xfer_cache = {}
    near = cache.get(x_stop)
    if near is None:
        ref = feed.stops[x_stop]
        near = [
            sid
            for sid, s in feed.stops.items()
            if sid in feed.departures_by_stop
            and _haversine_m(ref.stop_lat, ref.stop_lon, s.stop_lat, s.stop_lon) <= TRANSFER_WALK_M
        ]
        cache[x_stop] = near
    return near


def _transfer_legs(feed, trip_id: str, from_stop: str, to_stops: set[str],
                   dep_sec: int, active: frozenset[str], at: dict, ymd: str):
    """1-transfer journeys: ride trip_id partway, re-board elsewhere (same
    stop or a short walk away). yields (arr_sec, legs)"""
    seq = feed.stop_times_by_trip.get(trip_id)
    if not seq:
        return
    i = next((i for i, (_, sid) in enumerate(seq) if sid == from_stop and seq[i][0] == dep_sec), None)
    if i is None:
        return
    for j in range(i + 1, len(seq)):
        x_sec, x_stop = seq[j]
        if x_stop == from_stop or x_stop in to_stops:
            continue
        dwell = x_sec - dep_sec
        if dwell > JOURNEY_MAX_FIRST_LEG_S:
            break
        # candidate re-boards at x_stop or within walking distance of it
        cands = []
        for b_sid in _near_boardables(feed, x_stop):
            for d2, t2 in feed.departures_by_stop.get(b_sid, ()):
                if d2 < x_sec + JOURNEY_TRANSFER_BUFFER_S:
                    continue
                if d2 - x_sec > JOURNEY_MAX_TRANSFER_WAIT_S:
                    break
                if t2 == trip_id:
                    continue
                cands.append((d2, t2, b_sid))
        cands.sort()
        for d2, t2, b_sid in cands:
            trip2 = feed.trips.get(t2)
            if trip2 is None or trip2.service_id not in active:
                continue
            for out in _direct_arrivals(feed, t2, b_sid, to_stops, d2):
                arr2, out_stop = out
                total = arr2 - dep_sec
                if total > JOURNEY_MAX_TOTAL_S:
                    continue
                legs = [
                    _leg(feed, trip_id, from_stop, x_stop, dep_sec, x_sec),
                    _leg(feed, t2, b_sid, out_stop, d2, arr2),
                ]
                yield arr2, legs


def _normalize_stops(feed, stops) -> tuple[list[str], list[str]] | None:
    """single stop or list -> (departures-merged, valid ids); None when the
    request can't possibly be answered"""
    if isinstance(stops, str):
        stops = [stops]
    stops = [s for s in stops if s in feed.stops]
    return None if not stops else stops


def _merged_departures(feed, from_stops: list[str]) -> list[tuple[int, str, str]]:
    """(dep_sec, trip_id, board_stop) at every candidate boarding stop,
    sorted by time; a trip touching two candidates appears once with the
    later of the two boardings (you board at the nearest chance)"""
    best = {}
    for sid in from_stops:
        for dep_sec, trip_id in feed.departures_by_stop.get(sid, ()):
            best.setdefault((dep_sec, trip_id), sid)
    return sorted(((d, t, s) for (d, t), s in best.items()), key=lambda x: (x[0], x[1]))


def journeys_at(feed, from_stops, to_stops, count: int, at: dict,
                max_transfers: int = 1) -> list[dict] | None:
    """
    the last `count` A-to-B trips that already happened: departures at any of
    the candidate boarding stops (nearby platforms count - you can't always
    tell which side of the street) whose vehicle reached any of the candidate
    alighting stops (bus platform or train station, whichever is closer).
    direct or one transfer. every stop/time is the schedule that existed.
    """
    from_stops = _normalize_stops(feed, from_stops)
    to_stops = _normalize_stops(feed, to_stops)
    if from_stops is None or to_stops is None:
        return None
    to_set = set(to_stops)
    departures = _merged_departures(feed, from_stops)
    if not departures:
        return None

    journeys: list[dict] = []
    ymd = at["ymd"]
    for _day_back in range(MAX_DAYS_BACK + 1):
        active = services_on(feed, ymd)
        cutoff = at["secs"] if ymd == at["ymd"] else END_OF_SERVICE_DAY_SECS
        for dep_sec, trip_id, board_stop in reversed(departures):
            if len(journeys) >= count:
                return journeys
            if dep_sec > cutoff:
                continue
            trip = feed.trips.get(trip_id)
            if trip is None or trip.service_id not in active:
                continue
            dep_epoch_ms = _epoch_ms_of(ymd, dep_sec, at["tz"])
            found = []
            direct = next(_direct_arrivals(feed, trip_id, board_stop, to_set, dep_sec), None)
            if direct is not None:
                arr_sec, out_stop = direct
                found.append((arr_sec, [_leg(feed, trip_id, board_stop, out_stop,
                                             dep_sec, arr_sec)]))
            if max_transfers >= 1:
                found.extend(_transfer_legs(feed, trip_id, board_stop,
                                            to_set, dep_sec, active, at, ymd))
            for arr_sec, legs in found:
                journeys.append({
                    "from": legs[0]["from"],
                    "to": legs[-1]["to"],
                    "route": legs[0]["route"],
                    "headsign": legs[0]["headsign"],
                    "departure": legs[0]["departure"],
                    "arrival": legs[-1]["arrival"],
                    "duration_min": sum(l["duration_min"] for l in legs),
                    "schedule_date": ymd_to_dash_date(ymd),
                    "actually_departed_at": _epoch_iso(dep_epoch_ms),
                    "missed_by_minutes": round((at["epoch_ms"] - dep_epoch_ms) / 60_000),
                    "excuse": excuse_for(round((at["epoch_ms"] - dep_epoch_ms) / 60_000)),
                    "legs": legs,
                })
                break  # one journey per departure; keep the most direct
        ymd = shift_ymd(ymd, -1)
    return journeys  # empty list = nothing reachable in the feed


def next_journey_at(feed, from_stops, to_stops, at: dict,
                    max_transfers: int = 1) -> dict | None:
    """the next upcoming A-to-B trip: {"departure_epoch_ms", "arrival_epoch_ms"}
    for the stream wakeup trigger, same walk mirrored forward"""
    from_stops = _normalize_stops(feed, from_stops)
    to_stops = _normalize_stops(feed, to_stops)
    if from_stops is None or to_stops is None:
        return None
    to_set = set(to_stops)
    departures = _merged_departures(feed, from_stops)
    if not departures:
        return None

    ymd = at["ymd"]
    for _day_forward in range(MAX_DAYS_BACK + 1):
        active = services_on(feed, ymd)
        floor = at["secs"] if ymd == at["ymd"] else -1
        for dep_sec, trip_id, board_stop in departures:
            if dep_sec <= floor:
                continue
            trip = feed.trips.get(trip_id)
            if trip is None or trip.service_id not in active:
                continue
            arr_sec = next(_direct_arrivals(feed, trip_id, board_stop, to_set, dep_sec), None)
            if arr_sec is None:
                for arr2, _legs in _transfer_legs(feed, trip_id, board_stop,
                                                  to_set, dep_sec, active, at, ymd):
                    arr_sec = arr2
                    break
            if arr_sec is None:
                continue
            arr_epoch_ms = _epoch_ms_of(ymd, arr_sec, at["tz"])
            if arr_epoch_ms > at["epoch_ms"]:
                dep_epoch_ms = _epoch_ms_of(ymd, dep_sec, at["tz"])
                return {"departure_epoch_ms": dep_epoch_ms, "arrival_epoch_ms": arr_epoch_ms}
        ymd = shift_ymd(ymd, 1)
    return None
