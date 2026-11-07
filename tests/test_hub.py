"""The hub's own decisions: settings, sky, the element store, the limiter.

The engine's arithmetic is tested in the integration repository
(vendor/iss_track/tests); nothing here re-checks a position or a pass.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from conftest import LINE1, NOW, OBSERVER, FakeInternet, fixed_clock, stored_tle
from server import hub as hub_module
from server.hub import Hub, TleStore, describe, parse_settings, sky_for_passes
from server.limits import RequestLimiter
from server.vendor import engine


# ----- settings ------------------------------------------------------------

def test_settings_default_to_the_integrations_defaults():
    settings = parse_settings(None, None, None, None, None)
    assert settings.as_dict() == {
        "qualities": ["good", "excellent"],
        "faintest_magnitude": -2.0,
        "evening": True,
        "morning": False,
        "sky_limit": "partly_cloudy",
    }


def test_settings_read_every_field():
    settings = parse_settings("poor,fair", -3.5, False, True, "clear")
    assert settings.criteria.qualities == frozenset({"poor", "fair"})
    assert settings.criteria.faintest_magnitude == -3.5
    assert (settings.criteria.evening, settings.criteria.morning) == (False, True)
    assert settings.sky_limit == "clear"


@pytest.mark.parametrize(
    "args, words",
    [
        (("great", None, None, None, None), "Unknown pass quality"),
        ((" , ", None, None, None, None), "at least one pass quality"),
        ((None, 2.0, None, None, None), "Faintest magnitude"),
        ((None, None, False, False, None), "evening passes, morning passes, or both"),
        ((None, None, None, None, "foggy"), "Unknown sky limit"),
    ],
)
def test_settings_refuse_bad_values_in_words(args, words):
    with pytest.raises(ValueError, match=words):
        parse_settings(*args)


# ----- sky -----------------------------------------------------------------

def test_sky_uses_the_latest_row_within_reach_before_the_peak(pinned_passes):
    item = pinned_passes[0]
    forecast = [
        (item.peak - timedelta(minutes=100), engine.SKY_CLOUDY),
        (item.peak - timedelta(minutes=50), engine.SKY_CLEAR),
        (item.peak + timedelta(minutes=5), engine.SKY_CLOUDY),
    ]
    assert sky_for_passes(forecast, [item])[item.start] == engine.SKY_CLEAR


def test_sky_is_unknown_when_no_row_is_close_enough(pinned_passes):
    item = pinned_passes[0]
    forecast = [(item.peak - timedelta(minutes=91), engine.SKY_CLEAR)]
    assert sky_for_passes(forecast, [item])[item.start] == engine.SKY_UNKNOWN
    assert sky_for_passes([], [item])[item.start] == engine.SKY_UNKNOWN


def test_describe_words_a_pass_as_the_integration_does(pinned_passes):
    settings = parse_settings("fair,good,excellent", -2.0, True, True, "clear")
    for item in pinned_passes:
        data = describe(item, engine.SKY_PARTLY_CLOUDY, settings)
        assert data["matches"] == (data["reason"] is None)
        assert data["matches"] == settings.criteria.matches(item)
        # Partly cloudy under a "clear only" limit: matching passes are not alerts.
        assert data["alert"] is False
        assert data["sky"] == engine.SKY_PARTLY_CLOUDY
        assert data["path"]
    unknown = describe(pinned_passes[0], engine.SKY_UNKNOWN, settings)
    assert unknown["alert"] == unknown["matches"]


# ----- the element store ---------------------------------------------------

def test_tle_store_round_trips(tmp_path):
    store = TleStore(tmp_path / "data")
    orbit = engine.Orbit(LINE1, "2 25544  51.6329 326.3987 0007186 346.7494  13.3304 15.49173979593483")
    store.save(orbit, NOW)
    loaded = store.load()
    assert loaded is not None
    assert loaded[0].line1 == orbit.line1
    assert loaded[1] == NOW


def test_tle_store_treats_a_corrupt_file_as_absent(tmp_path):
    store = TleStore(tmp_path)
    store.path.write_text("{not json", encoding="utf-8")
    assert store.load() is None
    store.path.write_text('{"line1": "1 x", "line2": "2 x", "fetched_at": "2026-11-05T00:00:00+00:00"}', encoding="utf-8")
    assert store.load() is None
    assert TleStore(tmp_path / "missing").load() is None


# ----- the hub ---------------------------------------------------------------

def run(coro):
    return asyncio.run(coro)


def test_hub_keeps_the_last_good_elements_when_celestrak_is_down(tmp_path):
    stored_tle(tmp_path / "tle.json", NOW - timedelta(days=4))
    internet = FakeInternet(celestrak_ok=False)

    async def go():
        async with internet.client() as http:
            hub = Hub(tmp_path, http, clock=fixed_clock)
            await hub.start()
            try:
                assert hub.orbit is not None
                assert hub.tle_stale is True
                assert hub.tle_info()["stale"] is True
                track = await hub.track()
                assert len(track["points"]) == 1201
            finally:
                await hub.stop()

    run(go())
    assert internet.calls.count("celestrak.org") == 1


def test_hub_computes_passes_once_per_place_and_element_set(tmp_path):
    internet = FakeInternet(cover=80)
    settings = parse_settings("fair,good,excellent", -1.0, True, True, "partly_cloudy")

    async def go():
        async with internet.client() as http:
            hub = Hub(tmp_path, http, clock=fixed_clock)
            await hub.start()
            try:
                first = await hub.passes(OBSERVER, settings)
                nearby = await hub.passes(engine.Observer(40.004, -75.004, 0.0), settings)
                return first, nearby
            finally:
                await hub.stop()

    first, nearby = run(go())
    # The published predictions for this observer and window: seven passes.
    assert len(first["passes"]) == 7
    assert [p["start"] for p in first["passes"]] == [p["start"] for p in nearby["passes"]]
    assert all(p["sky"] == engine.SKY_CLOUDY for p in first["passes"])
    assert all(p["alert"] is False for p in first["passes"] if p["matches"])
    assert first["sky"] == {"source": "open-meteo", "available": True}
    assert first["settings"]["faintest_magnitude"] == -1.0
    assert internet.calls.count("api.open-meteo.com") == 1
    assert (tmp_path / "tle.json").is_file()


def test_hub_places_are_cleaned_and_cached(tmp_path):
    internet = FakeInternet()

    async def go():
        async with internet.client() as http:
            hub = Hub(tmp_path, http, clock=fixed_clock)
            first = await hub.places("Phila")
            again = await hub.places("  phila ")
            return first, again

    first, again = run(go())
    assert first == again
    assert first == [
        {
            "name": "Philadelphia",
            "region": "Pennsylvania",
            "country": "United States",
            "latitude": 39.9523,
            "longitude": -75.1638,
            "timezone": "America/New_York",
        }
    ]
    assert internet.calls.count("geocoding-api.open-meteo.com") == 1


def test_hub_policy_comes_from_the_integration():
    assert hub_module.TLE_REFRESH == timedelta(hours=2)
    assert hub_module.PASS_HORIZON == timedelta(days=10)
    assert hub_module.TLE_URL.startswith("https://celestrak.org/")


# ----- the limiter -----------------------------------------------------------

def test_limiter_allows_a_burst_then_refills():
    limiter = RequestLimiter(rate=60.0, burst=3)
    assert [limiter.allow("a", now=0.0) for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("b", now=0.0) is True
    assert limiter.allow("a", now=0.5) is False
    assert limiter.allow("a", now=1.1) is True
