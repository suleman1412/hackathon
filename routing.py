import os
import json
import time
import requests
import logging

logger = logging.getLogger(__name__)

ORS_BASE = "https://api.openrouteservice.org"
ORS_API_KEY = os.getenv("ORS_API_KEY", "")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_BASE = "https://router.project-osrm.org/route/v1"

MODE_PROFILES = {
    "bike": "cycling",
    "walk": "foot",
    "scooter": "cycling",
}

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "location.json")


def _load_location_cache() -> dict:
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_location_cache(cache: dict):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def _normalize_query(query: str) -> str:
    return query.strip().lower()


def geocode(query: str) -> dict:
    key = _normalize_query(query)
    cache = _load_location_cache()
    if key in cache:
        entry = cache[key]
        logger.info(f"Geocode cache hit for '{query}'")
        return {
            "lat": entry["lat"],
            "lon": entry["lon"],
            "display_name": entry["display_name"],
        }

    headers = {"User-Agent": "GreenCleanCommute/1.0 (Nuremberg Datathon)"}
    params = {"q": query, "format": "json", "limit": 1, "countrycodes": "de"}

    try:
        resp = requests.get(NOMINATIM_URL, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data:
            result = {
                "lat": float(data[0]["lat"]),
                "lon": float(data[0]["lon"]),
                "display_name": data[0].get("display_name", query),
            }
            cache[key] = {**result, "cached_at": time.time()}
            _save_location_cache(cache)
            return result
    except Exception as e:
        logger.error(f"Geocoding failed for '{query}': {e}")

    if ORS_API_KEY:
        try:
            headers = {"Authorization": ORS_API_KEY}
            params = {"text": query, "size": 1, "boundary.country": "DE"}
            resp = requests.get(f"{ORS_BASE}/geocode/search", headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("features"):
                feat = data["features"][0]
                coords = feat["geometry"]["coordinates"]
                result = {
                    "lat": coords[1],
                    "lon": coords[0],
                    "display_name": feat["properties"].get("label", query),
                }
                cache[key] = {**result, "cached_at": time.time()}
                _save_location_cache(cache)
                return result
        except Exception as e:
            logger.error(f"ORS geocoding failed for '{query}': {e}")

    return None


def get_routes(origin: dict, destination: dict, mode: str = "bike") -> list:
    profile = MODE_PROFILES.get(mode, "cycling")

    if ORS_API_KEY:
        routes = _get_routes_ors(origin, destination, mode)
        if routes:
            return routes

    return _get_routes_osrm(origin, destination, profile)


def _get_routes_ors(origin: dict, destination: dict, mode: str) -> list:
    ors_profiles = {
        "bike": "cycling-regular",
        "walk": "foot-walking",
        "scooter": "cycling-electric",
    }
    profile = ors_profiles.get(mode, "cycling-regular")

    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {
        "coordinates": [
            [origin["lon"], origin["lat"]],
            [destination["lon"], destination["lat"]],
        ],
        "alternative_routes": {"target_count": 3, "weight_factor": 1.6, "share_factor": 0.6},
        "instructions": False,
        "elevation": False,
    }

    url = f"{ORS_BASE}/v2/directions/{profile}/geojson"
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        routes = []
        for feat in data.get("features", []):
            geom = feat.get("geometry", {})
            coords = geom.get("coordinates", [])
            latlon_coords = [[c[1], c[0]] for c in coords]
            props = feat.get("summary", feat.get("properties", {}).get("summary", {}))
            if not props:
                props = feat.get("properties", {}).get("summary", {})
            routes.append({
                "coordinates": latlon_coords,
                "distance": props.get("distance", 0),
                "duration": props.get("duration", 0),
                "way_types": _extract_way_types(feat.get("properties", feat)),
            })
        return routes
    except Exception as e:
        logger.error(f"ORS routing failed: {e}")
        return []


def _get_routes_osrm(origin: dict, destination: dict, profile: str) -> list:
    coord_str = f"{origin['lon']},{origin['lat']};{destination['lon']},{destination['lat']}"
    url = f"{OSRM_BASE}/{profile}/{coord_str}"

    params = {"alternatives": "true", "overview": "full", "geometries": "geojson", "steps": "true"}

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        routes = []
        for route in data.get("routes", []):
            coords_data = route.get("geometry", {}).get("coordinates", [])
            latlon_coords = [[c[1], c[0]] for c in coords_data]
            routes.append({
                "coordinates": latlon_coords,
                "distance": route.get("distance", 0),
                "duration": route.get("duration", 0),
                "way_types": _extract_osrm_way_types(route),
            })

        if len(routes) < 3:
            mid_lat = (origin["lat"] + destination["lat"]) / 2
            mid_lon = (origin["lon"] + destination["lon"]) / 2
            offsets = [(0.01, 0.01), (-0.01, -0.01)]
            for dlat, dlon in offsets:
                if len(routes) >= 3:
                    break
                via_lat = mid_lat + dlat
                via_lon = mid_lon + dlon
                via_str = f"{origin['lon']},{origin['lat']};{via_lon},{via_lat};{destination['lon']},{destination['lat']}"
                via_url = f"{OSRM_BASE}/{profile}/{via_str}"
                via_params = {"overview": "full", "geometries": "geojson", "steps": "true"}
                try:
                    via_resp = requests.get(via_url, params=via_params, timeout=15)
                    via_resp.raise_for_status()
                    via_data = via_resp.json()
                    for route in via_data.get("routes", []):
                        coords_data = route.get("geometry", {}).get("coordinates", [])
                        latlon_coords = [[c[1], c[0]] for c in coords_data]
                        routes.append({
                            "coordinates": latlon_coords,
                            "distance": route.get("distance", 0),
                            "duration": route.get("duration", 0),
                            "way_types": _extract_osrm_way_types(route),
                        })
                        break
                except Exception:
                    pass

        return routes[:3]
    except Exception as e:
        logger.error(f"OSRM routing failed: {e}")
        return []


def _decode_polyline(encoded: str) -> list:
    coords = []
    index = 0
    lat = 0
    lng = 0

    while index < len(encoded):
        result = 0
        shift = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat

        result = 0
        shift = 0
        while True:
            if index >= len(encoded):
                break
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += dlng

        coords.append([round(lat * 1e-5, 6), round(lng * 1e-5, 6)])

    return coords


def _extract_way_types(route: dict) -> list:
    segments = route.get("segments", [])
    if not segments:
        return []
    steps = segments[0].get("steps", [])
    return [step.get("type", "unknown") for step in steps]


def _extract_osrm_way_types(route: dict) -> list:
    legs = route.get("legs", [])
    way_types = []
    for leg in legs:
        for step in leg.get("steps", []):
            maneuver = step.get("maneuver", {})
            mode = step.get("mode", "driving")
            if "cycle" in mode or mode == "cycling":
                way_types.append("cycleway")
            elif step.get("name") == "":
                way_types.append("unclassified")
            else:
                way_types.append("residential")
    return way_types if way_types else ["unknown"]