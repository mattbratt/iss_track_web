"""Fixtures: a fixed clock, a pinned element set, and fake Celestrak and
Open-Meteo answers, so no test touches the network or today's date.

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


class FakeInternet:
    """Answers Celestrak and Open-Meteo, and counts the calls."""

    def __init__(self, *, cover: int = 10, celestrak_ok: bool = True) -> None:
        self.cover = cover
        self.celestrak_ok = celestrak_ok
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.host)
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
def client(monkeypatch, internet: FakeInternet, data_dir: Path):
    """A test client for the app, wired to the fake internet and clock."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main_module, "make_http", internet.client)
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
