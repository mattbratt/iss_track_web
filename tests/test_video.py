"""The live video: which of NASA's streams is shown, and what survives a bad hour.

NASA restarts its stream every few months under a new id and nothing says
so; these are the decisions that keep the page from showing a dead one.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta

import httpx
import pytest

from conftest import HD_STREAM, NOW, PLAIN_STREAM, SPACEWALK, YOUTUBE_KEY, FakeInternet, fixed_clock
from server.video import (
    BUILT_IN,
    DEFAULT_VIDEO_ID,
    LOOKED_UP,
    VIDEO_REFRESH,
    LiveVideo,
    VideoStore,
    choose,
    is_live,
    live_streams,
)

NEW_STREAM = ("Zz9newSTREA","Live High-Definition Views from the International Space Station (Official NASA Stream)")


def looked_up(internet: FakeInternet, data_dir, *, key: str | None = YOUTUBE_KEY, clock=fixed_clock) -> LiveVideo:
    """A LiveVideo after its start, with the background loop stopped again."""
    async def run() -> LiveVideo:
        async with internet.client() as http:
            video = LiveVideo(data_dir, http, api_key=key, clock=clock)
            await video.start()
            await video.stop()
            return video
    return asyncio.run(run())


# ----- reading YouTube's replies -------------------------------------------

def test_the_high_definition_stream_is_wanted_first_and_events_never():
    assert choose([SPACEWALK, PLAIN_STREAM, HD_STREAM]) == HD_STREAM[0]
    assert choose([SPACEWALK, PLAIN_STREAM]) == PLAIN_STREAM[0]
    assert choose([SPACEWALK]) is None
    assert choose([]) is None
    # Case and stray spaces in a title do not hide a stream.
    assert choose([("abcdefghijk", "  LIVE VIDEO from the International Space Station")]) == "abcdefghijk"


def test_only_live_videos_with_a_real_id_are_read_from_a_search():
    payload = {
        "items": [
            {"id": {"videoId": HD_STREAM[0]}, "snippet": {"title": HD_STREAM[1], "liveBroadcastContent": "live"}},
            {"id": {"videoId": "not an id"}, "snippet": {"title": HD_STREAM[1], "liveBroadcastContent": "live"}},
            {"id": {"videoId": PLAIN_STREAM[0]}, "snippet": {"title": PLAIN_STREAM[1], "liveBroadcastContent": "none"}},
            {"id": {"channelId": "UCsomething"}, "snippet": {"title": "a channel"}},
            {"id": {"videoId": 12345678901}, "snippet": {"title": "a number"}},
            "not a row",
            None,
        ]
    }
    assert live_streams(payload) == [HD_STREAM]
    assert live_streams({"items": []}) == []
    for nothing in ({}, {"items": None}, [], "text", None):
        with pytest.raises(ValueError):
            live_streams(nothing)


def test_one_video_is_live_only_when_youtube_says_so():
    assert is_live({"items": [{"snippet": {"liveBroadcastContent": "live"}}]}) is True
    assert is_live({"items": [{"snippet": {"liveBroadcastContent": "none"}}]}) is False
    assert is_live({"items": []}) is False  # a deleted or private video
    with pytest.raises(ValueError):
        is_live({"error": "quota"})


# ----- without a key ---------------------------------------------------------

def test_without_a_key_the_built_in_stream_is_served_and_youtube_is_never_asked(data_dir):
    internet = FakeInternet()
    for key in (None, "", "   "):
        video = looked_up(internet, data_dir, key=key)
        assert video.info() == {
            "id": DEFAULT_VIDEO_ID,
            "source": BUILT_IN,
            "checked_at": None,
            "channel": "https://www.youtube.com/@NASA/streams",
        }
    assert internet.youtube_calls == []
    assert not (data_dir / "video.json").exists()


def test_a_saved_answer_is_ignored_once_the_key_is_gone(data_dir):
    # Nothing would ever refresh it, so an old answer must not outlive the key.
    VideoStore(data_dir).save("oldSTREAM00", NOW)
    assert looked_up(FakeInternet(), data_dir, key=None).video_id == DEFAULT_VIDEO_ID


# ----- with a key ------------------------------------------------------------

def test_a_restarted_stream_is_found_and_saved(data_dir):
    internet = FakeInternet()
    internet.youtube_live = [SPACEWALK, NEW_STREAM]
    video = looked_up(internet, data_dir)
    assert video.video_id == NEW_STREAM[0]
    assert video.source == LOOKED_UP
    assert video.checked_at == NOW
    assert internet.youtube_calls == ["search"]
    assert json.loads((data_dir / "video.json").read_text(encoding="utf-8")) == {
        "id": NEW_STREAM[0],
        "checked_at": NOW.isoformat(),
    }


def test_no_wanted_stream_and_the_shown_one_ended_means_no_video(data_dir):
    internet = FakeInternet()
    internet.youtube_live = [SPACEWALK]
    video = looked_up(internet, data_dir)
    assert internet.youtube_calls == ["search", "videos"]
    assert video.info()["id"] is None
    assert video.source == LOOKED_UP
    assert VideoStore(data_dir).load() == (None, NOW)


def test_an_empty_search_does_not_drop_a_stream_that_is_still_live(data_dir):
    # Search comes back empty now and then, and NASA may retitle a stream.
    # Asked about by id, the stream being shown says it is live: keep it.
    internet = FakeInternet()
    internet.youtube_live = []
    internet.youtube_still_live = {DEFAULT_VIDEO_ID}
    video = looked_up(internet, data_dir)
    assert internet.youtube_calls == ["search", "videos"]
    assert video.video_id == DEFAULT_VIDEO_ID
    assert video.source == LOOKED_UP


@pytest.mark.parametrize("status", [400, 403, 500])
def test_a_refused_lookup_keeps_the_last_answer(data_dir, status):
    VideoStore(data_dir).save(PLAIN_STREAM[0], NOW - 2 * VIDEO_REFRESH)
    internet = FakeInternet()
    internet.youtube_status = status
    video = looked_up(internet, data_dir)
    assert video.video_id == PLAIN_STREAM[0]
    assert video.checked_at == NOW - 2 * VIDEO_REFRESH
    assert VideoStore(data_dir).load() == (PLAIN_STREAM[0], NOW - 2 * VIDEO_REFRESH)


def test_an_unreadable_reply_or_a_dead_line_keeps_the_last_answer(data_dir):
    internet = FakeInternet()
    internet.youtube_body = "<html>not json</html>"
    assert looked_up(internet, data_dir).info()["id"] == DEFAULT_VIDEO_ID

    internet.youtube_body = json.dumps({"kind": "youtube#searchListResponse"})  # no items
    assert looked_up(internet, data_dir).info()["id"] == DEFAULT_VIDEO_ID

    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    async def run() -> LiveVideo:
        async with httpx.AsyncClient(transport=httpx.MockTransport(dead)) as http:
            video = LiveVideo(data_dir, http, api_key=YOUTUBE_KEY, clock=fixed_clock)
            await video.start()
            await video.stop()
            return video

    video = asyncio.run(run())
    assert video.video_id == DEFAULT_VIDEO_ID
    assert video.source == BUILT_IN
    assert not (data_dir / "video.json").exists()


def test_a_wrong_key_is_survived_and_never_written_to_the_log(data_dir, caplog):
    internet = FakeInternet()
    secret = "AIzaSy-a-wrong-key-that-must-stay-out-of-logs"
    with caplog.at_level(logging.DEBUG):
        video = looked_up(internet, data_dir, key=secret)
    assert video.video_id == DEFAULT_VIDEO_ID
    assert "HTTP 400" in caplog.text
    assert secret not in caplog.text
    assert "googleapis.com" not in caplog.text


def test_a_restart_within_the_hour_does_not_spend_a_search(data_dir):
    internet = FakeInternet()
    looked_up(internet, data_dir)
    assert internet.youtube_calls == ["search"]

    later = NOW + VIDEO_REFRESH - timedelta(minutes=1)
    video = looked_up(internet, data_dir, clock=lambda: later)
    assert internet.youtube_calls == ["search"]
    assert video.video_id == HD_STREAM[0]
    assert video.source == LOOKED_UP

    due = NOW + VIDEO_REFRESH
    looked_up(internet, data_dir, clock=lambda: due)
    assert internet.youtube_calls == ["search", "search"]


def test_a_damaged_saved_answer_is_set_aside(data_dir):
    data_dir.mkdir(parents=True)
    store = VideoStore(data_dir)
    for damaged in ("{", '{"id": "x"}', '{"id": "has spaces!!", "checked_at": "2026-11-05T00:00:00+00:00"}',
                    '{"id": 5, "checked_at": "2026-11-05T00:00:00+00:00"}', '{"id": null, "checked_at": "soon"}'):
        store.path.write_text(damaged, encoding="utf-8")
        assert store.load() is None, damaged
    store.path.write_text('{"id": null, "checked_at": "2026-11-05T00:00:00"}', encoding="utf-8")
    assert store.load() == (None, NOW)
