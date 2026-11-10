"""The calendar feed: matching passes as iCalendar events, so a person's own
calendar does the reminding.

The feed holds every pass that matches the settings baked into its address,
whatever the sky forecast, as the integration's calendar entity does: the
forecast is written into the event, never used to drop it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import quote

REMIND_CHOICES = (5, 10, 15, 30)
DEFAULT_REMIND = 10
REFRESH = "PT6H"
PRODUCT = "-//ISS Track//ISS Track (web)//EN"

QUALITY_LABEL = {"poor": "Poor", "fair": "Fair", "good": "Good", "excellent": "Excellent"}
SKY_LABEL = {
    "clear": "clear sky",
    "partly_cloudy": "partly cloudy",
    "cloudy": "cloudy",
    "unknown": "no forecast yet",
}


def escape(text: str) -> str:
    """Escape a text value as RFC 5545 asks: backslash, semicolon, comma, newline."""
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def fold(line: str) -> str:
    """Fold a content line at 75 octets, never inside a multibyte character."""
    out: list[str] = []
    current = ""
    budget = 75
    for char in line:
        size = len(char.encode("utf-8"))
        if len(current.encode("utf-8")) + size > budget:
            out.append(current)
            current = " "
            budget = 75
        current += char
    out.append(current)
    return "\r\n".join(out)


def file_name(place: str) -> str:
    """The name a downloaded feed is saved under."""
    clean = "".join(ch for ch in place if ch not in '\\/:*?"<>|\r\n\t').strip()
    return f"ISS passes - {clean}.ics" if clean else "ISS passes.ics"


def content_disposition(place: str, *, download: bool) -> str:
    """The Content-Disposition header: inline for a subscription, an attachment
    with the place in its name for a download. Both spellings of the name go
    out, the plain ASCII one for old clients and the UTF-8 one (RFC 6266)."""
    name = file_name(place)
    ascii_name = name.encode("ascii", "ignore").decode("ascii").replace('"', "")
    kind = "attachment" if download else "inline"
    return f'{kind}; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name)}'


def stamp(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def format_magnitude(value: float) -> str:
    sign = "−" if value < -0.05 else "+" if value > 0.05 else ""
    return f"{sign}{abs(value):.1f}"


def format_duration(seconds: int) -> str:
    minutes, rest = divmod(max(0, int(seconds)), 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


def describe_path(item: dict[str, Any]) -> str:
    """One sentence on how the pass begins and ends, worded as the page words it."""
    start = (
        f"Appears out of Earth's shadow in the {item['start_direction']}"
        if item.get("starts_in_shadow")
        else f"Rises in the {item['start_direction']}"
    )
    end = (
        f"fades into Earth's shadow in the {item['end_direction']}"
        if item.get("ends_in_shadow")
        else f"sets in the {item['end_direction']}"
    )
    return f"{start}, {end}"


def event_lines(
    item: dict[str, Any], *, place: str, remind: int, now: datetime, site: str, key: str
) -> list[str]:
    start = datetime.fromisoformat(item["start"])
    end = datetime.fromisoformat(item["end"])
    quality = QUALITY_LABEL.get(item["quality"], item["quality"])
    peak = round(item["peak_elevation"])
    summary = f"ISS pass: {quality}, {peak}°, magnitude {format_magnitude(item['magnitude'])}"
    description = (
        f"Visible for {format_duration(item['duration_seconds'])}. {describe_path(item)}. "
        f"Highest point {peak}° above the horizon, toward the {item['peak_direction']}. "
        f"Sky forecast when this was fetched: {SKY_LABEL.get(item.get('sky', 'unknown'), 'no forecast yet')}."
    )
    if site:
        description += f"\n\nLive map and where to look: {site}"
    lines = [
        "BEGIN:VEVENT",
        f"UID:iss-{key}-{stamp(start)}@iss-track-web",
        f"DTSTAMP:{stamp(now)}",
        f"DTSTART:{stamp(start)}",
        f"DTEND:{stamp(end)}",
        f"SUMMARY:{escape(summary)}",
        f"DESCRIPTION:{escape(description)}",
    ]
    if place:
        lines.append(f"LOCATION:{escape(place)}")
    if site:
        lines.append(f"URL:{site}")
    lines += [
        "BEGIN:VALARM",
        f"TRIGGER:-PT{int(remind)}M",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{escape(f'ISS pass in {int(remind)} minutes')}",
        "END:VALARM",
        "END:VEVENT",
    ]
    return lines


def render_calendar(
    passes: Iterable[dict[str, Any]],
    *,
    place: str,
    latitude: float,
    longitude: float,
    remind: int,
    now: datetime,
    site: str = "",
) -> str:
    """The whole feed as text, CRLF line endings, lines folded."""
    key = f"{latitude:.2f}-{longitude:.2f}"
    name = f"ISS passes, {place}" if place else "ISS passes"
    about = (
        "Passes of the International Space Station you can see from "
        f"{place or 'this place'}, as predicted by ISS Track. Only passes that match "
        "the settings in this feed's address are listed."
    )
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODUCT}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape(name)}",
        f"X-WR-CALDESC:{escape(about)}",
        f"REFRESH-INTERVAL;VALUE=DURATION:{REFRESH}",
        f"X-PUBLISHED-TTL:{REFRESH}",
    ]
    for item in passes:
        if not item.get("matches"):
            continue
        lines += event_lines(item, place=place, remind=remind, now=now, site=site, key=key)
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"
