"""The hub: orbital elements, passes, sky and places for any observer.

All arithmetic lives in the integration's engine. This module decides when
to ask the engine, caches the answers, and talks to Celestrak and Open-Meteo.
Its timings and limits are the integration's own, read from its const module,
so the website and the integration behave the same way.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from .vendor import engine, policy

_LOGGER = logging.getLogger(__name__)

TLE_URL = policy.TLE_URL
TLE_REFRESH: timedelta = policy.TLE_REFRESH
TLE_STALE_AFTER: timedelta = policy.TLE_STALE_AFTER
TLE_RETRY = timedelta(minutes=10)
PASS_HORIZON: timedelta = policy.PASS_HORIZON
PASS_LOOKBACK: timedelta = policy.PASS_LOOKBACK
UNSEEN_LOOKBACK: timedelta = policy.UNSEEN_LOOKBACK
UNSEEN_LOOKAHEAD: timedelta = policy.UNSEEN_LOOKAHEAD
TRACK_SPAN: timedelta = policy.TRACK_SPAN
TRACK_STEP_SECONDS: int = policy.TRACK_STEP_SECONDS
SKY_REFRESH: timedelta = policy.SKY_REFRESH
MAGNITUDE_BRIGHTEST: float = policy.MAGNITUDE_BRIGHTEST
MAGNITUDE_FAINTEST: float = policy.MAGNITUDE_FAINTEST

DEFAULT_QUALITIES = tuple(policy.DEFAULT_QUALITIES)
DEFAULT_FAINTEST_MAGNITUDE = policy.DEFAULT_FAINTEST_MAGNITUDE
DEFAULT_EVENING = policy.DEFAULT_EVENING
DEFAULT_MORNING = policy.DEFAULT_MORNING
DEFAULT_SKY_LIMIT = policy.DEFAULT_SKY_LIMIT

# A forecast row counts for a pass when it is this close before the peak,
# as the integration's coordinator applies it.
FORECAST_REACH = timedelta(minutes=90)
FORECAST_DAYS = 10

# Passes for one place are kept this long, for this many places. The
# integration keeps eight for its browsers; a website meets more visitors.
PASS_CACHE_TTL = timedelta(hours=1)
PASS_CACHE_SIZE = 256
PLACE_CACHE_TTL = timedelta(hours=1)
PLACE_CACHE_SIZE = 512
CONCURRENT_COMPUTATIONS = 2

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
SKY_SOURCE = "open-meteo"

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def milliseconds(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def observer_key(observer: engine.Observer) -> tuple[float, float]:
    """Two decimals, about a kilometer: the integration's own rounding."""
    return (round(observer.latitude, 2), round(observer.longitude, 2))


@dataclass(frozen=True, slots=True)
class Settings:
    """A visitor's pass settings, as sent with each request."""

    criteria: engine.Criteria
    sky_limit: str

    def as_dict(self) -> dict[str, Any]:
        c = self.criteria
        return {
            "qualities": [q for q in engine.QUALITIES if q in c.qualities],
            "faintest_magnitude": c.faintest_magnitude,
            "evening": c.evening,
            "morning": c.morning,
            "sky_limit": self.sky_limit,
        }


def parse_settings(
    qualities: str | None,
    faintest_magnitude: float | None,
    evening: bool | None,
    morning: bool | None,
    sky_limit: str | None,
) -> Settings:
    """Settings from query parameters. Anything missing takes the default;
    anything wrong raises ValueError with words a person can read."""
    if qualities is None or qualities == "":
        chosen = set(DEFAULT_QUALITIES)
    else:
        chosen = {part.strip() for part in qualities.split(",") if part.strip()}
        unknown = chosen - set(engine.QUALITIES)
        if unknown:
            raise ValueError(f"Unknown pass quality: {', '.join(sorted(unknown))}")
        if not chosen:
            raise ValueError("Choose at least one pass quality")
    magnitude = DEFAULT_FAINTEST_MAGNITUDE if faintest_magnitude is None else float(faintest_magnitude)
    if not MAGNITUDE_BRIGHTEST <= magnitude <= MAGNITUDE_FAINTEST:
        raise ValueError(
            f"Faintest magnitude must be between {MAGNITUDE_BRIGHTEST} and {MAGNITUDE_FAINTEST}"
        )
    evening_on = DEFAULT_EVENING if evening is None else bool(evening)
    morning_on = DEFAULT_MORNING if morning is None else bool(morning)
    if not evening_on and not morning_on:
        raise ValueError("Choose evening passes, morning passes, or both")
    limit = DEFAULT_SKY_LIMIT if sky_limit is None or sky_limit == "" else sky_limit
    if limit not in engine.SKY_LIMITS:
        raise ValueError(f"Unknown sky limit: {limit}")
    return Settings(
        criteria=engine.Criteria(
            qualities=frozenset(chosen),
            faintest_magnitude=magnitude,
            evening=evening_on,
            morning=morning_on,
        ),
        sky_limit=limit,
    )


def sky_for_passes(
    forecast: list[tuple[datetime, str]], passes: list[engine.Pass]
) -> dict[datetime, str]:
    """The forecast sky for each pass: the latest hourly row within
    FORECAST_REACH before the pass's peak, else unknown. The integration's
    coordinator applies the same rule to a weather entity's forecast."""
    result: dict[datetime, str] = {}
    for item in passes:
        sky = engine.SKY_UNKNOWN
        for when, row_sky in forecast:
            if when > item.peak:
                break
            if item.peak - when <= FORECAST_REACH:
                sky = row_sky
        result[item.start] = sky
    return result


def describe(item: engine.Pass, sky: str, settings: Settings) -> dict[str, Any]:
    """A pass as the page receives it, worded as the integration words it."""
    data = item.as_dict(with_path=True)
    reason = settings.criteria.reason(item)
    matches = reason is None
    data["matches"] = matches
    data["reason"] = reason
    data["sky"] = sky
    data["alert"] = matches and engine.sky_allows(sky, settings.sky_limit)
    return data


class TleStore:
    """The last good element set, on disk, written atomically."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "tle.json"

    def load(self) -> tuple[engine.Orbit, datetime] | None:
        try:
            with self.path.open(encoding="utf-8") as handle:
                stored = json.load(handle)
            orbit = engine.Orbit(stored["line1"], stored["line2"])
            fetched_at = datetime.fromisoformat(stored["fetched_at"])
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, engine.OrbitError) as err:
            _LOGGER.warning("Stored orbital elements at %s are unusable (%s)", self.path, err)
            return None
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        return orbit, fetched_at

    def save(self, orbit: engine.Orbit, fetched_at: datetime) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        payload = {"line1": orbit.line1, "line2": orbit.line2, "fetched_at": fetched_at.isoformat()}
        try:
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.replace(temp, self.path)
        except OSError as err:
            _LOGGER.warning("Could not save orbital elements to %s: %s", self.path, err)


class Hub:
    """Fetch orbital elements, keep passes and sky current for any observer."""

    def __init__(
        self,
        data_dir: Path,
        http: httpx.AsyncClient,
        *,
        clock: Clock = utcnow,
    ) -> None:
        self._store = TleStore(data_dir)
        self._http = http
        self._clock = clock
        self.orbit: engine.Orbit | None = None
        self.tle_fetched_at: datetime | None = None
        self._refresh_task: asyncio.Task | None = None
        self._compute = asyncio.Semaphore(CONCURRENT_COMPUTATIONS)
        self._passes: OrderedDict[tuple, tuple[datetime, list[engine.Pass]]] = OrderedDict()
        self._in_flight: dict[tuple, asyncio.Future] = {}
        self._forecasts: OrderedDict[tuple, tuple[datetime, list[tuple[datetime, str]]]] = OrderedDict()
        self._places: OrderedDict[str, tuple[datetime, list[dict[str, Any]]]] = OrderedDict()

    # ----- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        """Load or fetch the elements, then keep them fresh in the background."""
        stored = self._store.load()
        if stored is not None:
            self.orbit, self.tle_fetched_at = stored
        if self.tle_due:
            await self.fetch_tle()
        self._refresh_task = asyncio.create_task(self._refresh_loop(), name="iss-track-tle")

    async def stop(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None

    @property
    def tle_due(self) -> bool:
        return (
            self.orbit is None
            or self.tle_fetched_at is None
            or self._clock() - self.tle_fetched_at >= TLE_REFRESH
        )

    @property
    def tle_stale(self) -> bool:
        return (
            self.tle_fetched_at is None
            or self._clock() - self.tle_fetched_at > TLE_STALE_AFTER
        )

    async def _refresh_loop(self) -> None:
        while True:
            if self.tle_fetched_at is None:
                wait = TLE_RETRY
            else:
                wait = self.tle_fetched_at + TLE_REFRESH - self._clock()
            await asyncio.sleep(max(60.0, wait.total_seconds()))
            if not await self.fetch_tle():
                await asyncio.sleep(TLE_RETRY.total_seconds())

    # ----- orbital elements -----------------------------------------------

    async def fetch_tle(self) -> bool:
        """Fetch fresh elements. On any failure the last good set stays."""
        try:
            response = await self._http.get(TLE_URL, timeout=20.0)
            response.raise_for_status()
            orbit = engine.Orbit(*engine.parse_tle(response.text))
        except (httpx.HTTPError, engine.OrbitError) as err:
            _LOGGER.warning("Could not refresh ISS orbital elements from Celestrak: %s", err)
            return False
        changed = self.orbit is None or orbit.line1 != self.orbit.line1
        self.orbit = orbit
        self.tle_fetched_at = self._clock()
        if changed:
            self._passes.clear()
        self._store.save(orbit, self.tle_fetched_at)
        return True

    def tle_info(self) -> dict[str, Any]:
        fetched = self.tle_fetched_at
        return {
            "fetched_at": milliseconds(fetched) if fetched else None,
            "next_refresh": milliseconds(fetched + TLE_REFRESH) if fetched else None,
            "epoch": self.orbit.epoch.isoformat() if self.orbit else None,
            "stale": self.tle_stale,
        }

    # ----- the ground track -----------------------------------------------

    async def track(self) -> dict[str, Any]:
        """The ground track around now, the Sun, and the state of the elements.
        The same shape as the integration's iss_track/track command."""
        orbit = self._require_orbit()
        now = self._clock().replace(microsecond=0)
        start = now - TRACK_SPAN
        end = now + TRACK_SPAN
        points = await asyncio.to_thread(
            engine.ground_track, orbit.copy(), start, end, TRACK_STEP_SECONDS
        )
        return {
            "start": milliseconds(start),
            "step": TRACK_STEP_SECONDS,
            "points": points,
            "sun": [
                [milliseconds(when), *(round(value, 3) for value in engine.sun_subpoint(when))]
                for when in (start, now, end)
            ],
            "period_minutes": round(orbit.period_minutes, 2),
            "tle": self.tle_info(),
        }

    def _require_orbit(self) -> engine.Orbit:
        if self.orbit is None:
            raise NotReady("No orbital elements yet: Celestrak could not be reached")
        return self.orbit

    # ----- passes ---------------------------------------------------------

    async def passes(self, observer: engine.Observer, settings: Settings) -> dict[str, Any]:
        """Visible passes, unseen overflights and the sky for one observer.
        The same shape as the integration's iss_track/passes command."""
        orbit = self._require_orbit()
        now = self._clock().replace(microsecond=0)
        found = await self._passes_cached(orbit, observer, now)
        forecast = await self._forecast_cached(observer, now)
        sky = sky_for_passes(forecast, found)
        unseen = await asyncio.to_thread(
            engine.find_overflights,
            orbit.copy(),
            observer,
            now - UNSEEN_LOOKBACK,
            now + UNSEEN_LOOKAHEAD,
        )
        return {
            "observer": {
                "latitude": round(observer.latitude, 4),
                "longitude": round(observer.longitude, 4),
            },
            "now": milliseconds(now),
            "passes": [
                describe(item, sky.get(item.start, engine.SKY_UNKNOWN), settings)
                for item in found
                if item.end >= now
            ],
            # Up but not visible, close to now. Never passes: the page only
            # uses these to explain why there is nothing to see.
            "unseen": [item.as_dict() for item in unseen if item.set >= now],
            "settings": settings.as_dict(),
            "sky": {"source": SKY_SOURCE, "available": bool(forecast)},
        }

    async def _passes_cached(
        self, orbit: engine.Orbit, observer: engine.Observer, now: datetime
    ) -> list[engine.Pass]:
        key = (*observer_key(observer), orbit.line1)
        cached = self._passes.get(key)
        if cached is not None and now - cached[0] < PASS_CACHE_TTL:
            self._passes.move_to_end(key)
            return cached[1]
        # One computation per place, however many tabs ask at once.
        waiting = self._in_flight.get(key)
        if waiting is not None:
            return await waiting
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._in_flight[key] = future
        try:
            async with self._compute:
                found = await asyncio.to_thread(
                    engine.find_passes,
                    orbit.copy(),
                    observer,
                    now - PASS_LOOKBACK,
                    now + PASS_HORIZON,
                )
        except BaseException as err:
            future.set_exception(err)
            raise
        else:
            future.set_result(found)
        finally:
            self._in_flight.pop(key, None)
        self._passes[key] = (now, found)
        self._passes.move_to_end(key)
        while len(self._passes) > PASS_CACHE_SIZE:
            self._passes.popitem(last=False)
        return found

    # ----- sky ------------------------------------------------------------

    async def _forecast_cached(
        self, observer: engine.Observer, now: datetime
    ) -> list[tuple[datetime, str]]:
        key = observer_key(observer)
        cached = self._forecasts.get(key)
        if cached is not None and now - cached[0] < SKY_REFRESH:
            self._forecasts.move_to_end(key)
            return cached[1]
        rows = await self._fetch_forecast(key[0], key[1])
        if rows is None:
            # Keep the last good forecast, if there is one, and ask again later.
            return cached[1] if cached is not None else []
        self._forecasts[key] = (now, rows)
        self._forecasts.move_to_end(key)
        while len(self._forecasts) > PASS_CACHE_SIZE:
            self._forecasts.popitem(last=False)
        return rows

    async def _fetch_forecast(self, latitude: float, longitude: float) -> list[tuple[datetime, str]] | None:
        """Hourly cloud cover from Open-Meteo, classified by the engine."""
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "cloud_cover",
            "forecast_days": FORECAST_DAYS,
            "timezone": "UTC",
        }
        try:
            response = await self._http.get(FORECAST_URL, params=params, timeout=15.0)
            response.raise_for_status()
            payload = response.json()
            times = payload["hourly"]["time"]
            covers = payload["hourly"]["cloud_cover"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as err:
            # No position in the log: the privacy page says the server keeps none.
            _LOGGER.warning("No sky forecast for a place: %s", err)
            return None
        rows: list[tuple[datetime, str]] = []
        for stamp, cover in zip(times, covers):
            try:
                when = datetime.fromisoformat(str(stamp))
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            rows.append((when, engine.classify_sky(cover, None)))
        rows.sort(key=lambda row: row[0])
        return rows

    # ----- places ---------------------------------------------------------

    async def places(self, query: str) -> list[dict[str, Any]]:
        """Towns and cities matching a typed name, from Open-Meteo's geocoder."""
        text = " ".join(query.split()).lower()
        now = self._clock()
        cached = self._places.get(text)
        if cached is not None and now - cached[0] < PLACE_CACHE_TTL:
            self._places.move_to_end(text)
            return cached[1]
        params = {"name": text, "count": 6, "language": "en", "format": "json"}
        try:
            response = await self._http.get(GEOCODING_URL, params=params, timeout=10.0)
            response.raise_for_status()
            results = response.json().get("results") or []
        except (httpx.HTTPError, ValueError, AttributeError) as err:
            raise PlacesUnavailable("Place search is not available right now") from err
        places = []
        for row in results:
            try:
                places.append(
                    {
                        "name": str(row["name"]),
                        "region": str(row.get("admin1") or ""),
                        "country": str(row.get("country") or ""),
                        "latitude": round(float(row["latitude"]), 4),
                        "longitude": round(float(row["longitude"]), 4),
                        "timezone": str(row.get("timezone") or ""),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        self._places[text] = (now, places)
        self._places.move_to_end(text)
        while len(self._places) > PLACE_CACHE_SIZE:
            self._places.popitem(last=False)
        return places


class NotReady(RuntimeError):
    """The hub has no orbital elements yet."""


class PlacesUnavailable(RuntimeError):
    """The geocoder could not be reached."""
