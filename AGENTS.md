# Green & Clean Commute Optimizer — Architecture & Design

## 1. Project Overview

A Streamlit web application that finds healthy, scenic walking/cycling/e-scooter routes through Nuremberg, Germany. Users describe their trip in natural language ("Bike from Südstadt to adorsys, it's hot, avoid traffic pollution") and an AI "Commute Concierge" agent geocodes the locations, fetches 3 alternative routes, scores each against 7 environmental factors, and renders them on an interactive Folium map.

Built for the **Agentic Datathon** as a lightning-presentation demo. The entire app is a single Python project with no database — all persistence is file-based JSON caching.

---

## 2. System Architecture

### Module Dependency Graph
```
app.py  (entry point, Streamlit UI)
  ├── agent.py         (LLM orchestration)
  │     ├── routing.py  (geocoding + routing)
  │     ├── weather.py  (Open-Meteo)
  │     └── scoring.py  (route scoring engine)
  │           └── data_fetch.py  (Overpass API + file cache)
  │
  ├── folium            (map rendering)
  └── streamlit_geolocation  (browser geolocation component)
```

### Data Flow (request lifecycle)
```
USER          APP.PY               AGENT.PY                  SERVICE            DATA/API
────          ──────               ────────                  ───────            ────────
Type query → st.chat_input
              ↓
              process_query(query)
              ↓
              _extract_preferences() → {hot, pollution, scenic}
              ↓
              LLM(SystemPrompt + Tools) ───────────────────────────────────── OpenCode Zen
              ↓                               (tool_calls loop, max 5 rounds)
              ├── find_location × 2 ───────── routing.geocode() ───────────── Nominatim/ORS
              │                                         ↓                      cache/location.json
              │                                   {lat, lon, display_name}
              │
              ├── get_routes ────────────────── routing.get_routes() ──────── ORS / OSRM
              │                                         ↓
              │                              _route_cache["routes"] (full)
              │                              return simplified metadata to LLM
              │
              ├── get_weather ──────────────── wx.get_weather() ────────────── Open-Meteo
              │                                         ↓
              │                              _weather_cache["weather"]
              │
              ├── score_routes ─────────────── scoring.score_routes()
              │                              → data_fetch.get_trees/parks/water/historic
              │                                         ↓                  Overpass/cache
              │                              7-factor segment scoring
              │                              normalize to 75 max
              │
              └── LLM returns text summary
              ↓
              return {text, scored_routes, weather}
              ↓
         st.session_state update
         ├── build_map() ──→ Folium map with routes + user location
         └── render_scorecards() ──→ Unicode bar charts per route

  On LLM failure: _fallback_process() ──→ same pipeline, hardcoded coords fallback
```

### External APIs Used
| API | Module | Key | Rate Limit | Purpose |
|-----|--------|-----|-----------|---------|
| **Nominatim** (OSM) | `routing.py` | Free, no key | 1 req/s | Forward + reverse geocoding |
| **OpenRouteService** | `routing.py` | Free tier key | ~40 req/min | Primary routing + geocoding fallback |
| **OSRM** | `routing.py` | Free, no key | Fair use | Routing fallback (no key needed) |
| **Open-Meteo** | `weather.py` | Free, no key | 10k req/day | Current weather data |
| **Overpass API** (OSM) | `data_fetch.py` | Free, no key | Fair use | Environmental feature queries |
| **OpenCode Zen API** | `agent.py` | API key | Paid tier | LLM inference (DeepSeek V4) |

---

### 2.5 Layered Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         UI LAYER (app.py)                                │
│  Streamlit sidebar · Folium map · st_folium · Chat messages            │
│  streamlit_geolocation · Chat input · Scorecards · Weather display     │
│  Session state: chat_history, scored_routes, weather, location          │
├─────────────────────────────────────────────────────────────────────────┤
│                     ORCHESTRATION LAYER (agent.py)                       │
│  LiteLLM completion() · Tool dispatcher · _route_cache                 │
│  _weather_cache · _extract_preferences() · _fallback_process()          │
│  System prompt (Nuremberg local context) · 4 tool definitions           │
│  reasoning_content handling (DeepSeek) · Max 5 tool-call rounds         │
├─────────────────────────────────────────────────────────────────────────┤
│                         SERVICE LAYER                                    │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────────────┐ │
│  │ routing.py      │  │ weather.py       │  │ scoring.py             │ │
│  │ geocode()       │  │ get_weather()    │  │ score_routes()         │ │
│  │ get_routes()    │  │ Open-Meteo API   │  │ split_route()          │ │
│  │ Nominatim / ORS │  │ (free, no key)   │  │ score_segment()        │ │
│  │ OSRM fallback   │  └──────────────────┘  │ 7-factor model         │ │
│  └────────┬────────┘                        │ _load_env_data()       │ │
│           │                                 └───────────┬────────────┘ │
│           │                                              │              │
│           │          ┌──────────────────────────────┐     │              │
│           └──────────│ data_fetch.py                │─────┘              │
│                      │ Overpass API (3 mirrors)     │                    │
│                      │ 5-tier fallback chain        │                    │
│                      │ OSM → GeoJSON conversion     │                    │
│                      └──────────────────────────────┘                    │
├─────────────────────────────────────────────────────────────────────────┤
│                            DATA LAYER                                    │
│  ┌──────────────────────┐  ┌──────────────────────┐  ┌───────────────┐  │
│  │ cache/location.json  │  │ cache/overpass/      │  │ In-Memory     │  │
│  │ Geocode results      │  │ trees.json · parks   │  │ _cache        │  │
│  │ (Nominatim + ORS)    │  │ water.json · historic│  │ _route_cache  │  │
│  │ Keyed by query       │  │ 2-hour TTL per key   │  │ _weather_cache│  │
│  └──────────────────────┘  └──────────────────────┘  └───────────────┘  │
│  ┌──────────────────────┐  ┌──────────────────────┐                      │
│  │ data/trees.geojson   │  │ data/air_quality.csv │                      │
│  │ data/parks.geojson   │  │ 37 Nuremberg         │                      │
│  │ data/water.geojson   │  │ districts with       │                      │
│  │ data/historic.geojson│  │ NO2 + PM2.5 (µg/m³)  │                      │
│  │ (Offline fallback)   │  └──────────────────────┘                      │
│  └──────────────────────┘                                                │
├─────────────────────────────────────────────────────────────────────────┤
│                         EXTERNAL API LAYER                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐ │
│  │Nominatim │  │ ORS      │  │ OSRM     │  │Open-Meteo│  │ Overpass  │ │
│  │Geocode   │  │Route+Geo │  │Route     │  │Weather   │  │Env GIS    │ │
│  │Free      │  │Key req.  │  │Free      │  │Free      │  │Free       │ │
│  │1 req/s   │  │~40 req/m │  │Fair use  │  │10k req/d │  │Fair use   │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └───────────┘ │
│  ┌──────────────────┐  ┌────────────────────────────────────┐           │
│  │ OpenCode Zen API │  │ Browser Geolocation (JS GPS API)   │           │
│  │ LLM (DeepSeek V4)│  │ streamlit-geolocation component    │           │
│  └──────────────────┘  └────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Service Modules

### 3.1 `app.py` — Streamlit Frontend (262 lines)

**Role:** Single entry point. Orchestrates the Streamlit lifecycle, renders the UI, manages session state.

**Session State Variables:**
| Variable | Type | Purpose |
|----------|------|---------|
| `chat_history` | `list[dict]` | User + assistant message pairs for chat UI |
| `scored_routes` | `list[dict]` or `None` | 2-3 scored route dicts from scoring engine |
| `weather_data` | `dict` or `None` | Current weather at route midpoint |
| `my_location` | `dict` or `None` | Reverse-geocoded place name (set by "Reverse Geocode as Origin" button) |
| `raw_location` | `dict` or `None` | Raw browser geolocation from `streamlit_geolocation()` |

**Layout:**
- **Sidebar** (top to bottom):
  1. `streamlit_geolocation()` component — triggers browser GPS prompt, returns `{latitude, longitude, accuracy, ...}`
  2. "Reverse Geocode as Origin" button — calls Nominatim reverse geocode, pre-fills chat with "From <place>"
  3. Chat area (`st.chat_message` loop + `st.chat_input`) — pinned to bottom of sidebar
  4. Example query buttons — 4 one-click demos
- **Main area** (full width):
  1. Folium map — 700px height, `use_container_width=True`
  2. Scoring breakdown — `render_scorecards()` showing each route's 7-factor bars

**`build_map(scored_routes, user_location)` → `folium.Map`:**
- If `user_location` has valid lat/lon: centers map there (zoom 14), adds blue `CircleMarker` + translucent accuracy `Circle`
- If `scored_routes` exists: draws each route as a colored `PolyLine` (green/blue/orange), segment sub-lines colored green/yellow/red by raw factor average, start/end `Marker` icons
- Fits bounds to include all route coordinates + user location
- If no routes and no user location: shows single "Nuremberg" marker at center

**`render_scorecards(scored_routes)`:**
- One column per route (2-3 columns)
- Each column: route color, score/100, distance (km), duration (min)
- 7-factor bar chart using Unicode block characters (█/░)

**`reverse_geocode(lat, lon)` → `str`:**
- Nominatim reverse endpoint with `addressdetails=1`
- Extracts road, suburb, city from address components
- Returns comma-joined string, or `None` on failure

**Key design decisions:**
- `st.chat_input` inside sidebar works natively in Streamlit — no custom layout needed
- Map height of 700px leaves room for scorecards below
- `st_folium` with `use_container_width=True` makes the map responsive
- Session state is the only persistence mechanism across Streamlit reruns

---

### 3.2 `agent.py` — LLM Orchestration (345 lines)

**Role:** Mediates between the user's natural language request and the backend services. Uses function-calling LLM to plan and execute multi-step queries.

**Provider Abstraction (`PROVIDERS` dict):**
```python
{
  "opencode": {"model": "openai/deepseek-v4-flash-free", "api_key": ..., "api_base": "https://opencode.ai/zen/v1"},
  "gemini":   {"model": "gemini/gemini-2.0-flash",       "api_key": ..., "api_base": None},
  "ollama":   {"model": "ollama/llama3.2",               "api_key": None, "api_base": "http://localhost:11434"},
}
```
Selected by `LLM_PROVIDER` env var. `_llm_completion()` injects the provider-specific key/base into each `litellm.completion()` call.

**System Prompt:**
Instructs the LLM to act as a "Green & Clean Commute Concierge" for Nuremberg. Includes hardcoded local knowledge: Pegnitz river, major parks (Marienberg Park, Stadtpark, Wöhrder Wiese, Volkspark Dutzendteich), landmarks (Nürnberger Burg, Frauenkirche), adorsys office location, high-traffic streets to avoid (Frankenstraße, Dieselstraße, Bayernstraße).

**4 Function Tools (OpenAI-compatible JSON Schema):**
| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `find_location` | Geocode a place name | `query: string` |
| `get_routes` | Get 3 alternative routes | `origin: {lat, lon}`, `destination: {lat, lon}`, `mode: bike/walk/scooter` |
| `get_weather` | Fetch current weather | `lat: number`, `lon: number` |
| `score_routes` | Score previously fetched routes | `weather: object`, `preferences: {hot, pollution, scenic}` |

**`_execute_tool(name, args)` → `dict`:**
Simple dispatcher pattern. Key behaviors:
- `get_routes` stores full route data (including coordinate arrays) in `_route_cache["routes"]` but only returns simplified metadata (index, distance, duration) to the LLM. This prevents the LLM from receiving massive coordinate payloads.
- `get_weather` stores result in `_weather_cache["weather"]` for later return to the UI.
- `score_routes` reconstructs full route dicts from `_route_cache` before calling the scoring engine.

**`process_query(user_query)` → `{text, scored_routes, preferences, weather}`:**
1. Extracts user preferences via keyword matching (`_extract_preferences()`)
2. Enters a loop (max 5 rounds):
   a. Calls LLM with accumulated messages + tool definitions
   b. If response has tool_calls: iterates each, executes, appends tool results to messages
   c. If response has text content (no tool_calls): breaks
3. On any exception, falls back to `_fallback_process()`
4. Returns dict with agent response text, scored routes, preferences, and weather

**DeepSeek `reasoning_content` Handling:**
DeepSeek models return a non-standard `reasoning_content` field on assistant messages. When present, it must be included verbatim in the subsequent assistant message dict for multi-turn tool calling to work. `agent.py` detects this field and preserves it through the message chain.

**`_extract_preferences(user_query)` → `{hot, pollution, scenic}`:**
Keyword matching against normalized query text. Handles both English and German keywords:
- `hot`: hot, heat, warm, sonnig, heiß
- `pollution`: pollut, traffic, emission, abgas, verkehr, smog, dirty air
- `scenic`: scenic, beautiful, nice, pretty, schön, green

**`_fallback_process(user_query, preferences)` → `str`:**
Used when the LLM call fails entirely (network error, API timeout, malformed response). Complete offline pipeline:
1. Iterates known place keys ("südstadt", "adorsys", "mitte", "hauptbahnhof") found in the query
2. Calls `rt.geocode()` for each matched key
3. Falls back to hardcoded coordinates (Südstadt: 49.425/11.060, adorsys: 49.455/11.080, etc.)
4. Extracts transport mode from keywords (bike default)
5. Runs `rt.get_routes()` → `wx.get_weather()` → `sc.score_routes()`
6. Caches results in `_fallback_process._last_scored` and `_fallback_process._last_weather`
7. Returns a one-line summary of the best route

---

### 3.3 `routing.py` — Geocoding & Routing (268 lines)

**Role:** Converts place names to coordinates (geocoding) and calculates driving/walking/cycling paths between them (routing).

#### Geocoding (`geocode(query)` → `{lat, lon, display_name}`)

Three-tier fallback:

1. **Disk cache** (`cache/location.json`): Keyed by normalized query string. Returns cached result if present.
2. **Nominatim** (primary): GET `https://nominatim.openstreetmap.org/search` with `q=query`, `format=json`, `limit=1`, `countrycodes=de`. Free, no key, but rate-limited to 1 request/second. Results saved back to cache.
3. **ORS Geocode** (fallback): GET `https://api.openrouteservice.org/geocode/search` with `Authorization` header. Only attempted if `ORS_API_KEY` is set. Results saved back to cache.

Returns `None` if all tiers fail.

#### Routing (`get_routes(origin, dest, mode)` → `list[dict]`)

Two-tier fallback:

**ORS (primary)** — `_get_routes_ors()`:
- POST to `https://api.openrouteservice.org/v2/directions/{profile}/geojson`
- Profiles: `cycling-regular` (bike), `foot-walking` (walk), `cycling-electric` (scooter)
- Requests 3 alternative routes via `alternative_routes: {target_count: 3, weight_factor: 1.6, share_factor: 0.6}`. `weight_factor` controls how different alternatives must be in cost; `share_factor` controls how much path they may share.
- Returns GeoJSON FeatureCollection. Each feature has `geometry.coordinates` (lon/lat pairs) and `properties.summary` (distance in meters, duration in seconds).
- Converts coordinates to [lat, lon] format.
- Extracts way types from ORS `segments[0].steps[].type`.

**OSRM (fallback)** — `_get_routes_osrm()`:
- GET `https://router.project-osrm.org/route/v1/{profile}/{lon1},{lat1};{lon2},{lat2}` with `alternatives=true`, `overview=full`, `geometries=geojson`, `steps=true`
- OSRM rarely returns more than 1-2 alternatives. If fewer than 3 routes, generates synthetic alternatives by adding via-points offset 0.01° (~1km) from the midpoint in opposite directions and making a second OSRM request for each.
- Extracts way types from OSRM step `mode` field: "cycling" → cycleway, unnamed roads → unclassified, named roads → residential.
- Returns at most 3 routes.

#### `cache/location.json` Format
```json
{
  "südstadt nuremberg": {
    "lat": 49.4257629,
    "lon": 11.0596819,
    "display_name": "KGV Südstadt e.V., ...",
    "cached_at": 1719234567.0
  }
}
```

**Additional helpers:**
- `_decode_polyline(encoded)` — Standard polyline5 decoder for OSRM (encoder utility, not actively used since OSRM returns GeoJSON)
- `_normalize_query(query)` — lowercase + strip
- `MODE_PROFILES` — maps app mode names to OSRM profiles: `{bike: cycling, walk: foot, scooter: cycling}`

---

### 3.4 `scoring.py` — Route Scoring Engine (348 lines)

**Role:** Evaluates each route against 7 environmental and health factors, producing a 0–100 score that quantifies how "green and clean" the route is.

#### Segment Splitting (`split_route(coords, 100m)`)

Routes are divided into 100-meter segments using the Haversine formula for accurate distance on a sphere. The algorithm:
1. Walks coordinate-to-coordinate pairs
2. Accumulates running distance
3. When the running total crosses a 100m boundary, interpolates the exact split point using linear interpolation between the current coordinate pair
4. Collects intermediate points into segment arrays

A typical 6km route produces ~60 segments.

#### 7-Factor Scoring Model

Each segment is scored on 7 factors (each 0–10), then averaged across all segments per route.

| Factor | Weight | Scoring Method |
|--------|--------|----------------|
| **Trees & Shade** | 0.20 | `_count_nearby(midpoint, tree_points, 100m)` × 1.5, cap at 10. Counts OSM `natural=tree` nodes and `landuse=forest` ways within 100m of segment midpoint. |
| **Water Nearby** | 0.15 | 8.0 if segment within 0.002° (~200m) of a water line or polygon (`natural=water`, `waterway`), else 3.0. |
| **Parks & Green** | 0.15 | Percentage of segment points within 0.002° (~200m) of a park/garden polygon (`leisure=park`, `leisure=garden`), × 10. |
| **Quiet Roads** | 0.15 | Lookup from `ROAD_CLASS_SCORES` dict: cycleway=10, pedestrian=10, residential=9, living_street=9, unclassified=7, tertiary=6, secondary=4, primary=2, trunk=1, motorway=0. Default 5. |
| **Air Quality** | 0.15 | Average NO2 + PM2.5 across all 37 Nuremberg districts from CSV. Score = `max(0, 10 - combined/6)`, cap at 10. Typical: avg NO2≈21, avg PM2.5≈7 → combined≈28 → score≈5.3. |
| **Heat Comfort** | 0.10 | If temp ≥ 25°C: `trees×0.5 + water×0.3 + (10-air)×0.2`. If temp ≤ 5°C: 7.0. Else: 5.0. |
| **Historic Sites** | 0.10 | `_count_nearby(midpoint, historic_points, 100m)` × 3, cap at 10. Counts OSM `historic` nodes and `tourism=attraction` within 100m. |

#### Preference-Adaptive Weights

`DEFAULT_WEIGHTS` are overridden by `PREFERENCE_PROFILES` when specific keywords are detected:
- **hot**: heat weight boosted to 0.30, trees to 0.25, historic to 0.00
- **pollution**: air weight boosted to 0.30, quiet to 0.20, heat to 0.05
- **scenic**: water to 0.25, parks to 0.20, historic to 0.15

Weights are normalized to sum to 1.0 after preference adjustment.

#### Final Score Calculation

```
segment_score = sum(factor_score × weight for each of 7 factors)
total = avg_segment_score × 10  (range 0–100)
```

Mapping factor scores (0–10) through equal weights (≈0.143) gives a segment average of 0–10, then ×10 produces a 0–100 route score.

**Normalization**: If the highest-scoring route's raw score is below 75, all scores are scaled proportionally so the best route hits exactly 75. This compensates for the fact that real-world Nuremberg routes rarely achieve high absolute scores on all 7 factors simultaneously, while preserving relative ordering.

#### Spatial Helpers
| Function | Method |
|----------|--------|
| `_count_nearby(points, features, buffer_m)` | Haversine distance from segment midpoint to each feature point, count within buffer |
| `_min_distance_to(points, features)` | Haversine distance from segment midpoint to nearest feature point |
| `_is_near_polygon(points, polygons, buffer_deg)` | Shapely `.buffer().contains()` or `.distance()` on segment midpoint |
| `_pct_near_polygon(points, polygons, buffer_deg)` | Shapely check for each segment point, return fraction within buffer |
| `_is_near_line(points, lines, buffer_deg)` | Shapely `.distance()` from segment midpoint to nearest line geometry |
| `haversine(lat1, lon1, lat2, lon2)` | Earth-radius spherical distance in meters |

---

### 3.5 `weather.py` — Open-Meteo Integration (34 lines)

**Role:** Fetches current weather conditions at a given coordinate.

**API:** `https://api.open-meteo.com/v1/forecast`

**Parameters:**
- `latitude`, `longitude`: Route midpoint coordinates
- `current`: `temperature_2m,wind_speed_10m,precipitation,uv_index`
- `timezone`: `Europe/Berlin`

**Response** (on success):
```python
{"temperature": 30.9, "wind_speed": 9.7, "precipitation": 0.0, "uv_index": 0.7}
```

On failure, returns all keys as `None`. No API key required; Open-Meteo operates on a fair-use basis (~10k requests/day).

Weather data flows to the scoring engine (heat factor), the UI (temperature/wind/precipitation display), and the LLM agent (natural language summary includes weather context).

---

### 3.6 `data_fetch.py` — Overpass API & Fallback Data (220 lines)

**Role:** Sources environmental GIS data (trees, parks, water bodies, historic sites) from OpenStreetMap via the Overpass API, with aggressive caching and graceful degradation.

#### Overpass Query Engine

All queries target bounding box `49.35,10.95` to `49.50,11.25` (Nuremberg metropolitan area).

| Function | OSM Query | Elements Returned |
|----------|-----------|-------------------|
| `get_trees()` | `node["natural"="tree"]` + `way["landuse"="forest"]` | ~7,800 trees + forest areas |
| `get_parks()` | `way["leisure"="park"]` + `way["leisure"="garden"]` | ~280 parks + gardens |
| `get_water()` | `way["natural"="water"]` + `way["waterway"]` | ~2,500 water bodies + waterways |
| `get_historic()` | `node["historic"]` + `node["tourism"="attraction"]` + `way["tourism"="attraction"]` | ~700 historic sites + attractions |

#### 5-Tier Fallback Chain (`_fetch_feature()`)

```
1. In-memory cache (_cache dict)      → fastest, process lifetime
2. File cache fresh (< 2h TTL)        → persists across restarts
3. Overpass API live query             → most current data
4. File cache stale (≥ 2h TTL)        → degrades gracefully
5. Fallback GeoJSON file (data/*.geojson) → survives total network failure
   → last resort: empty FeatureCollection
```

**Overpass Query Execution** (`_overpass_query()`):
- POST to 3 mirrors in sequence: `overpass-api.de`, `overpass.osm.ch`, `overpass.kumi.systems`
- 25-second timeout per mirror
- 2 retry attempts with 3s/6s backoff
- Uses `[out:json][timeout:20]` output format

**OSM to GeoJSON Conversion** (`_overpass_to_geojson()`):
- `node` elements → Point features (`[lon, lat]`)
- `way` elements → Polygon (if closed ring with ≥4 nodes) or LineString features
- Preserves all OSM tags as feature properties
- Returns standard GeoJSON FeatureCollection

#### Air Quality Data

`get_air_quality()` reads `data/air_quality.csv` — hand-curated NO2 and PM2.5 values for 37 Nuremberg districts. Data sourced from public Umweltbundesamt (German Environment Agency) measurements. Format:
```csv
district,no2,pm25
Mitte,38,12
Süd,32,10
...
```
Cached in memory after first read.

#### Caching Details

- **File cache path**: `cache/overpass/{key}.json`
- **Cache payload**: `{"data": <FeatureCollection>, "cached_at": <unix_timestamp>}`
- **TTL**: 7200 seconds (2 hours). `_fetch_feature()` logs cache age in minutes for observability.
- **Directory creation**: Automatic via `os.makedirs(CACHE_DIR, exist_ok=True)`

---

## 4. External API Details

### 4.1 Nominatim (OpenStreetMap Geocoding)
**Endpoint:** `https://nominatim.openstreetmap.org/search` (forward), `/reverse` (reverse)  
**Auth:** None  
**Rate limit:** 1 request/second (enforced by Nominatim usage policy)  
**Usage:** Primary geocoder. `countrycodes=de` restricts results to Germany. `User-Agent: GreenCleanCommute/1.0` identifies the app per Nominatim ToS.  
**Fallback:** ORS Geocoding API (when key available)

### 4.2 OpenRouteService (Routing + Geocoding)
**Endpoint:** `https://api.openrouteservice.org/v2/directions/{profile}/geojson`  
**Auth:** `Authorization` header with API key (free tier: ~2,000 requests/day, 40 req/min)  
**Usage:** Primary routing engine. Returns real alternatives via `alternative_routes` parameter. Three profiles: cycling-regular, foot-walking, cycling-electric.  
**Why ORS over OSRM:** ORS natively returns 2-3 genuinely different alternative routes. OSRM rarely returns more than 1.

### 4.3 OSRM (Routing Fallback)
**Endpoint:** `https://router.project-osrm.org/route/v1/{profile}/{coordinates}`  
**Auth:** None  
**Usage:** Fallback when ORS key is missing or fails. Synthetic alternatives generated via via-points.  
**Limitation:** Alternatives parameter in OSRM is unreliable (often returns identical routes). The via-point strategy produces genuinely different paths.

### 4.4 Open-Meteo (Weather)
**Endpoint:** `https://api.open-meteo.com/v1/forecast`  
**Auth:** None  
**Rate limit:** 10,000 requests/day (fair use)  
**Fields used:** `temperature_2m`, `wind_speed_10m`, `precipitation`, `uv_index` (all from `current` weather block)  
**Why Open-Meteo:** Free, no API key needed, reliable, no rate limit anxiety for a demo app.

### 4.5 Overpass API (Environmental Data)
**Endpoint:** `https://overpass-api.de/api/interpreter` (primary, with 2 mirrors)  
**Auth:** None  
**Usage:** Queries OSM for trees, parks, water, historic sites within Nuremberg BBOX.  
**Format:** OSM XML → JSON via `[out:json]`. OSM elements converted to GeoJSON client-side.  
**Mirrors:** `overpass-api.de`, `overpass.osm.ch`, `overpass.kumi.systems` — tried in sequence for resilience.

### 4.6 OpenCode Zen API (LLM Inference)
**Endpoint:** `https://opencode.ai/zen/v1/chat/completions` (OpenAI-compatible)  
**Auth:** Bearer token in `Authorization` header  
**Model:** `deepseek-v4-flash-free` (free tier)  
**Protocol:** Standard OpenAI chat completions with `tools` parameter for function calling.  
**Provider abstraction:** LiteLLM normalizes the API. Same code works with Gemini or Ollama by changing one env var.

---

## 5. Data Layer

### Cache Directory Structure
```
cache/
  location.json              ← Geocoding results (Nominatim + ORS)
  overpass/
    trees.json               ← Tree/forest GeoJSON (2h TTL)
    parks.json               ← Park/garden GeoJSON (2h TTL)
    water.json               ← Water body GeoJSON (2h TTL)
    historic.json            ← Historic site GeoJSON (2h TTL)
```

### Caching Architecture & Pipeline

Three caching tiers, checked in order of speed. Every env data request traverses this exact chain:

```
CACHE TIERS (fastest → slowest)
══════════════════════════════════

  L1: IN-MEMORY (process lifetime)
  ┌────────────────────────────────────────────────────────────────┐
  │  data_fetch._cache   agent._route_cache   agent._weather_cache │
  │  {trees, parks,      {routes: [r0, r1,    {weather: {...}}    │
  │   water, historic}    r2]} (full coords)                       │
  │  Avoids re-fetching  LLM never sees       Holds weather for   │
  │  env data in same    coordinate arrays    UI return            │
  │  request                                                            │
  └────────────────────────────────────────────────────────────────┘
                              │  miss
                              ▼
  L2: FILE CACHE (disk, 2h TTL, survives restarts)
  ┌────────────────────────────────────────────────────────────────┐
  │  cache/location.json       cache/overpass/                     │
  │  Key: normalized query     trees.json · parks.json             │
  │  Val: {lat, lon, name,     water.json · historic.json          │
  │        cached_at}          Val: {data: FeatureCollection,     │
  │  Used by geocode()         cached_at: timestamp}               │
  │                          Used by _fetch_feature()            │
  └────────────────────────────────────────────────────────────────┘
                              │  miss (or stale)
                              ▼
  L3: LIVE API (network)
  ┌────────────────────────────────────────────────────────────────┐
  │  Overpass API (3 mirrors tried in sequence)                     │
  │  overpass-api.de → overpass.osm.ch → overpass.kumi.systems     │
  │  25s timeout per mirror, 2 retries with 3s/6s backoff          │
  │  On success: save result to file cache + in-memory cache       │
  │  On failure: fall through to stale cache → fallback GeoJSON    │
  └────────────────────────────────────────────────────────────────┘


REQUEST FLOW THROUGH CACHES
═══════════════════════════

  GEOCODE REQUEST                        ENV DATA REQUEST
  (routing.geocode)                      (data_fetch._fetch_feature)
       │                                        │
       ▼                                        ▼
  ┌──────────────┐                        ┌──────────────┐
  │ location.json │                        │ _cache       │
  │ cache hit?    │                        │ (in-memory)  │
  └───┬───────┬───┘                        │ hit?         │
   yes│       │no                          └───┬───────┬───┘
      │       ▼                           yes│       │no
      │  ┌────────────┐                       │       ▼
      │  │ Nominatim  │                       │  ┌──────────────┐
      │  │ API (free) │                       │  │ file cache   │
      │  └───┬────┬───┘                       │  │ < 2h TTL?    │
      │   ok  │    │fail                      │  └───┬──────┬───┘
      │      ▼    ▼                      yes  │      │      │no
      │  ┌──────┐ ┌──────────┐                │      ▼      ▼
      │  │save +│ │ORS Geo   │                │  ┌────┐ ┌──────────┐
      │  │return│ │(if key)  │                │  │hit │ │ Overpass │
      │  └──────┘ └──┬───────┘                │  └────┘ │ API live │
      │          ok   │  fail                 │         │ (3 mir.) │
      │               ▼    ▼                  │         └────┬──────┘
      │          ┌──────┐ ┌──────┐            │         ok   │  fail
      │          │save +│ │return│            │           ▼      ▼
      │          │return│ │None  │            │      ┌──────┐ ┌────────┐
      │          └──────┘ └──────┘            │      │save +│ │stale   │
      ▼                                       │      │return│ │cache?  │
  Return to caller                       return│      └──────┘ └───┬────┘
                                                │              yes │  no
                                                │                 ▼   ▼
                                                │           ┌────────┐ ┌──────┐
                                                │           │return  │ │fall- │
                                                │           └────────┘ │back  │
                                                │                     │Geo-  │
                                                │                     │JSON  │
                                                │                     │or [] │
                                                │                     └──────┘
                                                ▼
                                        Return to caller
```

### Fallback Data Files
```
data/
  trees.geojson              ← Pre-baked OSM tree export (Nuremberg)
  parks.geojson              ← Pre-baked OSM park export
  water.geojson              ← Pre-baked OSM water export
  historic.geojson           ← Pre-baked OSM historic export
  air_quality.csv            ← NO2/PM2.5 by district (37 rows)
```

### In-Memory Caches
- `data_fetch._cache` — `dict[key → FeatureCollection]`. Prevents re-fetching in a single request. Cleared on app restart.
- `agent._route_cache` — `dict["routes" → list[dict]]`. Stores full route coordinates between `get_routes` and `score_routes` tool calls. Process lifetime.
- `agent._weather_cache` — `dict["weather" → dict]`. Stores weather result for return to UI. Process lifetime.

---

## 6. LLM Integration Details

### Provider Abstraction (LiteLLM)
The `litellm` library normalizes API differences across providers. Each provider configuration specifies:
- `model`: Provider-prefixed model name (e.g., `openai/deepseek-v4-flash-free`, `gemini/gemini-2.0-flash`)
- `api_key`: Provider-specific API key (or None for no-auth providers like local Ollama)
- `api_base`: Custom base URL (or None to use provider default)

The `_llm_completion()` wrapper injects only the relevant parameters for the active provider — avoiding sending `api_base: None` to Gemini (which would override its default).

### Tool Calling Protocol
1. Tools are defined as OpenAI-compatible JSON Schema in `TOOLS` list
2. `tools` parameter passed to `litellm.completion()` alongside messages
3. LLM response includes `tool_calls` array (or not)
4. If present, each tool call is executed and the result appended as a `role: "tool"` message
5. The full message chain (with tool results) is sent back to the LLM for the next round
6. Loop continues until LLM returns plain text (no tool_calls) or max 5 rounds reached

### DeepSeek `reasoning_content`
DeepSeek models return a `reasoning_content` string alongside each assistant response during tool-calling flows. This non-standard field must be preserved and sent back in subsequent assistant messages within the same conversation turn. The `agent.py` code detects `msg.reasoning_content` and includes it in the `assistant_msg` dict appended to the message chain.

### Preferences Extraction (Client-side)
Before the first LLM call, `_extract_preferences()` scans the user query for keywords and produces a `{hot, pollution, scenic}` boolean dict. This dict is passed to `score_routes` alongside the weather data. The LLM also receives the raw user query and can extract preferences itself — the client-side extraction ensures scoring works correctly even if the LLM fails to propagate preferences through the tool call chain.

---

## 7. Scoring Engine Deep-Dive

### Segment Splitting Algorithm

Input: Route coordinate array `[[lat, lon], ...]`, target segment length (100m).

```
for each adjacent pair (p1, p2):
    d = haversine(p1, p2)
    while remaining_in_pair > 0:
        if current_segment_distance + remaining >= target:
            # Split the pair
            frac = needed_distance / d
            split_point = interpolate(p1, p2, frac)
            complete current segment at split_point
            start new segment at split_point
            remaining_in_pair -= needed_distance
        else:
            # Pair fits entirely in current segment
            add p2 to current segment
            current_segment_distance += remaining
            remaining_in_pair = 0
```

Linear interpolation for fractional splits:
```python
lat = p1[0] + (p2[0] - p1[0]) * frac
lon = p1[1] + (p2[1] - p1[1]) * frac
```

### Factor Scoring Detail

**Trees:** The `_count_nearby` function measures haversine distance from segment midpoint to each tree node. The `buffer_m=100` parameter means any tree within 100m of the midpoint is counted. The count is multiplied by 1.5 to amplify small counts (a segment with 2 trees within 100m scores 3.0/10 for trees).

**Water:** Binary-ish. The segment midpoint is checked against water `LineString` geometries (rivers, streams) with 0.002° (~200m) buffer and water `Polygon` geometries (lakes, ponds) with 0.002° buffer. Near any water → 8.0, otherwise 3.0.

**Parks:** The `_pct_near_polygon` function checks each coordinate in the segment (not just midpoint) against buffered park polygons. The fraction of points within 0.002° (~200m) of a park is multiplied by 10. A segment with 40% of its points near parks scores 4.0/10.

**Quiet:** Static lookup from `ROAD_CLASS_SCORES` using the way_type extracted from routing data. The way_type is the first entry in the route's way_types list (a simplification — assumes the route's predominant road type represents the whole route). This is a known limitation; per-segment road typing would be more accurate.

**Air Quality:** Currently uses the average of all 37 districts' NO2 + PM2.5 values. This is a simplification — the scoring engine receives segment coordinates but the air quality data lacks geographic boundaries for district matching. Using the city-wide average produces a consistent baseline score (~5.3/10).

**Heat:** Activates adaptive scoring when temperature ≥ 25°C. In hot conditions, heat comfort is computed as a weighted combination of tree coverage (shade), water proximity (cooling), and inverse air quality (cleaner air feels better). Below 5°C, returns a fixed 7.0 (cold routes are assumed to be tolerable). Between 5-25°C, defaults to 5.0.

**Historic:** Same algorithm as trees (`_count_nearby` with 100m buffer), but multiplier is 3× instead of 1.5×. Historic sites are rarer in OSM data, so the higher multiplier ensures they contribute meaningfully to the score.

### Weight Computation

```
weights = DEFAULT_WEIGHTS.copy()
if preference_profile matches:
    weights = PROFILE_WEIGHTS.copy()
for each factor with existing weight > 0:
    weights[factor] += any_extra_from_preferences  // residual boosting
normalize: each_weight = weight / sum(all_weights)
```

The residual boost (`weights[k] += prefs.get(k, 0)`) allows combining profiles with individual factor overrides, though in practice the current preference extraction only sets boolean flags, not numeric overrides.

### Normalization

After all routes are scored, if the top score is below 75:
```python
scale = 75 / max_score
for each route:
    route["total_score"] = round(route["total_score"] * scale, 1)
```

This produces visually impressive scores while maintaining relative ordering. The threshold of 75 was chosen as an ambitious but achievable target — representing ~75th percentile of a hypothetical perfect route.

---

## 8. Failure Modes & Graceful Degradation

| Failure Point | Detection | Fallback |
|--------------|-----------|----------|
| LLM API unreachable | Exception in `process_query()` → `try/except` | `_fallback_process()` runs full pipeline without LLM |
| LLM returns malformed tool calls | `json.loads()` exception on arguments | Exception propagates to outer handler → `_fallback_process()` |
| LLM returns same coordinates for origin/dest | `_find_place_in_query` returns first match for both | `_fallback_process` iterates keys sequentially and skips duplicate coords for destination |
| Nominatim rate-limited (HTTP 429) | `requests.exceptions.HTTPError` | Falls through to ORS Geocode |
| ORS Geocode fails | Exception in request or empty response | Returns `None` → hardcoded coordinates |
| ORS Routing fails | Exception or empty response list | Falls through to OSRM |
| All routing fails | Empty list returned | User sees "Sorry, I could not find any routes" |
| Open-Meteo unreachable | Exception | Weather dict with all `None`. Scoring uses default temp (15°C) for heat factor |
| Overpass API all mirrors down | All 3 POSTs fail, retries exhausted | Stale file cache → fallback GeoJSON → empty FeatureCollection |
| GeoJSON fallback file missing | `FileNotFoundError` in `_load_fallback()` | Empty FeatureCollection (`{"type": "FeatureCollection", "features": []}`) |
| Air quality CSV missing | `FileNotFoundError` | Empty list → `_get_air_score()` returns 5.0 (neutral) |
| Streamlit component failure | Component returns `None` | Component-specific defaults (geolocation: None → caption text; map: default Nuremberg marker) |

### The `_fallback_process` Safety Net

When the LLM-based pipeline fails, `_fallback_process` provides a complete offline replacement:
1. Extracts place names from the query by iterating a known list of Nuremberg locations
2. Attempts `rt.geocode()` for each before falling to hardcoded coordinates
3. Selects transport mode via keyword matching
4. Runs the full routing → weather → scoring pipeline (same functions as the LLM path)
5. Caches scores and weather on the function object itself (`function._last_*`) since process-internal state must survive across function boundaries
6. Returns a simplified one-line summary

This approach means the UI never shows a raw error — it always gets some route data as long as at least one backend API works.

---

## 9. Configuration

### Environment Variables (`.env`)
| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `ORS_API_KEY` | Yes* | - | OpenRouteService API key (free at openrouteservice.org) |
| `LLM_PROVIDER` | No | `opencode` | LLM backend to use: `opencode`, `gemini`, or `ollama` |
| `LLM_API_KEY` | Yes* | - | OpenCode Zen API key |
| `OPENCODE_MODEL` | No | `deepseek-v4-flash-free` | Model name for OpenCode provider |
| `GOOGLE_API_KEY` | No | - | Required when `LLM_PROVIDER=gemini` |
| `GEMINI_MODEL` | No | `gemini-2.0-flash` | Model name for Gemini provider |
| `OLLAMA_MODEL` | No | `llama3.2` | Model name for Ollama provider |
| `OLLAMA_BASE_URL` | No | `http://localhost:11434` | Ollama server URL |

*\* Key requirements depend on provider: ORS is optional (OSRM fallback works without it). LLM_API_KEY is required for OpenCode and Gemini providers, not for local Ollama.*

### Runtime Configuration
| Parameter | Location | Purpose |
|-----------|----------|---------|
| `BBOX` | `data_fetch.py:20` | Overpass query bounding box for Nuremberg |
| `TTL_SECONDS` | `data_fetch.py:12` | Overpass cache TTL (7200s = 2h) |
| `NUREMBERG_CENTER` | `app.py:20` | Default map center `[49.452, 11.077]` |
| `ROUTE_COLORS` | `app.py:21` | Route colors: green, blue, orange |

---

## 10. Key Design Decisions

### Why Streamlit (not FastAPI/Flask + React)
For a hackathon with a lightning presentation, Streamlit provides the fastest path from idea to working demo. The chat UI, map rendering, and data display are all built in a single Python file with minimal boilerplate. The trade-off is limited UI customization and no separation of frontend/backend.

### Why LiteLLM (not direct API calls)
LiteLLM abstracts away provider-specific API differences. The same code works with OpenCode Zen, Google Gemini, or local Ollama by changing one environment variable. This is valuable for a demo where the LLM backend might need to change due to API key availability or cost constraints.

### Why Nominatim (not Google Maps Geocoding)
Nominatim requires no API key, which simplifies setup for hackathon judges and reduces cost. For a Nuremberg-only app, its accuracy for German addresses is excellent. The 1 req/s rate limit is acceptable since geocoding happens infrequently and results are cached.

### Why ORS over OSRM (primary routing)
ORS's `alternative_routes` parameter reliably returns 2-3 genuinely different route alternatives with configurable diversity parameters (`weight_factor`, `share_factor`). OSRM's built-in `alternatives=true` rarely returns more than 1 route, and when it does the alternatives are often trivially different. ORS also supports electric cycling as a separate profile (useful for e-scooter routing).

### Why OSRM fallback (not just ORS)
ORS requires a free API key. Without it, the routing module falls back to OSRM which is completely free and requires no authentication. The via-point strategy compensates for OSRM's lack of reliable alternatives.

### Why Overpass API (not pre-downloaded GIS)
Using live Overpass queries allows the app to reflect current OSM data (tree additions, new parks, construction changes) within the 2-hour cache window. Pre-downloaded GeoJSON files serve as offline fallback.

### Why File-Based Caching (not a database)
No database server to set up or maintain. JSON files are human-readable for debugging, survive restarts, and are trivially portable. The 2-hour TTL balances data freshness with API rate limit avoidance.

### Why 100m Segments
100 meters captures street-level variation (one block might have trees while the next doesn't) while keeping segment counts manageable (~50-80 per 6km route). Finer granularity (e.g., 10m) would increase scoring time by 10x without meaningful accuracy improvement.

### Why LLM Never Sees Coordinate Arrays
Route coordinate arrays are large (200-500 coordinate pairs per route, each pair = array of 2 floats). Sending these to the LLM would consume tokens and potentially exceed context windows. By caching routes internally in `_route_cache`, the LLM only receives simplified metadata (index, distance, duration) and the scoring engine retrieves the full coordinates from the cache.

### Why Score Normalization
Raw scores rarely exceed 30-40 on a 0-100 scale because most Nuremberg routes don't have high densities of trees, parks, water, and historic sites simultaneously. Normalizing so the best route scores 75 makes the demo more impressive while preserving relative ordering. The threshold of 75 was chosen empirically — it allows meaningful separation between routes (e.g., 75 vs 63) while feeling like a strong score.

### Why File-Based Fallback GeoJSON
The `data/*.geojson` files are manually curated OSM exports for Nuremberg. They are loaded when Overpass API is completely unreachable (network failure, rate limiting, or all three mirrors down). This ensures the demo never shows an empty map — at minimum it shows routes scored against static but representative environmental data.

### Why 5 Max Tool-Call Rounds
Most queries require exactly 4 tool calls (find_location × 2, get_routes, get_weather, score_routes). Setting 5 rounds provides a safety margin for LLMs that occasionally need an extra round to correct or retry, while preventing infinite loops from buggy tool responses.

### Why `reasoning_content` Handling
DeepSeek V4 Flash Free sends a proprietary `reasoning_content` field in assistant messages during tool-calling conversations. Unlike OpenAI's API, DeepSeek requires this field to be passed back in subsequent requests for the multi-turn tool-calling state to remain consistent. Omitting this field causes DeepSeek to lose context between rounds.
