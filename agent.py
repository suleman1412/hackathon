import os
import json
import logging

from litellm import completion

import routing as rt
import weather as wx
import scoring as sc

logger = logging.getLogger(__name__)

PROVIDERS = {
    "opencode": {
        "model": "openai/" + os.getenv("OPENCODE_MODEL", "deepseek-v4-flash-free"),
        "api_key": os.getenv("LLM_API_KEY", os.getenv("OPENCODE_ZEN_API_KEY", "")),
        "api_base": "https://opencode.ai/zen/v1",
    },
    "gemini": {
        "model": "gemini/" + os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        "api_key": os.getenv("GOOGLE_API_KEY", ""),
        "api_base": None,
    },
    "ollama": {
        "model": "ollama/" + os.getenv("OLLAMA_MODEL", "llama3.2"),
        "api_key": None,
        "api_base": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    },
}

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "opencode")

_provider = PROVIDERS.get(LLM_PROVIDER, PROVIDERS["opencode"])
LLM_MODEL = _provider["model"]
LLM_API_KEY = _provider["api_key"]
LLM_API_BASE = _provider["api_base"]

logger.info(f"LLM provider: {LLM_PROVIDER}, model: {LLM_MODEL}, base: {LLM_API_BASE}")


def _llm_completion(messages, tools=None):
    kwargs = {
        "model": LLM_MODEL,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    if LLM_API_KEY:
        kwargs["api_key"] = LLM_API_KEY
    if LLM_API_BASE:
        kwargs["api_base"] = LLM_API_BASE
    return completion(**kwargs)


SYSTEM_PROMPT = """You are the Green & Clean Commute Concierge for Nuremberg, Germany.
You help users find the healthiest and most scenic routes for walking, cycling, or e-scooter trips.

Your job:
1. Understand the user's natural language request — extract origin, destination, transport mode, and any preferences (e.g., "hot", "avoid pollution", "scenic").
2. Use the available tools to:
   - find_location: geocode origin and destination
   - get_routes: get 3 alternative routes
   - get_weather: fetch current weather at the midpoint
   - score_routes: score all routes based on scenic and environmental factors
3. After getting scored routes, write a concise natural language summary explaining which route is best and why, referencing specific features (rivers, parks, trees, quiet roads, historic landmarks).

Nuremberg context:
- The Pegnitz river runs through the city center
- Major parks: Marienberg Park, Stadtpark, Wöhrder Wiese, Volkspark Dutzendteich
- Key landmarks: Nürnberger Burg, Frauenkirche, St. Sebalduskirche
- adorsys office is in Nuremberg (near Südliche Außenstadt)
- High-traffic streets to avoid: Frankenstraße, Dieselstraße, Bayernstraße

Keep your responses friendly, concise, and in the user's language. Always present all 3 routes with their scores."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_location",
            "description": "Geocode a place name or address in Nuremberg to latitude/longitude coordinates",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Place name or address, e.g. 'Südstadt Nuremberg' or 'adorsys Nuremberg'",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_routes",
            "description": "Get 3 alternative routes between two coordinates for a given transport mode",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {
                        "type": "object",
                        "properties": {
                            "lat": {"type": "number"},
                            "lon": {"type": "number"},
                        },
                        "required": ["lat", "lon"],
                    },
                    "destination": {
                        "type": "object",
                        "properties": {
                            "lat": {"type": "number"},
                            "lon": {"type": "number"},
                        },
                        "required": ["lat", "lon"],
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["bike", "walk", "scooter"],
                        "description": "Transport mode",
                    },
                },
                "required": ["origin", "destination", "mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather at a given location",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lon": {"type": "number"},
                },
                "required": ["lat", "lon"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "score_routes",
            "description": "Score the previously fetched routes based on scenic and environmental factors. Routes are automatically retrieved from the previous get_routes call — do NOT pass route data, just pass weather and preferences.",
            "parameters": {
                "type": "object",
                "properties": {
                    "weather": {
                        "type": "object",
                        "description": "Weather dict from get_weather",
                    },
                    "preferences": {
                        "type": "object",
                        "description": "User preferences dict with optional keys: hot (bool), pollution (bool), scenic (bool)",
                        "properties": {
                            "hot": {"type": "boolean"},
                            "pollution": {"type": "boolean"},
                            "scenic": {"type": "boolean"},
                        },
                    },
                },
                "required": ["weather", "preferences"],
            },
        },
    },
]


_route_cache = {}
_weather_cache = {}


def _execute_tool(name: str, args: dict) -> dict:
    if name == "find_location":
        return rt.geocode(args["query"]) or {"error": f"Could not find location: {args['query']}"}

    elif name == "get_routes":
        routes = rt.get_routes(args["origin"], args["destination"], args["mode"])
        _route_cache["routes"] = routes
        simplified = [
            {"route_index": i + 1, "distance": r["distance"], "duration": r["duration"]}
            for i, r in enumerate(routes)
        ]
        return {"routes": simplified, "count": len(routes)}

    elif name == "get_weather":
        result = wx.get_weather(args["lat"], args["lon"])
        _weather_cache["weather"] = result
        return result

    elif name == "score_routes":
        cached = _route_cache.get("routes", [])
        full_routes = []
        for r in cached:
            full_routes.append({
                "coordinates": r["coordinates"],
                "distance": r["distance"],
                "duration": r["duration"],
                "way_types": r.get("way_types", []),
            })
        return sc.score_routes(full_routes, args["weather"], args["preferences"])

    return {"error": f"Unknown tool: {name}"}


def _extract_preferences(user_query: str) -> dict:
    q = user_query.lower()
    return {
        "hot": any(w in q for w in ["hot", "heat", "warm", "sonnig", "heiß"]),
        "pollution": any(w in q for w in ["pollut", "traffic", "emission", "abgas", "verkehr", "smog", "dirty air"]),
        "scenic": any(w in q for w in ["scenic", "beautiful", "nice", "pretty", "scön", "schön", "undeadful", "green"]),
    }


def process_query(user_query: str) -> dict:
    preferences = _extract_preferences(user_query)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_query},
    ]

    scored_routes = None
    agent_text = ""

    try:
        max_rounds = 5
        for _round in range(max_rounds):
            response = _llm_completion(messages, tools=TOOLS)
            msg = response.choices[0].message

            reasoning = getattr(msg, "reasoning_content", None)

            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tool_results = {}
                assistant_msg = {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in msg.tool_calls
                    ],
                }
                if reasoning:
                    assistant_msg["reasoning_content"] = reasoning
                messages.append(assistant_msg)

                for tc in msg.tool_calls:
                    fn_name = tc.function.name
                    fn_args = json.loads(tc.function.arguments)
                    logger.info(f"Tool call: {fn_name}({fn_args})")
                    result = _execute_tool(fn_name, fn_args)
                    tool_results[fn_name] = result

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": fn_name,
                        "content": json.dumps(result, default=str),
                    })

                if "score_routes" in tool_results:
                    scored_routes = tool_results["score_routes"]

                continue
            else:
                agent_text = msg.content or ""
                break
        else:
            agent_text = msg.content or "" if "msg" in dir() else ""

    except Exception as e:
        logger.error(f"LLM processing failed: {e}")
        agent_text = _fallback_process(user_query, preferences)
        scored_routes = getattr(_fallback_process, "_last_scored", None)

    return {
        "text": agent_text,
        "scored_routes": scored_routes,
        "preferences": preferences,
        "weather": _weather_cache.get("weather"),
    }


def _fallback_process(user_query: str, preferences: dict) -> str:
    q_lower = user_query.lower()

    def _geocode(name: str) -> dict:
        result = rt.geocode(f"{name}, Nuremberg, Germany")
        if result:
            return {"lat": result["lat"], "lon": result["lon"]}
        return None

    hardcoded_coords = {
        "südstadt": {"lat": 49.425, "lon": 11.060},
        "adorsys": {"lat": 49.455, "lon": 11.080},
        "mitte": {"lat": 49.452, "lon": 11.077},
        "hauptbahnhof": {"lat": 49.446, "lon": 11.083},
    }

    origin = dest = None

    for key in ["südstadt", "adorsys", "mitte", "hauptbahnhof"]:
        if key in q_lower:
            geo = _geocode(key)
            if not origin:
                origin = geo or hardcoded_coords[key]
            elif not dest and geo and (geo["lat"] != origin["lat"] or geo["lon"] != origin["lon"]):
                dest = geo

    if not origin:
        origin = {"lat": 49.425, "lon": 11.060}
    if not dest:
        dest = {"lat": 49.452, "lon": 11.077}

    mode = "bike"
    if "walk" in q_lower or "flug" in q_lower:
        mode = "walk"
    elif "scooter" in q_lower or "roller" in q_lower:
        mode = "scooter"

    routes = rt.get_routes(origin, dest, mode)
    if not routes:
        return "Sorry, I could not find any routes. Please check your API key and try again."

    weather = wx.get_weather(
        (origin["lat"] + dest["lat"]) / 2,
        (origin["lon"] + dest["lon"]) / 2,
    )
    scored = sc.score_routes(routes, weather, preferences)
    _fallback_process._last_scored = scored
    _fallback_process._last_weather = weather

    best = scored[0] if scored else None
    if best:
        return f"Best route: Route {best['index']} (score: {best['total_score']}/100). " \
               f"Distance: {best['distance']/1000:.1f}km, Duration: {best['duration']/60:.0f}min."
    return "No routes found."