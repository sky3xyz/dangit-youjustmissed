# dangit, you just missed it

*This project was made by Omar Dahman (sky3xyz) and Aidan Voigt (Csilarion) for the [stupid ideas hackathon hosted on November 15th 2026 in Ottawa](https://www.stupideas.com/ottawa).*
*It won 1 of 4 main tracks, the 'stupidest API' award.*

An API that the last 3 trips you could have taken for Point A to Point B, and alerts you when you just missed a trip.

This project uses Google Maps' Routes API and OC Transpo's GTFS static schedules.

```json
{
  "route": "39",
  "departure": "07:57:00",
  "arrival": "08:55:16",
  "missed_by_minutes": 510,
  "excuse": "you were never making this one"
}
```

For multi-leg trips (bus + transfer + bus), each leg is reconstructed from the schedule, and Google's Routes API decorates the next trip with real directions.

## Run it

```bash
uv run server.py
```

By default, the server runs on port 8080.

The first start downloads the GTFS feed (~90 MB zipped) into `data/` and indexes it, the `/healthz` endpoint says `indexing` whilst it is indexing, then `ok`.

`.env`:

```
GOOGLE_MAPS_API_KEY=...   # optional: enables the legs decoration
GOOGLE_REFERER=http://localhost:8080   # Google Maps keys are referer-allowlisted
PORT=8080
DEFAULT_STOP=3023         # Hurdman A
```

## Endpoints

| Endpoint | What it does |
|---|---|
| `GET /` | the UI |
| `GET /healthz` | `ok` / `indexing` |
| `GET /stops?q=hurdman` | stop lookup: id, code, name substring |
| `GET /api/trips?from=&to=` | last 3 missed A -> B journeys (JSON) |
| `GET /api/trips?stop=` | last 3 missed departures at one stop |
| `GET /api/trip?from=&to=` | same, as an SSE stream that pushes when you miss the next one |

Places can be real stop codes (`3023`), stop ids (`9951`), stop names (`Hurdman`), or free text (`455 Laurier Ave Ottawa`). Additionally, free text is geocoded via Places Text Search and snapped to the nearest boardable stop (distance reported in `from_snapped`/`to_snapped`).

### Example

```bash
curl 'http://localhost:8080/api/trips?from=hurdman&to=Bayshore Shopping Centre'
```

SSE events on `/api/trip`: `legs` (Google directions for the next trip),
`missed` (journey list, re-emitted every time a departure passes + 15s grace),
`error`. Comments `: ping` keep proxies quiet.

## How it works

```
data/ (GTFS zip) -> loader.py -> GtfsFeed (in-memory indexes)
                                   |
   service.py: which services ran on a given day (calendar + exceptions)
   missed.py:  missed_trips_at   — walk departures backward, decorate w/ excuses
               journeys_at       — A->B: same-trip reach, or 1-transfer search
               next_departure_at / next_journey_at — the stream wakeup triggers
   google.py:  Routes API legs + Places geocoding (referer-locked key)
   server.py:  Flask: JSON + SSE + static UI
```

Time handling is the fun part: stop times can exceed 24:00:00 (last buses at
"26:40"), so GTFS service-day seconds are kept separate from wall clock and
recombined into real timestamps with `America/Toronto` via ZoneInfo.

## File map

```
├── app
│   ├── config.py -> Settings from .env
│   ├── google.py -> Routes API v2 legs, Places geocoding, caching
│   ├── gtfs
│   │   ├── csv_utils.py -> csv parsing utils
│   │   ├── __init__.py
│   │   ├── loader.py -> feed download/extract, dataclasses, indexes
│   │   └── service.py -> service-day logic, clock construction
│   ├── __init__.py
│   └── missed.py
├── data
│   └── *extracted data from OC Transpo's GTFS static static files go here*
├── index.html -> Frontend
├── pyproject.toml 
├── README.md -> this file :)
├── server.py -> Flask app, SSE stream, static file serving
├── styles.css -> Stylesheet for Frontend
└── uv.lock
```

## Deploy

During the Hackathon, we had setup a GitHub Actions workflow that rsyncs to a GCP e2-micro VM on every push to `main`, runs `uv sync`, restarts the systemd unit (`missed-trips`), and polls `/healthz` until the index is warm. The VM keeps its `data/` and `.env` across deploys (rsync-excluded), so the index stays warm. Required repo secrets: `VM_HOST`, `VM_SSH_KEY`.

## LLM Disclosure

Since this was a one day hackathon, we used Large Language Models (LLMs) to draft portions of this codebase, all code was tested and reviewed by us.
Specifically, we used the GLM 5.3 and Gemini 3.5 flash models.
