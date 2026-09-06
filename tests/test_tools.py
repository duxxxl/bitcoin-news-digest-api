"""
Offline tests for the data tools: we don't hit the real network, we fake the
responses and check that our parsing/formatting logic handles them correctly.
This is standard practice - CI machines shouldn't depend on reddit.com being up.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import tools  # noqa: E402


def test_fetch_rss_formats_entries():
    fake_parsed = SimpleNamespace(
        entries=[
            {
                "title": "Bitcoin hits new high",
                "link": "https://example.com/a",
                "published": "Mon, 20 Jul 2026",
                "summary": "<p>Some <b>HTML</b> summary</p>",
            }
        ]
    )
    with patch("feedparser.parse", return_value=fake_parsed):
        out = tools.fetch_rss("coindesk", max_items=5)

    assert "Bitcoin hits new high" in out
    assert "https://example.com/a" in out
    assert "<p>" not in out  # HTML tags must be stripped


def test_fetch_rss_known_name_resolves_to_url():
    fake_parsed = SimpleNamespace(entries=[])
    with patch("feedparser.parse", return_value=fake_parsed) as mock_parse:
        tools.fetch_rss("coindesk")
    called_url = mock_parse.call_args[0][0]
    assert called_url == tools.DEFAULT_FEEDS["coindesk"]


def test_fetch_rss_discovers_feed_from_bare_domain():
    """A plain site URL must be resolved via the <link rel=alternate> tag."""
    html = (
        '<html><head><link rel="alternate" type="application/rss+xml" '
        'title="RSS" href="/custom/feed.xml"></head><body></body></html>'
    )
    page = MagicMock()
    page.text = html
    page.raise_for_status.return_value = None

    fake_parsed = SimpleNamespace(
        entries=[{"title": "An article", "link": "https://example.com/p/x", "published": "Mon, 20 Jul 2026", "summary": ""}]
    )
    with patch("requests.get", return_value=page), patch(
        "feedparser.parse", return_value=fake_parsed
    ) as mock_parse:
        out = tools.fetch_rss("example.com")

    assert mock_parse.call_args[0][0] == "https://example.com/custom/feed.xml"
    assert "An article" in out


def test_fetch_rss_discovery_falls_back_to_common_paths():
    """No <link> tag -> probe /feed, /rss, ... and accept the one with items."""
    page_without_link = MagicMock()
    page_without_link.text = "<html><head></head><body>hi</body></html>"
    page_without_link.raise_for_status.return_value = None

    probe_hit = MagicMock()
    probe_hit.status_code = 200
    probe_hit.content = b"<rss><channel><item><title>x</title></item></channel></rss>"

    calls = {"n": 0}

    def _get(url, *args, **kwargs):
        calls["n"] += 1
        return page_without_link if calls["n"] == 1 else probe_hit

    fake_parsed = SimpleNamespace(entries=[{"title": "Probed", "link": "l", "published": "d", "summary": ""}])
    with patch("requests.get", side_effect=_get), patch(
        "feedparser.parse", return_value=fake_parsed
    ) as mock_parse:
        out = tools.fetch_rss("example.com")

    assert mock_parse.call_args[0][0] == "https://example.com/feed"
    assert "Probed" in out


def test_fetch_rss_reports_when_no_feed_found():
    page = MagicMock()
    page.text = "<html></html>"
    page.raise_for_status.return_value = None
    miss = MagicMock()
    miss.status_code = 404
    miss.content = b""

    def _get(url, *args, **kwargs):
        return page if url.rstrip("/").endswith("nofeed.example") else miss

    with patch("requests.get", side_effect=_get):
        out = tools.fetch_rss("nofeed.example")

    assert "Could not find an RSS feed" in out


def test_reddit_hot_formats_posts_and_skips_stickied():
    fake_json = {
        "data": {
            "children": [
                {"data": {"title": "PINNED RULES", "stickied": True, "permalink": "/x"}},
                {
                    "data": {
                        "title": "ETF inflows discussion",
                        "stickied": False,
                        "score": 512,
                        "num_comments": 300,
                        "permalink": "/r/Bitcoin/comments/abc",
                        "selftext": "What do you think?",
                    }
                },
            ]
        }
    }
    fake_response = MagicMock()
    fake_response.json.return_value = fake_json
    fake_response.raise_for_status.return_value = None

    with patch("requests.get", return_value=fake_response):
        out = tools.reddit_hot(max_posts=5)

    assert "ETF inflows discussion" in out
    assert "score: 512" in out
    assert "PINNED RULES" not in out  # stickied posts are skipped


def _fake_x_response(tweets):
    """Build a fake syndication HTML response from a list of tweet dicts."""
    import json

    next_data = {
        "props": {
            "pageProps": {
                "timeline": {
                    "entries": [
                        {"type": "tweet", "content": {"tweet": t}} for t in tweets
                    ]
                }
            }
        }
    }
    fake_response = MagicMock()
    fake_response.text = (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(next_data)
        + "</script></html>"
    )
    fake_response.raise_for_status.return_value = None
    return fake_response


def _iso_days_ago(days):
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def _fake_rss_response(items, status=200):
    """Build a fake RSS HTTP response for the Nitter strategy."""
    entries = "".join(
        f"<item><title>{i['title']}</title><link>{i['link']}</link>"
        f"<pubDate>{i['published']}</pubDate></item>"
        for i in items
    )
    resp = MagicMock()
    resp.status_code = status
    resp.content = f"<rss><channel>{entries}</channel></rss>".encode()
    resp.text = resp.content.decode()
    return resp


def _route_requests(rss_response=None, syndication_response=None):
    """Return a side_effect that answers based on which URL is requested."""

    def _side_effect(url, *args, **kwargs):
        if "syndication.twitter.com" in url:
            if syndication_response is None:
                raise AssertionError("syndication should not have been called")
            return syndication_response
        if rss_response is None:
            raise RuntimeError("nitter unreachable")
        return rss_response

    return _side_effect


def test_nitter_rss_strategy_preferred_when_fresh():
    """Strategy 1 (Nitter RSS) works -> syndication must not even be tried."""
    rss = _fake_rss_response(
        [{"title": "Fresh post from RSS", "link": "https://xcancel.com/jack/status/999#m", "published": _iso_days_ago(1)}]
    )
    with patch("requests.get", side_effect=_route_requests(rss_response=rss)):
        out = tools.x_user_posts("jack")

    assert "Fresh post from RSS" in out
    assert "source: https://xcancel.com" in out
    assert "https://x.com/jack/status/999" in out  # mirror link rewritten to x.com


def test_falls_through_to_syndication_when_nitter_stale():
    """Nitter returns only 2024 posts -> must fall through, not return junk."""
    rss = _fake_rss_response(
        [{"title": "Old 2024 post", "link": "https://xcancel.com/jack/status/1", "published": "2024-03-01T10:00:00Z"}]
    )
    syndication = _fake_x_response(
        [{"full_text": "Fresh from syndication", "created_at": _iso_days_ago(2), "favorite_count": 10, "retweet_count": 2, "id_str": "777"}]
    )
    with patch("requests.get", side_effect=_route_requests(rss, syndication)):
        out = tools.x_user_posts("jack")

    assert "Fresh from syndication" in out
    assert "Old 2024 post" not in out
    assert "source: syndication" in out


def test_nitter_http_error_is_reported_not_swallowed():
    """A 403 must show up in the diagnosis, not vanish as 'no entries'."""
    rss = _fake_rss_response([], status=403)
    with patch("requests.get", side_effect=_route_requests(rss, None)), patch.object(
        tools, "x_search", return_value="(search results)"
    ):
        # syndication raises AssertionError inside -> caught by the chain
        out = tools.x_user_posts("jack")

    assert "HTTP 403" in out


def test_all_strategies_stale_returns_explicit_warning():
    """Both strategies stale -> no stale content is passed on, warning instead."""
    rss = _fake_rss_response(
        [{"title": "Old RSS post", "link": "https://xcancel.com/x/status/1", "published": "2024-03-01T10:00:00Z"}]
    )
    syndication = _fake_x_response(
        [{"full_text": "Old syndication post", "created_at": "2024-01-05T10:00:00Z", "id_str": "1"}]
    )
    with patch("requests.get", side_effect=_route_requests(rss, syndication)), patch.object(
        tools, "x_search", return_value="(search results)"
    ):
        out = tools.x_user_posts("blocktrainer")

    assert "Could not get recent posts for @blocktrainer" in out
    assert "only stale posts" in out
    assert "Old RSS post" not in out
    assert "Old syndication post" not in out
    assert "do NOT" in out  # explicit instruction to the agent


def test_syndication_handles_legacy_date_format():
    from datetime import datetime, timedelta, timezone

    recent = datetime.now(timezone.utc) - timedelta(days=2)
    legacy = recent.strftime("%a %b %d %H:%M:%S +0000 %Y")
    syndication = _fake_x_response([{"full_text": "Legacy format post", "created_at": legacy, "id_str": "9"}])

    with patch("requests.get", side_effect=_route_requests(None, syndication)):
        out = tools.x_user_posts("saylor")

    assert "Legacy format post" in out


def test_social_prompt_includes_watched_accounts(monkeypatch):
    import specialists

    monkeypatch.setenv("BLUESKY_ACCOUNTS", "blocktrainer.bsky.social")
    monkeypatch.setenv("NOSTR_ACCOUNTS", "npub1abc")
    monkeypatch.setenv("MASTODON_ACCOUNTS", "someone@mastodon.social")
    prompt = specialists.social_system_prompt()
    assert "blocktrainer.bsky.social" in prompt
    assert "npub1abc" in prompt
    assert "someone@mastodon.social" in prompt
    assert "bluesky_user_posts" in prompt

    for var in ("BLUESKY_ACCOUNTS", "NOSTR_ACCOUNTS", "MASTODON_ACCOUNTS"):
        monkeypatch.setenv(var, "")
    prompt = specialists.social_system_prompt()
    assert "blocktrainer.bsky.social" not in prompt
    assert "No specific accounts are configured" in prompt


def test_x_tool_no_longer_offered_to_agent():
    """X stays in the code but must not be handed to the agent any more."""
    names = [s["name"] for s in tools.SOCIAL_TOOL_SCHEMAS]
    assert "x_user_posts" not in names
    assert "x_search" not in names
    assert "bluesky_user_posts" in names
    assert "nostr_user_posts" in names
    assert "mastodon_user_posts" in names
    # ...but the functions are still importable for manual use:
    assert callable(tools.x_user_posts)


def test_bluesky_parses_posts_and_skips_reposts():
    feed = {
        "feed": [
            {
                "reason": {"$type": "app.bsky.feed.defs#reasonRepost"},
                "post": {"record": {"text": "A repost", "createdAt": _iso_days_ago(1)}},
            },
            {
                "post": {
                    "uri": "at://did:plc:abc/app.bsky.feed.post/xyz789",
                    "author": {"handle": "blocktrainer.bsky.social"},
                    "record": {"text": "Bitcoin steigt", "createdAt": _iso_days_ago(1)},
                    "likeCount": 42,
                    "repostCount": 7,
                    "replyCount": 3,
                }
            },
        ]
    }
    resp = MagicMock()
    resp.json.return_value = feed
    resp.raise_for_status.return_value = None

    with patch("requests.get", return_value=resp):
        out = tools.bluesky_user_posts("blocktrainer.bsky.social")

    assert "Bitcoin steigt" in out
    assert "A repost" not in out  # reposts are skipped
    assert "likes: 42" in out
    assert "https://bsky.app/profile/blocktrainer.bsky.social/post/xyz789" in out


def test_bluesky_rejects_stale_feed():
    feed = {
        "feed": [
            {
                "post": {
                    "uri": "at://did/app.bsky.feed.post/old",
                    "author": {"handle": "x.bsky.social"},
                    "record": {"text": "Alter Post", "createdAt": "2024-05-01T10:00:00Z"},
                }
            }
        ]
    }
    resp = MagicMock()
    resp.json.return_value = feed
    resp.raise_for_status.return_value = None

    with patch("requests.get", return_value=resp):
        out = tools.bluesky_user_posts("x.bsky.social")

    assert "No Bluesky posts" in out
    assert "Alter Post" not in out


def test_bech32_npub_decodes_to_hex():
    # Known Nostr identity (jack): npub -> hex
    npub = "npub1sg6plzptd64u62a878hep2kev88swjh3tw00gjsfl8f237lmu63q0uf63m"
    expected = "82341f882b6eabcd2ba7f1ef90aad961cf074af15b9ef44a09f9d2a8fbfbe6a2"
    assert tools._bech32_to_hex(npub) == expected


def test_nostr_parses_events_from_relay():
    import json

    now_ts = int(__import__("time").time()) - 3600
    messages = [
        json.dumps(["EVENT", "digest", {"id": "abc123", "content": "Bitcoin fixes this", "created_at": now_ts}]),
        json.dumps(["EOSE", "digest"]),
    ]
    fake_ws = MagicMock()
    fake_ws.recv.side_effect = messages

    with patch("websocket.create_connection", return_value=fake_ws):
        out = tools.nostr_user_posts("deadbeef" * 8)

    assert "Bitcoin fixes this" in out
    assert "https://njump.me/abc123" in out


def test_mastodon_builds_feed_url_from_handle():
    rss = _fake_rss_response(
        [{"title": "Toot text", "link": "https://mastodon.social/@user/1", "published": _iso_days_ago(1)}]
    )
    with patch("requests.get", return_value=rss) as mock_get:
        out = tools.mastodon_user_posts("user@mastodon.social")

    assert mock_get.call_args[0][0] == "https://mastodon.social/@user.rss"
    assert "Toot text" in out
