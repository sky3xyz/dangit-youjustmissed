# dangit-youjustmissed

- ``fn`` -> returns: more info

### app/config.py
- ``Settings`` dataclass: contains port(int), default_stop(str), timezone(str), google_api_key(str | None), google_referer(str), data_dir(Path)

- ``load_settings()`` -> ``Settings``: load_dotenv(), basic loading error checking

### app/gtfs/csv_utils.py
- ``parse_csv_line(line: str)`` -> ``list[str]``: read csv
- ``hms_to_sec(t: str)`` -> ``int``: HH:MM:SS to seconds
- ``sec_to_clock(sec: int)`` -> ``str``: sec to clock string. 
- ``ymd_of(d: datetime)`` -> ``str``
- ``shift_ymd(ymd: str, days: int)`` -> ``str``

### app/gtfs/loader.py
- ``GtfsFeed`` dataclass
- ``Trip`` dataclass
- ``Stop`` dataclass
- ``CalendarRow`` dataclass
- ``Route`` dataclass
- ``load_file(filename, row_fn)`` -> ``None``: reads routes, calendar, etc.
- ``index_stop_times(feed)`` -> ``None``: Streams ``stop_times.txt``
- ``build_feed(data_dir)`` -> ``GtfsFeed``: fancy constructor

### app/gtfs/service.py
- ``weekday_of_ymd(ymd: str)`` -> ``int``: 0-6
- ``services_on(feed, ymd)`` -> ``set[str]``
- ``build_at(ymd, secs)`` -> ``dict``: constructor for ymd, secs, clock

### app/missed.py
- ``missed_trips_at(feed, stop_id, count, at)`` -> ``list[dict]`` or ``None``
- ``missed_by_minutes(epoch_ms, at_epoch_ms)`` -> ``int``
