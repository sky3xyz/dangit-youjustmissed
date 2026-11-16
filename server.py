from flask import Flask, Response, request, jsonify, stream_with_context
from flask_cors import CORS

import json
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import send_from_directory

from app.config import load_settings
from app.gtfs.loader import build_feed_async
from app.gtfs.service import build_at
from app.missed import missed_trips_at, next_departure_at, journeys_at, next_journey_at
from app.google import legs_for, place_for

SSE_GRACE_S = 15
SSE_HEARTBEAT_S = 25
SSE_IDLE_S = 60

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__)
CORS(app)

settings = load_settings()
tz = ZoneInfo(settings.timezone)
state = {"feed": None, "ready": threading.Event()}


def find_stop(feed, q):
    stop = feed.stops.get(q)
    if stop:
        return stop
    lowered = q.lower()
    for stop in feed.stops.values():
        if stop.stop_code and stop.stop_code.lower() == lowered:
            return stop
    matches = [s for s in feed.stops.values() if lowered in s.stop_name.lower()]
    if not matches:
        return None
    return min(matches, key=lambda s: len(s.stop_name))


def clean(trips):
    if trips is None:
        return None
    for m in trips:
        m.pop("_epoch_ms", None)
    return trips


def stop_brief(stop):
    return {"id": stop.stop_id, "code": stop.stop_code, "name": stop.stop_name}


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


CANDIDATE_RADIUS_M = 450
CANDIDATE_COUNT = 4


def candidates(feed, lat, lon, boardable):
    """
    the nearest few stops to a coordinate (nearby platforms count - which side
    of the street, or the train platform instead of the bus one, is decided by
    the schedule, not the pin)
    """
    scored = []
    for stop in feed.stops.values():
        if boardable and stop.stop_id not in feed.departures_by_stop:
            continue
        d = _haversine_m(lat, lon, stop.stop_lat, stop.stop_lon)
        if d <= CANDIDATE_RADIUS_M:
            scored.append((d, stop))
    scored.sort(key=lambda t: t[0])
    return [stop for _, stop in scored[:CANDIDATE_COUNT]]


def resolve_place(feed, text, boardable):
    """
    text -> (candidate stops, place) where place is None for direct stop-name
    matches or {query, place, address, lat, lon, snapped_to, distance_m} for
    geocodes; candidate stops is [] when nothing resolved. a substring match
    expands to nearby platforms (the text "carleton" shouldn't pin you to the
    bus platform when the train station is 200m away).
    """
    stop = find_stop(feed, text)
    if stop is not None:
        return candidates(feed, stop.stop_lat, stop.stop_lon, boardable), None
    if not settings.google_api_key:
        return [], None
    geo = place_for(settings.google_api_key, settings.google_referer, text)
    if "error" in geo or geo.get("lat") is None:
        return [], None
    stops = candidates(feed, geo["lat"], geo["lon"], boardable)
    if not stops:
        return [], None
    nearest_d = _haversine_m(geo["lat"], geo["lon"], stops[0].stop_lat, stops[0].stop_lon)
    return stops, {
        "query": text,
        "place": geo.get("name"),
        "address": geo.get("address"),
        "lat": geo["lat"],
        "lon": geo["lon"],
        "snapped_to": stop_brief(stops[0]),
        "distance_m": round(nearest_d),
    }


def iso(ms):
    return None if ms is None else datetime.fromtimestamp(
        ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_ms():
    return int(time.time() * 1000)


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def await_feed():
    state["ready"].wait(timeout=240)
    return state["feed"]


@app.route("/", methods=["GET"])
def landing():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/styles.css", methods=["GET"])
def stylesheet():
    return send_from_directory(BASE_DIR, "styles.css")


@app.errorhandler(Exception)
def on_error(e):
    if isinstance(e, ValueError):
        return jsonify({"error": str(e)}), 400
    return jsonify({"error": str(e)}), 500


@app.route("/healthz", methods=["GET"])
def healthz():
    if not state["ready"].is_set():
        return jsonify({"status": "indexing"})
    return jsonify({"status": "ok" if state["feed"] else "error"})


@app.route("/stops", methods=["GET"])
def stops():
    feed = await_feed()
    if feed is None:
        return jsonify({"error": "still indexing"}), 503
    q = (request.args.get("q") or "").lower()
    matches = [
        {"stop_id": s.stop_id, "stop_code": s.stop_code, "name": s.stop_name,
         "lat": s.stop_lat, "lon": s.stop_lon}
        for s in feed.stops.values()
        if not q or q in s.stop_name.lower() or q == (s.stop_code or "")
    ]
    return jsonify({"count": len(matches), "results": matches[:50]})


def snapped_brief(place):
    return {
        "query": place["query"],
        "place": place.get("place"),
        "address": place.get("address"),
        "snapped_to": place.get("snapped_to"),
        "distance_m": place.get("distance_m"),
    }


def geo_of(stop, place):
    """
    coordinates for the Google legs call: geocoded place when we have one, otherwise the snapped stop itself
    """
    if place and place.get("lat") is not None:
        return f"{place['lat']},{place['lon']}"
    return f"{stop.stop_lat},{stop.stop_lon}"


@app.route("/api/trips", methods=["GET"])
def trips_json():
    feed = await_feed()
    if feed is None:
        return jsonify({"error": "still indexing"}), 503
    to_q = request.args.get("to")
    at = build_at(datetime.now(), tz, request.args.get("at"))
    if to_q:
        frm_stops, frm_place = resolve_place(feed, request.args.get("from") or settings.default_stop, boardable=True)
        to_stops, to_place = resolve_place(feed, to_q, boardable=False)
        if not frm_stops or not to_stops:
            unresolved = request.args.get("from") if not frm_stops else to_q
            return jsonify({"error": "cannot resolve stop", "unresolved": unresolved,
                            "hint": "use a stop code/id/name, /stops?q=, or an address"}), 404
        frm_ids = [s.stop_id for s in frm_stops]
        to_ids = [s.stop_id for s in to_stops]
        nxt = next_journey_at(feed, frm_ids, to_ids, at)
        return jsonify({
            "from": stop_brief(frm_stops[0]),
            "to": stop_brief(to_stops[0]),
            "from_stops": [stop_brief(s) for s in frm_stops],
            "to_stops": [stop_brief(s) for s in to_stops],
            "from_snapped": snapped_brief(frm_place) if frm_place else None,
            "to_snapped": snapped_brief(to_place) if to_place else None,
            "as_of": iso(now_ms()),
            "looked_back_until": f"{at['ymd']} {at['secs'] // 3600:02d}:{at['secs'] % 3600 // 60:02d}:00",
            "next_journey_at": iso(nxt["departure_epoch_ms"]) if nxt else None,
            "missed_journeys": clean(journeys_at(feed, frm_ids, to_ids, 3, at)),
        })
    stop = find_stop(feed, request.args.get("stop") or settings.default_stop)
    if stop is None:
        return jsonify({"error": "unknown stop", "hint": "try /stops?q=hurdman"}), 404
    return jsonify({
        "stop": stop_brief(stop),
        "as_of": iso(now_ms()),
        "looked_back_until": f"{at['ymd']} {at['secs'] // 3600:02d}:{at['secs'] % 3600 // 60:02d}:00",
        "next_departure_at": iso(next_departure_at(feed, stop.stop_id, at)),
        "missed_trips": clean(missed_trips_at(feed, stop.stop_id, 3, at)),
    })


@app.route("/api/trip", methods=["GET"])
def stream_trip():
    def event_stream():
        start = request.args.get("start")
        end = request.args.get("end")
        to_q = request.args.get("to")

        feed = await_feed()
        if feed is None:
            yield sse("error", {"error": "feed unavailable"})
            return
        if to_q:
            frm_stops, frm_place = resolve_place(feed, request.args.get("from") or settings.default_stop, boardable=True)
            to_stops, to_place = resolve_place(feed, to_q, boardable=False)
            if not frm_stops or not to_stops:
                unresolved = request.args.get("from") if not frm_stops else to_q
                yield sse("error", {"error": "cannot resolve stop", "unresolved": unresolved})
                return
            frm_ids = [s.stop_id for s in frm_stops]
            to_ids = [s.stop_id for s in to_stops]
            frm_name, to_name = frm_stops[0].stop_name, to_stops[0].stop_name
            frm_geo, to_geo = geo_of(frm_stops[0], frm_place), geo_of(to_stops[0], to_place)
            snap = {
                "from": snapped_brief(frm_place) if frm_place else None,
                "to": snapped_brief(to_place) if to_place else None,
            }
        else:
            frm_ids = to_ids = frm_name = to_name = None
            stop_q = request.args.get("stop") or settings.default_stop
            stop = find_stop(feed, stop_q)
            if stop is None:
                yield sse("error", {"error": f"unknown stop: {stop_q}"})
                return

        g_start = start or (frm_geo if to_q else None)
        g_end = end or (to_geo if to_q else None)
        if g_start and g_end:
            if not settings.google_api_key:
                yield sse("error", {"error": "legs require GOOGLE_MAPS_API_KEY"})
            else:
                result = legs_for(settings.google_api_key, settings.google_referer,
                                  g_start, g_end, now_ms())
                if "legs" in result:
                    legs = [{k: v for k, v in leg.items()
                             if k not in ("departure_time", "arrival_time")}
                            for leg in result["legs"]]
                    yield sse("legs", legs)
                else:
                    yield sse("error", result)

        def missed_payload():
            at = build_at(datetime.now(), tz, None)
            if to_ids:
                return {
                    "from": frm_name, "to": to_name,
                    "snapped": snap,
                    "missed_journeys": clean(journeys_at(feed, frm_ids, to_ids, 3, at)),
                }
            return {
                "stop": stop_q,
                "missed_trips": clean(missed_trips_at(feed, stop.stop_id, 3, at)),
            }

        yield sse("missed", missed_payload())

        while True:
            at = build_at(datetime.now(), tz, None)
            if to_ids:
                nxt = next_journey_at(feed, frm_ids, to_ids, at)
                next_ms = nxt["departure_epoch_ms"] if nxt else None
            else:
                next_ms = next_departure_at(feed, stop.stop_id, at)
            if next_ms is None:
                time.sleep(SSE_IDLE_S)
                continue
            target_ms = next_ms + SSE_GRACE_S * 1000
            while now_ms() < target_ms:
                now = now_ms()
                yield f": ping {now}\n\n"
                time.sleep(min(SSE_HEARTBEAT_S, max((target_ms - now) / 1000, 0.1)))
            payload = missed_payload()
            yield sse("missed", payload)
            journeys = payload.get("missed_journeys") or []
            if journeys:
                # the "you just missed it" push: the newest missed journey must
                # be the departure we just slept past, not an old one
                dep_ms = int(datetime.fromisoformat(
                    journeys[0]["actually_departed_at"].replace("Z", "+00:00")
                ).timestamp() * 1000)
                if dep_ms > next_ms - 60_000:
                    yield sse("just_missed", journeys[0])

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    def _on_done(f):
        state["feed"] = f
        state["ready"].set()
        print("GTFS index ready")

    build_feed_async(
        settings.data_dir,
        on_done=_on_done,
        on_error=lambda e: print("feed build failed:", e),
    )
    print("missed-trips API on :8080; indexing schedule in background...")
    app.run(host="0.0.0.0", threaded=True, port=8080)
