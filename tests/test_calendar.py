"""The calendar feed: its text, its escaping and folding, and its route."""
from __future__ import annotations

from datetime import datetime, timezone

from server.calendar import content_disposition, escape, file_name, fold, render_calendar

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


def a_pass(**over):
    item = {
        "start": "2026-11-11T23:43:58+00:00",
        "peak": "2026-11-11T23:45:20+00:00",
        "end": "2026-11-11T23:46:34+00:00",
        "duration_seconds": 156,
        "peak_elevation": 49.6,
        "peak_direction": "NW",
        "start_direction": "WSW",
        "end_direction": "W",
        "magnitude": -2.9,
        "quality": "good",
        "starts_in_shadow": False,
        "ends_in_shadow": True,
        "sky": "cloudy",
        "matches": True,
    }
    item.update(over)
    return item


def test_feed_holds_matching_passes_only_with_an_alarm():
    text = render_calendar(
        [a_pass(), a_pass(start="2026-11-12T22:56:14+00:00", end="2026-11-12T23:01:21+00:00", matches=False)],
        place="Philadelphia, Pennsylvania",
        latitude=39.95,
        longitude=-75.16,
        remind=15,
        now=NOW,
        site="https://iss.example.com/",
    )
    assert text.count("BEGIN:VEVENT") == 1
    assert "DTSTART:20261111T234358Z" in text
    assert "DTEND:20261111T234634Z" in text
    assert "UID:iss-39.95--75.16-20261111T234358Z@iss-track-web" in text
    assert "SUMMARY:ISS pass: Good\\, 50°\\, magnitude −2.9" in text
    assert "TRIGGER:-PT15M" in text
    assert "X-WR-CALNAME:ISS passes\\, Philadelphia\\, Pennsylvania" in text
    assert "LOCATION:Philadelphia\\, Pennsylvania" in text
    assert "URL:https://iss.example.com/" in text
    assert "REFRESH-INTERVAL;VALUE=DURATION:PT6H" in text
    assert text.endswith("END:VCALENDAR\r\n")
    assert "\n" not in text.replace("\r\n", "")
    unfolded = text.replace("\r\n ", "")
    assert "fades into Earth's shadow in the W. Highest point 50° above the horizon" in unfolded
    assert "Sky forecast when this was fetched: cloudy." in unfolded


def test_escape_and_fold():
    assert escape("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"
    long = "DESCRIPTION:" + "é" * 100
    folded = fold(long)
    for piece in folded.split("\r\n"):
        assert len(piece.encode("utf-8")) <= 75
    assert folded.replace("\r\n ", "") == long


def test_calendar_route(client):
    response = client.get(
        "/calendar.ics",
        params={"latitude": 40, "longitude": -75, "name": "Test Town", "qualities": "fair,good,excellent", "remind": 5},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert response.headers["content-disposition"] == (
        'inline; filename="ISS passes - Test Town.ics"; filename*=UTF-8\'\'ISS%20passes%20-%20Test%20Town.ics'
    )
    assert response.headers["cache-control"] == "public, max-age=1800"
    text = response.text
    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert text.count("BEGIN:VEVENT") >= 1
    assert "TRIGGER:-PT5M" in text
    assert "LOCATION:Test Town" in text
    assert "URL:http://testserver/" in text

    assert client.get("/calendar.ics", params={"latitude": 40, "longitude": -75, "remind": 7}).status_code == 422
    assert client.get("/calendar.ics", params={"latitude": 40, "longitude": -75, "qualities": "huge"}).status_code == 422


def test_calendar_download_is_an_attachment_named_after_the_place(client):
    """download=1 is the desktop route: the browser saves the file and the
    person opens it in their calendar. Nothing else about the feed changes."""
    params = {"latitude": 40, "longitude": -75, "name": "Zürich", "remind": 15}
    feed = client.get("/calendar.ics", params=params)
    saved = client.get("/calendar.ics", params={**params, "download": 1})
    assert saved.status_code == 200
    assert saved.headers["content-type"].startswith("text/calendar")
    assert saved.headers["content-disposition"] == (
        'attachment; filename="ISS passes - Zrich.ics"; filename*=UTF-8\'\'ISS%20passes%20-%20Z%C3%BCrich.ics'
    )
    assert saved.headers["cache-control"] == "no-store"
    assert saved.text == feed.text


def test_calendar_answers_head(client):
    """Some calendar clients probe with HEAD before they fetch; a 404 there
    would read as 'no such feed'."""
    response = client.head("/calendar.ics", params={"latitude": 40, "longitude": -75})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert response.content == b""


def test_file_name_and_disposition():
    assert file_name("") == "ISS passes.ics"
    assert file_name('  São Paulo / "SP" ') == "ISS passes - São Paulo  SP.ics"
    assert content_disposition("", download=True) == (
        'attachment; filename="ISS passes.ics"; filename*=UTF-8\'\'ISS%20passes.ics'
    )
