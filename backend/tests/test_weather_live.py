"""Exercises the real Open-Meteo API. Open-Meteo needs no API key and has
generous rate limits for personal use, so unlike test_llm_extraction.py /
test_llm_chat.py these tests always run rather than skipping.
"""

from app.weather import OpenMeteoWeatherClient


def test_get_weather_resolves_a_real_location() -> None:
    client = OpenMeteoWeatherClient()

    result = client.get_weather("Boston, MA")

    assert result is not None
    assert "Boston" in result.resolved_location
    assert -50 <= result.current.temperature_f <= 130
    assert len(result.forecast) >= 1


def test_get_weather_returns_none_for_a_nonexistent_location() -> None:
    client = OpenMeteoWeatherClient()

    result = client.get_weather("Qwxzplorptown Nonexistent")

    assert result is None
