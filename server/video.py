"""Which YouTube video is NASA's live view from the station right now.

NASA restarts its stream every few months, and each restart gets a new video
id; the old id then answers "Video unavailable" for good. Nothing announces
the change, so a page that carries the id itself breaks silently.

With a YouTube Data API key in YOUTUBE_API_KEY the service asks YouTube,
about once an hour, which of NASA's streams are live, and keeps the one it
wants. Without a key it serves the id built in below, which is right until
NASA's next restart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

_LOGGER = logging.getLogger(__name__)

# NASA's "Live High-Definition Views from the International Space Station",
# live since 2026-10-22. The one before it, awQzjn72bI0, ended that morning.
DEFAULT_VIDEO_ID = "ROboUHfPUrU"
NASA_CHANNEL = "UCLA_DiR1FfKNvjuUpBHmylQ"
NASA_STREAMS_URL = "https://www.youtube.com/@NASA/streams"
SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# A search is one of 100 a day that a key is given, so hourly leaves room.
VIDEO_REFRESH = timedelta(hours=1)
REQUEST_TIMEOUT = 10.0

# The streams to show, best first, by how their titles begin. NASA kept the
# first title word for word across its last restart. Its other broadcasts
# (a spacewalk, a launch) are live on the same channel and are not wanted.
WANTED_TITLES = (
    "live high-definition views from the international space station",
    "live video from the international space station",
)
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

BUILT_IN = "built_in"
LOOKED_UP = "lookup"

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def live_streams(payload: Any) -> list[tuple[str, str]]:
    """The (id, title) of each live video in a search reply.

    A row that is not a live video with a well-formed id is skipped. A reply
    with no list of rows at all is not an answer, and raises ValueError.
    """
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("the reply has no list of items")
    found: list[tuple[str, str]] = []
    for item in items:
        try:
            video_id = item["id"]["videoId"]
            snippet = item["snippet"]
            title = snippet["title"]
            state = snippet.get("liveBroadcastContent", "live")
        except (KeyError, TypeError, AttributeError):
            continue
        if not isinstance(video_id, str) or not VIDEO_ID.match(video_id):
            continue
        if not isinstance(title, str) or state != "live":
            continue
        found.append((video_id, title))
    return found


def choose(live: list[tuple[str, str]]) -> str | None:
    """The stream to show from those live now, or None when none is wanted."""
    for wanted in WANTED_TITLES:
        for video_id, title in live:
            if title.strip().lower().startswith(wanted):
                return video_id
    return None


def is_live(payload: Any) -> bool:
    """Whether a reply about one video says it is live now."""
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("the reply has no list of items")
    for item in items:
        try:
            if item["snippet"]["liveBroadcastContent"] == "live":
                return True
        except (KeyError, TypeError):
            continue
    return False


class VideoStore:
    """The last answer from YouTube, on disk, written atomically."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "video.json"

    def load(self) -> tuple[str | None, datetime] | None:
        try:
            with self.path.open(encoding="utf-8") as handle:
                stored = json.load(handle)
            video_id = stored["id"]
            checked_at = datetime.fromisoformat(stored["checked_at"])
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as err:
            _LOGGER.warning("The stored live video at %s is unusable (%s)", self.path, err)
            return None
        if video_id is not None and not (isinstance(video_id, str) and VIDEO_ID.match(video_id)):
            _LOGGER.warning("The stored live video at %s is not a video id", self.path)
            return None
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        return video_id, checked_at

    def save(self, video_id: str | None, checked_at: datetime) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        try:
            with temp.open("w", encoding="utf-8") as handle:
                json.dump({"id": video_id, "checked_at": checked_at.isoformat()}, handle)
            os.replace(temp, self.path)
        except OSError as err:
            _LOGGER.warning("Could not save the live video to %s: %s", self.path, err)


class LiveVideo:
    """The video to show when someone clicks the station, kept current."""

    def __init__(
        self,
        data_dir: Path,
        http: httpx.AsyncClient,
        *,
        api_key: str | None,
        clock: Clock = utcnow,
    ) -> None:
        self._store = VideoStore(data_dir)
        self._http = http
        self._api_key = (api_key or "").strip() or None
        self._clock = clock
        self._task: asyncio.Task | None = None
        self._tried_at: datetime | None = None
        self.video_id: str | None = DEFAULT_VIDEO_ID
        self.checked_at: datetime | None = None
        self.source = BUILT_IN

    # ----- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        """With a key: load the last answer, ask again if it is an hour old,
        then keep asking in the background. Without one: nothing to do."""
        if self._api_key is None:
            _LOGGER.info("No YOUTUBE_API_KEY, so the live video is the built-in one (%s)", DEFAULT_VIDEO_ID)
            return
        stored = self._store.load()
        if stored is not None:
            self.video_id, self.checked_at = stored
            self.source = LOOKED_UP
            self._tried_at = self.checked_at
        if self.due:
            await self.refresh()
        self._task = asyncio.create_task(self._loop(), name="iss-track-video")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    @property
    def due(self) -> bool:
        return self._tried_at is None or self._clock() - self._tried_at >= VIDEO_REFRESH

    async def _loop(self) -> None:
        while True:
            wait = VIDEO_REFRESH
            if self._tried_at is not None:
                wait = self._tried_at + VIDEO_REFRESH - self._clock()
            await asyncio.sleep(max(60.0, wait.total_seconds()))
            try:
                await self.refresh()
            except Exception as err:  # noqa: BLE001 - one bad hour must not end the loop
                _LOGGER.warning("The live video lookup failed (%s)", type(err).__name__)

    # ----- the lookup -----------------------------------------------------

    async def _ask(self, url: str, params: dict[str, Any]) -> Any:
        # The key travels in the address, as YouTube documents it. Nothing
        # here may log that address or an httpx error's own text, which
        # quotes it; server.main keeps httpx's request log quiet as well.
        response = await self._http.get(url, params={**params, "key": self._api_key}, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    async def refresh(self) -> bool:
        """Ask YouTube which stream is live. On any failure the last answer stays.

        Search can come back empty, or NASA can retitle a stream, while the
        one being shown is still live. So before giving a stream up, it is
        asked about by id, which costs almost nothing and is exact.
        """
        self._tried_at = self._clock()
        try:
            live = live_streams(await self._ask(SEARCH_URL, {
                "part": "snippet",
                "channelId": NASA_CHANNEL,
                "eventType": "live",
                "type": "video",
                "maxResults": 50,
            }))
            chosen = choose(live)
            if chosen is None and self.video_id is not None:
                if is_live(await self._ask(VIDEOS_URL, {"part": "snippet", "id": self.video_id})):
                    chosen = self.video_id
        except httpx.HTTPStatusError as err:
            _LOGGER.warning(
                "YouTube refused the live video lookup (HTTP %s), keeping %s",
                err.response.status_code, self.video_id,
            )
            return False
        except httpx.HTTPError as err:
            _LOGGER.warning(
                "Could not reach YouTube for the live video lookup (%s), keeping %s",
                type(err).__name__, self.video_id,
            )
            return False
        except ValueError:
            _LOGGER.warning("YouTube's reply to the live video lookup was unreadable, keeping %s", self.video_id)
            return False
        if chosen != self.video_id or self.source != LOOKED_UP:
            _LOGGER.info("The live video is %s (was %s)", chosen or "none: no stream is live", self.video_id)
        self.video_id = chosen
        self.checked_at = self._tried_at
        self.source = LOOKED_UP
        self._store.save(chosen, self.checked_at)
        return True

    def info(self) -> dict[str, Any]:
        """What the page is told. An id of None means no stream is live."""
        checked = self.checked_at
        return {
            "id": self.video_id,
            "source": self.source,
            "checked_at": int(checked.timestamp() * 1000) if checked else None,
            "channel": NASA_STREAMS_URL,
        }
