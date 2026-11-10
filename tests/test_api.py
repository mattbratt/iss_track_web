"""The HTTP surface: shapes, refusals, headers and the static files."""
from __future__ import annotations

from conftest import NOW
from server.hub import milliseconds
from server.main import KEEP_A_DAY, REVALIDATE, cache_policy


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


def test_the_guide_says_who_counts_visits(client):
    guide = client.get("/guide/").text
    assert "Cloudflare, which counts visits" in guide
    assert "uses no cookies" in guide
    assert "Nothing is remembered anywhere else" not in guide
