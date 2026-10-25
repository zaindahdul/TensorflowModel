"""Thin client for Kalshi's public market data API. No API key is needed."""
import time

import pandas as pd
import requests

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
PAGE_SIZE = 1000

session = requests.Session()


def get(path, params=None, retries=6):
    """GET with backoff on rate limits and transient server errors."""
    for attempt in range(retries):
        resp = session.get(BASE_URL + path, params=params, timeout=30)
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
