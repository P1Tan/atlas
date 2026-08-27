from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol

import httpx

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo's WMO weather codes -- https://open-meteo.com/en/docs
WMO_WEATHER_CODES: Dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Fog",
    51: "Drizzle",
    53: "Drizzle",
    55: "Drizzle",
    56: "Freezing drizzle",
    57: "Freezing drizzle",
    61: "Rain",
    63: "Rain",
    65: "Rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Snow",
    73: "Snow",
    75: "Snow",
    77: "Snow grains",
    80: "Rain showers",
    81: "Rain showers",
    82: "Rain showers",
    85: "Snow showers",
    86: "Snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with hail",
}


def describe_weather_code(code: int) -> str:
    return WMO_WEATHER_CODES.get(code, f"Unknown conditions (code {code})")


@dataclass
class CurrentWeather:
    temperature_f: float
    condition: str
    wind_mph: float


@dataclass
class ForecastDay:
    date: str
    high_f: float
    low_f: float
    condition: str


@dataclass
class WeatherResult:
    resolved_location: str
    current: CurrentWeather
    forecast: List[ForecastDay]


class WeatherClient(Protocol):
    def get_weather(self, location: str) -> Optional[WeatherResult]:
        """Look up current conditions and a short forecast for a location
        name. Returns None if the location could not be resolved -- an
        expected outcome, not an error."""
        ...


class OpenMeteoWeatherClient:
    """Fetches current conditions and a short forecast from Open-Meteo (no
    API key required)."""

    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._client = client or httpx.Client(timeout=10.0)

    def get_weather(self, location: str) -> Optional[WeatherResult]:
        geocode_response = self._client.get(GEOCODING_URL, params={"name": location, "count": 1})
        geocode_response.raise_for_status()
        results = geocode_response.json().get("results") or []
        if not results:
            return None

        place = results[0]
        latitude = place["latitude"]
        longitude = place["longitude"]
        parts = [place.get("name"), place.get("admin1"), place.get("country_code")]
        resolved_location = ", ".join(part for part in parts if part)

        forecast_response = self._client.get(
            FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "timezone": "auto",
                "forecast_days": 3,
            },
        )
        forecast_response.raise_for_status()
        payload = forecast_response.json()

        current_data = payload["current"]
        current = CurrentWeather(
            temperature_f=current_data["temperature_2m"],
            condition=describe_weather_code(current_data["weather_code"]),
            wind_mph=current_data["wind_speed_10m"],
        )

        daily_data = payload["daily"]
        forecast = [
            ForecastDay(
                date=date,
                high_f=daily_data["temperature_2m_max"][i],
                low_f=daily_data["temperature_2m_min"][i],
                condition=describe_weather_code(daily_data["weather_code"][i]),
            )
            for i, date in enumerate(daily_data["time"])
        ]

        return WeatherResult(resolved_location=resolved_location, current=current, forecast=forecast)


def get_default_weather_client() -> WeatherClient:
    return OpenMeteoWeatherClient()


def get_weather_client() -> WeatherClient:
    """FastAPI dependency -- the one function every route depending on a
    weather client should use, mirroring app.extraction.get_extractor so
    dependency_overrides actually takes effect in tests."""
    return get_default_weather_client()
