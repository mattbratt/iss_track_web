"""Fixtures: a fixed clock, a pinned element set, and fake Celestrak,
Open-Meteo and YouTube answers, so no test touches the network or today's date.

The element set and observer are the integration's own test fixtures, so the
passes the service returns here are the published ones its engine tests
check against.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import hub as hub_module  # noqa: E402
from server import main as main_module  # noqa: E402
from server import video as video_module  # noqa: E402
from server.vendor import engine  # noqa: E402

LINE1 = "1 25544U 98067A   26308.36953014  .00005228  00000+0  10236-3 0  9991"
LINE2 = "2 25544  51.6329 326.3987 0007186 346.7494  13.3304 15.49173979593483"
NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)
OBSERVER = engine.Observer(40.0, -75.0, 0.0)


def fixed_clock() -> datetime:
    return NOW


def forecast_payload(start: datetime, cover: int = 10) -> dict:
    hours = [start + timedelta(hours=i) for i in range(24 * 10)]
    return {
        "hourly": {
            "time": [h.strftime("%Y-%m-%dT%H:%M") for h in hours],
            "cloud_cover": [cover] * len(hours),
        }
    }


GEOCODING = {
    "results": [
        {
            "name": "Philadelphia",
            "admin1": "Pennsylvania",
            "country": "United States",
            "latitude": 39.95233,
            "longitude": -75.16379,
            "timezone": "America/New_York",
        },
        {"name": "Broken", "latitude": "not a number"},
    ]
}


HD_STREAM = ("ROboUHfPUrU", "Live High-Definition Views from the International Space Station (Official NASA Stream)")
PLAIN_STREAM = ("M3HKLzjvKPc", "Live Video from the International Space Station (Official NASA Stream)")
SPACEWALK = ("25JEQqgv5I4", "U.S. Spacewalk 101")
YOUTUBE_KEY = "test-key-not-a-real-one"


def youtube_search(streams: list[tuple[str, str]]) -> dict:
    return {
        "items": [
            {
                "id": {"kind": "youtube#video", "videoId": video_id},
                "snippet": {"title": title, "liveBroadcastContent": "live"},
            }
            for video_id, title in streams
        ]
    }


class FakeInternet:
    """Answers Celestrak, Open-Meteo and YouTube's API, and counts the calls."""

    def __init__(self, *, cover: int = 10, celestrak_ok: bool = True) -> None:
        self.cover = cover
        self.celestrak_ok = celestrak_ok
        self.calls: list[str] = []
        # YouTube: what a search for NASA's live streams finds, which ids
        # answer "live" when asked about directly, and the reply's status.
        self.youtube_live: list[tuple[str, str]] = [SPACEWALK, PLAIN_STREAM, HD_STREAM]
        self.youtube_still_live: set[str] = set()
        self.youtube_status = 200
        self.youtube_body: str | None = None
        self.youtube_calls: list[str] = []

    def youtube(self, request: httpx.Request) -> httpx.Response:
        kind = request.url.path.rsplit("/", 1)[-1]
        self.youtube_calls.append(kind)
        if request.url.params.get("key") != YOUTUBE_KEY:
            return httpx.Response(400, json={"error": {"message": "API key not valid"}})
        if self.youtube_status != 200:
            return httpx.Response(self.youtube_status, json={"error": {"message": "refused"}})
        if self.youtube_body is not None:
            return httpx.Response(200, text=self.youtube_body)
        if kind == "search":
            assert request.url.params["eventType"] == "live"
            assert request.url.params["type"] == "video"
            return httpx.Response(200, json=youtube_search(self.youtube_live))
        if kind == "videos":
            state = "live" if request.url.params["id"] in self.youtube_still_live else "none"
            return httpx.Response(200, json={"items": [{"snippet": {"liveBroadcastContent": state}}]})
        return httpx.Response(404, text="unknown method")

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.host)
        if request.url.host == "www.googleapis.com":
            return self.youtube(request)
        if request.url.host == "celestrak.org":
            if not self.celestrak_ok:
                return httpx.Response(503, text="down")
            return httpx.Response(200, text=f"{LINE1}\n{LINE2}\n")
        if request.url.host == "api.open-meteo.com":
            return httpx.Response(200, json=forecast_payload(NOW, self.cover))
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json=GEOCODING)
        return httpx.Response(404, text="unknown host")

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def internet() -> FakeInternet:
    return FakeInternet()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def youtube_key() -> str | None:
    """No key unless a test asks for one, whatever the machine's own
    environment holds: parametrize this fixture to run with a key."""
    return None


@pytest.fixture
def client(monkeypatch, internet: FakeInternet, data_dir: Path, youtube_key: str | None):
    """A test client for the app, wired to the fake internet and clock."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main_module, "make_http", internet.client)
    monkeypatch.setattr(
        main_module,
        "make_video",
        lambda http: video_module.LiveVideo(data_dir, http, api_key=youtube_key, clock=fixed_clock),
    )
    monkeypatch.setattr(
        main_module,
        "make_hub",
        lambda http: hub_module.Hub(data_dir, http, clock=fixed_clock),
    )
    with TestClient(main_module.app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def pinned_passes():
    orbit = engine.Orbit(LINE1, LINE2)
    return engine.find_passes(orbit, OBSERVER, NOW, NOW + timedelta(days=10))


def stored_tle(path: Path, fetched_at: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"line1": LINE1, "line2": LINE2, "fetched_at": fetched_at.isoformat()}),
        encoding="utf-8",
    )
