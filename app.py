import os
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

import streamlit as st
import folium
from streamlit_folium import st_folium
from streamlit_geolocation import streamlit_geolocation

from agent import process_query, _fallback_process
import routing as rt
import weather as wx
import scoring as sc

logging.basicConfig(level=logging.INFO)

NUREMBERG_CENTER = [49.452, 11.077]
ROUTE_COLORS = ["green", "blue", "orange"]

FACTOR_LABELS = {
    "trees": "Trees & Shade",
    "water": "Water Nearby",
    "parks": "Parks & Green",
    "quiet": "Quiet Roads",
    "air": "Air Quality",
    "heat": "Heat Comfort",
    "historic": "Historic Sites",
}

st.set_page_config(
    page_title="Green & Clean Commute Optimizer",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "scored_routes" not in st.session_state:
    st.session_state.scored_routes = None
if "weather_data" not in st.session_state:
    st.session_state.weather_data = None
if "my_location" not in st.session_state:
    st.session_state.my_location = None
if "raw_location" not in st.session_state:
    st.session_state.raw_location = None


def reverse_geocode(lat: float, lon: float) -> str:
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {"lat": lat, "lon": lon, "format": "json", "addressdetails": 1}
    headers = {"User-Agent": "GreenCleanCommute/1.0 (Nuremberg Datathon)"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        addr = data.get("address", {})
        parts = [addr.get(k, "") for k in ("road", "suburb", "city", "town", "village") if addr.get(k)]
        return ", ".join(parts) if parts else data.get("display_name", "Unknown")
    except Exception as e:
        logging.warning(f"Reverse geocode failed: {e}")
        return None


def build_map(scored_routes=None, user_location=None) -> folium.Map:
    center = NUREMBERG_CENTER
    zoom = 13

    if user_location and user_location.get("latitude"):
        center = [user_location["latitude"], user_location["longitude"]]
        zoom = 14

    m = folium.Map(location=center, zoom_start=zoom)

    if user_location and user_location.get("latitude"):
        lat, lon = user_location["latitude"], user_location["longitude"]
        folium.CircleMarker(
            location=[lat, lon],
            radius=8,
            color="#2563eb",
            fill=True,
            fillColor="#3b82f6",
            fillOpacity=0.7,
            weight=2,
            tooltip="📍 You are here",
            popup=f"{lat:.4f}, {lon:.4f}",
        ).add_to(m)
        if user_location.get("accuracy"):
            folium.Circle(
                location=[lat, lon],
                radius=user_location["accuracy"],
                color="#3b82f6",
                fill=True,
                fillOpacity=0.08,
                weight=1,
            ).add_to(m)

    if scored_routes:
        for i, route in enumerate(scored_routes):
            color = ROUTE_COLORS[i % len(ROUTE_COLORS)]
            coords = route["coordinates"]

            folium.PolyLine(
                coords,
                color=color,
                weight=5,
                opacity=0.8,
                popup=f"Route {route['index']} — Score: {route['total_score']}/100",
                tooltip=f"Route {route['index']} ({route['total_score']}/100)",
            ).add_to(m)

            for seg, seg_scores in zip(route.get("segments", []), route.get("segment_scores", [])):
                seg_total = sum(seg_scores.values()) / len(seg_scores)
                if seg_total >= 7:
                    seg_color = "green"
                elif seg_total >= 4:
                    seg_color = "yellow"
                else:
                    seg_color = "red"

                folium.PolyLine(
                    seg["points"],
                    color=seg_color,
                    weight=2,
                    opacity=0.5,
                ).add_to(m)

            if coords:
                mid = coords[len(coords) // 2]
                folium.Marker(
                    location=coords[0],
                    icon=folium.Icon(color=color, icon="play", prefix="fa"),
                    popup=f"Route {route['index']} Start",
                ).add_to(m)
                folium.Marker(
                    location=coords[-1],
                    icon=folium.Icon(color=color, icon="flag", prefix="fa"),
                    popup=f"Route {route['index']} End",
                ).add_to(m)

        bounds = []
        for route in scored_routes:
            bounds.extend(route["coordinates"])
        if user_location and user_location.get("latitude"):
            bounds.append([user_location["latitude"], user_location["longitude"]])
        if bounds:
            m.fit_bounds(bounds)
    else:
        if not user_location or not user_location.get("latitude"):
            folium.Marker(
                location=NUREMBERG_CENTER,
                icon=folium.Icon(color="green", icon="leaf", prefix="fa"),
                popup="Nuremberg",
            ).add_to(m)

    return m


def render_scorecards(scored_routes):
    if not scored_routes:
        return

    cols = st.columns(len(scored_routes))
    for i, route in enumerate(scored_routes):
        with cols[i]:
            color = ROUTE_COLORS[i % len(ROUTE_COLORS)]
            st.markdown(f"### :{color}[Route {route['index']}] — {route['total_score']}/100")
            st.caption(f"📏 {route['distance']/1000:.1f} km · ⏱️ {route['duration']/60:.0f} min")

            for factor, label in FACTOR_LABELS.items():
                val = route["factors"].get(factor, 0)
                bar = "█" * int(val) + "░" * (10 - int(val))
                st.text(f"{label:16s} {bar} {val:.1f}")


st.title("🌿 Green & Clean Commute Optimizer")
st.markdown("*Find the healthiest, most scenic route through Nuremberg*")

with st.sidebar:
    st.markdown("### 📍 My Location")
    location = streamlit_geolocation()
    if location and location.get("latitude") is not None:
        st.session_state.raw_location = location
        lat, lon = location["latitude"], location["longitude"]
        st.success(f"📍 {lat:.4f}, {lon:.4f}" + (f" ±{location['accuracy']:.0f}m" if location.get('accuracy') else ""))
        if st.button("🔍 Reverse Geocode as Origin"):
            place_str = reverse_geocode(lat, lon)
            if place_str:
                st.session_state.my_location = {"lat": lat, "lon": lon, "name": place_str}
                st.info(f"Origin set to: {place_str[:60]}...")
                st.session_state.chat_history.append({
                    "role": "user",
                    "content": f"From {place_str.split(',')[0].strip()} ",
                })
                st.rerun()
    else:
        st.caption("Allow browser location access to auto-detect your position.")

    if st.session_state.my_location:
        st.markdown(f"**Origin:** {st.session_state.my_location['name'][:60]}")

    st.markdown("---")
    st.markdown("**Examples:**")
    examples = [
        "Bike from Südstadt to adorsys, it's hot",
        "Walk from Hauptbahnhof to Wöhrder Wiese",
        "Scooter to Marienberg Park, avoid traffic",
        "Scenic bike route from Mitte to Volkspark Dutzendteich",
    ]
    for ex in examples:
        if st.button(ex, key=f"ex_{ex[:20]}"):
            st.session_state.chat_history.append({"role": "user", "content": ex})
            st.rerun()

col_chat, col_map = st.columns([1, 2])

with col_chat:
    st.markdown("### 💬 Commute Concierge")

    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    user_input = st.chat_input("e.g., 'Bike from Südstadt to adorsys, it's hot, avoid traffic pollution'")

    if user_input:
        st.session_state.chat_history.append({"role": "user", "content": user_input})

        with st.chat_message("user"):
            st.write(user_input)

        with st.chat_message("assistant"):
            with st.spinner("🤖 Finding your perfect route..."):
                result = process_query(user_input)
                st.write(result["text"])
                st.session_state.chat_history.append({"role": "assistant", "content": result["text"]})

                if result.get("scored_routes"):
                    st.session_state.scored_routes = result["scored_routes"]
                    st.session_state.weather_data = result.get("weather")
                elif result.get("preferences"):
                    scored = getattr(_fallback_process, "_last_scored", None)
                    if scored:
                        st.session_state.scored_routes = scored
                        st.session_state.weather_data = getattr(_fallback_process, "_last_weather", None)

with col_map:
    st.markdown("### 🗺️ Route Map")
    m = build_map(st.session_state.scored_routes, st.session_state.raw_location)
    st_folium(m, use_container_width=True, height=500)

st.markdown("---")
if st.session_state.scored_routes:
    st.markdown("### 📊 Scoring Breakdown")
    render_scorecards(st.session_state.scored_routes)

    if st.session_state.weather_data:
        w = st.session_state.weather_data
        if w.get("temperature") is not None:
            st.markdown(f"**Current Weather:** {w['temperature']}°C · 💨 {w.get('wind_speed', '?')} km/h · 🌧️ {w.get('precipitation', '?')} mm")
else:
    st.info("👆 Ask the Commute Concierge above to see scored routes on the map.")