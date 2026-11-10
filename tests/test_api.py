"""The HTTP surface: shapes, refusals, headers and the static files."""
from __future__ import annotations

import math
import re
from pathlib import Path

from conftest import NOW
from server.hub import milliseconds
from server.main import KEEP_A_DAY, REVALIDATE, cache_policy

ROOT = Path(__file__).resolve().parent.parent


def test_health_and_track(client):
    health = client.get("/api/health").json()
    assert health["ok"] is True
    assert health["tle"]["stale"] is False

    track = client.get("/api/track").json()
    assert set(track) == {"start", "step", "points", "sun", "period_minutes", "tle"}
    assert track["step"] == 10
    assert track["start"] == milliseconds(NOW) - 100 * 60 * 1000
    assert len(track["points"]) == 1201
    assert len(track["points"][0]) == 4
    assert len(track["sun"]) == 3
    assert 90 < track["period_minutes"] < 95


def test_passes_with_default_settings(client):
    response = client.get("/api/passes", params={"latitude": 40.0, "longitude": -75.0})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"observer", "now", "passes", "unseen", "settings", "sky"}
    assert body["observer"] == {"latitude": 40.0, "longitude": -75.0}
    assert body["settings"]["qualities"] == ["good", "excellent"]
    assert len(body["passes"]) == 7
    for item in body["passes"]:
        assert {"start", "peak", "end", "quality", "magnitude", "matches", "reason", "sky", "alert", "path"} <= set(item)
        assert item["matches"] == (item["quality"] in ("good", "excellent") and item["magnitude"] <= -2.0 and item["period"] == "evening")
    assert isinstance(body["unseen"], list)


def test_passes_refuse_bad_places_and_settings(client):
    assert client.get("/api/passes", params={"latitude": 91, "longitude": 0}).status_code == 422
    assert client.get("/api/passes", params={"latitude": 0}).status_code == 422
    response = client.get(
        "/api/passes", params={"latitude": 40, "longitude": -75, "evening": "false", "morning": "false"}
    )
    assert response.status_code == 422
    assert response.json() == {
        "error": "bad_settings",
        "message": "Choose evening passes, morning passes, or both",
    }
    response = client.get("/api/passes", params={"latitude": 40, "longitude": -75, "qualities": "great"})
    assert response.status_code == 422
    assert response.json()["error"] == "bad_settings"


def test_places(client):
    response = client.get("/api/places", params={"q": "phila"})
    assert response.status_code == 200
    assert response.json()["places"][0]["timezone"] == "America/New_York"
    assert client.get("/api/places", params={"q": "p"}).status_code == 422


def test_too_many_requests_are_refused(client):
    client.app.state.limiter._burst = 3.0
    client.app.state.limiter._buckets.clear()
    codes = [client.get("/api/places", params={"q": "phila"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_visitor_key_honours_the_tunnel_header_only_when_trusted(client):
    from server.main import visitor_key

    class FakeRequest:
        def __init__(self, trust):
            self.headers = {"cf-connecting-ip": "203.0.113.9", "x-forwarded-for": "198.51.100.2, 10.0.0.1"}
            self.client = type("Client", (), {"host": "172.18.0.5"})()
            self.app = type("App", (), {"state": type("State", (), {"trust_proxy": trust})()})()

    assert visitor_key(FakeRequest(False)) == "172.18.0.5"
    assert visitor_key(FakeRequest(True)) == "203.0.113.9"


def test_page_and_vendor_assets_are_served_with_headers(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "<title>ISS Track</title>" in page.text
    assert 'src="/app.js"' in page.text
    assert page.headers["content-security-policy"].startswith("default-src 'self'")
    assert page.headers["x-content-type-options"] == "nosniff"

    shared = client.get("/vendor/iss-track-shared.js")
    assert shared.status_code == 200
    assert "export function describeUnseen" in shared.text
    assert client.get("/vendor/images/mapday.jpg").status_code == 200
    assert client.get("/map.js").status_code == 200
    assert client.get("/nothing-here").status_code == 404

    guide = client.get("/guide/")
    assert guide.status_code == 200
    assert "<title>ISS Track guide</title>" in guide.text
    assert 'href="#why"' in guide.text
    assert client.get("/guide", follow_redirects=False).status_code in (301, 307, 308)
    assert client.get("/guide.css").status_code == 200


def test_the_page_and_its_code_are_checked_on_every_visit(client):
    # A release changes these files and never their names, so a browser or a
    # proxy that kept one for hours would run the old page.
    for path in ("/", "/guide/", "/app.js", "/map.js", "/site.css", "/guide.css",
                 "/vendor/iss-track-shared.js", "/vendor/astronomy.browser.js"):
        reply = client.get(path)
        assert reply.status_code == 200, path
        assert reply.headers["cache-control"] == REVALIDATE, path
        assert reply.headers["etag"], path


def test_an_unchanged_script_costs_one_304(client):
    first = client.get("/app.js")
    again = client.get("/app.js", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["cache-control"] == REVALIDATE


def test_pictures_and_fonts_are_kept_for_a_day(client):
    for path in ("/vendor/images/mapday.jpg", "/vendor/images/iss_tiny.png",
                 "/vendor/fonts/RobotoCondensed-Regular.ttf"):
        reply = client.get(path)
        assert reply.status_code == 200, path
        assert reply.headers["cache-control"] == KEEP_A_DAY, path


def test_a_missing_file_is_never_kept(client):
    for path in ("/vendor/images/nothing.png", "/nothing.js", "/nothing-here"):
        reply = client.get(path)
        assert reply.status_code == 404, path
        assert reply.headers["cache-control"] == REVALIDATE, path


def test_the_api_and_the_feed_keep_their_own_caching(client):
    assert "cache-control" not in client.get("/api/health").headers
    assert "cache-control" not in client.get("/api/track").headers
    feed = client.get("/calendar.ics", params={"latitude": 40, "longitude": -75})
    assert feed.headers["cache-control"] == "public, max-age=1800"
    saved = client.get("/calendar.ics", params={"latitude": 40, "longitude": -75, "download": 1})
    assert saved.headers["cache-control"] == "no-store"


def test_cache_policy_by_path_and_status():
    assert cache_policy("/", 200) == REVALIDATE
    assert cache_policy("/guide", 307) == REVALIDATE
    assert cache_policy("/APP.JS", 200) == REVALIDATE
    assert cache_policy("/vendor/images/sun_128.png", 200) == KEEP_A_DAY
    assert cache_policy("/vendor/images/sun_128.png", 304) == KEEP_A_DAY
    assert cache_policy("/vendor/images/sun_128.png", 404) == REVALIDATE
    assert cache_policy("/api/passes", 200) is None
    assert cache_policy("/calendar.ics", 200) is None
    assert cache_policy("/notes.txt", 200) is None


def test_favicon_is_the_station_icon(client):
    icon = client.get("/favicon.ico")
    assert icon.status_code == 200
    assert icon.headers["content-type"] == "image/png"
    assert icon.headers["cache-control"] == KEEP_A_DAY
    assert icon.content == client.get("/vendor/images/iss_tiny.png").content
    assert icon.content.startswith(b"\x89PNG")

    head = client.head("/favicon.ico")
    assert head.status_code == 200
    assert head.content == b""


def test_the_security_policy_names_every_outside_address(client):
    # The page may run code from, and talk to, the site itself and the two
    # Cloudflare Web Analytics addresses; the video frame is YouTube's. A new
    # outside address has to be added here on purpose.
    policy = dict(
        part.strip().split(" ", 1)
        for part in client.get("/").headers["content-security-policy"].split(";")
    )
    assert policy["default-src"] == "'self'"
    assert policy["script-src"] == "'self' https://static.cloudflareinsights.com"
    assert policy["connect-src"] == "'self' https://cloudflareinsights.com"
    assert policy["style-src"] == "'self' 'unsafe-inline'"
    assert policy["img-src"] == "'self' data:"
    assert policy["font-src"] == "'self'"
    assert policy["frame-src"] == "https://www.youtube.com"
    assert policy["base-uri"] == "'none'"
    assert policy["frame-ancestors"] == "'self'"
    assert len(policy) == 9
    # No inline or evaluated script, analytics or not.
    assert "unsafe" not in policy["script-src"]

    # The same policy rides on every reply, the API's included.
    assert client.get("/api/health").headers["content-security-policy"] == client.get("/").headers["content-security-policy"]


def test_the_privacy_page_is_served_and_says_what_is_true(client):
    page = client.get("/privacy/")
    assert page.status_code == 200
    assert page.headers["cache-control"] == REVALIDATE
    assert "<title>ISS Track privacy</title>" in page.text
    for chapter in ("short", "location", "browser", "reminders", "visits", "services", "server", "questions"):
        assert f'<h2 id="{chapter}">' in page.text, chapter
    # The claims the rest of the code has to keep true.
    assert "only to within about half a mile (0.8 km)" in page.text
    assert '<h3 id="precision">' in page.text
    assert "It keeps a log of requests" in page.text
    assert "The log does not record your internet address." in page.text
    assert "Nothing is loaded from YouTube until you open the video." in page.text
    assert "Cloudflare says this uses no cookies" in page.text
    # Without the slash it leads to the page.
    assert client.get("/privacy", follow_redirects=False).status_code in (301, 307, 308)
    assert client.get("/privacy").status_code == 200


def test_the_code_keeps_the_privacy_pages_promises():
    # "It keeps a log of requests": the container runs with one. Turning it
    # off, or logging more than the page says, means rewriting the page.
    assert "--no-access-log" not in (ROOT / "Dockerfile").read_text(encoding="utf-8").split("CMD")[-1]
    # "to within about half a mile": two decimal places, rounded in the browser
    # before anything is sent. Half the diagonal of a 0.01 degree square at the
    # equator, where it is largest, is the farthest the true spot can be.
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "latitude: Number(latitude.toFixed(2))," in app
    assert "longitude: Number(longitude.toFixed(2))," in app
    farthest_km = math.hypot(0.005 * 111.32, 0.005 * 111.32)
    assert 0.75 < farthest_km < 0.80
    assert farthest_km * 0.6214 < 0.5
    # "Nothing is loaded from YouTube until you open the video": the frame
    # has no address of its own until the page's code gives it one.
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'data-src="https://www.youtube.com/embed/' in index
    assert '\n      src="https://www.youtube.com' not in index
    assert "modalIframe.dataset.src" in (ROOT / "web" / "map.js").read_text(encoding="utf-8")


def test_the_footer_credits_the_author_first_and_esa_last(client):
    page = client.get("/").text
    foot = page[page.index('<footer class="foot">'):page.index("</footer>")]
    order = [foot.index(name) for name in (
        "Matthew Baker", ">sgp4<", ">Celestrak<", ">astronomy.browser.js<", ">Open-Meteo<", ">ESA ISS Tracker<",
    )]
    assert order == sorted(order)
    assert 'href="https://github.com/mattbratt"' in foot
    assert '<a href="/privacy">Privacy</a>' in foot
    # The ESA credit closes the credits line.
    assert '>ESA ISS Tracker</a></p>' in foot

    credits = client.get("/guide/").text
    credits = credits[credits.index('<h2 id="credits">'):]
    assert credits.index("Matthew Baker") < credits.index("sgp4") < credits.index("ESA ISS Tracker")
    assert credits.index("ESA ISS Tracker") > credits.index("Open-Meteo")


def test_what_is_kept_is_said_on_the_privacy_page_only(client):
    # One place to keep true. The pages link to it instead of restating it.
    for path in ("/", "/guide/"):
        text = client.get(path).text
        for phrase in ("stored on the server", "remembered in this browser", "remembered in your browser",
                       "stores nothing about you", "Cloudflare", "cookies"):
            assert phrase not in text, (path, phrase)
        assert 'href="/privacy' in text, path
    assert "nothing is stored on the server" not in client.get("/app.js").text


def test_us_spelling_on_the_pages_and_in_the_code():
    british = re.compile(
        r"\b(?:kilo|centi|milli)?metres?\b|\bgrey\b|\bneighbours?\b|\btravell(?:ing|ed|ers?)\b|\btowards\b"
        r"|\bcolours?\b|\bcentred?\b|\bfavour\w*|\bprogramme\b|\blicence\b|\bcatalogue\b|\banalyse\w*",
        re.IGNORECASE,
    )
    files = [ROOT / "README.md", ROOT / "Dockerfile", ROOT / "docker-compose.yml"]
    for folder, patterns in (("web", ("*.html", "*.js", "*.css")), ("server", ("*.py",))):
        for pattern in patterns:
            files += sorted((ROOT / folder).rglob(pattern))
    assert len(files) > 12
    found = []
    for file in files:
        for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            for word in british.findall(line):
                found.append(f"{file.relative_to(ROOT)}:{number} {word}")
    assert found == []
