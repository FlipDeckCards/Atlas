"""Gale, the weather agent, offline: a fake Open-Meteo answers; nothing real is called."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from openjarvis.server.crew import engine as eng_mod
from openjarvis.server.crew import weather


def run(c):
    return asyncio.run(c)


def _factory(geo=None, fc_status=200):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json={"results": geo if geo is not None else [{"name": "Los Angeles", "admin1": "California", "country_code": "US", "country": "United States",
                                                                                      "latitude": 34.05, "longitude": -118.24}]})
        if fc_status != 200:
            return httpx.Response(fc_status, json={})
        return httpx.Response(200, json={
            "current": {"temperature_2m": 71.6, "apparent_temperature": 70.2, "weather_code": 1, "wind_speed_10m": 6.4, "relative_humidity_2m": 48},
            "daily": {"time": ["2026-10-25", "2026-10-26"], "weather_code": [0, 63], "temperature_2m_max": [78.1, 66.9], "temperature_2m_min": [58.0, 55.2],
                      "precipitation_probability_max": [2, 70], "wind_speed_10m_max": [9.0, 14.2]}})

    return (lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))), seen


def test_forecast_reads_now_and_each_day_in_plain_words():
    cf, seen = _factory()
    out = run(weather.forecast("Los Angeles, CA", 2, cf))
    assert out.startswith("Weather for Los Angeles, California, US:")
    assert "Now: mostly clear, 72°F" in out and "wind 6 mph" in out
    assert "Today: clear, high 78°F, low 58°F, 2% chance of rain" in out and "Mon Oct 26: rain, high 67°F" in out and "70% chance of rain" in out
    assert seen[1].url.params["forecast_days"] == "2" and seen[1].url.params["temperature_unit"] == "fahrenheit"


def test_metric_days_are_capped_and_default_place_is_used():
    cf, seen = _factory()
    out = run(weather.forecast("", 99, cf, metric=True))
    assert "°C" in out and "km/h" in out
    assert seen[0].url.params["name"] == "Los Angeles" and seen[1].url.params["forecast_days"] == "7"


def test_an_unknown_place_or_a_down_service_says_so_plainly():
    with pytest.raises(weather.WeatherError, match="could not find a place"):
        run(weather.forecast("Nowhereville", 3, _factory(geo=[])[0]))
    with pytest.raises(weather.WeatherError, match="answered 503"):
        run(weather.forecast("Los Angeles", 3, _factory(fc_status=503)[0]))


def test_gale_has_the_weather_tool_and_no_hands_beyond_it():
    names = {t["function"]["name"] for t in eng_mod.tools_for("weather")}
    assert "weather" in eng_mod.ROLES and eng_mod.ROLES["weather"]["name"] == "Gale" and eng_mod.ROLES["weather"]["model"] == "lookup"
    assert "get_weather" in names and not names & {"repo_commit", "request_merge", "use_connection", "generate_image", "check_site"}


def test_the_engine_runs_get_weather_for_gale_only_and_reports_a_bad_place_in_words():
    from tests.server.test_crew import two_task_plan, world

    w = world(two_task_plan(), {})
    task, mission = {"user_id": "u", "mission_id": "m", "id": "t", "role": "weather", "_transcript": []}, {"id": "m"}
    w.engine.client_factory = _factory()[0]
    out, _ = run(w.engine._tool(task, mission, "get_weather", {"place": "Los Angeles, CA", "days": 2}))
    assert "Weather for Los Angeles" in out and "Today: clear" in out
    w.engine.client_factory = _factory(geo=[])[0]
    out, _ = run(w.engine._tool(task, mission, "get_weather", {"place": "Nowhereville"}))
    assert "could not find a place" in out
    other, _ = run(w.engine._tool(dict(task, role="writer"), mission, "get_weather", {"place": "Los Angeles"}))
    assert "Weather for" not in other


def test_jet_plans_trips_with_search_and_the_forecast_and_cannot_book_or_spend():
    names = {t["function"]["name"] for t in eng_mod.tools_for("travel")}
    assert eng_mod.ROLES["travel"]["name"] == "Jet" and eng_mod.ROLES["travel"]["model"] == "agent"
    assert {"web_search", "get_weather", "save_output", "finish"} <= names
    assert not names & {"use_connection", "marnyx_post", "repo_commit", "request_merge", "generate_image", "check_site", "make_book"}


def test_the_engine_gives_jet_the_forecast_for_its_travel_dates():
    from tests.server.test_crew import two_task_plan, world

    w = world(two_task_plan(), {})
    w.engine.client_factory = _factory()[0]
    task = {"user_id": "u", "mission_id": "m", "id": "t", "role": "travel", "_transcript": []}
    out, _ = run(w.engine._tool(task, {"id": "m"}, "get_weather", {"place": "Los Angeles, CA", "days": 2}))
    assert "Weather for Los Angeles" in out


def test_cue_plans_events_with_search_and_the_forecast_and_cannot_send_book_or_pay():
    from tests.server.test_crew import two_task_plan, world

    names = {t["function"]["name"] for t in eng_mod.tools_for("event")}
    assert eng_mod.ROLES["event"]["name"] == "Cue" and eng_mod.ROLES["event"]["model"] == "agent"
    assert {"web_search", "get_weather", "save_output", "finish"} <= names
    assert not names & {"use_connection", "marnyx_post", "repo_commit", "request_merge", "generate_image", "check_site", "make_book"}
    w = world(two_task_plan(), {})
    w.engine.client_factory = _factory()[0]
    out, _ = run(w.engine._tool({"user_id": "u", "mission_id": "m", "id": "t", "role": "event", "_transcript": []}, {"id": "m"}, "get_weather", {"place": "Los Angeles, CA"}))
    assert "Weather for Los Angeles" in out


def test_aibussol_is_installable_as_a_phone_app():
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    from openjarvis.server.app import create_app

    c = TestClient(create_app(MagicMock(), "m", api_key="k", require_auth=True), raise_server_exceptions=False)
    m = c.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.json()["start_url"] == "/station" and m.json()["display"] == "standalone"
    assert {i["sizes"] for i in m.json()["icons"]} == {"192x192", "512x512"}
    for icon in m.json()["icons"]:
        r = c.get(icon["src"])
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert c.get("/crew/icons/apple-touch-icon.png").status_code == 200 and c.get("/crew/icons/nope.png").status_code == 404
    sw = c.get("/sw.js")
    assert sw.status_code == 200 and "fetch" in sw.text and "javascript" in sw.headers["content-type"]
    assert c.get("/crew/bots.png").status_code == 200                                     # the sprite sheet route still wins its own path
    page = c.get("/station").text
    assert 'rel="manifest"' in page and "apple-touch-icon" in page and "serviceWorker.register('/sw.js')" in page


def test_the_station_page_has_the_outer_ring_and_its_walkway():
    from openjarvis.server.crew.station import STATION_PAGE

    for part in ("function drawWalkway", "OUTER_ORDER = [0, 4, 2, 6, 1, 5, 3, 7]", "ringGeom(", "let walkway = null", "drawWalkway(t);\n  mods.forEach(m => drawModule"):
        assert part in STATION_PAGE
    assert "SC *= Math.sin(Math.PI / N)" not in STATION_PAGE                    # the old shrink-everything ring is gone


def test_the_station_page_has_the_16_outer_slots_branch_routes_and_lit_junctions():
    from openjarvis.server.crew import tenant
    from openjarvis.server.crew.station import STATION_PAGE

    for part in ("const MAX_EXTRA_BRANCHES = 16", "function routeOf(m)", "function routeAt(rt, d)", "const branch = j >= 8", "function plateDepth(S)", "const JCOL = [", "an open bay is a factory that is switched off"):
        assert part in STATION_PAGE
    assert "setLineDash([6 * SC, 6 * SC]); ctx.strokeStyle = alpha(col, 0.3" not in STATION_PAGE      # open bays are no longer dashed outlines
    assert tenant.MAX_BUSINESSES == 17                                                                  # 4 inner + 16 outer slots, less the owner's fixed three


def test_the_station_has_a_planet_and_twenty_walkway_walkers():
    from openjarvis.server.crew.station import STATION_PAGE

    assert "function drawPlanet(t)" in STATION_PAGE and "  drawPlanet(t);\n  drawMiniStation(t, dt);" in STATION_PAGE
    assert "Array.from({ length: 20 }, (_, i) => WALK_ROLES[i % WALK_ROLES.length])" in STATION_PAGE


def test_on_a_phone_the_station_fills_its_box_and_the_desktop_sizing_is_untouched():
    from openjarvis.server.crew.station import STATION_PAGE

    assert "Math.min(W, H) / (2 * 382.5) * 0.95" in STATION_PAGE
    assert "SC = Math.max(0.34, Math.min(1.25, Math.min(dw / 1060, dh / 800), Math.min(W / 1060, H / 800)));" in STATION_PAGE   # the desktop line, as it was


def test_every_name_plate_is_the_size_of_helix_business_except_the_expansion_bays():
    from openjarvis.server.crew.station import STATION_PAGE

    assert "ctx.measureText('HELIX BUSINESS…').width + 18 * SC + 6" in STATION_PAGE
    assert "const pw = m.expansion ? Math.max(nw, sw) + 18 * SC + 6 : fixedW" in STATION_PAGE
