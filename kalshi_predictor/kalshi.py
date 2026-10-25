"""Thin client for Kalshi's public market data API. No API key is needed."""
import functools
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
# The search behind kalshi.com's search box. It is not part of the documented
# trade API, so it may change without notice.
SEARCH_URL = "https://api.elections.kalshi.com/v1/search/series"
PAGE_SIZE = 1000

session = requests.Session()


def get(path, params=None, retries=6):
    """GET with backoff on rate limits and transient server errors.

    path is relative to BASE_URL unless it is a full URL.
    """
    url = path if path.startswith("https://") else BASE_URL + path
    for attempt in range(retries):
        resp = session.get(url, params=params, timeout=30)
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(2**attempt)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"GET {path} kept failing (last status {resp.status_code})")


def get_or_none(path, params=None):
    """Like get, but None when Kalshi has nothing at that path."""
    try:
        return get(path, params)
    except requests.HTTPError as err:
        if err.response.status_code == 404:
            return None
        raise


def is_combo(ticker):
    # Multivariate "combo" markets are parlays of other markets, not standalone ones.
    return ticker.startswith("KXMVE")


def fetch_trades(ticker, max_pages, **filters):
    """Most recent trades for one market, newest first, up to max_pages pages.

    filters are passed to the API as-is, e.g. min_ts / max_ts in Unix seconds.
    """
    rows, params = [], {"limit": PAGE_SIZE, "ticker": ticker, **filters}
    for _ in range(max_pages):
        page = get("/markets/trades", params)
        rows.extend(page["trades"])
        if not page.get("cursor") or not page["trades"]:
            break
        params["cursor"] = page["cursor"]
    return rows


def trades_frame(rows):
    """Trades as returned by the API -> the DataFrame layout dataset.py expects."""
    return pd.DataFrame(
        {
            "ticker": [r["ticker"] for r in rows],
            "created_time": pd.to_datetime(
                [r["created_time"] for r in rows], format="ISO8601", utc=True
            ),
            "yes_price": [float(r["yes_price_dollars"]) for r in rows],
            "count": [float(r["count_fp"]) for r in rows],
            "taker_side": [r["taker_side"] for r in rows],
        }
    )


def parse_ticker(target):
    """The ticker in a pasted ticker or kalshi.com link, upper-cased.

    Links end in the ticker of what they show, e.g.
    https://kalshi.com/markets/kxbtc15m/bitcoin-price-up-down/kxbtc15m-26oct251900
    """
    text = target.strip().split("#")[0].split("?")[0].rstrip("/")
    return text.split("/")[-1].upper()


def open_events(series_ticker):
    """Open events in a series, with their markets, soonest to close first."""
    found = get_or_none(
        "/events", {"series_ticker": series_ticker, "status": "open", "with_nested_markets": "true"}
    )
    events = [e for e in (found or {}).get("events", []) if e.get("markets")]
    return sorted(events, key=lambda e: min(m["close_time"] for m in e["markets"]))


def find_markets(target):
    """Ticker named by a ticker or kalshi.com link, and its markets (None if Kalshi has none).

    The ticker may name one market, a whole event (every market in it is
    returned), or a series, which picks the open event in it that closes next.
    """
    ticker = parse_ticker(target)
    if not ticker:
        return ticker, None
    found = get_or_none(f"/markets/{ticker}")
    if found:
        return ticker, [found["market"]]
    found = get_or_none(f"/events/{ticker}")
    if found and found.get("markets"):
        return ticker, found["markets"]
    events = open_events(ticker)
    if events:
        return events[0]["event_ticker"], events[0]["markets"]
    return ticker, None


def dollars(value):
    """A *_dollars string from the API as a float; None when missing."""
    return None if value in (None, "") else float(value)


def search(query, limit=20, category=None):
    """Open events matching free text, busiest first, each with its open markets."""
    params = {"query": query.strip(), "page_size": limit, "status": "open"}
    if category:
        params["category"] = category
    events = []
    for e in get(SEARCH_URL, params).get("current_page", []):
        markets = [
            {
                "ticker": m["ticker"],
                "name": m.get("yes_subtitle") or m.get("title", ""),
                "yes_bid": dollars(m.get("yes_bid_dollars")),
                "yes_ask": dollars(m.get("yes_ask_dollars")),
                "last_price": dollars(m.get("last_price_dollars")),
                "close_time": m.get("close_ts"),
                "volume": m.get("volume", 0),
            }
            for m in e.get("markets", [])
            if not is_combo(m["ticker"]) and not m.get("result")
        ]
        if not markets:
            continue
        events.append(
            {
                "event_ticker": e["event_ticker"],
                "series_ticker": e.get("series_ticker", ""),
                "title": e.get("event_title", ""),
                "subtitle": e.get("event_subtitle", ""),
                "category": e.get("category", ""),
                "volume": e.get("recent_volume", 0),
                "markets": sorted(markets, key=lambda m: -m["volume"]),
            }
        )
    return events


@functools.lru_cache(maxsize=None)
def series_info(series_ticker):
    """A series' metadata (title, category, fee_multiplier, ...). Cached; errors are not."""
    return get(f"/series/{series_ticker}")["series"]


def series_of(market):
    return market["event_ticker"].split("-")[0]


def market_summary(m, category=""):
    """The fields the dashboard shows for a market from the markets endpoint."""
    return {
        "ticker": m["ticker"],
        "event_ticker": m["event_ticker"],
        "title": m.get("title", ""),
        "name": m.get("yes_sub_title") or m.get("title", ""),
        "category": category,
        "status": m.get("status", ""),
        "yes_bid": dollars(m.get("yes_bid_dollars")),
        "yes_ask": dollars(m.get("yes_ask_dollars")),
        "last_price": dollars(m.get("last_price_dollars")),
        "previous_price": dollars(m.get("previous_price_dollars")),
        "volume_24h": float(m.get("volume_24h_fp") or 0),
        "open_interest": float(m.get("open_interest_fp") or 0),
        "close_time": m.get("close_time"),
    }


def categories(markets):
    """{series ticker: category} for these markets, looked up in parallel."""
    names = sorted({series_of(m) for m in markets})

    def category(name):
        try:
            return series_info(name).get("category", "")
        except (requests.RequestException, RuntimeError, KeyError):
            return ""

    with ThreadPoolExecutor(8) as pool:
        return dict(zip(names, pool.map(category, names)))


def tape_activity(pages=4):
    """How much each market traded in the last few pages of the exchange-wide
    trade tape (a few thousand trades, usually the last 10-30 seconds).

    Returns ({ticker: {trades, yes_taker_share, last_trade}} busiest first,
    seconds of trading the sample covers).
    """
    rows, params = [], {"limit": PAGE_SIZE}
    for _ in range(pages):
        page = get("/markets/trades", params)
        rows.extend(r for r in page["trades"] if not is_combo(r["ticker"]))
        if not page.get("cursor"):
            break
        params["cursor"] = page["cursor"]
    if not rows:
        return {}, 0.0
    times = pd.to_datetime([r["created_time"] for r in rows], format="ISO8601", utc=True)
    counts, yes_takers, last_seen = Counter(), Counter(), {}
    for r, ts in zip(rows, times):
        counts[r["ticker"]] += 1
        yes_takers[r["ticker"]] += r["taker_side"] == "yes"
        last_seen[r["ticker"]] = max(last_seen.get(r["ticker"], ts), ts)
    activity = {
        ticker: {
            "trades": n,
            "yes_taker_share": yes_takers[ticker] / n,
            "last_trade": last_seen[ticker].isoformat(),
        }
        for ticker, n in counts.most_common()
    }
    return activity, (times.max() - times.min()).total_seconds()


def get_markets(tickers):
    """Market dicts for these tickers in the same order, skipping unknown ones."""
    if not tickers:
        return []
    found = get("/markets", {"tickers": ",".join(tickers), "limit": len(tickers)})["markets"]
    by_ticker = {m["ticker"]: m for m in found}
    return [by_ticker[t] for t in tickers if t in by_ticker]


def live_markets(limit=24, pages=4):
    """Summaries of the markets trading the most right now, busiest first,
    and the seconds of trading that ranking is based on."""
    activity, span_s = tape_activity(pages)
    markets = get_markets(list(activity)[:limit])
    cats = categories(markets)
    summaries = [
        dict(market_summary(m, cats.get(series_of(m), "")), **activity[m["ticker"]]) for m in markets
    ]
    return summaries, span_s


def orderbook(ticker, depth=8):
    """Resting bids for Yes and No as [price, contracts] pairs, best first.

    Kalshi only lists bids: a No bid at p is also an offer to sell Yes at 1 - p.
    """
    book = get(f"/markets/{ticker}/orderbook", {"depth": depth}).get("orderbook_fp") or {}

    def levels(side):
        return sorted(([float(p), float(q)] for p, q in book.get(side) or []), reverse=True)

    return {"yes": levels("yes_dollars"), "no": levels("no_dollars")}


# Range name -> (seconds of history, candle length in minutes). Kalshi returns
# at most 5000 candles and only offers 1, 60 and 1440 minute candles.
HISTORY_RANGES = {"1h": (3600, 1), "6h": (6 * 3600, 1), "1d": (86400, 1), "1w": (7 * 86400, 60)}


def price_history(ticker, event_ticker, span="6h"):
    """Candles as {t, price, yes_bid, yes_ask, volume}, oldest first.

    Candles only exist for periods where something changed. price is the last
    traded price, carried forward through candles without trades.
    """
    seconds, minutes = HISTORY_RANGES[span]
    end = int(time.time())
    found = get(
        f"/series/{event_ticker.split('-')[0]}/markets/{ticker}/candlesticks",
        {"start_ts": end - seconds, "end_ts": end, "period_interval": minutes},
    )
    candles, last = [], None
    for c in found.get("candlesticks", []):
        price = c.get("price") or {}
        last = dollars(price.get("close_dollars")) or dollars(price.get("previous_dollars")) or last
        candles.append(
            {
                "t": c["end_period_ts"],
                "price": last,
                "yes_bid": dollars((c.get("yes_bid") or {}).get("close_dollars")),
                "yes_ask": dollars((c.get("yes_ask") or {}).get("close_dollars")),
                "volume": float(c.get("volume_fp") or 0),
            }
        )
    return candles
