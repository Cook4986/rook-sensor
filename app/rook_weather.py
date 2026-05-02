"""
rook_enrichment.py — Zero-compute context enrichment for Rook detections.

Two strategies, both thermally free (no on-device model inference):

1. WEATHER: Open-Meteo API (free, no key) — authoritative WMO weather codes,
   fetched every 15 minutes in a background thread.

2. SPECIES CONTEXT: iNaturalist Observations API (free, no key) — fetches the
   top research-grade species observed near the device's coordinates at startup.
   When YOLO detects a generic class like "bird" or "dog", Rook appends the
   most likely local species names as a passive hint — zero inference cost.

Design principles:
   - All network calls are async/cached. The main detection loop is NEVER blocked.
   - Both features degrade gracefully — if the APIs are unreachable, the engine
     continues normally without species hints or weather context.
   - No secondary neural network runs on-device. Thermal budget is unchanged.
"""

import os
import time
import logging
import threading
from typing import Optional
import numpy as np

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False


# ─── WMO Weather Code → (emoji, label, score_bonus) ──────────────────────────
WMO_MAP = {
    0:  ("☀️",   "Clear sky",            0),
    1:  ("🌤️",   "Mostly clear",         0),
    2:  ("⛅",   "Partly cloudy",        0),
    3:  ("☁️",   "Overcast",             0),
    45: ("🌫️",   "Fog",                  5),
    48: ("🌫️",   "Icy fog",              5),
    51: ("🌦️",   "Light drizzle",        3),
    53: ("🌧️",   "Moderate drizzle",     3),
    55: ("🌧️",   "Dense drizzle",        5),
    61: ("🌧️",   "Slight rain",          3),
    63: ("🌧️",   "Moderate rain",        5),
    65: ("🌧️",   "Heavy rain",           8),
    71: ("❄️",   "Slight snow",          8),
    73: ("❄️",   "Moderate snow",       10),
    75: ("❄️⛄",  "Heavy snow",          15),
    77: ("🌨️",   "Snow grains",          8),
    80: ("🌦️",   "Rain showers",         5),
    81: ("🌧️",   "Heavy showers",        8),
    82: ("⛈️",   "Violent showers",     15),
    85: ("🌨️",   "Snow showers",        10),
    86: ("🌨️",   "Heavy snow shower",   15),
    95: ("⛈️",   "Thunderstorm",        20),
    96: ("⛈️⚡", "T-storm w/ hail",     25),
    99: ("⛈️⚡", "T-storm heavy hail",  30),
}

# iNat iconic_taxa → COCO class names that map to them
INAT_TAXON_TO_COCO = {
    "Aves":      ["bird"],
    "Mammalia":  ["dog", "cat", "bear", "horse", "sheep", "cow"],
}

FETCH_INTERVAL_SECONDS = 900   # Weather: refresh every 15 minutes
SPECIES_LIMIT = 5              # Max species hints per class in a Slack message


class RookEnrichment:
    """
    Holds cached weather + species context. Thread-safe reads.
    Instantiate once at engine startup; call .start() to begin background refresh.
    """

    def __init__(self, lat: float, lon: float):
        self.lat = lat
        self.lon = lon
        self._weather: Optional[dict] = None
        self._weather_ts: float = 0
        self._species: dict[str, list[str]] = {}   # coco_class → [common names]
        self._lock = threading.Lock()

    def start(self):
        """Kick off background threads for weather + species fetch."""
        threading.Thread(target=self._refresh_weather_loop, daemon=True).start()
        threading.Thread(target=self._fetch_species_context, daemon=True).start()

    # ── Weather ───────────────────────────────────────────────────────────────

    def _refresh_weather_loop(self):
        while True:
            self._fetch_weather()
            time.sleep(FETCH_INTERVAL_SECONDS)

    def _fetch_weather(self):
        if not _HTTPX_AVAILABLE:
            return
        try:
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={self.lat}&longitude={self.lon}"
                f"&current=weather_code,temperature_2m,wind_speed_10m,precipitation"
                f"&forecast_days=1"
            )
            r = httpx.get(url, timeout=5.0)
            r.raise_for_status()
            curr = r.json()["current"]
            code = curr["weather_code"]
            emoji, label, bonus = WMO_MAP.get(code, ("🌡️", f"WMO-{code}", 0))
            with self._lock:
                self._weather = {
                    "emoji": emoji, "label": label, "score_bonus": bonus,
                    "temp_c": curr["temperature_2m"],
                    "precip_mm": curr["precipitation"],
                    "wind_kmh": curr["wind_speed_10m"],
                    "wmo_code": code,
                }
            logging.info(f"🌤️  Weather updated: {emoji} {label} {curr['temperature_2m']}°C")
        except Exception as e:
            logging.debug(f"Weather fetch skipped: {e}")

    def get_weather(self) -> Optional[dict]:
        with self._lock:
            return self._weather

    def get_weather_emoji(self) -> str:
        w = self.get_weather()
        return w["emoji"] if w else ""

    def get_weather_score_bonus(self) -> int:
        w = self.get_weather()
        return w.get("score_bonus", 0) if w else 0

    # ── Species Context ───────────────────────────────────────────────────────

    def _fetch_species_context(self):
        """
        One-time startup fetch: get top locally-observed species from iNat
        for each relevant COCO taxon group. No auth required.
        """
        if not _HTTPX_AVAILABLE:
            return
        for taxon, coco_classes in INAT_TAXON_TO_COCO.items():
            try:
                r = httpx.get(
                    "https://api.inaturalist.org/v1/observations",
                    params={
                        "lat": self.lat, "lng": self.lon, "radius": 15,
                        "quality_grade": "research", "per_page": 30,
                        "order_by": "votes", "iconic_taxa": taxon,
                    },
                    timeout=8.0,
                )
                r.raise_for_status()
                seen, names = set(), []
                for obs in r.json().get("results", []):
                    taxon_data = obs.get("taxon", {})
                    name = taxon_data.get("preferred_common_name") or taxon_data.get("name", "")
                    if name and name not in seen:
                        seen.add(name)
                        names.append(name)
                    if len(names) >= SPECIES_LIMIT:
                        break

                with self._lock:
                    for cls in coco_classes:
                        self._species[cls] = names

                logging.info(f"🦅  iNat species context loaded for {taxon}: {names[:3]}...")
            except Exception as e:
                logging.debug(f"iNat species fetch skipped for {taxon}: {e}")

    def get_species_hint(self, coco_class: str) -> str:
        """
        Returns a parenthetical species hint string, or empty string.
        e.g. "bird" → "(locally: Red-tailed Hawk, Canada Goose, Great Horned Owl)"
        """
        with self._lock:
            names = self._species.get(coco_class, [])
        if not names:
            return ""
        return f"(locally: {', '.join(names[:3])})"

    # ── Vision Heuristics ─────────────────────────────────────────────────────

    @staticmethod
    def analyze_frame(frame_bgr: np.ndarray) -> Optional[str]:
        """
        Cheap numpy analysis of the frame to detect conditions invisible to YOLO.
        Returns an emoji string or None. Costs <5ms — safe to call every frame.

        Fog:       high mean luminance + very low contrast std
        Low-light: mean luminance below threshold
        """
        gray = frame_bgr.mean(axis=2)
        mean_lum = float(gray.mean())
        std_lum = float(gray.std())

        if std_lum < 18.0 and mean_lum > 70.0:
            return "🌫️"   # Fog: bright but flat contrast
        if mean_lum < 28.0:
            return "🌑"   # Very dark frame (lens blocked / deep night)
        return None
