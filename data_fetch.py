import os
import json
import time
import requests
import logging

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache", "overpass")

TTL_SECONDS = 7200

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

BBOX = f"({49.35},{10.95},{49.50},{11.25})"

_cache = {}


def _save_overpass_cache(key: str, data: dict):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{key}.json")
    payload = {"data": data, "cached_at": time.time()}
    try:
        with open(path, "w") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Failed to write Overpass cache for '{key}': {e}")


def _load_overpass_cache(key: str):
    path = os.path.join(CACHE_DIR, f"{key}.json")
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, "r") as f:
            payload = json.load(f)
        data = payload.get("data")
        cached_at = payload.get("cached_at", 0)
        age = time.time() - cached_at
        return data, age
    except Exception as e:
        logger.warning(f"Failed to read Overpass cache for '{key}': {e}")
        return None, None


def _overpass_query(query_string: str, attempt: int = 0) -> dict:
    headers = {"User-Agent": "GreenCleanCommute/1.0 (Nuremberg Datathon)"}
    for url in OVERPASS_URLS:
        try:
            resp = requests.post(url, data={"data": query_string}, headers=headers, timeout=25)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"Overpass query failed on {url}: {e}")
            continue

    if attempt < 2:
        wait = 3 * (attempt + 1)
        logger.info(f"Overpass retry in {wait}s (attempt {attempt + 1})")
        time.sleep(wait)
        return _overpass_query(query_string, attempt + 1)

    return None


def _overpass_to_geojson(overpass_data: dict) -> dict:
    features = []
    for elem in overpass_data.get("elements", []):
        etype = elem.get("type")
        tags = elem.get("tags", {})
        props = {k: v for k, v in tags.items()}

        if etype == "node":
            lon = elem.get("lon")
            lat = elem.get("lat")
            if lon is not None and lat is not None:
                features.append({
                    "type": "Feature",
                    "properties": props,
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                })

        elif etype == "way":
            geom = elem.get("geometry", [])
            if not geom:
                continue
            if elem.get("tags", {}).get("area") == "yes" or _is_closed_ring(geom):
                coords = [[g["lon"], g["lat"]] for g in geom]
                features.append({
                    "type": "Feature",
                    "properties": props,
                    "geometry": {"type": "Polygon", "coordinates": [coords]},
                })
            else:
                coords = [[g["lon"], g["lat"]] for g in geom]
                features.append({
                    "type": "Feature",
                    "properties": props,
                    "geometry": {"type": "LineString", "coordinates": coords},
                })

    return {"type": "FeatureCollection", "features": features}


def _is_closed_ring(geom: list) -> bool:
    if len(geom) < 4:
        return False
    first = geom[0]
    last = geom[-1]
    return first.get("lat") == last.get("lat") and first.get("lon") == last.get("lon")


def _fetch_feature(key: str, query: str, fallback_file: str) -> dict:
    if key in _cache:
        return _cache[key]

    file_data, age = _load_overpass_cache(key)
    if file_data is not None and age is not None and age < TTL_SECONDS:
        logger.info(f"Overpass file cache hit for '{key}' (age: {age/60:.0f}min)")
        _cache[key] = file_data
        return file_data

    raw = _overpass_query(query)

    if raw is not None:
        result = _overpass_to_geojson(raw)
        _save_overpass_cache(key, result)
        _cache[key] = result
        return result

    if file_data is not None:
        logger.warning(f"Overpass failed, using stale cache for '{key}' (age: {age/60:.0f}min)")
        _cache[key] = file_data
        return file_data

    logger.warning(f"Overpass failed, no cache for '{key}', using fallback {fallback_file}")
    result = _load_fallback(fallback_file)
    _cache[key] = result
    return result


def _load_fallback(filename: str) -> dict:
    path = os.path.join(DATA_DIR, filename)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    logger.warning(f"Fallback file not found: {path}")
    return {"type": "FeatureCollection", "features": []}


def get_trees() -> dict:
    query = f"""
[out:json][timeout:20];
(
  node["natural"="tree"]{BBOX};
  way["landuse"="forest"]{BBOX};
);
out body geom;
"""
    return _fetch_feature("trees", query, "trees.geojson")


def get_parks() -> dict:
    query = f"""
[out:json][timeout:20];
(
  way["leisure"="park"]{BBOX};
  way["leisure"="garden"]{BBOX};
);
out body geom;
"""
    return _fetch_feature("parks", query, "parks.geojson")


def get_water() -> dict:
    query = f"""
[out:json][timeout:20];
(
  way["natural"="water"]{BBOX};
  way["waterway"]{BBOX};
);
out body geom;
"""
    return _fetch_feature("water", query, "water.geojson")


def get_historic() -> dict:
    query = f"""
[out:json][timeout:20];
(
  node["historic"]{BBOX};
  node["tourism"="attraction"]{BBOX};
  way["tourism"="attraction"]{BBOX};
);
out body geom;
"""
    return _fetch_feature("historic", query, "historic.geojson")


def get_air_quality() -> list:
    if "air_quality" in _cache:
        return _cache["air_quality"]
    path = os.path.join(DATA_DIR, "air_quality.csv")
    data = []
    if os.path.exists(path):
        import csv
        with open(path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row["no2"] = float(row["no2"])
                row["pm25"] = float(row["pm25"])
                data.append(row)
    _cache["air_quality"] = data
    return data