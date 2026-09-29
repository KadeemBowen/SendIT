"""Distance, routing, ETA and address lookup (OpenStreetMap services, no API keys)."""
import json
import math
import urllib.parse
import urllib.request
from functools import lru_cache

from . import config


def haversine_km(a, b):
    lat1, lng1 = map(math.radians, a)
    lat2, lng2 = map(math.radians, b)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def estimated_road_km(points):
    return sum(haversine_km(points[i], points[i + 1]) for i in range(len(points) - 1)) * config.ROAD_FACTOR


def minutes_for_km(km):
    return km / config.AVG_SPEED_KMH * 60


def _get_json(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": config.HTTP_USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def route(points):
    """Road route through points [(lat, lng), ...]. Uses OSRM, falls back to a straight-line estimate."""
    if config.USE_OSRM:
        coords = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points)
        url = f"https://router.project-osrm.org/route/v1/driving/{coords}?overview=simplified&geometries=geojson"
        try:
            data = _get_json(url, timeout=5)
            if data.get("code") == "Ok":
                best = data["routes"][0]
                return {
                    "distance_km": best["distance"] / 1000,
                    "duration_min": best["duration"] / 60,
                    "geometry": [[lat, lng] for lng, lat in best["geometry"]["coordinates"]],
                    "source": "osrm",
                }
        except Exception:
            pass
    km = estimated_road_km(points)
    return {
        "distance_km": km,
        "duration_min": minutes_for_km(km),
        "geometry": [list(p) for p in points],
        "source": "estimate",
    }


def _short_address(display_name):
    parts = [p.strip() for p in display_name.split(",")]
    return ", ".join(parts[:3])


@lru_cache(maxsize=512)
def search(q):
    params = {"q": q, "format": "jsonv2", "limit": 6}
    if config.GEOCODE_COUNTRY:
        params["countrycodes"] = config.GEOCODE_COUNTRY
    data = _get_json("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params), timeout=6)
    return [{"address": _short_address(d["display_name"]), "lat": float(d["lat"]), "lng": float(d["lon"])} for d in data]


@lru_cache(maxsize=512)
def reverse(lat, lng):
    params = {"lat": f"{lat:.5f}", "lon": f"{lng:.5f}", "format": "jsonv2", "zoom": 18}
    data = _get_json("https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(params), timeout=6)
    name = data.get("display_name")
    return _short_address(name) if name else None
