import math
import logging
from shapely.geometry import Point, LineString, Polygon, shape
from shapely.ops import nearest_points
import data_fetch

logger = logging.getLogger(__name__)

R_EARTH = 6371000.0

DEFAULT_WEIGHTS = {
    "trees": 0.20,
    "water": 0.15,
    "parks": 0.15,
    "quiet": 0.15,
    "air": 0.15,
    "heat": 0.10,
    "historic": 0.10,
}

PREFERENCE_PROFILES = {
    "hot": {
        "trees": 0.25, "water": 0.15, "parks": 0.10,
        "quiet": 0.10, "air": 0.10, "heat": 0.30, "historic": 0.00,
    },
    "pollution": {
        "trees": 0.15, "water": 0.10, "parks": 0.10,
        "quiet": 0.20, "air": 0.30, "heat": 0.05, "historic": 0.10,
    },
    "scenic": {
        "trees": 0.20, "water": 0.25, "parks": 0.20,
        "quiet": 0.10, "air": 0.05, "heat": 0.05, "historic": 0.15,
    },
}

ROAD_CLASS_SCORES = {
    "residential": 9,
    "pedestrian": 10,
    "cycleway": 10,
    "living_street": 9,
    "unclassified": 7,
    "tertiary": 6,
    "tertiary_link": 6,
    "secondary": 4,
    "secondary_link": 4,
    "primary": 2,
    "primary_link": 2,
    "trunk": 1,
    "motorway": 0,
    "motorway_link": 0,
    "unknown": 5,
}


def haversine(lat1, lon1, lat2, lon2) -> float:
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(a))


def split_route(coords: list, segment_length: float = 100.0) -> list:
    if len(coords) < 2:
        return [{"start": coords[0] if coords else [0, 0], "points": coords or []}]

    segments = []
    current_seg = []
    current_dist = 0.0

    for i in range(len(coords) - 1):
        p1 = coords[i]
        p2 = coords[i + 1]
        d = haversine(p1[0], p1[1], p2[0], p2[1])
        remaining = d

        while remaining > 0:
            if current_dist + remaining >= segment_length:
                needed = segment_length - current_dist
                frac = needed / d if d > 0 else 1.0
                lat = p1[0] + (p2[0] - p1[0]) * (1 - remaining / d + frac)
                lon = p1[1] + (p2[1] - p1[1]) * (1 - remaining / d + frac)

                if not current_seg:
                    current_seg.append(p1)
                current_seg.append([round(lat, 6), round(lon, 6)])
                segments.append({"points": current_seg[:]})

                current_seg = [[round(lat, 6), round(lon, 6)]]
                remaining -= needed
                current_dist = 0.0
            else:
                if not current_seg:
                    current_seg.append(p1)
                current_seg.append(p2)
                current_dist += remaining
                remaining = 0

    if len(current_seg) >= 2:
        segments.append({"points": current_seg[:]})

    return segments


def _geojson_points(fc: dict) -> list:
    points = []
    for feat in fc.get("features", []):
        geom = feat.get("geometry", {})
        if geom.get("type") == "Point":
            lon, lat = geom["coordinates"]
            points.append((lat, lon))
    return points


def _geojson_polygons(fc: dict) -> list:
    polys = []
    for feat in fc.get("features", []):
        geom = feat.get("geometry", {})
        if geom.get("type") == "Polygon":
            try:
                polys.append(shape(geom))
            except Exception:
                pass
        elif geom.get("type") == "MultiPolygon":
            try:
                polys.append(shape(geom))
            except Exception:
                pass
    return polys


def _geojson_lines(fc: dict) -> list:
    lines = []
    for feat in fc.get("features", []):
        geom = feat.get("geometry", {})
        if geom.get("type") == "LineString":
            try:
                lines.append(shape(geom))
            except Exception:
                pass
    return lines


def _count_nearby(segment_points: list, feature_points: list, buffer_m: float = 100.0) -> int:
    if not segment_points or not feature_points:
        return 0
    mid = segment_points[len(segment_points) // 2]
    count = 0
    for lat, lon in feature_points:
        d = haversine(mid[0], mid[1], lat, lon)
        if d <= buffer_m:
            count += 1
    return count


def _min_distance_to(segment_points: list, feature_points: list) -> float:
    if not segment_points or not feature_points:
        return float("inf")
    mid = segment_points[len(segment_points) // 2]
    min_d = float("inf")
    for lat, lon in feature_points:
        d = haversine(mid[0], mid[1], lat, lon)
        if d < min_d:
            min_d = d
    return min_d


def _is_near_polygon(segment_points: list, polygons: list, buffer_deg: float = 0.0005) -> bool:
    if not segment_points or not polygons:
        return False
    mid = segment_points[len(segment_points) // 2]
    pt = Point(mid[1], mid[0])
    for poly in polygons:
        if poly.buffer(buffer_deg).contains(pt):
            return True
        if poly.distance(pt) < buffer_deg:
            return True
    return False


def _pct_near_polygon(segment_points: list, polygons: list, buffer_deg: float = 0.0005) -> float:
    if not segment_points or not polygons:
        return 0.0
    near_count = 0
    for pt in segment_points:
        p = Point(pt[1], pt[0])
        for poly in polygons:
            if poly.buffer(buffer_deg).contains(p):
                near_count += 1
                break
    return near_count / len(segment_points) if segment_points else 0.0


def _is_near_line(segment_points: list, lines: list, buffer_deg: float = 0.001) -> bool:
    if not segment_points or not lines:
        return False
    mid = segment_points[len(segment_points) // 2]
    pt = Point(mid[1], mid[0])
    for line in lines:
        if line.distance(pt) < buffer_deg:
            return True
    return False


def _get_air_score(lat: float, lon: float) -> float:
    aq_data = data_fetch.get_air_quality()
    if not aq_data:
        return 5.0
    avg_no2 = sum(row["no2"] for row in aq_data) / len(aq_data)
    avg_pm25 = sum(row["pm25"] for row in aq_data) / len(aq_data)
    combined = avg_no2 + avg_pm25
    score = max(0, 10 - (combined / 6))
    return min(10, score)


def _get_weights(preferences: dict) -> dict:
    prefs = preferences or {}
    weights = DEFAULT_WEIGHTS.copy()

    if prefs.get("hot"):
        weights = PREFERENCE_PROFILES["hot"].copy()
    elif prefs.get("pollution"):
        weights = PREFERENCE_PROFILES["pollution"].copy()
    elif prefs.get("scenic"):
        weights = PREFERENCE_PROFILES["scenic"].copy()

    for k in weights:
        if weights[k] > 0:
            weights[k] += prefs.get(k, 0)

    total = sum(weights.values())
    if total > 0:
        weights = {k: v / total for k, v in weights.items()}

    return weights


def score_segment(segment: dict, env_data: dict, weather: dict) -> dict:
    seg_points = segment["points"]
    if not seg_points:
        return {f: 0 for f in DEFAULT_WEIGHTS}

    tree_points = env_data.get("tree_points", [])
    parks_polys = env_data.get("parks_polys", [])
    water_lines = env_data.get("water_lines", [])
    water_polys = env_data.get("water_polys", [])
    historic_points = env_data.get("historic_points", [])

    tree_count = _count_nearby(seg_points, tree_points, buffer_m=100)
    trees_score = min(10, tree_count * 1.5)

    parks_pct = _pct_near_polygon(seg_points, parks_polys, buffer_deg=0.002)
    parks_score = parks_pct * 10

    near_water = _is_near_line(seg_points, water_lines, buffer_deg=0.002) or _is_near_polygon(seg_points, water_polys, buffer_deg=0.002)
    water_score = 8.0 if near_water else 3.0

    quiet_score = 5.0
    if segment.get("way_type"):
        quiet_score = ROAD_CLASS_SCORES.get(segment["way_type"], 5.0)

    mid = seg_points[len(seg_points) // 2]
    air_score = _get_air_score(mid[0], mid[1])

    temp = weather.get("temperature") or 15
    if temp >= 25:
        heat_score = trees_score * 0.5 + water_score * 0.3 + (10 - air_score) * 0.2
    elif temp <= 5:
        heat_score = 7.0
    else:
        heat_score = 5.0

    historic_count = _count_nearby(seg_points, historic_points, buffer_m=100)
    historic_score = min(10, historic_count * 3)

    return {
        "trees": trees_score,
        "water": water_score,
        "parks": parks_score,
        "quiet": quiet_score,
        "air": air_score,
        "heat": heat_score,
        "historic": historic_score,
    }


def score_routes(routes: list, weather: dict, preferences: dict) -> list:
    env_data = _load_env_data()
    weights = _get_weights(preferences)

    scored = []
    for i, route in enumerate(routes):
        segments = split_route(route["coordinates"], 100.0)

        seg_scores = []
        factor_scores = {f: [] for f in DEFAULT_WEIGHTS}

        for seg in segments:
            seg["way_type"] = route.get("way_types", [None] * len(segments))[0] if route.get("way_types") else "unknown"
            scores = score_segment(seg, env_data, weather)
            seg_scores.append(scores)
            for f, v in scores.items():
                factor_scores[f].append(v)

        avg_factors = {}
        for f, vals in factor_scores.items():
            avg_factors[f] = sum(vals) / len(vals) if vals else 0

        total = sum(avg_factors[f] * weights.get(f, 0) for f in avg_factors) * 10

        scored.append({
            "index": i + 1,
            "coordinates": route["coordinates"],
            "distance": route["distance"],
            "duration": route["duration"],
            "total_score": round(total, 1),
            "factors": {f: round(v, 1) for f, v in avg_factors.items()},
            "segments": segments,
            "segment_scores": seg_scores,
            "weights": weights,
        })

    scored.sort(key=lambda r: r["total_score"], reverse=True)

    if scored:
        max_score = scored[0]["total_score"]
        if max_score > 0 and max_score < 75:
            scale = 75 / max_score
            for r in scored:
                r["total_score"] = round(r["total_score"] * scale, 1)

    return scored


def _load_env_data() -> dict:
    trees_fc = data_fetch.get_trees()
    parks_fc = data_fetch.get_parks()
    water_fc = data_fetch.get_water()
    historic_fc = data_fetch.get_historic()

    return {
        "tree_points": _geojson_points(trees_fc),
        "parks_polys": _geojson_polygons(parks_fc),
        "water_lines": _geojson_lines(water_fc),
        "water_polys": _geojson_polygons(water_fc),
        "historic_points": _geojson_points(historic_fc),
    }