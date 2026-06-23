# Green & Clean Commute Optimizer — Design Spec

## Overview

A Streamlit web app for Nuremberg that finds the healthiest and most scenic walking, cycling, or e-scooter routes. An AI "Commute Concierge" agent parses natural language requests, queries routing/environmental APIs, scores 3 alternative routes on 7 scenic and environmental factors, and presents them on an interactive map with a scoring breakdown.

## Context

Built for the Nuremberg "Agentic Datathon" (Nuremberg Data Science & AI Meetup @ Nürnberg Digital Festival 2026). Lightning presentation format — needs a working demo.

## Architecture

```
┌─────────────────────────────────────────┐
│           Streamlit App (app.py)          │
│  ┌──────────┐  ┌──────────────────────┐  │
│  │ Chat UI  │  │  Folium Map Display  │  │
│  │ (input)  │  │  (route visualization)│  │
│  └────┬─────┘  └──────────────────────┘  │
│       │                  ▲                │
│       ▼                  │                │
│  ┌─────────────────────────────────────┐   │
│  │      Commute Concierge Agent       │   │
│  │        (agent.py, LiteLLM)         │   │
│  │  Parses NL query → function calls  │   │
│  └──┬─────────┬──────────┬──────────┐    │
│     ▼         ▼          ▼          ▼    │
│  routing   scoring     weather  data_fetch │
│   .py       .py         .py      .py      │
│     │         │          │          │     │
│     ▼         ▼          ▼          ▼     │
│   ORS API   data/*    Open-Meteo  Overpass│
│  (3 routes) (fallback)  (live)   (OSM data)│
└─────────────────────────────────────────┘
```

**Data flow:**
1. User types natural language query in chat
2. LiteLLM agent parses intent, extracts origin/destination/mode/preferences
3. Agent calls `find_location` for geocoding, `get_routes` for 3 ORS alternatives, `get_weather` for live conditions, `score_routes` for environmental+scenic scoring
4. Results rendered: 3 color-coded routes on Folium map + scoring breakdown cards + agent's natural language explanation

## Components

### app.py — Streamlit UI

A single Streamlit page with three zones:

- **Left sidebar**: Chat interface (message history + text input). Displays agent responses with route recommendation explanations.
- **Main area**: Interactive Folium map (full height). Shows 3 color-coded routes (green = best, blue = 2nd, yellow = 3rd). Individual road segments are colored by local score (green = clean/shaded, yellow = moderate, red = exposed/polluted).
- **Bottom panel**: Three scorecards (one per route) breaking down per-factor scores.

Uses `streamlit-folium` for map integration. Session state stores chat history and last computed routes.

### agent.py — AI Commute Concierge

Uses **LiteLLM** (model-agnostic layer, default model: `deepseek-v4-flash-free`, configurable via `LLM_MODEL` env var).

**Tool functions exposed to LLM:**

| Function | Parameters | Returns | Implementation |
|----------|-----------|---------|----------------|
| `find_location(query)` | place name string | `{lat, lon, display_name}` | ORS geocoding API |
| `get_routes(origin, destination, mode)` | lat/lon pairs, mode string | List of 3 routes (coords, distance, duration) | ORS directions API with `alternative_routes=3` |
| `get_weather(lat, lon)` | coordinates | `{temperature, wind_speed, precipitation, uv_index}` | Open-Meteo API |
| `score_routes(routes, weather, preferences)` | route list, weather dict, pref dict | List of 3 scored routes with per-factor breakdown + per-segment scores | scoring.py |
After all tool calls complete, the app renders the Folium map and scorecards from the scored route data. Map rendering is a post-processing step handled by the app layer, not by the LLM.

**Agent flow:**
1. User query → LLM receives system prompt (Nuremberg context, tool definitions, scoring factor descriptions)
2. LLM decides tool calls (single round: find_location × 2 → get_routes → get_weather → score_routes)
3. LLM also extracts user preferences ("hot today" → boost heat/shade weight; "avoid pollution" → boost air quality weight)
4. Results returned to LLM which generates natural language summary
5. Map + scorecards rendered in Streamlit UI

**System prompt** includes Nuremberg geography context, describes the 7 scoring factors, and instructs the LLM to always produce 3 routes for comparison.

**Fallback**: If LLM fails or times out, a simple form UI appears (text inputs for origin/destination, dropdown for mode, checkboxes for preferences).

### routing.py — OpenRouteService Integration

- Geocoding via ORS `/geocode/search`
- Directions via ORS `/v2/directions/{profile}` with `alternative_routes=3` (profiles: cycling-regular, foot-walking, scooter)
- Returns route geometry (list of [lat, lon] coordinates), total distance (m), total duration (s), and per-segment way type information
- API key stored in `.env` as `ORS_API_KEY`
- Free tier: 2,000 requests/day

### weather.py — Open-Meteo Integration

- Calls Open-Meteo `/v1/forecast` with `current=temperature_2m,wind_speed_10m,precipitation,uv_index`
- No API key required (free, always available)
- Feeds into heat/shade scoring — on hot days (≥25°C), unshaded segments are penalized harder

### scoring.py — Scoring Engine

Each route is split into ~100m segments. Each segment scored independently on **7 factors**:

| Factor | Data Source | Scoring Method | Default Weight |
|--------|-------------|----------------|----------------|
| Tree canopy | OSM `natural=tree`, `landuse=forest` via Overpass | Count tree tags within 50m buffer of segment, normalized to 0-10 | 20% |
| Water features | OSM `natural=water`, `waterway=*` via Overpass | Binary proximity: +points if segment within 100m of water | 15% |
| Parks & green spaces | OSM `leisure=park`, `leisure=garden` via Overpass | % of segment length within 50m of park polygon | 15% |
| Quiet / low-traffic | OSM `highway` tag types (residential, pedestrian, cycleway vs primary/secondary) | Score inversely weighted by road class | 15% |
| Air quality | UmweltAtlas/LfU fallback data (NO2, PM2.5 by district) | Lower pollution = higher score, interpolated by district | 15% |
| Urban heat / shade | Open-Meteo temp + OSM tree cover (trees as shade proxy) | Hot day penalty for unshaded segments; shade bonus from tree count within 50m | 10% |
| Historic / cultural | OSM `historic=*`, `tourism=*` via Overpass | Count points of interest within 50m of segment | 10% |

**Adaptive weighting** based on user query preferences:
- Default weights as shown above
- If user mentions "hot"/"heat": heat weight → 30%, trees → 25% (shade priority), others reduced proportionally
- If user mentions "pollution"/"air"/"traffic": air quality → 30%, quiet → 20%, others reduced proportionally
- If user mentions "scenic"/"beautiful": water → 25%, historic → 20%, parks → 20%, others reduced proportionally

**Route total score** = weighted average of segment scores (0–100 scale).

### data_fetch.py — OSM Overpass Queries

- Queries Overpass API for Nuremberg bounding box
- Fetches: trees (`node["natural"="tree"]`), forests (`way["landuse"="forest"]`), parks (`way["leisure"="park"]`), water (`way["natural"="water"]`, `way["waterway"]`), historic sites (`node["historic"]`, `node["tourism"]`)
- Caches results in memory for session duration
- If Overpass times out (>10s) or fails, loads pre-baked fallback GeoJSON from `data/` directory

### data/ — Pre-baked Fallback Data

| File | Content | Source |
|------|---------|--------|
| `trees.geojson` | Tree points + forest polygons for Nuremberg | OSM export |
| `parks.geojson` | Park and garden polygons for Nuremberg | OSM export |
| `water.geojson` | Water bodies and waterways (Pegnitz river, canals, lakes) | OSM export |
| `historic.geojson` | Historic landmarks and cultural points of interest | OSM export |
| `air_quality.csv` | District-level NO2 and PM2.5 values for Nuremberg districts | UmweltAtlas/LfU |

## File Structure

```
hackathon/
├── app.py              # Streamlit UI (chat + map + scorecards)
├── agent.py            # LiteLLM function-calling concierge
├── routing.py          # OpenRouteService integration (3 alt routes)
├── scoring.py          # 7-factor scoring engine + per-segment analysis
├── weather.py          # Open-Meteo integration
├── data_fetch.py       # OSM Overpass queries (live + fallback bundling)
├── requirements.txt    # streamlit, folium, streamlit-folium, litellm, requests, python-dotenv
├── .env.example        # ORS_API_KEY, LLM_MODEL, LLM_API_KEY
└── data/
    ├── trees.geojson
    ├── parks.geojson
    ├── water.geojson
    ├── historic.geojson
    └── air_quality.csv
```

## Error Handling

- **No ORS API key**: App starts in "demo mode" with a pre-saved sample route and explanation.
- **LLM timeout/failure**: Falls back to a simple form UI (origin/destination text inputs, mode dropdown, preference checkboxes).
- **Overpass API timeout/failure**: Silently uses pre-baked fallback GeoJSON files. Logs warning to console.
- **Weather API failure**: Proceeds without weather factor — heat weight set to 0, redistributed proportionally across remaining factors.
- **ORS routing failure**: Shows error message in chat, suggests retry.

No automated tests for the hackathon — manual testing via the Streamlit UI.

## Environment Variables

```
ORS_API_KEY=your_openrouteservice_key
LLM_MODEL=deepseek/deepseek-v4-flash-free
LLM_API_KEY=your_llm_api_key
```

## Tech Stack

- **Python 3.11+**
- **Streamlit** — web UI framework
- **streamlit-folium** + **Folium** — map rendering
- **LiteLLM** — unified LLM layer (model-agnostic)
- **requests** — HTTP API calls
- **python-dotenv** — environment variable loading
- **OpenRouteService API** — routing + geocoding
- **Open-Meteo API** — live weather
- **OSM Overpass API** — environmental/scenic feature data
- **UmweltAtlas / LfU** — air quality data (with static CSV fallback)

## Transport Modes

- Cycling (ORS profile: `cycling-regular`)
- Walking (ORS profile: `foot-walking`)
- E-scooter (ORS profile: `cycling-electric` — ORS may not have a dedicated scooter profile; fall back to `cycling-regular` if unavailable)

## User Experience Flow

1. User opens app → sees chat box and empty map centered on Nuremberg
2. User types: *"Bike from Südstadt to adorsys, it's hot, avoid traffic pollution"*
3. Agent processes for a few seconds (geocode → 3 routes → weather → score → render)
4. Map shows 3 routes color-coded by score
5. Chat shows explanation: *"Route 1 scored best (87/100) — it follows the Pegnitz river through Marienberg Park, keeping you in shade and away from Dieselstraße..."*
6. Scorecards at bottom show per-factor breakdown for all 3 routes
7. User can submit a new query to recalculate