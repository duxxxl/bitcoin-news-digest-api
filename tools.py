"""
Tools the specialist agents can call.

Each tool is a plain Python function plus a JSON schema so Claude knows how
to call it. The tools are grouped per specialist at the bottom of this file:

- News specialist:   fetch_rss, web_search, scrape_url
- Social specialist: reddit_hot, x_search, scrape_url
"""

import re

# ---------------------------------------------------------------------------
# 1. Web search (DuckDuckGo, no API key required)
# ---------------------------------------------------------------------------


def web_search(query: str, max_results: int = 6) -> str:
    try:
        from ddgs import DDGS
    except ImportError:
        return "Error: 'ddgs' package not installed. Run: pip install -r requirements.txt"

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
    except Exception as exc:
        return f"Search failed: {exc}"

    if not results:
        return f"No results found for '{query}'."

    formatted = []
    for i, r in enumerate(results, start=1):
        title = r.get("title", "(no title)")
        url = r.get("href", r.get("link", ""))
        snippet = r.get("body", "")
        formatted.append(f"[{i}] {title}\n{url}\n{snippet}")

    return "\n\n".join(formatted)


# ---------------------------------------------------------------------------
# 2. Scrape URL (fetch a page and extract clean, readable text)
# ---------------------------------------------------------------------------


def scrape_url(url: str, max_chars: int = 4000) -> str:
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return (
            "Error: 'requests' and 'beautifulsoup4' packages not installed. "
            "Run: pip install -r requirements.txt"
        )

    headers = {"User-Agent": "Mozilla/5.0 (compatible; bitcoin-digest/1.0)"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
    except Exception as exc:
        return f"Could not fetch {url}: {exc}"

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type:
        return f"Skipped {url}: not an HTML page (content-type: {content_type})"

    soup = BeautifulSoup(response.text, "html.parser")

    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript", "form"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned = "\n".join(lines)

    if not cleaned:
        return f"No readable text found on {url}."

    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + "\n...[truncated]"

    return cleaned


# ---------------------------------------------------------------------------
# 3. RSS feeds (structured news directly from Bitcoin news sites)
# ---------------------------------------------------------------------------

# Known-good feeds the news specialist can use without guessing URLs.
DEFAULT_FEEDS = {
    "bitcoinmagazine": "https://bitcoinmagazine.com/feed",
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph": "https://cointelegraph.com/rss",
    "decrypt": "https://decrypt.co/feed",
}


def _discover_feed_url(site_url: str) -> str:
    """Find a site's RSS feed by reading its HTML <link rel="alternate"> tag.

    Well-behaved sites advertise their feed in the page head, e.g.
        <link rel="alternate" type="application/rss+xml" href="/feed.xml">
    This is how feed readers work, and it beats guessing /feed, /rss, /feed.xml.
    Falls back to trying the common paths if no tag is found.
    """
    from urllib.parse import urljoin

    import requests

    try:
        response = requests.get(site_url, headers={"User-Agent": _BROWSER_UA}, timeout=15)
        response.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"could not load {site_url}: {exc}")

    match = re.search(
        r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*>',
        response.text,
        re.IGNORECASE,
    )
    if match:
        href = re.search(r'href=["\']([^"\']+)["\']', match.group(0), re.IGNORECASE)
        if href:
            return urljoin(site_url, href.group(1))

    # No advertised feed - try the usual suspects.
    for path in ("/feed", "/rss", "/feed.xml", "/rss.xml", "/atom.xml"):
        candidate = site_url.rstrip("/") + path
        try:
            probe = requests.get(candidate, headers={"User-Agent": _BROWSER_UA}, timeout=10)
            if probe.status_code == 200 and b"<item" in probe.content[:5000]:
                return candidate
        except Exception:
            continue

    raise RuntimeError(f"no RSS feed advertised or found at {site_url}")


def fetch_rss(feed: str, max_items: int = 8) -> str:
    """Fetch an RSS feed.

    `feed` can be a known name (see DEFAULT_FEEDS), a full feed URL, or a plain
    site URL/domain - in the last case the feed URL is discovered automatically.
    """
    try:
        import feedparser
    except ImportError:
        return "Error: 'feedparser' package not installed. Run: pip install -r requirements.txt"

    url = DEFAULT_FEEDS.get(feed.lower().strip(), feed.strip())
    if not url.startswith("http"):
        url = "https://" + url

    # A bare site URL (no feed-ish path) -> discover the real feed first.
    # Only the PATH may count: a domain like "nofeed.example" must not be
    # mistaken for a feed just because its name contains "feed".
    from urllib.parse import urlparse

    path = urlparse(url).path
    looks_like_feed = re.search(r"(feed|rss|atom|\.xml)", path, re.IGNORECASE)
    if not looks_like_feed:
        try:
            url = _discover_feed_url(url)
        except Exception as exc:
            return f"Could not find an RSS feed for {feed}: {exc}"

    try:
        parsed = feedparser.parse(url)
    except Exception as exc:
        return f"Could not fetch feed {url}: {exc}"

    if not parsed.entries:
        return f"Feed {url} returned no entries (maybe wrong URL or temporarily down)."

    formatted = []
    for entry in parsed.entries[:max_items]:
        title = entry.get("title", "(no title)")
        link = entry.get("link", "")
        published = entry.get("published", entry.get("updated", "unknown date"))
        summary = entry.get("summary", "")
        # summaries often contain HTML - strip tags crudely and shorten
        summary = re.sub(r"<[^>]+>", "", summary)[:300]
        formatted.append(f"- {title}\n  {link}\n  published: {published}\n  {summary}")

    return f"Feed: {url}\n\n" + "\n\n".join(formatted)


# ---------------------------------------------------------------------------
# 4. Reddit (public JSON endpoint, no API key required)
# ---------------------------------------------------------------------------


def reddit_hot(subreddit: str = "Bitcoin", max_posts: int = 10) -> str:
    """Fetch the current hot posts of a subreddit via Reddit's public JSON endpoint."""
    try:
        import requests
    except ImportError:
        return "Error: 'requests' package not installed."

    url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit={max_posts}"
    headers = {"User-Agent": "bitcoin-digest/1.0 (learning project)"}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        return f"Could not fetch r/{subreddit}: {exc}"

    posts = data.get("data", {}).get("children", [])
    if not posts:
        return f"No posts found in r/{subreddit}."

    formatted = []
    for post in posts:
        p = post.get("data", {})
        if p.get("stickied"):  # skip pinned mod posts, they're rarely news
            continue
        title = p.get("title", "(no title)")
        score = p.get("score", 0)
        comments = p.get("num_comments", 0)
        permalink = "https://www.reddit.com" + p.get("permalink", "")
        selftext = (p.get("selftext") or "")[:200]
        formatted.append(
            f"- {title}\n  score: {score}, comments: {comments}\n  {permalink}"
            + (f"\n  {selftext}" if selftext else "")
        )

    return f"Hot posts in r/{subreddit}:\n\n" + "\n\n".join(formatted)


# ---------------------------------------------------------------------------
# 5. X/Twitter search (best effort - X blocks scraping, so we search the web
#    restricted to x.com and rely on what search engines have indexed)
# ---------------------------------------------------------------------------


def x_search(query: str, max_results: int = 8) -> str:
    """Search for recent X/Twitter posts about a topic (indirect, via web search)."""
    try:
        from ddgs import DDGS
    except ImportError:
        return "Error: 'ddgs' package not installed."

    search_query = f"{query} site:x.com OR site:twitter.com"
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(search_query, max_results=max_results))
    except Exception as exc:
        return f"X search failed: {exc}"

    if not results:
        return (
            f"No indexed X posts found for '{query}'. Note: X blocks direct scraping, "
            "so coverage is limited to what search engines have indexed."
        )

    formatted = []
    for i, r in enumerate(results, start=1):
        title = r.get("title", "(no title)")
        url = r.get("href", r.get("link", ""))
        snippet = r.get("body", "")
        formatted.append(f"[{i}] {title}\n{url}\n{snippet}")

    return "\n\n".join(formatted)


# ---------------------------------------------------------------------------
# 6. X user timeline (specific accounts, via X's syndication endpoint)
#
# X's paid API is the only official way to read tweets, but X still runs a
# public "syndication" endpoint that powers embedded timelines on websites.
# It returns the ~12 most recent posts of a public profile without any login.
# It is UNOFFICIAL and can break at any time - so on any failure we fall back
# to a web search restricted to that account's page.
# ---------------------------------------------------------------------------


def _parse_tweet_date(raw: str):
    """Parse the two date formats X uses. Returns a timezone-aware datetime or None."""
    from datetime import datetime, timezone

    if not raw:
        return None
    # Format A (legacy API): "Wed Oct 10 20:19:24 +0000 2018"
    try:
        return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
    except (ValueError, TypeError):
        pass
    # Format B (ISO): "2026-07-20T10:00:00.000Z"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


# Browser-like User-Agent: xcancel requires one since 2026-01, and the
# syndication endpoint also rejects obvious bot agents.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Nitter forks that still expose per-account RSS (checked against
# status.d420.de - most instances have RSS disabled). First one that works wins.
# Note: these are volunteer-run mirrors. Keep request volume low; for anything
# beyond a personal daily digest, host your own Nitter instance.
NITTER_INSTANCES = [
    "https://xcancel.com",
    "https://nitter.net",
    "https://nitter.poast.org",
]


def _x_posts_via_nitter_rss(username: str) -> list[dict]:
    """Strategy 1: per-account RSS from a Nitter mirror.

    We fetch with requests (so we control headers and can see the real HTTP
    status) and only then hand the bytes to feedparser. Fetching via
    feedparser directly hides whether a failure was a 403, a redirect, or a
    genuinely empty feed.
    """
    import feedparser
    import requests

    errors = []
    for base in NITTER_INSTANCES:
        feed_url = f"{base}/{username}/rss"
        try:
            response = requests.get(
                feed_url,
                headers={"User-Agent": _BROWSER_UA, "Accept": "application/rss+xml, application/xml"},
                timeout=15,
            )
            if response.status_code != 200:
                errors.append(f"{base} -> HTTP {response.status_code}")
                continue

            parsed = feedparser.parse(response.content)
            if not parsed.entries:
                snippet = response.text[:120].replace("\n", " ")
                errors.append(f"{base} -> 200 but 0 entries (body starts: {snippet!r})")
                continue

            posts = []
            for e in parsed.entries:
                text = re.sub(r"<[^>]+>", "", e.get("title", "")).strip()
                link = e.get("link", "")
                # Mirror links point at the Nitter host - cite the real X URL.
                link = re.sub(r"https?://[^/]+/", "https://x.com/", link).replace("#m", "")
                posts.append(
                    {
                        "text": text,
                        "date": _parse_tweet_date(e.get("published", "")),
                        "link": link,
                        "engagement": "",
                        "source": base,
                    }
                )
            if posts:
                return posts
        except Exception as exc:
            errors.append(f"{base} -> {type(exc).__name__}: {exc}")

    raise RuntimeError("no Nitter mirror worked [" + " | ".join(errors) + "]")


def _x_posts_via_syndication(username: str) -> list[dict]:
    """Strategy 2: X's own syndication endpoint. Often returns only a stale
    cache these days, but costs nothing to try. Raises on failure."""
    import json as _json

    import requests

    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}"
    response = requests.get(url, headers={"User-Agent": _BROWSER_UA}, timeout=10)
    response.raise_for_status()

    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        response.text,
        re.DOTALL,
    )
    if not match:
        raise ValueError("No __NEXT_DATA__ found (endpoint structure changed)")

    data = _json.loads(match.group(1))
    entries = (
        data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])
    )
    tweets = [e.get("content", {}).get("tweet", {}) for e in entries if e.get("type") == "tweet"]
    if not tweets:
        raise ValueError("Timeline contained no tweets")

    posts = []
    for t in tweets:
        tweet_id = t.get("id_str", "")
        posts.append(
            {
                "text": (t.get("full_text") or t.get("text") or "").strip(),
                "date": _parse_tweet_date(t.get("created_at", "")),
                "link": f"https://x.com/{username}/status/{tweet_id}" if tweet_id else "",
                "engagement": f"likes: {t.get('favorite_count', 0)}, reposts: {t.get('retweet_count', 0)}",
                "source": "syndication",
            }
        )
    return posts


def x_user_posts(username: str, max_posts: int = 10, max_age_days: int = 7) -> str:
    """Fetch recent posts of a specific X account.

    X has locked down public access, so no single method is reliable. We try
    several strategies in order and use the first one that returns genuinely
    RECENT posts - a strategy that only returns stale/cached posts is treated
    as a failure, because stale data silently poisons the digest.
    """
    from datetime import datetime, timedelta, timezone

    username = username.strip().lstrip("@")
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    attempts = []  # what we tried and why it didn't work (for the agent's info)
    for strategy in (_x_posts_via_nitter_rss, _x_posts_via_syndication):
        try:
            posts = strategy(username)
        except Exception as exc:
            attempts.append(f"{strategy.__name__}: {exc}")
            continue

        posts.sort(
            key=lambda p: p["date"] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        recent = [p for p in posts if p["date"] and p["date"] >= cutoff]

        if not recent:
            newest = posts[0]["date"] if posts and posts[0]["date"] else None
            newest_str = newest.strftime("%Y-%m-%d") if newest else "unknown"
            attempts.append(
                f"{strategy.__name__}: only stale posts (newest {newest_str})"
            )
            continue  # try the next strategy instead of returning junk

        formatted = []
        for p in recent[:max_posts]:
            date_str = p["date"].strftime("%Y-%m-%d %H:%M UTC")
            meta = f"{date_str}" + (f" | {p['engagement']}" if p["engagement"] else "")
            formatted.append(f"- {p['text']}\n  {meta}\n  {p['link']}")

        return (
            f"Recent posts from @{username} (source: {recent[0]['source']}, "
            f"{len(recent)} posts newer than {max_age_days} days):\n\n"
            + "\n\n".join(formatted)
        )

    # Every strategy failed or returned only stale data -> last resort.
    fallback = x_search(f"site:x.com/{username}", max_results=max_posts)
    return (
        f"Could not get recent posts for @{username}. Tried: {'; '.join(attempts)}. "
        f"Indexed search results as a weak substitute (dates unreliable, do NOT "
        f"present these as current):\n\n{fallback}"
    )


# ---------------------------------------------------------------------------
# Shared helper: keep only genuinely recent posts
#
# Every social source below returns the same post dict shape:
#   {text, date, link, engagement, source}
# so they can all share this filter. "Recent enough" is the single quality
# gate that decides whether a source counts as usable.
# ---------------------------------------------------------------------------


def _select_recent(posts: list[dict], max_age_days: int):
    """Sort newest-first, drop anything older than max_age_days.
    Returns (recent_posts, newest_date_seen)."""
    from datetime import datetime, timedelta, timezone

    floor = datetime.min.replace(tzinfo=timezone.utc)
    posts.sort(key=lambda p: p["date"] or floor, reverse=True)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    recent = [p for p in posts if p["date"] and p["date"] >= cutoff]
    newest = posts[0]["date"] if posts and posts[0]["date"] else None
    return recent, newest


def _format_posts(header: str, posts: list[dict], max_posts: int) -> str:
    lines = []
    for p in posts[:max_posts]:
        date_str = p["date"].strftime("%Y-%m-%d %H:%M UTC")
        meta = date_str + (f" | {p['engagement']}" if p.get("engagement") else "")
        lines.append(f"- {p['text']}\n  {meta}\n  {p.get('link', '')}")
    return header + "\n\n" + "\n\n".join(lines)


def _no_recent_posts_message(who: str, newest, max_age_days: int, platform: str) -> str:
    newest_str = newest.strftime("%Y-%m-%d") if newest else "unknown"
    return (
        f"No {platform} posts from {who} within the last {max_age_days} days "
        f"(newest seen: {newest_str}). Skip this account - do NOT present old "
        "posts as current."
    )


# ---------------------------------------------------------------------------
# 7. Bluesky (public AT Protocol API - no key, no login required)
# ---------------------------------------------------------------------------

BLUESKY_API = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"


def bluesky_user_posts(handle: str, max_posts: int = 10, max_age_days: int = 7) -> str:
    """Fetch recent posts of a Bluesky account. `handle` e.g. 'blocktrainer.bsky.social'."""
    import requests

    handle = handle.strip().lstrip("@")
    try:
        response = requests.get(
            BLUESKY_API,
            params={"actor": handle, "limit": 40},
            headers={"User-Agent": _BROWSER_UA},
            timeout=15,
        )
        response.raise_for_status()
        feed = response.json().get("feed", [])
    except Exception as exc:
        return f"Could not fetch Bluesky posts for @{handle}: {exc}"

    if not feed:
        return f"Bluesky returned no posts for @{handle} (handle may be wrong)."

    posts = []
    for item in feed:
        # Skip reposts - we want what this account actually wrote.
        if item.get("reason", {}).get("$type", "").endswith("reasonRepost"):
            continue
        post = item.get("post", {})
        record = post.get("record", {})
        text = (record.get("text") or "").strip()
        if not text:
            continue
        # Build the human-readable URL from the at:// URI's record key.
        rkey = post.get("uri", "").rsplit("/", 1)[-1]
        author = post.get("author", {}).get("handle", handle)
        posts.append(
            {
                "text": text,
                "date": _parse_tweet_date(record.get("createdAt", "")),
                "link": f"https://bsky.app/profile/{author}/post/{rkey}" if rkey else "",
                "engagement": f"likes: {post.get('likeCount', 0)}, reposts: {post.get('repostCount', 0)}, replies: {post.get('replyCount', 0)}",
                "source": "bluesky",
            }
        )

    recent, newest = _select_recent(posts, max_age_days)
    if not recent:
        return _no_recent_posts_message(f"@{handle}", newest, max_age_days, "Bluesky")

    return _format_posts(
        f"Recent Bluesky posts from @{handle} ({len(recent)} newer than {max_age_days} days):",
        recent,
        max_posts,
    )


# ---------------------------------------------------------------------------
# 8. Nostr (open protocol, home of much of the Bitcoin community)
#
# No API and no keys: you open a WebSocket to a "relay", send a filter, and
# receive matching events until the relay says EOSE ("end of stored events").
# Accounts are public keys. Users share them as npub1... (bech32), but the
# protocol wants raw hex - so we decode bech32 ourselves below.
# ---------------------------------------------------------------------------

# Order matters: the ones that reliably accept plain connections come first.
# Some relays (damus) reject handshakes without browser-like headers or block
# datacenter IPs, so they go last as a bonus rather than the primary path.
NOSTR_RELAYS = [
    "wss://relay.primal.net",
    "wss://nos.lol",
    "wss://relay.nostr.band",
    "wss://nostr.wine",
    "wss://relay.damus.io",
]

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _bech32_to_hex(npub: str) -> str:
    """Decode an npub1... key to raw hex. Minimal bech32 decoder (no deps)."""
    npub = npub.strip().lower()
    if not npub.startswith("npub1"):
        raise ValueError("not an npub key")

    data_part = npub[5:]
    # Last 6 chars are the checksum - we drop them (we only decode, not verify).
    values = []
    for char in data_part[:-6]:
        if char not in _BECH32_CHARSET:
            raise ValueError(f"invalid bech32 character: {char}")
        values.append(_BECH32_CHARSET.index(char))

    # Convert from 5-bit groups to 8-bit bytes.
    acc, bits, out = 0, 0, bytearray()
    for value in values:
        acc = (acc << 5) | value
        bits += 5
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)

    if len(out) != 32:
        raise ValueError(f"decoded key has {len(out)} bytes, expected 32")
    return out.hex()


def nostr_user_posts(pubkey: str, max_posts: int = 10, max_age_days: int = 7) -> str:
    """Fetch recent notes of a Nostr account. `pubkey` may be npub1... or hex."""
    import json as _json
    from datetime import datetime, timezone

    try:
        import websocket
    except ImportError:
        return "Error: 'websocket-client' package not installed. Run: pip install -r requirements.txt"

    pubkey = pubkey.strip()
    try:
        hex_key = _bech32_to_hex(pubkey) if pubkey.startswith("npub") else pubkey.lower()
    except Exception as exc:
        return f"Invalid Nostr key '{pubkey}': {exc}"

    # kind 1 = short text note (the "tweet" of Nostr)
    request = _json.dumps(
        ["REQ", "digest", {"authors": [hex_key], "kinds": [1], "limit": 40}]
    )

    # Some relays reject WebSocket handshakes that don't look like a browser.
    ws_headers = [f"User-Agent: {_BROWSER_UA}"]

    errors = []
    best_events = []          # remember events even if they were only stale,
    best_newest = None        # so we can report the true newest date at the end
    for relay in NOSTR_RELAYS:
        events = []
        try:
            ws = websocket.create_connection(
                relay, timeout=10, header=ws_headers, origin="https://nostr.com"
            )
            ws.settimeout(10)
            ws.send(request)
            while True:
                message = _json.loads(ws.recv())
                if message[0] == "EVENT":
                    events.append(message[2])
                elif message[0] in ("EOSE", "CLOSED"):
                    break
            ws.close()
        except Exception as exc:
            errors.append(f"{relay}: {type(exc).__name__}")
            continue

        if not events:
            errors.append(f"{relay}: no events")
            continue

        posts = [
            {
                "text": (e.get("content") or "").strip(),
                "date": datetime.fromtimestamp(e.get("created_at", 0), tz=timezone.utc),
                "link": f"https://njump.me/{e.get('id', '')}",
                "engagement": "",
                "source": f"nostr ({relay})",
            }
            for e in events
            if (e.get("content") or "").strip()
        ]

        recent, newest = _select_recent(posts, max_age_days)
        if recent:
            return _format_posts(
                f"Recent Nostr notes from {pubkey[:12]}... via {relay} "
                f"({len(recent)} newer than {max_age_days} days):",
                recent,
                max_posts,
            )
        # Events existed but were older than the window - keep the freshest we
        # saw so the final message can name a concrete date.
        if newest and (best_newest is None or newest > best_newest):
            best_newest, best_events = newest, posts
        errors.append(f"{relay}: only notes older than {max_age_days}d")

    if best_newest:
        return (
            f"No Nostr notes from {pubkey[:12]}... within the last {max_age_days} days, "
            f"but this account IS active (newest note: {best_newest.strftime('%Y-%m-%d')}). "
            "Consider a wider window if you need its posts. Do NOT present old notes as current."
        )
    return (
        f"Could not get recent Nostr notes for {pubkey[:16]}... "
        f"Tried: {'; '.join(errors)}."
    )


# ---------------------------------------------------------------------------
# 9. Mastodon (every account has a public RSS feed - no key needed)
# ---------------------------------------------------------------------------


def mastodon_user_posts(account: str, max_posts: int = 10, max_age_days: int = 7) -> str:
    """Fetch recent posts of a Mastodon account.
    `account` as 'user@instance.social' or a full profile URL."""
    import feedparser
    import requests

    account = account.strip().lstrip("@")
    if account.startswith("http"):
        feed_url = account.rstrip("/") + ".rss"
    else:
        try:
            user, instance = account.split("@", 1)
        except ValueError:
            return f"Invalid Mastodon account '{account}'. Use 'user@instance.social'."
        feed_url = f"https://{instance}/@{user}.rss"

    try:
        response = requests.get(feed_url, headers={"User-Agent": _BROWSER_UA}, timeout=15)
        if response.status_code != 200:
            return f"Mastodon feed for {account} returned HTTP {response.status_code}."
        parsed = feedparser.parse(response.content)
    except Exception as exc:
        return f"Could not fetch Mastodon feed for {account}: {exc}"

    if not parsed.entries:
        return f"Mastodon feed for {account} contained no posts."

    posts = [
        {
            "text": re.sub(r"<[^>]+>", "", e.get("description", e.get("title", ""))).strip(),
            "date": _parse_tweet_date(e.get("published", "")),
            "link": e.get("link", ""),
            "engagement": "",
            "source": "mastodon",
        }
        for e in parsed.entries
    ]

    recent, newest = _select_recent(posts, max_age_days)
    if not recent:
        return _no_recent_posts_message(account, newest, max_age_days, "Mastodon")

    return _format_posts(
        f"Recent Mastodon posts from {account} ({len(recent)} newer than {max_age_days} days):",
        recent,
        max_posts,
    )


def x_debug_timeline(username: str) -> str:
    """Diagnostic helper (not an agent tool): run every strategy and show what
    each one returns, with dates, so you can see which path actually works.

    Run with: python -c "from tools import x_debug_timeline; print(x_debug_timeline('blocktrainer'))"
    """
    username = username.strip().lstrip("@")
    lines = [f"=== X strategy diagnosis for @{username} ==="]

    for strategy in (_x_posts_via_nitter_rss, _x_posts_via_syndication):
        lines.append(f"\n--- {strategy.__name__} ---")
        try:
            posts = strategy(username)
        except Exception as exc:
            lines.append(f"  FAILED: {exc}")
            continue

        lines.append(f"  {len(posts)} posts returned")
        for p in posts[:8]:
            date_str = p["date"].strftime("%Y-%m-%d") if p["date"] else "UNPARSEABLE"
            text = (p["text"] or "")[:65].replace("\n", " ")
            lines.append(f"    {date_str} | {text}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool registries, grouped per specialist
# ---------------------------------------------------------------------------

_WEB_SEARCH_SCHEMA = {
    "name": "web_search",
    "description": (
        "Search the web via DuckDuckGo and get back a list of results "
        "(title, URL, snippet). Use this to find recent Bitcoin news."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
            "max_results": {
                "type": "integer",
                "description": "How many results to return (default 6)",
            },
        },
        "required": ["query"],
    },
}

_SCRAPE_URL_SCHEMA = {
    "name": "scrape_url",
    "description": (
        "Fetch a web page and extract its readable text content. Use this "
        "to read the full content of a promising result instead of relying "
        "on a short snippet alone."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The full URL to fetch"},
            "max_chars": {
                "type": "integer",
                "description": "Max characters of text to return (default 4000)",
            },
        },
        "required": ["url"],
    },
}

_FETCH_RSS_SCHEMA = {
    "name": "fetch_rss",
    "description": (
        "Fetch the latest articles from an RSS feed. Pass one of the known "
        "feed names (bitcoinmagazine, coindesk, cointelegraph, decrypt), a "
        "full feed URL, or just a site domain - the feed URL is then "
        "discovered automatically. Returns titles, links, dates, and "
        "summaries. This is the most reliable way to get very recent Bitcoin news."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "feed": {
                "type": "string",
                "description": "Feed name (e.g. 'coindesk'), a feed URL, or a site domain",
            },
            "max_items": {
                "type": "integer",
                "description": "How many entries to return (default 8)",
            },
        },
        "required": ["feed"],
    },
}

_REDDIT_HOT_SCHEMA = {
    "name": "reddit_hot",
    "description": (
        "Fetch the current hot posts of a subreddit (default: Bitcoin), "
        "including score and comment count. Use this to see what the "
        "community is discussing and how strongly."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "subreddit": {
                "type": "string",
                "description": "Subreddit name without r/ (default 'Bitcoin')",
            },
            "max_posts": {
                "type": "integer",
                "description": "How many posts to return (default 10)",
            },
        },
        "required": [],
    },
}

_X_SEARCH_SCHEMA = {
    "name": "x_search",
    "description": (
        "Search for recent X/Twitter posts about a topic. Works indirectly "
        "via web search (X blocks scraping), so treat results as a rough "
        "signal of what accounts are talking about, not a complete picture."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Topic to search for on X"},
            "max_results": {
                "type": "integer",
                "description": "How many results to return (default 8)",
            },
        },
        "required": ["query"],
    },
}

# What the NEWS specialist gets:
NEWS_TOOL_SCHEMAS = [_FETCH_RSS_SCHEMA, _WEB_SEARCH_SCHEMA, _SCRAPE_URL_SCHEMA]
NEWS_TOOL_FUNCTIONS = {
    "fetch_rss": fetch_rss,
    "web_search": web_search,
    "scrape_url": scrape_url,
}

_X_USER_POSTS_SCHEMA = {
    "name": "x_user_posts",
    "description": (
        "Fetch the most recent posts (~12) of a specific public X/Twitter "
        "account, with dates, like/repost counts, and links. Much more precise "
        "than x_search - prefer this for the creator's watched accounts."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "username": {
                "type": "string",
                "description": "X username without @, e.g. 'saylor'",
            },
            "max_posts": {
                "type": "integer",
                "description": "How many posts to return (default 10)",
            },
            "max_age_days": {
                "type": "integer",
                "description": "Only return posts newer than this many days (default 7)",
            },
        },
        "required": ["username"],
    },
}

_BLUESKY_SCHEMA = {
    "name": "bluesky_user_posts",
    "description": (
        "Fetch recent posts of a Bluesky account, with dates, likes, reposts "
        "and links. Reliable and current - use this as a primary social source."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "handle": {
                "type": "string",
                "description": "Bluesky handle, e.g. 'blocktrainer.bsky.social'",
            },
            "max_posts": {"type": "integer", "description": "How many posts (default 10)"},
            "max_age_days": {
                "type": "integer",
                "description": "Only posts newer than this many days (default 7)",
            },
        },
        "required": ["handle"],
    },
}

_NOSTR_SCHEMA = {
    "name": "nostr_user_posts",
    "description": (
        "Fetch recent notes of a Nostr account. Nostr is where much of the "
        "Bitcoin community posts, so this is a high-value signal for Bitcoin "
        "topics. Accepts an npub1... key or raw hex pubkey."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pubkey": {"type": "string", "description": "npub1... or hex public key"},
            "max_posts": {"type": "integer", "description": "How many notes (default 10)"},
            "max_age_days": {
                "type": "integer",
                "description": "Only notes newer than this many days (default 7)",
            },
        },
        "required": ["pubkey"],
    },
}

_MASTODON_SCHEMA = {
    "name": "mastodon_user_posts",
    "description": (
        "Fetch recent posts of a Mastodon account via its public RSS feed. "
        "Accepts 'user@instance.social' or a full profile URL."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "account": {
                "type": "string",
                "description": "Account as 'user@instance.social' or profile URL",
            },
            "max_posts": {"type": "integer", "description": "How many posts (default 10)"},
            "max_age_days": {
                "type": "integer",
                "description": "Only posts newer than this many days (default 7)",
            },
        },
        "required": ["account"],
    },
}

# What the SOCIAL specialist gets.
#
# NOTE: x_user_posts is deliberately NOT offered any more. X shut down usable
# free access (the syndication endpoint only serves a frozen 2024/25 cache and
# the Nitter mirrors no longer deliver RSS). The code stays in this file - the
# strategy chain still works the moment a path becomes viable again - but the
# agent is not given a tool that reliably wastes a turn. Bluesky, Nostr and
# Mastodon replace it with open, key-free access.
SOCIAL_TOOL_SCHEMAS = [
    _BLUESKY_SCHEMA,
    _NOSTR_SCHEMA,
    _MASTODON_SCHEMA,
    _REDDIT_HOT_SCHEMA,
    _SCRAPE_URL_SCHEMA,
]
SOCIAL_TOOL_FUNCTIONS = {
    "bluesky_user_posts": bluesky_user_posts,
    "nostr_user_posts": nostr_user_posts,
    "mastodon_user_posts": mastodon_user_posts,
    "reddit_hot": reddit_hot,
    "scrape_url": scrape_url,
}

# Kept for manual use / future re-activation (see note above):
# tools.x_user_posts(...), tools.x_search(...), tools.x_debug_timeline(...)
