import csv
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .csv_utils import hms_to_sec

@dataclass(slots=True)
class Route:
    short: str
    long: str
    route_type: int
    color: str #routes color based on oc's gtfs spec

@dataclass(slots=True)
class CalendarRow:
    service_id: str
    days: frozenset[str]
    start: str
    end: str

@dataclass(slots=True)
class Stop:
    stop_id: str
    stop_code: str
    stop_name: str
    stop_lat: float
    stop_lon: float

@dataclass(slots=True)
class Trip:
    service_id: str
    headsign: str
    route_id: str

@dataclass(frozen=True)
class GtfsFeed:
    routes: dict[str, Route] = field(default_factory=dict)
    calendar: list[CalendarRow] = field(default_factory=list)
    exceptions: dict[str, list[tuple[str,int]]] = field(default_factory=dict)
    stops: dict[str, Stop] = field(default_factory=dict)
    trips: dict[str, Trip] = field(default_factory=dict)
    departures_by_stop: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    days_cache: dict[str, set[str]] = field(default_factory=dict)

# --- loading ---
def _iter_rows(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            yield dict(zip(header, row))


def _load_routes(path: Path, feed: GtfsFeed) -> None:
    for r in _iter_rows(path):
        feed.routes[r["route_id"]] = Route(
            short=r["route_short_name"],
            long=r["route_long_name"],
            route_type=int(r["route_type"] or 0),
            color=r["route_color"],
        )

def _load_calendar(path: Path, feed: GtfsFeed) -> None:
    for c in _iter_rows(path):
        feed.calendar.append(CalendarRow(
            service_id=c["service_id"],
            days=frozenset(d for d in WEEKDAYS if c[d] == "1"),
            start=c["start_date"],
            end=c["end_date"],
        ))


def _load_calendar_dates(path: Path, feed: GtfsFeed) -> None:
    for e in _iter_rows(path):
        feed.exceptions.setdefault(e["date"], []).append(
            (e["service_id"], int(e["exception_type"]))
        )


def _load_stops(path: Path, feed: GtfsFeed) -> None:
    for s in _iter_rows(path):
        feed.stops[s["stop_id"]] = Stop(
            stop_id=s["stop_id"],
            stop_code=s["stop_code"],
            stop_name=s["stop_name"],
            stop_lat=float(s["stop_lat"]),
            stop_lon=float(s["stop_lon"]),
        )


def _load_trips(path: Path, feed: GtfsFeed) -> None:
    for t in _iter_rows(path):
        feed.trips[t["trip_id"]] = Trip(
            service_id=t["service_id"],
            headsign=t["trip_headsign"],
            route_id=t["route_id"],
        )

def _index_stop_times(path: Path, feed: GtfsFeed) -> None:
    """
    expensive function that loads all info as typles
    """
    pending: dict[str, list[tuple[int, str]]] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        i_trip = header.index("trip_id")
        i_dep = header.index("departure_time")
        i_stop = header.index("stop_id")
        for row in reader:
            stop_id = row[i_stop]
            dep = row[i_dep]
            trip_id = row[i_trip]
            if not stop_id or not dep or not trip_id:
                continue
            bucket = pending.get(stop_id)
            if bucket is None:
                bucket = pending[stop_id] = []
            bucket.append((hms_to_sec(dep), trip_id))
    for stop_id, bucket in pending.items():
        bucket.sort()
        feed.departures_by_stop[stop_id] = bucket

# --- ctor ---
def build_feed(data_dir: Path) -> GtfsFeed:
    feed = GtfsFeed()
    p = lambda name: data_dir / name
    _load_routes(p("routes.txt"), feed)
    _load_calendar(p("calendar.txt"), feed)
    _load_calendar_dates(p("calendar_dates.txt"), feed)
    _load_stops(p("stops.txt"), feed)
    _load_trips(p("trips.txt"), feed)
    _index_stop_times(p("stop_times.txt"), feed)
    return feed

def build_feed_async(data_dir: Path, on_done=None, on_error=None) -> threading.Thread:
    def run():
        try:
            feed = build_feed(data_dir)
            if on_done:
                on_done(feed)
        except Exception as e:
            if on_error:
                on_error(e)

    t = threading.Thread(target=run, name="gtfs-load", daemon=True)
    t.start()
    return t
