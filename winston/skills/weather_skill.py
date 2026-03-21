"""
Weather skill - Real-time weather data via Open-Meteo (free, no API key needed).
Uses Open-Meteo Geocoding + Forecast APIs for accurate, structured weather data.
"""

import httpx
import logging
from datetime import datetime
from typing import Optional

from winston.skills.base import BaseSkill, SkillResult

logger = logging.getLogger("winston.skills.weather")

# WMO Weather interpretation codes → description
WMO_CODES = {
    0: "Klar", 1: "Überwiegend klar", 2: "Teilweise bewölkt", 3: "Bewölkt",
    45: "Nebel", 48: "Reifnebel",
    51: "Leichter Nieselregen", 53: "Nieselregen", 55: "Starker Nieselregen",
    61: "Leichter Regen", 63: "Regen", 65: "Starker Regen",
    66: "Leichter Gefrierregen", 67: "Starker Gefrierregen",
    71: "Leichter Schneefall", 73: "Schneefall", 75: "Starker Schneefall",
    77: "Schneekörner",
    80: "Leichte Regenschauer", 81: "Regenschauer", 82: "Starke Regenschauer",
    85: "Leichte Schneeschauer", 86: "Starke Schneeschauer",
    95: "Gewitter", 96: "Gewitter mit Hagel", 99: "Starkes Gewitter mit Hagel",
}

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


class WeatherSkill(BaseSkill):
    name = "weather"
    description = "Get current weather and forecast for a city. Returns real-time data."
    parameters = {
        "city": "City name (e.g. 'Dortmund', 'Berlin', 'New York')",
    }

    def _geocode(self, city: str) -> Optional[dict]:
        """Resolve city name to coordinates via Open-Meteo Geocoding."""
        resp = httpx.get(GEOCODING_URL, params={
            "name": city, "count": 1, "language": "de",
        }, timeout=10.0)
        resp.raise_for_status()
        results = resp.json().get("results")
        if not results:
            return None
        r = results[0]
        return {
            "name": r.get("name", city),
            "country": r.get("country", ""),
            "admin1": r.get("admin1", ""),
            "lat": r["latitude"],
            "lon": r["longitude"],
            "timezone": r.get("timezone", "auto"),
        }

    def execute(self, **kwargs) -> SkillResult:
        city = kwargs.get("city", "").strip()
        if not city:
            return SkillResult(success=False, message="Please specify a city.")

        try:
            # Step 1: Geocode city name
            geo = self._geocode(city)
            if not geo:
                return SkillResult(success=False, message=f"Stadt '{city}' nicht gefunden.")

            # Step 2: Fetch weather data
            resp = httpx.get(FORECAST_URL, params={
                "latitude": geo["lat"],
                "longitude": geo["lon"],
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m,precipitation",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
                "hourly": "temperature_2m,precipitation_probability,weather_code",
                "timezone": geo["timezone"],
                "forecast_days": 1,
            }, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()

            current = data.get("current", {})
            daily = data.get("daily", {})
            hourly = data.get("hourly", {})

            temp = current.get("temperature_2m", "?")
            feels = current.get("apparent_temperature", "?")
            humidity = current.get("relative_humidity_2m", "?")
            wind = current.get("wind_speed_10m", "?")
            wind_dir = current.get("wind_direction_10m", "?")
            precip = current.get("precipitation", 0)
            wmo = current.get("weather_code", -1)
            desc = WMO_CODES.get(wmo, "Unbekannt")

            max_t = daily.get("temperature_2m_max", ["?"])[0]
            min_t = daily.get("temperature_2m_min", ["?"])[0]
            daily_precip = daily.get("precipitation_sum", [0])[0]
            daily_wmo = daily.get("weather_code", [-1])[0]
            daily_desc = WMO_CODES.get(daily_wmo, "")

            # Build hourly forecast (every 3 hours)
            h_temps = hourly.get("temperature_2m", [])
            h_rain = hourly.get("precipitation_probability", [])
            h_codes = hourly.get("weather_code", [])
            hourly_lines = []
            for i in range(0, min(24, len(h_temps)), 3):
                h_desc = WMO_CODES.get(h_codes[i] if i < len(h_codes) else -1, "")
                rain_pct = h_rain[i] if i < len(h_rain) else 0
                hourly_lines.append(f"  {i:02d}:00 — {h_temps[i]}°C, {h_desc}, Regen: {rain_pct}%")

            location = geo["name"]
            if geo["country"]:
                location += f", {geo['country']}"

            message = (
                f"Wetter in {location}:\n"
                f"Aktuell: {temp}°C (gefühlt {feels}°C), {desc}\n"
                f"Wind: {wind} km/h (Richtung {wind_dir}°)\n"
                f"Luftfeuchtigkeit: {humidity}%\n"
                f"Niederschlag: {precip} mm\n"
                f"Tagesprognose: {min_t}°C bis {max_t}°C, {daily_desc}, Gesamtniederschlag: {daily_precip} mm\n"
            )
            if hourly_lines:
                message += "\nStündlich:\n" + "\n".join(hourly_lines)

            return SkillResult(success=True, message=message, data={
                "city": geo["name"],
                "temp_c": temp,
                "feels_like_c": feels,
                "description": desc,
                "humidity": humidity,
                "wind_kmh": wind,
                "min_temp_c": min_t,
                "max_temp_c": max_t,
            })

        except httpx.HTTPStatusError as e:
            logger.error(f"Weather API error: {e}")
            return SkillResult(success=False, message=f"Wetter-API Fehler für '{city}'.")
        except Exception as e:
            logger.error(f"Weather skill error: {e}")
            return SkillResult(success=False, message=f"Wetterabfrage fehlgeschlagen: {e}")
