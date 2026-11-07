"""The HTTP surface: shapes, refusals, headers and the static files."""
from __future__ import annotations

from conftest import NOW
from server.hub import milliseconds


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
