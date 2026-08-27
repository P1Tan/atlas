from typing import Optional

from app.tools import _build_get_weather_tool
from app.weather import (
    CurrentWeather,
    ForecastDay,
    WeatherResult,
    describe_weather_code,
)


class FakeWeatherClient:
    def __init__(self, result: Optional[WeatherResult] = None) -> None:
        self._result = result
        self.received_location = None

    def get_weather(self, location: str) -> Optional[WeatherResult]:
        self.received_location = location
        return self._result


def _sample_result() -> WeatherResult:
    return WeatherResult(
        resolved_location="Boston, Massachusetts, US",
        current=CurrentWeather(temperature_f=72.5, condition="Partly cloudy", wind_mph=8.2),
        forecast=[
            ForecastDay(date="2026-08-27", high_f=78.0, low_f=64.0, condition="Partly cloudy"),
            ForecastDay(date="2026-08-28", high_f=80.0, low_f=66.0, condition="Clear sky"),
        ],
    )


def test_get_weather_tool_returns_current_and_forecast_on_success() -> None:
    weather_client = FakeWeatherClient(_sample_result())
    tool = _build_get_weather_tool(weather_client)

    result = tool.handler({"location": "Boston, MA"})

    assert weather_client.received_location == "Boston, MA"
    assert result == {
        "ok": True,
        "location": "Boston, Massachusetts, US",
        "current": {
            "temperature_f": 72.5,
            "condition": "Partly cloudy",
            "wind_mph": 8.2,
        },
        "forecast": [
            {"date": "2026-08-27", "high_f": 78.0, "low_f": 64.0, "condition": "Partly cloudy"},
            {"date": "2026-08-28", "high_f": 80.0, "low_f": 66.0, "condition": "Clear sky"},
        ],
    }


def test_get_weather_tool_reports_unresolvable_location() -> None:
    weather_client = FakeWeatherClient(None)
    tool = _build_get_weather_tool(weather_client)

    result = tool.handler({"location": "Qwxzplorptown Nonexistent"})

    assert result["ok"] is False
    assert "reason" in result
    assert "Qwxzplorptown Nonexistent" in result["reason"]


def test_get_weather_tool_schema() -> None:
    tool = _build_get_weather_tool(FakeWeatherClient())

    schema = tool.to_openai_schema()

    assert schema["function"]["name"] == "get_weather"
    assert schema["function"]["parameters"]["required"] == ["location"]


def test_describe_weather_code_known_code() -> None:
    assert describe_weather_code(0) == "Clear sky"
    assert describe_weather_code(61) == "Rain"


def test_describe_weather_code_unknown_code_does_not_raise() -> None:
    description = describe_weather_code(12345)

    assert "12345" in description
