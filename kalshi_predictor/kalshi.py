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
