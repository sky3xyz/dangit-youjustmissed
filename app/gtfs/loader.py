"""
loading gtfs staically. (one-time)
"""

import csv
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .csv_utils import hms_to_sec

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

GTFS_EXPORT_URL = "https://oct-gtfs-emasagcnfmcgeham.z01.azurefd.net/public-access/GTFSExport.zip"
REQUIRED_FILES = (
    "routes.txt", "calendar.txt", "calendar_dates.txt",
    "stops.txt", "trips.txt", "stop_times.txt",
)
DOWNLOAD_TIMEOUT_S = (10, 300)


# --- dataclasses

@dataclass(slots=True)
class Route:
    short: str
    long: str
    route_type: int
    color: str

@dataclass(slots=True)
class CalendarRow:
    service_id: str
    days: frozenset[str]
    start: str  # "YYYYMMDD"
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

@dataclass
class GtfsFeed:
    routes: dict[str, Route] = field(default_factory=dict)
    calendar: list[CalendarRow] = field(default_factory=list)
    exceptions: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    stops: dict[str, Stop] = field(default_factory=dict)
    trips: dict[str, Trip] = field(default_factory=dict)
    departures_by_stop: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    stop_times_by_trip: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    days_cache: dict[str, frozenset[str]] = field(default_factory=dict)

# --- row loaders
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
            days=frozenset(d for d in WEEKDAYS if c.get(d) == "1"),
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
        if s.get("location_type") not in ("", "0"):
            continue
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
    pending: dict[str, list[tuple[int, str]]] = {}
    by_trip: dict[str, list[tuple[int, str]]] = {}
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
            sec = hms_to_sec(dep)
            bucket = pending.get(stop_id)
            if bucket is None:
                bucket = pending[stop_id] = []
            bucket.append((sec, trip_id))
            bucket_t = by_trip.get(trip_id)
            if bucket_t is None:
                bucket_t = by_trip[trip_id] = []
            bucket_t.append((sec, stop_id))
    for stop_id, bucket in pending.items():
        bucket.sort()
        feed.departures_by_stop[stop_id] = bucket
    for trip_id, bucket in by_trip.items():
        bucket.sort()  # stop_sequence order ~= time order; sorting is the guarantee
        feed.stop_times_by_trip[trip_id] = bucket


# --- public constructors
def ensure_feed_files(data_dir: Path) -> None:
    """
    download and extract gtfs files from gtfs static.

    delete old files to redownload
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    if all((data_dir / f).is_file() for f in REQUIRED_FILES):
        return

    print(f"downloading GTFS export -> {data_dir}")
    response = requests.get(GTFS_EXPORT_URL, timeout=DOWNLOAD_TIMEOUT_S)
    response.raise_for_status()

    zip_path = data_dir / "GTFSExport.zip"
    zip_path.write_bytes(response.content)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad:
                raise RuntimeError(f"corrupt entry in GTFS export: {bad}")
            zf.extractall(data_dir)
    finally:
        zip_path.unlink(missing_ok=True)

    missing = [f for f in REQUIRED_FILES if not (data_dir / f).is_file()]
    if missing:
        raise RuntimeError(f"GTFS export is missing expected files: {missing}")


def build_feed(data_dir: Path) -> GtfsFeed:
    ensure_feed_files(data_dir)
    feed = GtfsFeed()
    _load_routes(data_dir / "routes.txt", feed)
    _load_calendar(data_dir / "calendar.txt", feed)
    _load_calendar_dates(data_dir / "calendar_dates.txt", feed)
    _load_stops(data_dir / "stops.txt", feed)
    _load_trips(data_dir / "trips.txt", feed)
    _index_stop_times(data_dir / "stop_times.txt", feed)
    return feed

def build_feed_async(data_dir: Path, on_done, on_error) -> threading.Thread:
    def run():
        try:
            on_done(build_feed(data_dir))
        except Exception as e:
            on_error(e)

    t = threading.Thread(target=run, name="gtfs-loader", daemon=True)
    t.start()
    return t
