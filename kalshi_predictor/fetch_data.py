"""Download public trade history for a mix of high-volume Kalshi markets.

No API key is needed: trades and market metadata are public endpoints.

    python -m kalshi_predictor.fetch_data --markets 400 --max-pages 5
"""
import argparse
import time
from collections import Counter

import pandas as pd
import requests

from .paths import DATA_DIR

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


def is_combo(ticker):
    # Multivariate "combo" markets are parlays of other markets, not standalone ones.
    return ticker.startswith("KXMVE")


def discover_tickers(n_markets, per_series, days, slices, pages_per_slice):
    """Rank tickers by how often they appear in samples of the global trade feed.

    The markets endpoint cannot be sorted by volume and lists tens of thousands of
    markets, so instead we sample the exchange-wide trade tape at evenly spaced
    points over the last `days` days and count trades per ticker.
    """
    now = int(time.time())
    counts = Counter()
    for i in range(slices):
        params = {"limit": PAGE_SIZE, "max_ts": now - i * days * 86400 // slices}
        for _ in range(pages_per_slice):
            page = get("/markets/trades", params)
            counts.update(t["ticker"] for t in page["trades"] if not is_combo(t["ticker"]))
            if not page.get("cursor"):
                break
            params["cursor"] = page["cursor"]
        print(f"\rsampling trade tape {i + 1}/{slices}", end="", flush=True)
    print()

    chosen, used = [], Counter()
    for ticker, _ in counts.most_common():
        series = ticker.split("-")[0]
        if used[series] >= per_series:
            continue
        used[series] += 1
        chosen.append(ticker)
        if len(chosen) == n_markets:
            break
    return chosen


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


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--markets", type=int, default=400, help="number of markets to download")
    ap.add_argument("--per-series", type=int, default=15, help="max markets from one series")
    ap.add_argument("--max-pages", type=int, default=5, help="max pages of 1000 trades per market")
    ap.add_argument("--days", type=int, default=3, help="look-back used to find active markets")
    ap.add_argument("--slices", type=int, default=72)
    ap.add_argument("--pages-per-slice", type=int, default=2)
    args = ap.parse_args()

    tickers = discover_tickers(
        args.markets, args.per_series, args.days, args.slices, args.pages_per_slice
    )
    print(f"selected {len(tickers)} markets")

    trades, markets = [], []
    for i, ticker in enumerate(tickers, 1):
        # Recorded before the download so no label can look past what we observed.
        fetched_at = pd.Timestamp.now(tz="UTC")
        rows = fetch_trades(ticker, args.max_pages)
        market = get(f"/markets/{ticker}")["market"]
        trades.extend(rows)
        markets.append(
            {
                "ticker": ticker,
                "title": market.get("title", ""),
                "status": market.get("status", ""),
                "close_time": market["close_time"],
                "fetched_at": fetched_at,
            }
        )
        print(f"[{i}/{len(tickers)}] {ticker}: {len(rows)} trades")

    trades_df = pd.DataFrame(trades).drop_duplicates("trade_id")
    trades_df = pd.DataFrame(
        {
            "ticker": trades_df["ticker"],
            "created_time": pd.to_datetime(trades_df["created_time"], format="ISO8601", utc=True),
            "yes_price": trades_df["yes_price_dollars"].astype(float),
            "count": trades_df["count_fp"].astype(float),
            "taker_side": trades_df["taker_side"],
        }
    )
    markets_df = pd.DataFrame(markets)
    markets_df["close_time"] = pd.to_datetime(markets_df["close_time"], format="ISO8601", utc=True)

    DATA_DIR.mkdir(exist_ok=True)
    trades_df.to_parquet(DATA_DIR / "trades.parquet", index=False)
    markets_df.to_parquet(DATA_DIR / "markets.parquet", index=False)
    print(f"saved {len(trades_df):,} trades across {len(markets_df)} markets to {DATA_DIR}")


if __name__ == "__main__":
    main()
