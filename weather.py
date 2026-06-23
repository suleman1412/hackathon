import requests
import logging

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def get_weather(lat: float, lon: float) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,wind_speed_10m,precipitation,uv_index",
        "timezone": "Europe/Berlin",
    }
    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        current = data.get("current", {})
        return {
            "temperature": current.get("temperature_2m"),
            "wind_speed": current.get("wind_speed_10m"),
            "precipitation": current.get("precipitation"),
            "uv_index": current.get("uv_index"),
        }
    except Exception as e:
        logger.warning(f"Weather API failed: {e}")
        return {
            "temperature": None,
            "wind_speed": None,
            "precipitation": None,
            "uv_index": None,
        }