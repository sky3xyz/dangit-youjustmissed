"""
Google Route API calls

Uses POST calls to compareRoutes with localhost:8080 as refferer (change in prod)
"""

from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import requests

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
REQUEST_TIMEOUT_S = 20
FIELD_MASK = (
    "routes.legs.steps.transitDetails,"
    "routes.legs.steps.travelMode,"
    "routes.legs.steps.navigationInstruction,"
    "routes.legs.steps.distanceMeters,"
    "routes.legs.steps.staticDuration"
)

def _fetch_routes(api_key: str, referer: str, payload: dict) -> dict:
    response = requests.post(
        ROUTES_URL,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": FIELD_MASK,
            "Referer": referer,
        },
        json=payload,
        timeout=REQUEST_TIMEOUT_S,
    )
    try:
        return response.json()
    except ValueError:
        return {"status": f"HTTP {response.status_code}"}

def _latlng(spec: str) -> dict:
    lat, _, lon = spec.partition(",")
    return {"location": {"latLng": {"latitude": float(lat), "longitude": float(lon)}}}

def _ok(routes_response: dict, used_fallback: bool) -> dict:
    routes = routes_response.get("routes", [])
    return {
        "used_departure_fallback": used_fallback,
        "legs": extract_legs(routes[0]) if routes else [],
    }

def extract_legs(route: dict[str, Any]) -> list[dict]:
    legs = []
    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            if step.get("travelMode") == "TRANSIT" and step.get("transitDetails"):
                td = step["transitDetails"]
                line = td.get("transitLine", {})
                stops = td.get("stopDetails", {})
                legs.append({
                    "mode": "TRANSIT",
                    "line": line.get("nameShort") or line.get("name") or "",
                    "line_color": line.get("color"),
                    "agency": (td.get("transitLine", {}).get("agencies") or [{}])[0]
                              .get("name"),
                    "headsign": td.get("headsign", ""),
                    "from": stops.get("departureStop", {}).get("name"),
                    "departure_time": stops.get("departureTime"),
                    "to": stops.get("arrivalStop", {}).get("name"),
                    "arrival_time": stops.get("arrivalTime"),
                })
            else:
                legs.append({
                    "mode": step.get("travelMode", "WALK"),
                    "instruction": (step.get("navigationInstruction") or {})
                                   .get("instructions", "").strip(),
                    "distance_m": step.get("distanceMeters"),
                    "duration_s": _duration_to_s(step.get("staticDuration")),
                })
    return legs

def _duration_to_s(duration: str | None) -> int | None:
    """convert from routes API durations"""
    if not duration:
        return None
    try:
        return round(float(duration.rstrip("s")))
    except ValueError:
        return None

@lru_cache(maxsize=256)
def _legs_cached(api_key: str, referer: str, origin: str, destination: str,
                 at_minute: int | None) -> dict:
    base = {
        "origin": _latlng(origin),
        "destination": _latlng(destination),
        "travelMode": "TRANSIT",
    }

    used_fallback = False
    if at_minute is not None:
        departure_iso = datetime.fromtimestamp(
            at_minute * 60, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = _fetch_routes(api_key, referer, {**base, "departureTime": departure_iso})
        if result.get("routes"): # check for errors
            return _ok(result, used_fallback)
        used_fallback = True  # past departureTime: refused; retry from "now"

    result = _fetch_routes(api_key, referer, base)
    if not result.get("routes"):
        return {
            "error": _error_reason(result),
            "used_departure_fallback": used_fallback,
        }
    return _ok(result, used_fallback)

def _error_reason(result: dict) -> str:
    err = result.get("error") or {}
    return err.get("reason") or err.get("status") or "UNKNOWN_ERROR"


PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELD_MASK = "places.displayName,places.formattedAddress,places.location"


@lru_cache(maxsize=256)
def _place_cached(api_key: str, referer: str, text: str) -> dict:
    response = requests.post(
        PLACES_URL,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": PLACES_FIELD_MASK,
            "Referer": referer,
        },
        json={"textQuery": text},
        timeout=REQUEST_TIMEOUT_S,
    )
    try:
        body = response.json()
    except ValueError:
        return {"error": f"HTTP {response.status_code}"}
    places = body.get("places")
    if not places:
        return {"error": body.get("error", {}).get("message") or "place not found"}
    place = places[0]
    loc = place.get("location", {})
    return {
        "name": (place.get("displayName") or {}).get("text"),
        "address": place.get("formattedAddress"),
        "lat": loc.get("latitude"),
        "lon": loc.get("longitude"),
    }


def place_for(api_key: str, referer: str, text: str) -> dict:
    """
    free text (address, landmark, name) -> {"lat", "lon", "name", "address"}
    or {"error": reason}
    """
    return _place_cached(api_key, referer, text)


def legs_for(api_key: str, referer: str, origin: str, destination: str,
             at_ms: int | None = None) -> dict:
    at_minute = None
    if at_ms is not None:
        at_minute = int(at_ms // 1000) // 60
    return _legs_cached(api_key, referer, origin, destination, at_minute)
