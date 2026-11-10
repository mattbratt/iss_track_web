"""The web service: a few JSON endpoints, and the page that draws them.

The replies keep the shapes of the integration's WebSocket commands, so the
page's drawer and map overlay are the integration's own code with the
transport swapped.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .calendar import DEFAULT_REMIND, REMIND_CHOICES, content_disposition, render_calendar
from .hub import (
    MAGNITUDE_BRIGHTEST,
    MAGNITUDE_FAINTEST,
    Hub,
    NotReady,
    PlacesUnavailable,
    parse_settings,
)
from .limits import RequestLimiter
from .vendor import WEB, WWW, engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx logs every outbound request at INFO; the hub already logs the failures.
logging.getLogger("httpx").setLevel(logging.WARNING)

USER_AGENT = "iss-track-web (https://github.com/mattbratt/iss_track_web)"
# Cloudflare Web Analytics, for a site hosted behind Cloudflare with it turned
# on: Cloudflare adds one script to each page, loaded from the first host, and
# the script reports page views and load times to the second. They are the
# only two addresses outside the site that the page may run code from or talk
# to. Where Cloudflare is not in front, nothing ever asks for them.
ANALYTICS_SCRIPT = "https://static.cloudflareinsights.com"
ANALYTICS_REPORT = "https://cloudflareinsights.com"
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(self), fullscreen=(self)",
    "Content-Security-Policy": (
        f"default-src 'self'; script-src 'self' {ANALYTICS_SCRIPT}; style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' data:; font-src 'self'; connect-src 'self' {ANALYTICS_REPORT}; "
        "frame-src https://www.youtube.com; base-uri 'none'; frame-ancestors 'self'"
    ),
}

# How long browsers and the proxy in front may keep a file without asking.
# The page, its scripts and its styles change with every release and their
# names do not, so they are checked on every visit: "no-cache" stores the
# file but revalidates it, and an unchanged file costs one 304. Without this
# a proxy picks its own lifetime (Cloudflare: four hours), and a release
# reaches visitors late. Pictures and fonts change only with the engine.
REVALIDATE = "no-cache"
KEEP_A_DAY = "public, max-age=86400"
KEPT_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".ttf", ".woff", ".woff2")
CHECKED_SUFFIXES = (".html", ".js", ".mjs", ".css")
FAVICON = WWW / "images" / "iss_tiny.png"


def cache_policy(path: str, status: int) -> str | None:
    """The Cache-Control for a static reply, or None to leave it alone.

    The API and the calendar feed decide for themselves. A picture or font is
    kept for a day only when it was actually served: a 404 kept that long
    would hide the file after it appears.
    """
    if path.startswith("/api/") or path == "/calendar.ics":
        return None
    lower = path.lower()
    if lower.endswith(KEPT_SUFFIXES):
        return KEEP_A_DAY if status in (200, 206, 304) else REVALIDATE
    last = lower.rsplit("/", 1)[-1]
    if lower.endswith(CHECKED_SUFFIXES) or "." not in last:
        # A page ("/", "/guide/") or one of its scripts and styles.
        return REVALIDATE
    return None


def data_dir() -> Path:
    return Path(os.environ.get("ISS_TRACK_DATA_DIR", "data"))


def trust_proxy() -> bool:
    return os.environ.get("ISS_TRACK_TRUST_PROXY", "0") not in ("", "0", "false", "no")


def make_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def make_hub(http: httpx.AsyncClient) -> Hub:
    return Hub(data_dir(), http)


@asynccontextmanager
async def lifespan(app: FastAPI):
    http = make_http()
    hub = make_hub(http)
    app.state.hub = hub
    app.state.limiter = RequestLimiter()
    app.state.trust_proxy = trust_proxy()
    await hub.start()
    try:
        yield
    finally:
        await hub.stop()
        await http.aclose()


app = FastAPI(title="ISS Track (web)", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if "cache-control" not in response.headers:
        policy = cache_policy(request.url.path, response.status_code)
        if policy:
            response.headers["Cache-Control"] = policy
    return response


def visitor_key(request: Request) -> str:
    if request.app.state.trust_proxy:
        for header in ("cf-connecting-ip", "x-forwarded-for"):
            value = request.headers.get(header)
            if value:
                return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def limited(request: Request) -> None:
    if not request.app.state.limiter.allow(visitor_key(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Try again in a minute.")


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message})


@app.exception_handler(NotReady)
async def not_ready(_request: Request, err: NotReady):
    return error(503, "not_ready", str(err))


@app.exception_handler(engine.OrbitError)
async def orbit_error(_request: Request, err: engine.OrbitError):
    return error(500, "orbit_error", str(err))


@app.exception_handler(PlacesUnavailable)
async def places_unavailable(_request: Request, err: PlacesUnavailable):
    return error(503, "places_unavailable", str(err))


@app.get("/api/health")
async def health(request: Request):
    hub: Hub = request.app.state.hub
    return {"ok": hub.orbit is not None, "tle": hub.tle_info()}


@app.get("/api/track")
async def track(request: Request):
    hub: Hub = request.app.state.hub
    return await hub.track()


@app.get("/api/passes")
async def passes(
    request: Request,
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    qualities: str | None = Query(default=None, max_length=60),
    faintest_magnitude: float | None = Query(default=None, ge=MAGNITUDE_BRIGHTEST, le=MAGNITUDE_FAINTEST),
    evening: bool | None = None,
    morning: bool | None = None,
    sky_limit: str | None = Query(default=None, max_length=20),
):
    limited(request)
    try:
        settings = parse_settings(qualities, faintest_magnitude, evening, morning, sky_limit)
    except ValueError as err:
        return error(422, "bad_settings", str(err))
    hub: Hub = request.app.state.hub
    observer = engine.Observer(latitude, longitude, 0.0)
    return await hub.passes(observer, settings)


def site_url(request: Request) -> str:
    """The address visitors use for this site, for the calendar's links.
    Behind the tunnel the service sees plain HTTP, so trust_proxy says https."""
    host = request.headers.get("host") or request.url.netloc
    scheme = "https" if request.app.state.trust_proxy else request.url.scheme
    return f"{scheme}://{host}/"


@app.api_route("/calendar.ics", methods=["GET", "HEAD"])
async def calendar(
    request: Request,
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    name: str | None = Query(default=None, max_length=80),
    qualities: str | None = Query(default=None, max_length=60),
    faintest_magnitude: float | None = Query(default=None, ge=MAGNITUDE_BRIGHTEST, le=MAGNITUDE_FAINTEST),
    evening: bool | None = None,
    morning: bool | None = None,
    sky_limit: str | None = Query(default=None, max_length=20),
    remind: int = DEFAULT_REMIND,
    download: bool = False,
):
    """Matching passes as a calendar feed. The place and settings are in the address.
    With download=1 the reply is an attachment named after the place, so a browser
    saves it and the person opens it in their calendar; without it, a subscription
    feed. HEAD is answered too: some calendar clients probe before they fetch."""
    limited(request)
    if remind not in REMIND_CHOICES:
        return error(422, "bad_settings", "Remind me 5, 10, 15 or 30 minutes before a pass")
    try:
        settings = parse_settings(qualities, faintest_magnitude, evening, morning, sky_limit)
    except ValueError as err:
        return error(422, "bad_settings", str(err))
    hub: Hub = request.app.state.hub
    result = await hub.passes(engine.Observer(latitude, longitude, 0.0), settings)
    place = " ".join((name or "").split())
    text = render_calendar(
        result["passes"],
        place=place,
        latitude=latitude,
        longitude=longitude,
        remind=remind,
        now=datetime.fromtimestamp(result["now"] / 1000, timezone.utc),
        site=site_url(request),
    )
    return Response(
        content=text,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": content_disposition(place, download=download),
            "Cache-Control": "no-store" if download else "public, max-age=1800",
        },
    )


@app.get("/api/places")
async def places(request: Request, q: str = Query(min_length=2, max_length=80)):
    limited(request)
    hub: Hub = request.app.state.hub
    return {"places": await hub.places(q)}


@app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
async def favicon():
    """The station icon. The page names its icon itself, but crawlers and some
    browsers ask for this address whatever the page says, and used to get 404."""
    return FileResponse(FAVICON, media_type="image/png")


# The integration's own frontend assets: the shared module, fonts, images
# and the Moon library, served straight from the submodule.
app.mount("/vendor", StaticFiles(directory=WWW), name="vendor")
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
