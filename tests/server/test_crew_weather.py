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


def test_every_name_plate_is_the_size_of_helix_business_expansion_bays_too():
    from openjarvis.server.crew.station import STATION_PAGE

    assert "ctx.measureText('HELIX BUSINESS…').width + 18 * SC + 6" in STATION_PAGE
    assert "const pw = fixedW, ph = fs + fs2 + 12 * SC;" in STATION_PAGE and "Expansion Bays included" in STATION_PAGE


def test_the_3d_view_is_a_button_and_two_files_loaded_only_on_demand():
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    from openjarvis.server.app import create_app
    from openjarvis.server.crew.station import STATION_PAGE

    assert 'id="t-view"' in STATION_PAGE and "VIEW: 2D" in STATION_PAGE and "async function setView(on)" in STATION_PAGE
    assert "document.body.classList.contains('v3d')" in STATION_PAGE and "Station3D.update(mods)" in STATION_PAGE
    assert "<script src=\"/crew/three.min.js" not in STATION_PAGE            # three.js is fetched only when the button is first used
    c = TestClient(create_app(MagicMock(), "m", api_key="k", require_auth=True), raise_server_exceptions=False)
    three, s3d = c.get("/crew/three.min.js"), c.get("/crew/station3d.js")
    assert three.status_code == 200 and "javascript" in three.headers["content-type"] and len(three.content) > 100000
    assert s3d.status_code == 200 and "window.Station3D" in s3d.text and "geodesic" in s3d.text


def test_3d_view_has_big_dome_panels_coloured_rim_lights_and_the_crew_on_helixs_ring():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / "src" / "openjarvis" / "server" / "crew" / "assets" / "station3d.js").read_text()
    assert "IcosahedronGeometry" not in js and "InstancedMesh(new THREE.CylinderGeometry(1, 1, 1, 6)" not in js   # a plain clear glass dome: no frame, no hexagons, no piping
    assert "function updateRim(t)" in js and "setColorAt(i" in js and "0xffe9b8" not in js          # the rim lights are coloured and animated, not all yellow
    assert "function syncCrew()" in js and "getRoster" in js                                          # the idle crew walk Helix's own ring
    assert "userData.v = 0.0000055 + (i % 5) * 0.000002" in js                                                   # the walkway crew move slowly


def test_3d_walkways_are_wide_with_clean_junction_pads_and_helixs_model_loads_when_present():
    from pathlib import Path

    from fastapi.testclient import TestClient
    from unittest.mock import MagicMock

    from openjarvis.server.app import create_app

    js = (Path(__file__).resolve().parents[2] / "src" / "openjarvis" / "server" / "crew" / "assets" / "station3d.js").read_text()
    assert "const OW = 30, PAD = 27;" in js and "band(g, a, 142, end, WB, C, lit, sd)" in js and "VC + 22" in js        # wide walkways, spokes start at the hub ring's edge, cut around the corner pads
    assert "loadHelix()" in js and "'/crew/helix.glb?v=3'" in js and "const HELIX_H = 168" in js and "holo.visible = false" in js
    c = TestClient(create_app(MagicMock(), "m", api_key="k", require_auth=True), raise_server_exceptions=False)
    three = c.get("/crew/three.min.js")
    assert three.status_code == 200 and b"GLTFLoader" in three.content                                        # the bundle carries the model loader
    assert c.get("/crew/helix.glb").status_code in (200, 404)                                                  # 404 until her model is added: the hologram stays


def test_3d_crew_use_their_robot_artwork_at_one_size_and_helix_is_her_real_model():
    from pathlib import Path

    from openjarvis.server.crew import routes

    crew = Path(__file__).resolve().parents[2] / "src" / "openjarvis" / "server" / "crew"
    js = (crew / "assets" / "station3d.js").read_text()
    assert "function makeAgent(role" in js and "cellTex(row, col)" in js and "const AGENT_H = 85" in js                # the sprite sheet, not capsules
    assert "CapsuleGeometry" not in js and js.count("makeAgent(") >= 4                                                   # walkway, Helix's ring and the trays all use it
    assert "g.scale.setScalar(1.5)" not in js                                                                            # no second size for the ring crew
    glb = (crew / "assets" / "helix.glb").read_bytes()
    assert glb[:4] == b"glTF" and len(glb) < 4_000_000                                                                   # her model, slimmed from 26 MB so a phone can load it
    assert any(getattr(r, "path", "") == "/crew/helix.glb" for r in routes.router.routes)


def test_3d_files_are_cache_busted_and_the_ring_thruster_touches_the_keel():
    from pathlib import Path

    from openjarvis.server.crew.station import STATION_PAGE

    js = (Path(__file__).resolve().parents[2] / "src" / "openjarvis" / "server" / "crew" / "assets" / "station3d.js").read_text()
    assert "/crew/three.min.js?v=3" in STATION_PAGE and "/crew/station3d.js?v=" in STATION_PAGE          # a changed bundle must not be served from a day-old cache
    assert "T.position.y = -272" in js and "'/crew/helix.glb?v=3'" in js


def test_helix_stands_in_the_middle_with_no_projector_rings_and_plays_her_walk():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / "src" / "openjarvis" / "server" / "crew" / "assets" / "station3d.js").read_text()
    assert "helix.position.set(0, FLOOR_Y + 14, 0)" in js and "const HELIX_H = 168" in js and "walking_woman" in js
    assert "beamM" not in js and "hubGlow" not in js and "[[60, FLOOR_Y + 26]" not in js                  # the projector beam, its rings and its glow are gone
    glb = (Path(__file__).resolve().parents[2] / "src" / "openjarvis" / "server" / "crew" / "assets" / "helix.glb").read_bytes()
    assert glb[:4] == b"glTF" and len(glb) < 4_000_000 and b"Walking_Woman" in glb                         # the animated model, textures slimmed


def test_helix_loads_and_there_are_no_travelling_balls_on_the_walkways():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / "src" / "openjarvis" / "server" / "crew" / "assets" / "station3d.js").read_text()
    assert js.count("loadHelix();") == 1                                                                   # the call that starts loading her model must stay in buildStructure
    assert "buildDots" not in js and "moveDots" not in js and "dotPaths" not in js                          # the lines surge; there are no balls


def test_walkway_lines_carry_a_colour_surge_and_the_crew_are_two_and_a_half_times_bigger():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / "src" / "openjarvis" / "server" / "crew" / "assets" / "station3d.js").read_text()
    assert "function surgeMap(len, dir, fixed)" in js and "function moveSurges(t)" in js and "map: surgeMap(" in js          # a bright band scrolls along every lit line
    assert "const AGENT_H = 85" in js and "const HELIX_H = 168" in js


def test_the_3d_take_off_sequence_is_wired_to_missions_clicks_and_the_chat():
    from pathlib import Path

    from openjarvis.server.crew.station import STATION_PAGE

    js = (Path(__file__).resolve().parents[2] / "src" / "openjarvis" / "server" / "crew" / "assets" / "station3d.js").read_text()
    for part in ("function startLaunch()", "function stepLaunch()", "boostPivots.forEach", "rootG.rotation.y = -6.283185 * 3", "rootG.position.set(_away.x * 7800 * out", "/backflip/i", "launch() { return startLaunch(); }"):
        assert part in js
    assert "Station3D.launch(); return; }" in STATION_PAGE                    # a mission starting or finishing, or a click on Helix, launches the 3D station
    assert "Prepare for take off!" in STATION_PAGE and "onHelix:" in STATION_PAGE
